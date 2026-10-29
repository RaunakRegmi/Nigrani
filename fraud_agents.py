"""Deterministic fraud agents that run alongside the ML model.

Each agent returns an AgentVerdict when it fires, or None when it has no
objection. api.py combines the verdicts with the model's decision and keeps
the most restrictive one.

Stateful agents keep their state in Redis, not process memory, so every API
worker and node sees the same velocity windows and baselines. They take the
Redis client as a parameter (api.py owns the connection pool).
"""
import json
from dataclasses import asdict, dataclass

import redis

# Most restrictive wins
DECISION_SEVERITY = {"ALLOW": 0, "OTP": 1, "DUAL_OTP": 2, "BLOCK": 3}

# Velocity Agent
VELOCITY_WINDOW_S = 60
VELOCITY_MAX_TXNS = 3          # more than this many in the window -> BLOCK

# Graph Agent
MULE_MIN_DEGREE_IN = 200        # degree_in strictly above this...
MULE_MIN_AMOUNT = 50_000       # ...and amount strictly above this -> BLOCK

# SIM Swap Agent: mock device registry (account_id -> enrolled device_ids).
# Accounts without enrolled devices are not evaluated: there is no baseline
# to call a device "unrecognized" against.
TRUSTED_DEVICES = {
    "ACC-8622639": {"DEV-0AE666", "DEV-0D2C17"},
    "ACC-2522726": {"DEV-14D91A"},
}


@dataclass(frozen=True)
class AgentVerdict:
    agent: str
    decision: str
    reason: str
    detail: str = ""

    def to_dict(self):
        return asdict(self)


# Redis key layout. Every operation touches a single key, so this also works on Redis Cluster.
KEY_PREFIX = "nigrani"
VELOCITY_KEY = KEY_PREFIX + ":velocity:{account_id}"      # ZSET: member=txn id, score=epoch ms
BASELINE_KEY = KEY_PREFIX + ":baseline:{account_id}"      # HASH: avg_amount, known_districts (JSON)

# Atomic sliding window on the Redis server clock: one clock for every worker,
# and no read-modify-write race between concurrent requests for one account.
# Keeps events with ts >= now - window (same boundary as before), then lets an
# idle account's key expire on its own.
_VELOCITY_LUA = """
local t = redis.call('TIME')
local now_ms = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local window_ms = tonumber(ARGV[1])
redis.call('ZADD', KEYS[1], now_ms, ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', '(' .. (now_ms - window_ms))
redis.call('PEXPIRE', KEYS[1], window_ms)
return redis.call('ZCARD', KEYS[1])
"""


class VelocityAgent:
    """Sliding-window burst detector backed by a Redis sorted set per account."""

    name = "velocity_agent"

    def __init__(self, window_s=VELOCITY_WINDOW_S, max_txns=VELOCITY_MAX_TXNS):
        self.window_s = window_s
        self.max_txns = max_txns

    def evaluate(self, r: redis.Redis, account_id: str, event_id: str) -> AgentVerdict | None:
        """Record one transaction (event_id must be unique) and check the window.

        Raises redis.RedisError when Redis is unreachable; the caller decides
        whether to fail closed.
        """
        count = int(r.eval(
            _VELOCITY_LUA, 1, VELOCITY_KEY.format(account_id=account_id),
            int(self.window_s * 1000), event_id,
        ))
        if count > self.max_txns:
            return AgentVerdict(
                self.name, "BLOCK", "Velocity Burst",
                f"{count} transactions in {self.window_s}s",
            )
        return None

    def reset(self, r: redis.Redis) -> int:
        """Delete every account's window. Returns the number of keys removed."""
        removed = 0
        batch = []
        for key in r.scan_iter(match=VELOCITY_KEY.format(account_id="*"), count=1000):
            batch.append(key)
            if len(batch) >= 1000:
                removed += r.unlink(*batch)
                batch.clear()
        if batch:
            removed += r.unlink(*batch)
        return removed


# Shared instance used by api.py. It holds config only; state lives in Redis.
velocity_agent = VelocityAgent()


def clear_velocity_cache(r: redis.Redis) -> int:
    """Drop every account's velocity window (testing/ops utility)."""
    return velocity_agent.reset(r)


