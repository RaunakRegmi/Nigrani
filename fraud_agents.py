"""Deterministic fraud agents that run alongside the ML model.

Each agent returns an AgentVerdict when it fires, or None when it has no
objection. api.py combines the verdicts with the model's decision and keeps
the most restrictive one.
"""
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass

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


class VelocityAgent:
    """Sliding-window burst detector, in process memory.

    Thread-safe (FastAPI runs sync endpoints in a threadpool). State is per
    process: with several uvicorn workers each keeps its own window, and a
    restart clears it.
    """

    name = "velocity_agent"

    def __init__(self, window_s=VELOCITY_WINDOW_S, max_txns=VELOCITY_MAX_TXNS, clock=time.monotonic):
        self.window_s = window_s
        self.max_txns = max_txns
        self.clock = clock
        self._events: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()
        self._calls = 0

    def evaluate(self, account_id: str) -> AgentVerdict | None:
        now = self.clock()
        with self._lock:
            window = self._events[account_id]
            window.append(now)
            self._expire(window, now)
            count = len(window)

            # Periodically drop idle accounts so memory stays bounded
            self._calls += 1
            if self._calls % 1000 == 0:
                self._sweep(now)

        if count > self.max_txns:
            return AgentVerdict(
                self.name, "BLOCK", "Velocity Burst",
                f"{count} transactions in {self.window_s}s",
            )
        return None

    def _expire(self, window: deque, now: float):
        while window and now - window[0] > self.window_s:
            window.popleft()

    def _sweep(self, now: float):
        for account_id in list(self._events):
            window = self._events[account_id]
            self._expire(window, now)
            if not window:
                del self._events[account_id]

    def reset(self):
        with self._lock:
            self._events.clear()


# Process-wide instance used by api.py, so the cache can be cleared from here
velocity_agent = VelocityAgent()


def clear_velocity_cache():
    """Drop every account's velocity timestamps (testing/ops utility)."""
    velocity_agent.reset()


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


# Behavior (UEBA) Agent: mock per-account baselines. Accounts without a
# baseline are not evaluated, same as the SIM Swap Agent.
mock_user_baselines = {
    "ACC-8622639": {"avg_amount": 4000, "known_districts": ["Kathmandu", "Lalitpur"]},
    "ACC-2522726": {"avg_amount": 8000, "known_districts": ["Lalitpur"]},
}
BEHAVIOR_AMOUNT_MULTIPLIER = 5
BEHAVIOR_NIGHT_HOURS = range(0, 6)     # 00:00-05:59
BEHAVIOR_MIN_ANOMALIES = 2


class BehaviorAgent:
    """UEBA: compares a transaction with the account's usual behavior.

    OTP when at least BEHAVIOR_MIN_ANOMALIES of {amount, time, location}
    deviate from the baseline. A signal that can't be assessed (no district
    sent) counts as not anomalous.
    """

    name = "behavior_agent"

    def __init__(self, baselines=mock_user_baselines):
        self.baselines = baselines

    def evaluate(self, account_id: str, amount: float, hour_of_day: int, district: str | None) -> AgentVerdict | None:
        baseline = self.baselines.get(account_id)
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
