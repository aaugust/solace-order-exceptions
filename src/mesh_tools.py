"""Agent Mesh tool functions for the Meridian agents.

SAM loads these via `tool_type: python` in the agent YAML. Each function becomes
a tool the model can call, and its docstring becomes the description the model
reasons about — so the docstrings here are interface, not commentary.

THE BOUNDARY THIS FILE ENFORCES
Every number an agent reasons about comes from here. The model chooses among
options; it does not supply data. That is the design rule from
demo-agentmesh-design-2026-09-02.md §2, and it is what makes "how do you stop the
model doing something stupid" answerable with a mechanism.

The logic is imported from agent_tools, which the HTTP control arm also imports.
Both arms therefore run byte-identical business logic and the ONLY variable in
the comparison is the transport. If these diverged the comparison would be
worthless, so they deliberately share a module rather than being written twice.
"""
import os
import sys
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_tools


async def check_stock(
    sku: str,
    quantity_needed: int,
    tool_context: Optional[Any] = None,
    tool_config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Check live stock for a SKU across every warehouse.

    Returns availability per site plus whether the need can be met from one site
    or only by splitting across sites. Use this before deciding how to resolve a
    stock shortfall — never assume stock levels.

    Args:
        sku: The product code, e.g. MC-BRG-1042.
        quantity_needed: How many units are short.
    """
    return agent_tools.check_inventory(sku, quantity_needed)


async def check_substitutes(
    sku: str,
    tool_context: Optional[Any] = None,
    tool_config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Find an approved substitute for a SKU and whether this customer accepts one.

    A substitute existing is not sufficient: some customers contractually refuse
    substitutions, and that case must escalate to a human rather than be decided.

    Args:
        sku: The product code to find a substitute for.
    """
    return agent_tools.check_substitutes(sku)


async def resolve_shortfall(
    sku: str,
    quantity_short: int,
    tool_context: Optional[Any] = None,
    tool_config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Decide how to resolve a stock shortfall, or escalate it to a human.

    Applies the agreed thresholds rather than improvising: transfer from another
    site if one can cover it, otherwise an approved substitute the customer
    accepts, otherwise a split shipment, otherwise escalate.

    Args:
        sku: The product code that is short.
        quantity_short: How many units are missing.
    """
    inv = agent_tools.check_inventory(sku, quantity_short)
    subs = agent_tools.check_substitutes(sku)
    escalate, reason = agent_tools.should_escalate_shortfall(quantity_short, inv, subs)
    return {
        "escalate": escalate,
        "action": reason,
        "inventory": inv,
        "substitutes": subs,
    }


async def check_credit_standing(
    account: str,
    tool_context: Optional[Any] = None,
    tool_config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Look up a distributor's current credit standing.

    Returns limit, balance, available headroom, any payment in flight, whether
    the account has prior disputes, and how long they have been a customer.

    Args:
        account: The distributor account code, e.g. ACCT-12345.
    """
    return agent_tools.check_credit(account)


async def resolve_credit_hold(
    account: str,
    order_value: float,
    tool_context: Optional[Any] = None,
    tool_config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Decide whether to release a credit hold, or escalate it to a human.

    Releases when a payment in flight covers the overage or the overage is
    within the agreed tolerance. Escalates on prior disputes, on orders above
    the auto-resolve ceiling, or when the account is simply over its limit.

    Args:
        account: The distributor account code.
        order_value: The total value of the held order.
    """
    credit = agent_tools.check_credit(account)
    escalate, reason = agent_tools.should_escalate_credit(order_value, credit)
    return {"escalate": escalate, "action": reason, "credit": credit}
