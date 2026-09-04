"""Topic architecture for the Meridian Components order-exception demo.

This module is the single place topics are composed. Nothing else in the project
builds a topic string by hand — that is deliberate, because the topic architecture
is the contract between publishers and subscribers, and a contract that is
re-implemented in five places is not a contract.

Design and rationale: docs/messaging-design.md

    Root                     Properties
    mc/order/{verb}/v1       {orderId}
    mc/order/exception/v1    {exceptionType}/{orderId}[/{lineId}]
    mc/order/resolution/v1   {exceptionType}/{orderId}[/{lineId}]

Solace field mapping: Domain / Object-type / Verb / Version, then properties
ordered least-specific to most-specific, ending in the object identifier.

GOVERNING RULE: a topic architecture serves routing
and access control. A level serving neither has no justification and becomes
noise. Every level below passes on at least one leg — see the justification
table in the design doc.

ONE ROOT, VARIABLE DEPTH — changed 2026-09-02, and the reasoning matters
An earlier design gave line-scoped events their own object type,
`mc/orderLine/exception/v1/...`, so an order-scoped and a line-scoped exception
lived under different roots. Re-examined against the governing rule, that split
could not be defended: it serves no routing purpose the merged form does not
serve, and it costs a subscription.

Counting what each consumer needs settles it. One exception type: one
subscription either way. All line-scoped: one either way. All order-scoped: one
either way. One order across both scopes: two either way. EVERYTHING: two under
the split, ONE under the merged root. The split lost one case and tied the rest.

The line id is therefore simply absent when an exception is order-scoped, which
is one of the two things Solace's guidance sanctions for a level that is not
always present (the other is an explicit null token — the route taken and then
abandoned for {locality}, see below). It works because `>` matches ONE OR MORE
levels and never zero, verified against the broker rather than assumed:

    mc/order/exception/v1/*/*     -> order-scoped ONLY  (exactly 6 levels)
    mc/order/exception/v1/*/*/>   -> line-scoped ONLY   (7 or more)
    mc/order/exception/v1/>       -> both

What the split root would have bought is modelling, not routing: an order line
is a distinct object with a distinct payload schema. That is real, and it is not
worth a permanent extra subscription on every all-exceptions consumer. If
line-level LIFECYCLE events are ever added, revisit this — merging makes every
order topic variable-depth, not just the exception ones, and at that point the
separate object type earns its place.

REMOVED 2026-08-29: a {locality} level sat between exceptionType and orderId.
It was cut because it failed both legs. No consumer in this design subscribes on
it, and the access-control argument — warehouse staff seeing only their own site
— does not survive contact with the business: exception handling here is
central, not site-local. The one exception that looked site-shaped, stock
shortfall, is the clearest case against it, because resolving a shortfall
requires live inventory ACROSS other warehouses. A site-scoped consumer could
not resolve the very exception the level was supposed to route to it.

Where a site- or department-scoped requirement would go if one appeared:
  1. an existing level — most departmental rules land on exceptionType or
     Object-type, which are already here;
  2. APPENDED at the end, where `>` subscribers absorb it for free;
  3. behind a Version bump, if it genuinely must be inserted mid-sequence;
  4. a separate Message VPN, when the requirement is tenancy rather than
     filtering.
"""

DOMAIN = "mc"
VERSION = "v1"

# --- value spaces -----------------------------------------------------------
# Every level declares its value space, including its null case where one
# exists. A level whose value is sometimes absent is a design defect UNLESS the
# absence is itself meaningful and structural — which is exactly the case for
# {lineId}: it is missing when, and only when, the exception is not about a
# line. That is a scope distinction a subscriber can route on, not a gap.

LIFECYCLE_VERBS = ("created", "allocated", "picked", "shipped", "invoiced")

# Order-scoped: one decision, taken once for the whole order. No {lineId}.
ORDER_EXCEPTIONS = ("credit-hold", "address-invalid")

# Line-scoped: the resolution is per line — substitute, split-ship, or backorder
# *this item*. A five-line order can have one line short and four fine. Carries
# a trailing {lineId}.
#
# carrier-rejected is line-scoped, decided 2026-09-02. A carrier refuses
# specific items before shipment, or belatedly refuses a box once it realises
# what is inside it. It never refuses an order: a carrier's business is boxes
# and the items in them, and an order is not a unit it transacts in. Modelling
# it order-scoped would have forced the rejected item into the payload, where
# the carrier desk cannot route or apply access control on it.
LINE_EXCEPTIONS = ("stock-shortfall", "carrier-rejected")


