"""Tools the Act 3 agents call.

DESIGN RULE, from demo-agentmesh-design-2026-09-02.md §2:
no agent invents inventory or credit facts. Every number an agent reasons about
comes from one of these functions. The model chooses among options; it does not
supply data. That boundary is what makes "how do you stop the model doing
something stupid" answerable with a mechanism rather than a hope.

These are deterministic stand-ins for the systems a real deployment would call
(WMS, credit, TMS). They are seeded from the order id so the same order gives
the same answer every time, which matters when rehearsing a beat.
"""
import hashlib
from typing import Any


def _seed(*parts: Any) -> int:
    """Stable pseudo-random seed from the arguments.

    Deterministic on purpose: rehearsing the same order twice must give the same
    numbers, or the narration stops matching the screen.
    """
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


SITES = ("dfw-01", "atl-02", "phx-03")


def check_inventory(sku: str, quantity_needed: int) -> dict:
    """Live stock for a SKU across every warehouse.

    Returns per-site availability. Note this is a CROSS-SITE lookup: resolving a
    shortfall requires seeing other warehouses, which is exactly why the
    consumer is not site-scoped and why a locality level was cut from the topic
    architecture.
    """
    s = _seed("inv", sku)
    sites = {site: (s >> (i * 5)) % 60 for i, site in enumerate(SITES)}
    total = sum(sites.values())
    return {
        "sku": sku,
        "quantityNeeded": quantity_needed,
        "bySite": sites,
        "totalAvailable": total,
        "canFulfilFromOneSite": any(v >= quantity_needed for v in sites.values()),
        "canFulfilSplit": total >= quantity_needed,
    }


def check_substitutes(sku: str) -> dict:
    """Approved substitutes for a SKU, and whether the customer permits them."""
    s = _seed("sub", sku)
    has_sub = s % 3 != 0
    return {
        "sku": sku,
        "substitute": f"{sku[:-1]}{(int(sku[-1]) + 2) % 10}" if has_sub else None,
        "customerAllowsSubstitution": (s >> 4) % 4 != 0,
    }


def check_credit(account: str) -> dict:
    """Current credit standing for a distributor account."""
    s = _seed("credit", account)
    limit = 10_000 + (s % 90_000)
    balance = int(limit * ((s >> 8) % 100) / 100)
    return {
        "account": account,
        "creditLimit": round(limit, 2),
        "currentBalance": round(balance, 2),
        "available": round(limit - balance, 2),
        "paymentInFlight": round(limit * 0.4, 2) if (s >> 12) % 2 else 0.0,
        "priorDisputes": (s >> 16) % 5 == 0,
        "accountStandingYears": 1 + (s >> 20) % 12,
    }


# --- escalation thresholds -------------------------------------------------
# Explicit, not model judgement. "The LLM decides where the boundary is" is a
# losing answer under questioning; a named threshold is defensible.

AUTO_RESOLVE_CEILING = 25_000.0   # order value above which a human decides
CREDIT_TOLERANCE_PCT = 0.05       # release within 5% of the limit


def should_escalate_credit(order_value: float, credit: dict) -> tuple[bool, str]:
    """Escalate a credit hold, or resolve it. Returns (escalate, reason)."""
    over_by = order_value - credit["available"]
    if credit["priorDisputes"]:
        return True, "account has prior disputes, needs a human"
    if credit["paymentInFlight"] >= over_by:
        return False, f"payment in flight ${credit['paymentInFlight']:,.2f} covers ${over_by:,.2f}"
    if over_by <= credit["creditLimit"] * CREDIT_TOLERANCE_PCT:
        return False, f"${over_by:,.2f} is within {CREDIT_TOLERANCE_PCT:.0%} tolerance"
    if order_value > AUTO_RESOLVE_CEILING:
        return True, f"order value ${order_value:,.2f} exceeds the auto-resolve ceiling"
    return True, f"over limit by ${over_by:,.2f} with no payment in flight"


def should_escalate_shortfall(short: int, inventory: dict, subs: dict) -> tuple[bool, str]:
    """Escalate a stock shortfall, or resolve it. Returns (escalate, reason)."""
    if inventory["canFulfilFromOneSite"]:
        best = max(inventory["bySite"].items(), key=lambda kv: kv[1])
        return False, f"transfer {short} from {best[0]}"
    if subs["substitute"] and subs["customerAllowsSubstitution"]:
        return False, f"offer approved substitute {subs['substitute']}"
    if subs["substitute"] and not subs["customerAllowsSubstitution"]:
        return True, "a substitute exists but this customer does not accept substitutions"
    if inventory["canFulfilSplit"]:
        return False, "split-ship across sites"
    return True, "no stock anywhere and no substitute, needs a human decision"
