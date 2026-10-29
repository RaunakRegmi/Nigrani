import logging
import os
import secrets
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from itertools import count

import joblib
import jwt
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from fraud_agents import (
    behavior_agent,
    clear_velocity_cache,
    graph_agent,
    most_restrictive,
    sim_swap_agent,
    velocity_agent,
)

# Load DATABASE_URL from .env (never hardcode credentials)
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
MODEL_PATH = os.getenv("MODEL_PATH", "frozen_model.pkl")

# JWT signing key. Without NIGRANI_JWT_SECRET a random key is generated per
# process, so tokens stop working whenever the API restarts.
JWT_SECRET = os.getenv("NIGRANI_JWT_SECRET") or secrets.token_urlsafe(32)
JWT_ALGORITHM = "HS256"
JWT_TTL = timedelta(hours=8)

# Mock identity store for the demo -- not for production use
DEMO_USERS = {
    "sandbox": {"password": "demo", "role": "wallet"},
    "analyst": {"password": "demo", "role": "admin"},
}

# SHAP: exact TreeSHAP by default. "approx" (Saabas) is much faster on large
# forests but less faithful; switch with NIGRANI_SHAP_MODE=approx if latency hurts.
SHAP_APPROXIMATE = os.getenv("NIGRANI_SHAP_MODE", "exact").lower() == "approx"
SHAP_TOP_N = 3

HISTORY_SIZE = 50

# Must match train_engine.py exactly
FEATURES = [
    "amount",
    "txn_count_1h",
    "txn_count_24h",
    "total_amount_24h_npr",
    "weekend_flag",
    "churn_risk_score",
    "impossible_travel",
    "is_vpn",
    "km_from_home_district",
    "degree_in",
    "degree_out",
    "kyc_tier",
    "risk_tier",
    "age_group",
]
BLOCK_THRESHOLD = 0.80
OTP_THRESHOLD = 0.40

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nigrani.api")

# Velocity of the account's history over the trailing 1h/24h windows, measured
# from "now" on the database clock (same clock and naive TIMESTAMP type as the
# stored transactions). An account with no recent history gets 0 everywhere.
VELOCITY_SQL = text("""
SELECT
    COUNT(*) FILTER (
        WHERE transaction_timestamp >= LOCALTIMESTAMP - INTERVAL '1 HOUR'
    ) AS txn_count_1h,
    COUNT(*) AS txn_count_24h,
    COALESCE(SUM(amount_npr), 0) AS total_amount_24h_npr,
    EXTRACT(ISODOW FROM LOCALTIMESTAMP) IN (6, 7) AS weekend_flag
FROM features.velocity_snapshots
WHERE account_id = :account_id
  AND transaction_timestamp >= LOCALTIMESTAMP - INTERVAL '24 HOURS'
  AND transaction_timestamp <= LOCALTIMESTAMP
""")

# Latest KYC profile for the account, normalized exactly like train_engine.py.
# No row -> 'UNKNOWN' categories and a NaN churn score (median-imputed by the
# pipeline), the same treatment accounts without a profile got in training.
PROFILE_SQL = text("""
SELECT
    CAST(NULLIF(TRIM(churn_risk_score), '') AS NUMERIC) AS churn_risk_score,
    COALESCE(NULLIF(UPPER(TRIM(kyc_tier)), ''), 'UNKNOWN') AS kyc_tier,
    COALESCE(NULLIF(UPPER(TRIM(risk_tier)), ''), 'UNKNOWN') AS risk_tier,
    COALESCE(NULLIF(UPPER(TRIM(age_group)), ''), 'UNKNOWN') AS age_group
FROM raw.customer_profiles
WHERE TRIM(account_id) = TRIM(:account_id)
ORDER BY uploaded_at DESC NULLS LAST, ctid DESC
LIMIT 1
""")
NO_PROFILE = {"churn_risk_score": None, "kyc_tier": "UNKNOWN", "risk_tier": "UNKNOWN", "age_group": "UNKNOWN"}

