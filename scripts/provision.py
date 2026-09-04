"""Provision the broker for the Meridian Components demo, via the SEMP v2 config API.

Idempotent AND reconciling: re-running is safe, and it also corrects drift.
An object that already exists is PATCHed back to the definition here rather than
skipped — so changing a queue setting in this file and re-running actually
changes the broker. The run prints created / updated / exists so you can see
which happened.

Subscriptions are the exception: one is identified by its topic string, so an
existing subscription already matches by definition. Removing a subscription
from this file does NOT remove it from the broker — use --teardown for that.

Why a script and not the admin console: the queue topology and its topic
subscriptions ARE the design. Clicking them into a UI leaves no artefact, cannot
be reviewed, and cannot be reproduced by anyone who clones the repo. This file is
the deployable form of docs/messaging-design.md section 2.

Usage:
    python scripts/provision.py            # create
    python scripts/provision.py --teardown # remove what this script created
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import requests  # noqa: E402
from requests.auth import HTTPBasicAuth  # noqa: E402

import profile as _profile  # noqa: E402
import topics  # noqa: E402

# Same profile as the publishers and consumers, so provisioning cannot be
# pointed at one broker while the demo runs against another.
_profile.load(quiet=True)

SEMP = os.environ.get("SOLACE_SEMP", "http://localhost:8080/SEMP/v2/config")
VPN = os.environ.get("SOLACE_VPN", "default")
AUTH = HTTPBasicAuth(
    os.environ.get("SOLACE_ADMIN_USER", "admin"),
    os.environ.get("SOLACE_ADMIN_PASSWORD", "admin"),
)

# The default dead message queue. Solace uses a queue with this exact name as the
# DMQ by default, which is why it is not configurable here.
DMQ_NAME = "#DEAD_MSG_QUEUE"

# --- the queue topology -----------------------------------------------------
# One durable queue per consuming service, each with its own topic
# subscriptions. Rejected: a single shared queue with client-side filtering —
# a slow consumer applies back-pressure to every other consumer, each consumer
# burns work discarding messages it does not want, and adding the twelfth
# consumer means touching shared infrastructure.
#
# max_redelivery: 2 on the service queues, so one poison message steps aside and
# the other 109 exceptions that day keep flowing. 0 on the audit queue, which
# means retry forever — a poison message there should block and be noticed.
QUEUES = [
    {
        "name": "Q/inventory-planning/exceptions",
        "subscriptions": [topics.SUB_STOCK_SHORTFALL],
        "max_redelivery": 2,
    },
    {
        "name": "Q/credit-desk/exceptions",
        "subscriptions": [topics.SUB_CREDIT_HOLD],
        "max_redelivery": 2,
    },
    {
        # ONE subscription covers every exception at every scope, because
        # order-scoped and line-scoped events share a root and differ only by a
        # trailing line id. This queue carried TWO until 2026-09-02, one per
        # Object-type root, and a new object type would have meant a third -
        # a change to shared infrastructure every time the domain grew.
        #
        # A queue can still hold several subscriptions, and the desk queues
        # above show that mechanism; this one no longer needs it.
        "name": "Q/audit/all-exceptions",
        "subscriptions": [
            topics.SUB_ALL_EXCEPTIONS,
        ],
        "max_redelivery": 0,  # 0 means retry forever
    },
]


def _q(name: str) -> str:
    """URL-encode an object name for use as a path segment."""
    return requests.utils.quote(name, safe="")


def _url(path: str) -> str:
    # '#' must be percent-encoded or everything after it is read as a fragment.
    return f"{SEMP}/msgVpns/{VPN}{path}".replace("#", "%23")


def _post(path: str, body: dict, label: str, patch_path: str = None) -> bool:
    """Create, or reconcile if it already exists.

    A provisioning script that skips existing objects looks idempotent and is
    not: change a subscription in topics.py, re-run, and the broker keeps the
    old one while the script reports success. So ALREADY_EXISTS is followed by a
    PATCH when the object is patchable, and the run says which happened.

    patch_path: the object's own URI. Omitted for children (a subscription is
    identified by its topic, so an existing one already matches by definition).
    """
    r = requests.post(_url(path), json=body, auth=AUTH, timeout=15)
    if r.status_code in (200, 201):
        print(f"  created  {label}")
        return True

    if r.status_code == 400 and "ALREADY_EXISTS" in r.text:
        if patch_path is None:
            print(f"  exists   {label}")
            return True
        pr = requests.patch(_url(patch_path), json=body, auth=AUTH, timeout=15)
        if pr.status_code in (200, 201):
            print(f"  updated  {label}")
            return True
        print(f"  FAILED   {label} (patch)\n           {pr.status_code} {pr.text[:400]}")
        return False

    print(f"  FAILED   {label}\n           {r.status_code} {r.text[:400]}")
    return False


def _delete(path: str, label: str) -> bool:
    r = requests.delete(_url(path), auth=AUTH, timeout=15)
    if r.status_code in (200, 204):
        print(f"  deleted  {label}")
        return True
    if r.status_code == 400 and "NOT_FOUND" in r.text:
        print(f"  absent   {label}")
        return True
    print(f"  FAILED   {label}\n           {r.status_code} {r.text[:400]}")
    return False


def queue_body(name: str, max_redelivery: int, is_dmq: bool = False) -> dict:
    """Body for a queue create.

    is_dmq: the dead message queue is not an ordinary queue and the broker
    rejects two settings on it.
      - respect-ttl: refused outright — "respect-ttl cannot be set on
        #DEAD_MSG_QUEUE". A DMQ is where expired messages LAND; honouring TTL
        there would expire them a second time and discard the evidence.
      - deadMsgQueue: pointing the DMQ at itself is meaningless.
    Both are therefore omitted for the DMQ rather than set to False, so the
    broker keeps its own defaults.
    """
    body = {
        "queueName": name,
        # exclusive: one consumer at a time, which preserves per-order ordering.
        # Non-partitioned deliberately — message replay works only on
        # non-partitioned queues and topic endpoints, and the replay demo is a
        # stated success criterion.
        "accessType": "exclusive",
        "permission": "consume",
        "ingressEnabled": True,
        "egressEnabled": True,
        "maxRedeliveryCount": max_redelivery,
    }
    if not is_dmq:
        # Route exhausted and expired messages to the DMQ rather than
        # discarding them. NOTE: this is only half the contract — the publisher
        # must also set the DMQ-eligible flag on the message, or an exhausted
        # message is silently discarded while the system looks correct.
        # See publisher.py.
        body["deadMsgQueue"] = DMQ_NAME
        body["respectTtlEnabled"] = True
    return body


def provision() -> int:
    ok = True
    print(f"SEMP  {SEMP}\nVPN   {VPN}\n")

    print("Dead message queue")
    ok &= _post("/queues", queue_body(DMQ_NAME, 0, is_dmq=True), DMQ_NAME,
                patch_path=f"/queues/{_q(DMQ_NAME)}")

    for q in QUEUES:
        print(f"\nQueue {q['name']}  (max-redelivery {q['max_redelivery']})")
        ok &= _post("/queues", queue_body(q["name"], q["max_redelivery"]), q["name"],
                    patch_path=f"/queues/{_q(q['name'])}")
        for sub in q["subscriptions"]:
            ok &= _post(
                f"/queues/{_q(q['name'])}/subscriptions",
                {"subscriptionTopic": sub},
                f"subscription  {sub}",
            )

    print("\nOK" if ok else "\nCOMPLETED WITH FAILURES")
    return 0 if ok else 1


def _purge(queue: str) -> bool:
    """Empty a queue by DELETING and recreating it, not by asking it to purge.

    SEMP's action endpoint `deleteMsgs` looked like the right tool and is not:
    on the trial cloud service it returns 200 and removes nothing. Verified
    2026-09-02 - six messages before, 200 back, six messages ten seconds later.
    That is the same control-plane-accepts / data-plane-ignores split as message
    replay (see constraint 8 in the design doc), and the second instance of it
    on this service.

    Delete does work, and provision() recreates the DMQ immediately afterwards,
    so the queue is absent for well under a second and only during teardown.
    """
    r = requests.delete(_url(f"/queues/{_q(queue)}"), auth=AUTH, timeout=15)
    if r.status_code in (200, 400):      # 400 = already absent
        print(f"  emptied  {queue}")
        return True
    print(f"  FAILED   empty {queue}: {r.status_code} {r.text[:120]}")
    return False


def teardown() -> int:
    ok = True
    for q in QUEUES:
        ok &= _delete(f"/queues/{_q(q['name'])}", q["name"])
    # The DMQ is deliberately NOT deleted - it is broker-level furniture that
    # other things may rely on - but its CONTENTS must still go. Leaving them
    # meant -Fresh emptied the three service queues while the DMQ kept its
    # backlog, so Beat 4's "this message landed in the dead message queue"
    # pointed at a queue that already held three from earlier rehearsals. The
    # reveal only lands on an empty DMQ.
    ok &= _purge(DMQ_NAME)
    print("\nOK" if ok else "\nCOMPLETED WITH FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--teardown", action="store_true",
                    help="remove the queues this script creates")
    args = ap.parse_args()
    sys.exit(teardown() if args.teardown else provision())
