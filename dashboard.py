import math
import os
import random
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# NIGRANI_API_BASE wins; NIGRANI_API_URL (the old score-endpoint setting) still works
API_BASE = os.getenv("NIGRANI_API_BASE") or os.getenv(
    "NIGRANI_API_URL", "http://127.0.0.1:8000/api/v1/score-transaction"
).rsplit("/", 1)[0]
REQUEST_TIMEOUT_S = 15
REFRESH_MS = 2000

# The transaction types present in core.transactions
TRANSACTION_TYPES = [
    "ESEWA_P2P",
    "KHALTI_QR",
    "CARD_POS",
    "ATM_WITHDRAWAL",
    "MOBILE_TOPUP",
    "UTILITY_BILL",
    "SWIFT_OUTWARD",
    "RTGS",
]

# Status = icon + label, never color alone
DECISION_LABELS = {
    "ALLOW": "🟢 ALLOW",
    "OTP": "🟡 OTP",
    "DUAL_OTP": "🟠 DUAL OTP",
    "BLOCK": "🔴 BLOCK",
}
FEATURE_LABELS = {
    "amount": "Amount",
    "txn_count_1h": "Txns in last 1h",
    "txn_count_24h": "Txns in last 24h",
    "total_amount_24h_npr": "Amount in last 24h",
    "weekend_flag": "Weekend",
    "churn_risk_score": "Churn risk score",
    "impossible_travel": "Impossible travel",
    "is_vpn": "VPN",
    "km_from_home_district": "Distance from home",
    "degree_in": "Inbound connections",
    "degree_out": "Outbound connections",
    "kyc_tier": "KYC tier",
    "risk_tier": "Risk tier",
    "age_group": "Age group",
}
# Diverging pair (validated palette): red pushes toward fraud, blue toward legit
TOWARD_FRAUD = "#e34948"
TOWARD_LEGIT = "#2a78d6"
CRITICAL = "#d03b3b"
NEUTRAL_NODE = "#8a8984"

st.set_page_config(page_title="Nigrani Live Terminal", page_icon="🛡️", layout="wide")

