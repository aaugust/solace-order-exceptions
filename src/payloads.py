"""Payload generation for Meridian Components.

Uses faker so the data reads as plausible rather than synthetic, in preference
to publishing hand-typed strings from the Solace CLI. A demo whose payloads are "foo"/"bar" invites the audience to wonder
what else is a placeholder.

WHAT GOES IN THE PAYLOAD vs THE TOPIC
Anything a consumer routes on or an access rule pins is a topic level. Anything a
consumer merely reads is payload. So exception type is a level; the shortfall
quantity is payload. See the design doc, section 1.
"""
import random
import secrets
import uuid
from datetime import datetime, timezone

from faker import Faker

fake = Faker("en_US")


def seed(value: int | None = None) -> int:
    """Seed payload generation and return the seed actually used.

    Called with no argument, it mints a RANDOM seed from system entropy. That
    gives fresh data on every run — different orders, different distributors —
    while still leaving the run reproducible, because the seed is returned and
    printed. Note the seed from a run you liked and `--seed <n>` replays it
    exactly.

    Three states, and the middle one is the default because it is the only one
    that is both fresh and replayable:

        no seeding at all   fresh, NOT replayable (you cannot recover the state)
        random seed         fresh AND replayable          <- default
        fixed seed          identical every run, for debugging

    The first version of this module seeded a constant at import time, so every
    run emitted SO-4479068 / "Jenkins, Hays and Douglas" — fine for development,
    wrong in a live demo, where a repeated order number makes an audience
    wonder what else is canned.

    exceptionId is a uuid4 and is NOT affected by any of this: uuid4 draws from
    os.urandom rather than the random module. So even a fixed-seed re-run of the
    same order produces a new exception id, and consumers — which dedupe on
    (orderId, exceptionId) — treat it as a genuinely new exception rather than a
    duplicate.
    """
    if value is None:
        value = secrets.randbelow(1_000_000)
    Faker.seed(value)
    random.seed(value)
    return value

PRODUCTS = [
    ("MC-BRG-1042", "Tapered roller bearing, 45mm"),
    ("MC-VLV-2231", "Bronze gate valve, 2in"),
    ("MC-HYD-0917", "Hydraulic hose assembly, 3/8in"),
    ("MC-FST-5560", "Grade 8 hex bolt, box of 250"),
    ("MC-SEAL-330", "Nitrile O-ring kit, assorted"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_order() -> dict:
    """An order with several lines — because orders have several lines.

    That is not a throwaway detail. It is the fact that split exception handling
    across two Object-types: a credit hold is about this whole dict, a stock
    shortfall is about one entry in `lines`.
    """
    order_id = f"SO-{random.randint(4400000, 4499999)}"
    lines = []
    for line_no in range(1, random.randint(2, 5) + 1):
        sku, desc = random.choice(PRODUCTS)
        lines.append({
            "lineId": line_no,
            "sku": sku,
            "description": desc,
            "quantityOrdered": random.choice([5, 10, 25, 50, 100]),
            "unitPrice": round(random.uniform(4.5, 380.0), 2),
        })
    return {
        "orderId": order_id,
        "distributor": fake.company(),
        "distributorAccount": f"ACCT-{random.randint(10000, 99999)}",
        "orderedAt": _now(),
        "shipTo": {
            "street": fake.street_address(),
            "city": fake.city(),
            "state": fake.state_abbr(),
            "postalCode": fake.postcode(),
        },
        "lines": lines,
        "orderValue": round(sum(l["quantityOrdered"] * l["unitPrice"] for l in lines), 2),
    }


def _envelope(order: dict, exception_type: str) -> dict:
    """Fields every exception carries.

    exceptionId is the idempotency key. Guaranteed delivery is at-least-once —
    redelivery happens on reconnect and on unacknowledged messages — so consumers
    dedupe on (orderId, exceptionId) rather than pretending exactly-once exists.
    """
    return {
        "exceptionId": str(uuid.uuid4()),
        "exceptionType": exception_type,
        "orderId": order["orderId"],
        "distributor": order["distributor"],
        "distributorAccount": order["distributorAccount"],
        "detectedAt": _now(),
    }


def credit_hold(order: dict) -> dict:
    """Order-scoped: one decision, taken once, for the whole order.

    Note there is no warehouse here, and that is not an omission — credit
    checking runs before allocation, so no warehouse has been assigned yet.
    """
    limit = round(order["orderValue"] * random.uniform(0.55, 0.9), 2)
    body = _envelope(order, "credit-hold")
    body.update({
        "scope": "order",
        "orderValue": order["orderValue"],
        "creditLimit": limit,
        "currentBalance": round(limit * random.uniform(0.7, 0.99), 2),
        "paymentInFlight": round(random.choice([0.0, order["orderValue"] * 0.4]), 2),
        "overBy": round(order["orderValue"] - limit, 2),
    })
    return body


def stock_shortfall(order: dict, line: dict) -> dict:
    """Line-scoped: a fact about ONE line, not about the order.

    A five-line order can have one line short and four fine. Resolving it needs
    inventory across other warehouses — which is exactly why the consumer is not
    site-scoped, and why a locality level was cut from the topic architecture.
    """
    on_hand = random.randint(0, max(0, line["quantityOrdered"] - 1))
    body = _envelope(order, "stock-shortfall")
    body.update({
        "scope": "orderLine",
        "lineId": line["lineId"],
        "sku": line["sku"],
        "description": line["description"],
        "quantityOrdered": line["quantityOrdered"],
        "quantityOnHand": on_hand,
        "quantityShort": line["quantityOrdered"] - on_hand,
        "alternateSites": [
            {"site": s, "available": random.randint(0, 200)}
            for s in random.sample(["dfw-01", "atl-02", "phx-03"], 2)
        ],
        "substituteSku": random.choice([None, "MC-BRG-1044"]),
    })
    return body


def resolution(exception_body: dict, action: str, resolved_by: str) -> dict:
    """The audit trail of what was decided and by whom."""
    return {
        "exceptionId": exception_body["exceptionId"],
        "exceptionType": exception_body["exceptionType"],
        "orderId": exception_body["orderId"],
        "lineId": exception_body.get("lineId"),
        "action": action,
        "resolvedBy": resolved_by,
        "resolvedAt": _now(),
    }