# Latest graph node for the account. No node -> degrees of 0, the same
# default train_engine.py applies.
GRAPH_SQL = text("""
SELECT
    COALESCE(CAST(NULLIF(TRIM(degree_in), '') AS NUMERIC), 0) AS degree_in,
    COALESCE(CAST(NULLIF(TRIM(degree_out), '') AS NUMERIC), 0) AS degree_out
FROM raw.account_graph_nodes
WHERE TRIM(id) = TRIM(:account_id)
ORDER BY uploaded_at DESC NULLS LAST, ctid DESC
LIMIT 1
""")
NO_GRAPH_NODE = {"degree_in": 0, "degree_out": 0}


class TransactionRequest(BaseModel):
    account_id: str = Field(..., min_length=1, max_length=64)
    amount: float = Field(..., gt=0)
    transaction_type: str = Field(..., min_length=1, max_length=32)
    ip_address: str = Field(..., min_length=1, max_length=45)
    # Live geo telemetry from the caller. A live transaction has no row in
    # raw.geo_events yet, so these come in the payload. Defaults match the
    # training defaults for transactions without a geo event.
    is_vpn: bool = False
    impossible_travel: bool = False
    km_from_home_district: float = Field(0.0, ge=0)
    # Checked by the SIM Swap Agent; not a model feature
    device_id: str = Field(..., min_length=1, max_length=64)
    # Recipient of a wallet transfer; recorded for analysts, not a model feature
    target_account: str | None = Field(None, max_length=64)
    # Behavior Agent context. hour_of_day defaults to the server's current
    # hour; without a district the location check is skipped.
    hour_of_day: int | None = Field(None, ge=0, le=23)
    district: str | None = Field(None, max_length=64)


class AgentFlag(BaseModel):
    agent: str
    decision: str
    reason: str
    detail: str


class ScoreResponse(BaseModel):
    transaction_ref: int
    account_id: str
    fraud_score: float
    decision: str            # final: most restrictive of ml_decision and agent decisions
    ml_decision: str
    agent_flags: list[AgentFlag]
    # Top features pushing the ML score up (+) or down (-), in probability units
    shap_values: dict[str, float]


class Token(BaseModel):
    access_token: str
    token_type: str
    role: str
    expires_in: int


# ---------------------------------------------------------------- auth

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/token", auto_error=False)


def current_user(token: str | None = Depends(oauth2_scheme)) -> dict:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise unauthorized
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise unauthorized
    if claims.get("role") not in {u["role"] for u in DEMO_USERS.values()}:
        raise unauthorized
    return {"username": claims["sub"], "role": claims["role"]}


def require_role(*roles: str):
    def check(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user
    return check


# ---------------------------------------------------------------- SHAP

class ShapExplainer:
    """TreeSHAP over the pipeline's Random Forest, reported per original feature.

    The forest sees preprocessed columns (scaled numerics + one-hot
    categoricals), so one-hot contributions are summed back to their source
    feature: 'risk_tier_HIGH' and 'risk_tier_LOW' both report as 'risk_tier'.
    """

    def __init__(self, pipeline):
        import shap  # heavy import, only paid when SHAP is enabled

        self.preprocess = pipeline[:-1]
        self.explainer = shap.TreeExplainer(pipeline[-1])
        self.lock = threading.Lock()

        out_names = self.preprocess.get_feature_names_out()
        self.groups = np.array([self._source_feature(name) for name in out_names])

    @staticmethod
    def _source_feature(out_name: str) -> str:
        _, _, name = out_name.partition("__")   # 'num__amount' / 'cat__risk_tier_HIGH'
        for feature in FEATURES:
            if name == feature or name.startswith(feature + "_"):
                return feature
        return name

    def top_contributions(self, features: pd.DataFrame, n: int = SHAP_TOP_N) -> dict[str, float]:
        X = self.preprocess.transform(features)
        if hasattr(X, "toarray"):
            X = X.toarray()
        with self.lock:
            values = self.explainer.shap_values(X, check_additivity=False, approximate=SHAP_APPROXIMATE)
        # shap returns a per-class list (older) or an (n, features, classes) array
        fraud = values[1] if isinstance(values, list) else values[..., 1]
        per_feature = pd.Series(np.asarray(fraud)[0], index=self.groups).groupby(level=0).sum()
        top = per_feature.reindex(per_feature.abs().sort_values(ascending=False).index).head(n)
        return {feature: round(float(v), 4) for feature, v in top.items()}


# ---------------------------------------------------------------- live history

class TransactionHistory:
    """Last N scored transactions, newest first. In process memory: per worker,
    cleared on restart."""

    def __init__(self, maxlen=HISTORY_SIZE):
        self._items = deque(maxlen=maxlen)
        self._ids = count(1)
        self._lock = threading.Lock()

    def next_id(self) -> int:
        return next(self._ids)

    def append(self, record: dict):
        with self._lock:
            self._items.appendleft(record)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._items)