st.markdown(
    """
    <style>
    .st-key-wallet {
        max-width: 430px;
        margin: 0 auto;
        padding: 0 0 1.25rem 0;
        border-radius: 28px;
        border: 1px solid rgba(128, 128, 128, 0.28);
        box-shadow: 0 18px 48px rgba(0, 0, 0, 0.12);
        overflow: hidden;
    }
    .st-key-wallet > div { padding-left: 1.25rem; padding-right: 1.25rem; }
    .wallet-header {
        margin: 0 -1.25rem 0.75rem -1.25rem;
        padding: 1.4rem 1.5rem 1.2rem 1.5rem;
        background: linear-gradient(135deg, #6250d6 0%, #2a78d6 100%);
        color: #ffffff;
    }
    .wallet-header .brand { font-size: 0.85rem; letter-spacing: 0.08em; opacity: 0.9; }
    .wallet-header .balance { font-size: 1.9rem; font-weight: 700; margin-top: 0.35rem; }
    .wallet-header .caption { font-size: 0.8rem; opacity: 0.85; }
    .st-key-login { max-width: 400px; margin: 4rem auto 0 auto; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------- API client

def api_call(method: str, path: str, *, auth: bool = True, **kwargs):
    """Call the API without ever raising. Returns (data, error_message)."""
    headers = kwargs.pop("headers", {})
    if auth and st.session_state.get("token"):
        headers["Authorization"] = f"Bearer {st.session_state.token}"
    try:
        response = requests.request(method, f"{API_BASE}{path}", headers=headers, timeout=REQUEST_TIMEOUT_S, **kwargs)
    except requests.exceptions.ConnectionError:
        return None, ("Cannot reach the fraud scoring API. Start it with "
                      "`venv/bin/uvicorn api:app --host 127.0.0.1 --port 8000`.")
    except requests.exceptions.Timeout:
        return None, f"The API did not respond within {REQUEST_TIMEOUT_S}s."
    except requests.exceptions.RequestException:
        return None, "Request to the API failed."

    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}

    if response.ok:
        return body, None
    if response.status_code == 401 and auth:
        # Expired token, or the API restarted with a new signing key
        logout("Your session expired. Please sign in again.")
    if response.status_code == 422:
        fields = ", ".join(str(e.get("loc", ["?"])[-1]) for e in body.get("detail", []) if isinstance(e, dict))
        return None, f"Invalid input: {fields or 'check the form'}."
    detail = body.get("detail") if isinstance(body, dict) else None
    return None, f"{detail or response.reason} ({response.status_code})"


def logout(message: str | None = None):
    for key in ("token", "role", "username", "last_snapshot", "last_receipt", "selected_ref"):
        st.session_state.pop(key, None)
    if message:
        st.session_state.flash = message
    st.rerun()


# ---------------------------------------------------------------- login

def login_screen():
    with st.container(key="login"):
        st.title("🛡️ Nigrani")
        st.caption("Fraud Operations · Demo Environment")
        if msg := st.session_state.pop("flash", None):
            st.info(msg)
        with st.form("login"):
            username = st.text_input("Username", placeholder="sandbox or analyst")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        st.caption("Demo accounts: `sandbox` (Digital Wallet) · `analyst` (Analyst Monitor) · password `demo`")

        if submitted:
            data, error = api_call(
                "POST", "/token", auth=False,
                data={"username": username.strip(), "password": password},
            )
            if error:
                st.error("Incorrect username or password." if "401" in error else error)
            else:
                st.session_state.token = data["access_token"]
                st.session_state.role = data["role"]
                st.session_state.username = username.strip()
                st.rerun()


# ---------------------------------------------------------------- Digital Wallet

def digital_wallet():
    _, phone, _ = st.columns([1, 1.3, 1])
    with phone, st.container(key="wallet"):
        st.markdown(
            """
            <div class="wallet-header">
              <div class="brand">NIGRANI PAY · SANDBOX</div>
              <div class="balance">NPR 2,48,750.00</div>
              <div class="caption">Available balance · demo wallet</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.form("send_money", border=False):
            account_id = st.text_input("From account", value="ACC-8622639")
            target_account = st.text_input("To account", value="ACC-2522726")
            amount = st.number_input("Amount (NPR)", min_value=1.0, value=2500.0, step=500.0, format="%.2f")
            transaction_type = st.selectbox("Payment method", TRANSACTION_TYPES)
            device_id = st.text_input("Device ID", value="DEV-0AE666",
                                      help="DEV-0AE666 is enrolled for ACC-8622639; anything else trips the SIM Swap Agent.")

            with st.expander("Sandbox conditions (time, location, network)"):
                st.caption("Demo triggers: VPN + impossible travel → ML risk · unknown device → SIM Swap · "
                           "from `ACC-0011204` over NPR 50,000 → Mule Network · 4 sends in a minute → Velocity · "
                           "2 of {night hour 0–5, new district, amount > 5× usual (NPR 20,000 for ACC-8622639)} → Behavior")
                hour_of_day = st.slider("Time of day (hour)", 0, 23, datetime.now().hour, format="%d:00")
                district = st.text_input("Current district", value="Kathmandu",
                                         help="ACC-8622639 usually transacts from Kathmandu or Lalitpur.")
                ip_address = st.text_input("IP address", value="112.196.26.109")
                is_vpn = st.toggle("Connected via VPN")
                impossible_travel = st.toggle("Impossible travel")
                km_from_home_district = st.slider("Distance from home district (km)", 0, 1000, 0, step=10)

            send = st.form_submit_button("Send Money", type="primary", width="stretch")

        if send:
            payload = {
                "account_id": account_id.strip(),
                "target_account": target_account.strip() or None,
                "amount": amount,
                "transaction_type": transaction_type,
                "device_id": device_id.strip(),
                "ip_address": ip_address.strip(),
                "is_vpn": is_vpn,
                "impossible_travel": impossible_travel,
                "km_from_home_district": float(km_from_home_district),
                "hour_of_day": int(hour_of_day),
                "district": district.strip() or None,
            }
            with st.spinner("Securing your payment..."):
                data, error = api_call("POST", "/score-transaction", json=payload)
            if error:
                st.error(error)
            else:
                st.session_state.last_receipt = {"payload": payload, "result": data}

        if receipt := st.session_state.get("last_receipt"):
            render_receipt(receipt["payload"], receipt["result"])


def render_receipt(payload: dict, result: dict):
    amount = f"NPR {payload['amount']:,.2f}"
    to = payload.get("target_account") or "recipient"
    decision = result.get("decision")
    if decision == "ALLOW":
        st.success(f"✅ **Payment sent** · {amount} to {to}")
    elif decision == "OTP":
        st.warning(f"🔐 **Verify it's you** · enter the OTP sent to your phone to send {amount}.")
    elif decision == "DUAL_OTP":
        st.warning(f"🔐🔐 **Extra verification needed** · confirm with OTPs on your phone *and* email to send {amount}.")
    elif decision == "BLOCK":
        st.error(f"⛔ **Payment blocked** · {amount} to {to} was stopped for your protection.")
    else:
        st.info(f"Decision: {decision}")
    st.caption(f"Ref #{result.get('transaction_ref', '—')} · risk check {float(result.get('fraud_score', 0)):.1%}")


# ---------------------------------------------------------------- Analyst Monitor

def analyst_monitor():
    live = st.session_state.get("live_updates", True)
    if live:
        st_autorefresh(interval=REFRESH_MS, key="live_poll")

    st.title("Analyst Monitor")
    data, error = api_call("GET", "/live-transactions")
    if error:
        # Keep showing the last good snapshot so the screen doesn't blank mid-demo
        st.warning(f"Live feed interrupted: {error}")
        data = st.session_state.get("last_snapshot", [])
    else:
        st.session_state.last_snapshot = data

    st.caption(f"{'Live · polling every 2s' if live else 'Paused'} · last {len(data)} transactions · "
               f"updated {datetime.now():%H:%M:%S}")

    if not data:
        st.info("No transactions yet. Sign in as `sandbox` in another tab and send money from the Digital Wallet.")
        return

    render_kpis(data)
    render_feed(data)
    st.divider()
    render_drilldown(data)


def render_kpis(data: list[dict]):
    decisions = pd.Series([t["decision"] for t in data])
    overridden = sum(t["decision"] != t["ml_decision"] for t in data)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Transactions", len(data))
    k2.metric("🔴 Blocked", int((decisions == "BLOCK").sum()))
    k3.metric("🟡 Step-up (OTP / Dual OTP)", int(decisions.isin(["OTP", "DUAL_OTP"]).sum()))
    k4.metric("Agent overrides", overridden, help="Final decision stricter than the ML model's own decision")


def render_feed(data: list[dict]):
    rows = [{
        "Ref": t["transaction_ref"],
        "Time (UTC)": t["timestamp"][11:19],
        "Account": t["payload"]["account_id"],
        "To": t["payload"].get("target_account") or "—",
        "Amount (NPR)": t["payload"]["amount"],
        "Type": t["payload"]["transaction_type"],
        "Hour": f"{t['payload']['hour_of_day']:02d}:00" if t["payload"].get("hour_of_day") is not None else "—",
        "District": t["payload"].get("district") or "—",
        "ML score": t["fraud_score"],
        "Decision": DECISION_LABELS.get(t["decision"], t["decision"]),
        "Agents": ", ".join(f["reason"] for f in t["agent_flags"]) or "—",
    } for t in data]
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        height=min(38 + 35 * len(rows), 360),
        column_config={
            "Amount (NPR)": st.column_config.NumberColumn(format="%,.2f"),
            "ML score": st.column_config.ProgressColumn(format="percent", min_value=0.0, max_value=1.0),
        },
    )