def graph_agent(degree_in: float, amount: float) -> AgentVerdict | None:
    if degree_in > MULE_MIN_DEGREE_IN and amount > MULE_MIN_AMOUNT:
        return AgentVerdict(
            "graph_agent", "BLOCK", "Mule Network Signature",
            f"degree_in {degree_in:g} with amount {amount:,.2f}",
        )
    return None


def sim_swap_agent(account_id: str, device_id: str, trusted_devices=TRUSTED_DEVICES) -> AgentVerdict | None:
    enrolled = trusted_devices.get(account_id)
    if enrolled and device_id not in enrolled:
        return AgentVerdict(
            "sim_swap_agent", "DUAL_OTP", "Unrecognized Device - Possible SIM Swap",
            f"device {device_id} is not enrolled for this account",
        )
    return None


# Behavior (UEBA) Agent. Baselines live in Redis hashes; these mock profiles
# seed Redis at startup (seed_baselines) and never overwrite a stored value.
# Accounts without a baseline are not evaluated, same as the SIM Swap Agent.
mock_user_baselines = {
    "ACC-8622639": {"avg_amount": 4000, "known_districts": ["Kathmandu", "Lalitpur"]},
    "ACC-2522726": {"avg_amount": 8000, "known_districts": ["Lalitpur"]},
}
BEHAVIOR_AMOUNT_MULTIPLIER = 5
BEHAVIOR_NIGHT_HOURS = range(0, 6)     # 00:00-05:59
BEHAVIOR_MIN_ANOMALIES = 2


def save_baseline(r: redis.Redis, account_id: str, avg_amount: float, known_districts: list[str], overwrite=True):
    key = BASELINE_KEY.format(account_id=account_id)
    fields = {"avg_amount": str(avg_amount), "known_districts": json.dumps(known_districts)}
    if overwrite:
        r.hset(key, mapping=fields)
    else:
        with r.pipeline() as pipe:
            for field, value in fields.items():
                pipe.hsetnx(key, field, value)
            pipe.execute()


def load_baseline(r: redis.Redis, account_id: str) -> dict | None:
    raw = r.hgetall(BASELINE_KEY.format(account_id=account_id))
    if not raw or "avg_amount" not in raw:
        return None
    return {
        "avg_amount": float(raw["avg_amount"]),
        "known_districts": json.loads(raw.get("known_districts") or "[]"),
    }


def seed_baselines(r: redis.Redis, baselines=mock_user_baselines):
    """Write the mock baselines without clobbering anything already in Redis."""
    for account_id, b in baselines.items():
        save_baseline(r, account_id, b["avg_amount"], b["known_districts"], overwrite=False)


class BehaviorAgent:
    """UEBA: compares a transaction with the account's usual behavior.

    OTP when at least BEHAVIOR_MIN_ANOMALIES of {amount, time, location}
    deviate from the baseline. A signal that can't be assessed (no district
    sent) counts as not anomalous.
    """

    name = "behavior_agent"

    def evaluate(self, r: redis.Redis, account_id: str, amount: float, hour_of_day: int,
                 district: str | None) -> AgentVerdict | None:
        baseline = load_baseline(r, account_id)
        if not baseline:
            return None

        anomalies = []
        if amount > baseline["avg_amount"] * BEHAVIOR_AMOUNT_MULTIPLIER:
            anomalies.append(f"amount {amount:,.0f} > {BEHAVIOR_AMOUNT_MULTIPLIER}x avg {baseline['avg_amount']:,.0f}")
        if hour_of_day in BEHAVIOR_NIGHT_HOURS:
            anomalies.append(f"night-time at {hour_of_day:02d}:00")
        known = {d.strip().lower() for d in baseline["known_districts"]}
        if district and district.strip().lower() not in known:
            anomalies.append(f"new district {district.strip()}")

        if len(anomalies) >= BEHAVIOR_MIN_ANOMALIES:
            return AgentVerdict(self.name, "OTP", "Anomalous User Behavior Detected", "; ".join(anomalies))
        return None


behavior_agent = BehaviorAgent()


def most_restrictive(decisions) -> str:
    return max(decisions, key=DECISION_SEVERITY.__getitem__)