transaction_history = TransactionHistory()


# ---------------------------------------------------------------- app

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Add it to a .env file next to api.py.")

    # Fail fast: refuse to start without a model that matches the feature contract
    model = joblib.load(MODEL_PATH)
    trained_on = list(getattr(model, "feature_names_in_", []))
    if trained_on != FEATURES:
        raise RuntimeError(f"Model features {trained_on} do not match expected {FEATURES}")

    app.state.model = model
    app.state.velocity_agent = velocity_agent
    app.state.engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10)
    log.info("Loaded %s (%s)", MODEL_PATH, type(model).__name__)

    # Explainability is best-effort: scoring must keep working without it
    try:
        app.state.explainer = ShapExplainer(model)
        log.info("SHAP explainer ready (%s)", "approximate" if SHAP_APPROXIMATE else "exact")
    except Exception:
        log.exception("SHAP explainer unavailable; responses will carry empty shap_values")
        app.state.explainer = None

    if not os.getenv("NIGRANI_JWT_SECRET"):
        log.warning("NIGRANI_JWT_SECRET not set: using a random key, tokens expire on restart")
    yield
    app.state.engine.dispose()


app = FastAPI(title="NIGRANI Fraud Scoring API", version="2.0.0", lifespan=lifespan)

# Browser clients (nigrani-client on Vite) live on another origin. Comma-separated
# override for deployments; credentials aren't needed since auth is a Bearer header.
CORS_ORIGINS = os.getenv("NIGRANI_CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


def decide(score: float) -> str:
    if score > BLOCK_THRESHOLD:
        return "BLOCK"
    if score >= OTP_THRESHOLD:
        return "OTP"
    return "ALLOW"


@app.post("/api/v1/token", response_model=Token)
def issue_token(form: OAuth2PasswordRequestForm = Depends()):
    user = DEMO_USERS.get(form.username)
    if not user or not secrets.compare_digest(form.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    now = datetime.now(timezone.utc)
    claims = {"sub": form.username, "role": user["role"], "iat": now, "exp": now + JWT_TTL}
    return Token(
        access_token=jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGORITHM),
        token_type="bearer",
        role=user["role"],
        expires_in=int(JWT_TTL.total_seconds()),
    )


# Sync handler on purpose: FastAPI runs it in a threadpool, so the blocking
# DB query and CPU-bound predict_proba/SHAP don't stall the event loop.
@app.post("/api/v1/score-transaction", response_model=ScoreResponse)
def score_transaction(
    txn: TransactionRequest,
    request: Request,
    user: dict = Depends(require_role("wallet", "admin")),
):
    if txn.hour_of_day is None:
        txn.hour_of_day = datetime.now().hour

    # Deterministic agents run first. Velocity, SIM swap and behavior need no
    # DB, so a burst is still recorded even if the feature store is down.
    verdicts = [
        request.app.state.velocity_agent.evaluate(txn.account_id),
        sim_swap_agent(txn.account_id, txn.device_id),
        behavior_agent.evaluate(txn.account_id, txn.amount, txn.hour_of_day, txn.district),
    ]

    try:
        with request.app.state.engine.connect() as conn:
            history = conn.execute(VELOCITY_SQL, {"account_id": txn.account_id}).mappings().one()
            profile = conn.execute(PROFILE_SQL, {"account_id": txn.account_id}).mappings().first() or NO_PROFILE
            node = conn.execute(GRAPH_SQL, {"account_id": txn.account_id}).mappings().first() or NO_GRAPH_NODE
    except SQLAlchemyError:
        log.exception("Velocity lookup failed for account %s", txn.account_id)
        raise HTTPException(status_code=503, detail="Feature store unavailable")

    # degree_in comes from the account graph, not the payload: a caller can't
    # talk its way past the mule check by under-reporting it.
    verdicts.append(graph_agent(float(node["degree_in"]), txn.amount))
    agent_flags = [v for v in verdicts if v is not None]

    # Training windows include the current transaction (RANGE ... CURRENT ROW),
    # so fold the live transaction into the history the same way.
    features = pd.DataFrame(
        [{
            "amount": txn.amount,
            "txn_count_1h": int(history["txn_count_1h"]) + 1,
            "txn_count_24h": int(history["txn_count_24h"]) + 1,
            "total_amount_24h_npr": float(history["total_amount_24h_npr"]) + txn.amount,
            "weekend_flag": float(bool(history["weekend_flag"])),
            "churn_risk_score": (
                float("nan") if profile["churn_risk_score"] is None else float(profile["churn_risk_score"])
            ),
            "impossible_travel": float(txn.impossible_travel),
            "is_vpn": float(txn.is_vpn),
            "km_from_home_district": txn.km_from_home_district,
            "degree_in": float(node["degree_in"]),
            "degree_out": float(node["degree_out"]),
            "kyc_tier": profile["kyc_tier"],
            "risk_tier": profile["risk_tier"],
            "age_group": profile["age_group"],
        }],
        columns=FEATURES,
    )

    fraud_score = float(request.app.state.model.predict_proba(features)[0, 1])
    ml_decision = decide(fraud_score)

    shap_values = {}
    explainer = request.app.state.explainer
    if explainer is not None:
        started = time.perf_counter()
        try:
            shap_values = explainer.top_contributions(features)
        except Exception:
            log.exception("SHAP failed for account %s; returning without explanation", txn.account_id)
        log.debug("SHAP took %.0f ms", (time.perf_counter() - started) * 1000)

    # Override logic: the most restrictive decision wins (BLOCK > DUAL_OTP > OTP > ALLOW)
    decision = most_restrictive([ml_decision] + [v.decision for v in agent_flags])
    log.info(
        "user=%s account=%s score=%.4f ml=%s final=%s agents=%s",
        user["username"], txn.account_id, fraud_score, ml_decision, decision,
        [v.agent for v in agent_flags] or "-",
    )

    ref = transaction_history.next_id()
    flags = [v.to_dict() for v in agent_flags]
    transaction_history.append({
        "transaction_ref": ref,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "submitted_by": user["username"],
        "payload": txn.model_dump(),
        "fraud_score": round(fraud_score, 4),
        "ml_decision": ml_decision,
        "decision": decision,
        "agent_flags": flags,
        "shap_values": shap_values,
    })

    return ScoreResponse(
        transaction_ref=ref,
        account_id=txn.account_id,
        fraud_score=round(fraud_score, 4),
        decision=decision,
        ml_decision=ml_decision,
        agent_flags=flags,
        shap_values=shap_values,
    )


@app.get("/api/v1/live-transactions")
def live_transactions(user: dict = Depends(require_role("admin"))):
    return transaction_history.snapshot()


@app.post("/api/v1/clear-cache")
def clear_cache(request: Request, user: dict = Depends(require_role("admin"))):
    clear_velocity_cache()
    log.warning("Velocity cache cleared by %s from %s", user["username"],
                request.client.host if request.client else "unknown")
    return {"status": "success", "message": "Velocity cache cleared"}


@app.get("/health")
def health():
    return {"status": "ok"}