def render_drilldown(data: list[dict]):
    by_ref = {t["transaction_ref"]: t for t in data}
    # Selection is keyed by transaction_ref, not row position: the feed re-sorts
    # every refresh, and a row index would silently point at another transaction.
    if st.session_state.get("selected_ref") not in by_ref:
        st.session_state.pop("selected_ref", None)
    ref = st.selectbox(
        "Investigate a transaction",
        options=list(by_ref),
        index=None,
        key="selected_ref",
        placeholder="Select a transaction to drill down",
        format_func=lambda r: (f"#{r} · {by_ref[r]['payload']['account_id']} · "
                               f"NPR {by_ref[r]['payload']['amount']:,.0f} · "
                               f"{DECISION_LABELS.get(by_ref[r]['decision'], by_ref[r]['decision'])}"),
    )
    if ref is None:
        return

    txn = by_ref[ref]
    summary = f"**Final: {DECISION_LABELS.get(txn['decision'], txn['decision'])}** · ML model: {txn['ml_decision']} ({txn['fraud_score']:.1%})"
    if txn["agent_flags"]:
        summary += " · " + " · ".join(f"`{f['agent']}` → {f['decision']}" for f in txn["agent_flags"])
    st.markdown(summary)

    explain_pane, ring_pane = st.columns(2, gap="large")
    with explain_pane:
        st.subheader("Why the model scored it")
        render_shap(txn)
    with ring_pane:
        st.subheader("Fraud ring")
        mule = next((f for f in txn["agent_flags"] if f["agent"] == "graph_agent"), None)
        if mule:
            st.caption(f"{mule['reason']} · {mule['detail']}")
            render_fraud_ring(txn)
        else:
            st.info("No mule-network signature on this transaction. The Graph Agent did not fire.")

    with st.expander("Raw transaction record"):
        st.json(txn)


