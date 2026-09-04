"""Exception-routing consumers.

Usage:
    python src/consumer.py desk-credit
    python src/consumer.py desk-inventory
    python src/consumer.py desk-audit

WHY THE ROLES ARE NAMED desk-*
They used to be `credit-desk` and `inventory-planning`. `credit-desk` (a consumer
role) sits one character away from `credit-hold` (an exception type) in the part
that matters, and the two are different kinds of thing entirely — who does the
work, versus what went wrong. Typing the wrong one mid-sentence in a live demo
gets an argparse error at the worst possible moment. The `desk-` prefix
makes the role names a different shape from the exception types, so they cannot
be confused under pressure.

The QUEUE names are unchanged — those are broker objects, nobody types them
live, and renaming them would mean re-provisioning for no benefit.

Each consumer binds to its own durable queue. The queue already carries the topic
subscriptions — provisioned by scripts/provision.py — so the consumer names a
queue, not a topic. That separation is the point: the topic is the routing
address, the queue is the durability container, and the subscription that links
them lives on the queue rather than in this code. It is why stopping a consumer
loses nothing.

AT-LEAST-ONCE, NOT EXACTLY-ONCE
Guaranteed delivery redelivers on reconnect and on unacknowledged messages. There
is no exactly-once here or anywhere else in this class of system. Consumers
dedupe on (orderId, exceptionId) and say so plainly when asked.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from solace.messaging.resources.queue import Queue  # noqa: E402
from solace.messaging.receiver.message_receiver import MessageHandler  # noqa: E402
from solace.messaging.config.message_acknowledgement_configuration import Outcome  # noqa: E402

import broker  # noqa: E402
import topics  # noqa: E402

QUEUES = {
    "desk-credit": "Q/credit-desk/exceptions",
    "desk-inventory": "Q/inventory-planning/exceptions",
    "desk-audit": "Q/audit/all-exceptions",
}

# Shown at startup so each desk states what it listens to. These are the same
# constants provisioning attaches to the queues, so what is printed cannot
# drift from what is actually subscribed.
SUBSCRIPTIONS = {
    "desk-credit": topics.SUB_CREDIT_HOLD,
    "desk-inventory": topics.SUB_STOCK_SHORTFALL,
    "desk-audit": topics.SUB_ALL_EXCEPTIONS,
}


class Handler(MessageHandler):
    """Dispatches one message, acknowledging only after successful work.

    The ack placement is the whole reliability story. Acking on receipt would
    make the queue look reliable while losing anything that failed downstream.
    Acking after the work means a crash mid-processing redelivers rather than
    drops — at the cost of a possible duplicate, which is what the dedupe set is
    for.
    """

    def __init__(self, receiver, name: str, work) -> None:
        self.receiver = receiver
        self.name = name
        self.work = work
        self.seen: set[tuple] = set()
        self.processed = 0
        self.duplicates = 0
        self.failed = 0

    def on_message(self, message) -> None:
        raw = message.get_payload_as_string()
        try:
            body = json.loads(raw)
        except (ValueError, TypeError):
            # Unparseable: nothing to dedupe on and no retry will help. Ack it
            # so it does not block the queue; a real system would side-line it.
            print(f"[{self.name}] unparseable payload, acking to unblock")
            self.receiver.settle(message, Outcome.ACCEPTED)
            return

        key = (body.get("orderId"), body.get("exceptionId"))
        if key in self.seen:
            # Redelivery after a reconnect, or a genuine duplicate publish.
            # Ack and move on — this is what "at-least-once plus idempotent
            # consumers" looks like in practice.
            self.duplicates += 1
            print(f"[{self.name}] duplicate {key[1][:8]} — already handled, acking")
            self.receiver.settle(message, Outcome.ACCEPTED)
            return

        try:
            self.work(self.name, body)
        except Exception as exc:  # noqa: BLE001 — deliberate: see below
            # Negative acknowledgement, not a withheld one. Withholding an ack
            # does not trigger redelivery: the message stays "delivered unacked"
            # against this flow until the flow closes, so a consumer that fails
            # and stays connected never retries. settle(FAILED) redelivers and
            # increments the redelivery count, so max-redelivery eventually
            # moves the message to the dead message queue. REJECTED would skip
            # the retries and go straight there.
            self.failed += 1
            print(f"[{self.name}] FAILED {body.get('exceptionId', '?')[:8]}: {exc}"
                  f"  — event message rejected")
            self.receiver.settle(message, Outcome.FAILED)
            return

        self.seen.add(key)
        self.processed += 1
        self.receiver.settle(message, Outcome.ACCEPTED)


# --- the work each desk actually does ---------------------------------------
# Deterministic rules, deliberately. This is Act 2: the resolution is a rule, and
# that is the point. Act 3 replaces the judgment calls with agents.

def credit_desk_work(name: str, body: dict) -> None:
    over_by = body["orderValue"] - body["creditLimit"]   # raises on a None limit
    payment = body.get("paymentInFlight", 0)
    if payment >= over_by:
        action = f"release — payment in flight ${payment:,.2f} covers ${over_by:,.2f}"
    elif over_by <= body["creditLimit"] * 0.05:
        action = f"release — ${over_by:,.2f} within 5% tolerance"
    else:
        action = f"escalate to account manager — over by ${over_by:,.2f}"
    print(f"[{name}] {body['orderId']}  credit-hold  -> {action}")


def inventory_planning_work(name: str, body: dict) -> None:
    short = body["quantityShort"]
    best = max(body.get("alternateSites", []), key=lambda s: s["available"], default=None)
    if best and best["available"] >= short:
        action = f"transfer {short} from {best['site']}"
    elif body.get("substituteSku"):
        action = f"offer substitute {body['substituteSku']}"
    elif best and best["available"] > 0:
        action = f"split-ship {best['available']} from {best['site']}, backorder the rest"
    else:
        action = "backorder — no alternate stock"
    print(f"[{name}] {body['orderId']} line {body['lineId']}  {body['sku']}  "
          f"short {short}  -> {action}")


def audit_work(name: str, body: dict) -> None:
    # Fixed-width columns: the audit desk is the only consumer that sees both
    # scopes, so without padding the optional " line N" shifts every field after
    # it and order- and line-scoped rows do not line up.
    line = f" line {body['lineId']}" if body.get("lineId") else ""
    entity = f"{body['orderId']}{line}"
    scope = f"({body.get('scope', '?')})"
    print(f"[{name}] {entity:<18} {body['exceptionType']:<16} {scope:<12} "
          f"id={body['exceptionId'][:8]}")


WORK = {
    "desk-credit": credit_desk_work,
    "desk-inventory": inventory_planning_work,
    "desk-audit": audit_work,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("role", choices=sorted(QUEUES), help="which desk to run as")
    args = ap.parse_args()

    queue_name = QUEUES[args.role]
    with broker.session(args.role) as service:
        receiver = (
            service.create_persistent_message_receiver_builder()
            # Declare the outcomes this receiver will settle with. Without this
            # the broker does not negotiate negative-acknowledgement support and
            # settle(msg, Outcome.FAILED) fails at runtime.
            .with_required_message_outcome_support(Outcome.ACCEPTED, Outcome.FAILED, Outcome.REJECTED)
            .build(Queue.durable_exclusive_queue(queue_name))
        )
        receiver.start()
        handler = Handler(receiver, args.role, WORK[args.role])

        print(f"[{args.role}] queue        {queue_name}")
        print(f"[{args.role}] subscription {SUBSCRIPTIONS[args.role]}")
        print("Ctrl-C to stop\n")
        try:
            # Blocking receive rather than receive_async: settling from inside
            # the async callback did not take effect against this broker, and
            # handled messages were redelivered until they reached the DMQ.
            while True:
                message = receiver.receive_message(timeout=1000)
                if message is not None:
                    handler.on_message(message)
        except KeyboardInterrupt:
            print(f"\n[{args.role}] processed={handler.processed} "
                  f"duplicates={handler.duplicates} failed={handler.failed}")
            return 0
        finally:
            receiver.terminate()


if __name__ == "__main__":
    sys.exit(main())