def lifecycle(verb: str, order_id: str) -> str:
    """mc/order/{verb}/v1/{orderId}"""
    assert verb in LIFECYCLE_VERBS, f"unknown lifecycle verb: {verb}"
    return f"{DOMAIN}/order/{verb}/{VERSION}/{order_id}"


def order_exception(exception_type: str, order_id: str) -> str:
    """mc/order/exception/v1/{exceptionType}/{orderId} — no line id."""
    assert exception_type in ORDER_EXCEPTIONS, f"not order-scoped: {exception_type}"
    return f"{DOMAIN}/order/exception/{VERSION}/{exception_type}/{order_id}"


def order_resolution(exception_type: str, order_id: str) -> str:
    """mc/order/resolution/v1/{exceptionType}/{orderId}"""
    assert exception_type in ORDER_EXCEPTIONS, f"not order-scoped: {exception_type}"
    return f"{DOMAIN}/order/resolution/{VERSION}/{exception_type}/{order_id}"


def line_exception(exception_type: str, order_id: str, line_id: int) -> str:
    """mc/order/exception/v1/{exceptionType}/{orderId}/{lineId}

    Same root as order_exception. The trailing line id is what makes this
    line-scoped, and what a line-only subscriber matches on.
    """
    assert exception_type in LINE_EXCEPTIONS, f"not line-scoped: {exception_type}"
    return f"{DOMAIN}/order/exception/{VERSION}/{exception_type}/{order_id}/{line_id}"


def line_resolution(exception_type: str, order_id: str, line_id: int) -> str:
    """mc/order/resolution/v1/{exceptionType}/{orderId}/{lineId}"""
    assert exception_type in LINE_EXCEPTIONS, f"not line-scoped: {exception_type}"
    return f"{DOMAIN}/order/resolution/{VERSION}/{exception_type}/{order_id}/{line_id}"


# --- subscriptions ----------------------------------------------------------
# Named here rather than inline at each consumer, so the routing story is
# readable in one place, which is the thing an audience will ask about.

SUB_CREDIT_HOLD = f"{DOMAIN}/order/exception/{VERSION}/credit-hold/>"
SUB_STOCK_SHORTFALL = f"{DOMAIN}/order/exception/{VERSION}/stock-shortfall/>"

# ONE subscription for every exception, whatever its scope. Under the previous
# two-root design this consumer needed two, and a new object type would have
# meant a third — a shared-infrastructure change every time the domain grew.
SUB_ALL_EXCEPTIONS = f"{DOMAIN}/order/exception/{VERSION}/>"

# Scope-selective subscriptions. Nothing in the demo binds these; they are here
# because they are the proof that merging the roots costs no routing power, and
# because the exact-length form is the non-obvious half.
#   `*` matches exactly one level, so an all-`*` subscription is length-exact
#   and stops at the order id. `>` matches one or more, never zero, so adding
#   it requires at least a line id to be present.
SUB_ORDER_SCOPED_ONLY = f"{DOMAIN}/order/exception/{VERSION}/*/*"
SUB_LINE_SCOPED_ONLY = f"{DOMAIN}/order/exception/{VERSION}/*/*/>"

# One rule isolates the whole application on a shared broker. This is the access
# control justification for the Domain level: without it, isolation means
# enumerating every Object-type, and each new one is a silent hole.
SUB_APPLICATION_ROOT = f"{DOMAIN}/>"

# --- access control ---------------------------------------------------------
# Departmental access control rides on exceptionType, which is already justified
# on routing grounds and so costs nothing extra. The credit desk sees credit
# holds and nothing else, enforced at the broker rather than trusted to the
# consumer. This is the ACL story after {locality} was removed.
#
# Note the audit entry is now a single rule. Scope-restricted access is still
# expressible — SUB_LINE_SCOPED_ONLY is a valid ACL topic exception — so merging
# the roots gave up no access-control granularity either.

ACL_SUBSCRIBE_EXCEPTIONS = {
    "credit-desk": [SUB_CREDIT_HOLD],
    "inventory-planning": [SUB_STOCK_SHORTFALL],
    "audit": [SUB_ALL_EXCEPTIONS],
}