def render_shap(txn: dict):
    shap_values = txn.get("shap_values") or {}
    if not shap_values:
        st.info("No explanation available for this transaction (SHAP was unavailable on the API).")
        return

    items = sorted(shap_values.items(), key=lambda kv: abs(kv[1]), reverse=True)
    names = [FEATURE_LABELS.get(k, k) for k, _ in items]
    values = [v * 100 for _, v in items]  # probability -> percentage points

    fig = go.Figure()
    for label, color, keep in [("Pushes toward fraud", TOWARD_FRAUD, lambda v: v > 0),
                               ("Pushes toward legit", TOWARD_LEGIT, lambda v: v <= 0)]:
        sel = [(n, v) for n, v in zip(names, values) if keep(v)]
        fig.add_trace(go.Bar(
            name=label,
            orientation="h",
            y=[n for n, _ in sel],
            x=[v for _, v in sel],
            marker=dict(color=color, cornerradius=4),
            text=[f"{v:+.1f} pp" for _, v in sel],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{y}: %{x:+.2f} percentage points<extra>" + label + "</extra>",
        ))
    span = max(abs(v) for v in values) or 1
    fig.update_layout(
        height=280,
        margin=dict(l=10, r=40, t=10, b=10),
        bargap=0.45,
        barmode="relative",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis=dict(title="Contribution to fraud score (percentage points)", range=[-span * 1.35, span * 1.35],
                   zeroline=True, zerolinewidth=1, showgrid=True),
        yaxis=dict(categoryorder="array", categoryarray=names[::-1]),
    )
    st.plotly_chart(fig, width="stretch", theme="streamlit", config={"displayModeBar": False})
    st.caption(f"Top {len(items)} SHAP contributions to the ML score of {txn['fraud_score']:.1%}.")


def render_fraud_ring(txn: dict):
    collector = txn["payload"]["account_id"]
    # Seeded by the transaction so the mock ring is stable across 2s refreshes
    rng = random.Random(f"{collector}-{txn['transaction_ref']}")
    n = rng.randint(15, 20)

    senders = []
    for i in range(n):
        angle = 2 * math.pi * i / n + rng.uniform(-0.08, 0.08)
        radius = rng.uniform(0.82, 1.0)
        senders.append({
            "id": f"ACC-{rng.randint(1000000, 9999999)}",
            "x": radius * math.cos(angle),
            "y": radius * math.sin(angle),
            "amount": rng.choice([4_900, 9_800, 14_500, 19_900, 24_500, 49_000]),
            "minutes_ago": rng.randint(2, 180),
        })

    fig = go.Figure()
    # Red edges flowing into the collector, each with an arrowhead
    for s in senders:
        fig.add_annotation(
            x=0, y=0, ax=s["x"], ay=s["y"], xref="x", yref="y", axref="x", ayref="y",
            showarrow=True, arrowhead=2, arrowsize=1.1, arrowwidth=1.5, arrowcolor=TOWARD_FRAUD,
            standoff=24, opacity=0.75,
        )
    fig.add_trace(go.Scatter(
        x=[s["x"] for s in senders], y=[s["y"] for s in senders],
        mode="markers",
        marker=dict(size=14, color=NEUTRAL_NODE, line=dict(width=2, color="rgba(255,255,255,0.9)")),
        customdata=[[s["id"], s["amount"], s["minutes_ago"]] for s in senders],
        hovertemplate="Sender %{customdata[0]}<br>Sent NPR %{customdata[1]:,}<br>%{customdata[2]} min ago<extra></extra>",
        name="Sender (mock)",
    ))
    fig.add_trace(go.Scatter(
        x=[0], y=[0], mode="markers+text",
        marker=dict(size=40, color=CRITICAL, line=dict(width=3, color="rgba(255,255,255,0.9)")),
        text=[collector], textposition="bottom center", textfont=dict(size=13),
        hovertemplate=f"Collector {collector}<br>{n} inbound senders<extra></extra>",
        name="Collector",
    ))
    fig.update_layout(
        height=420,
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        xaxis=dict(visible=False, range=[-1.25, 1.25]),
        yaxis=dict(visible=False, range=[-1.25, 1.25], scaleanchor="x"),
        dragmode="pan",
    )
    st.plotly_chart(fig, width="stretch", theme="streamlit", config={"displayModeBar": False})
    total = sum(s["amount"] for s in senders)
    st.caption(f"{n} accounts → {collector} · NPR {total:,} inbound in 3h · simulated sender network (mock data)")


# ---------------------------------------------------------------- routing

if "token" not in st.session_state:
    login_screen()
    st.stop()

with st.sidebar:
    st.markdown(f"**{st.session_state.username}** · `{st.session_state.role}`")
    if st.button("Log out", width="stretch"):
        logout()
    if st.session_state.role == "admin":
        st.divider()
        st.header("Ops Tools")
        st.toggle("Live updates (2s)", value=True, key="live_updates")
        if st.button("🧹 Clear Velocity Cache", width="stretch"):
            data, error = api_call("POST", "/clear-cache")
            if error:
                st.error(error)
            else:
                st.success(data.get("message", "Velocity cache cleared"))

if st.session_state.role == "wallet":
    digital_wallet()
elif st.session_state.role == "admin":
    analyst_monitor()
else:
    logout("Unknown role. Please sign in again.")
