"""Publish order lifecycle and exception events.

Usage:
    python src/publisher.py                 # BEAT 1: 20 orders at ~3/s, then exits
    python src/publisher.py --orders 5      # publish 5 orders and exit
    python src/publisher.py --orders 0      # run until Ctrl-C
    python src/publisher.py --rate 20       # 20 orders/second, for the load run
    python src/publisher.py --credit-hold   # force ONE order-scoped exception
    python src/publisher.py --shortfall     # force ONE line-scoped exception
    python src/publisher.py --poison        # publish one un-processable message

WHY THE FORCED-EXCEPTION FLAGS EXIST
The ambient rates below are faithful to the problem statement's ~4%, which means
a twenty-order run may produce no exceptions at all. That is honest and it is
useless for a live demo, where the exception has to appear while you are pointing
at the window. So: the trickle keeps the realistic rate for background traffic,
and the flags fire a specific exception on cue. The narration works either way —
"about four percent of orders break; here is one".

TWO PUBLISHERS, ON PURPOSE
Lifecycle events go out DIRECT; exceptions and resolutions go out PERSISTENT
(guaranteed). That split is not an optimisation detail, it is the architectural
argument: one broker carrying different guarantees per stream. A dashboard that
misses a "picked" event during a reconnect is not wrong — it refreshes — so
spooling lifecycle chatter would buy nothing and cost spool depth. Losing an
exception means an order silently stops, so those are guaranteed and never
negotiable. The alternatives force one guarantee across everything: either pay
spool cost for dashboard traffic, or accept loss on business events.

If this file used one publisher for both, the demo would assert that argument
without demonstrating it.

THE HALF OF THE DMQ CONTRACT THAT LIVES HERE
The queues are configured with a max-redelivery count and a deadMsgQueue, but
that is only half of it. A message is moved to the DMQ **only if the publisher
marked it DMQ-eligible**. Without the flag below, a message that exhausts its
redelivery count is SILENTLY DISCARDED while every piece of configuration still
looks correct. The two halves live on opposite sides of the system and neither
validates the other — see the design doc, section 3.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from solace.messaging.resources.topic import Topic  # noqa: E402
from solace.messaging.config.solace_properties import message_properties as msgprops  # noqa: E402

import broker  # noqa: E402
import payloads  # noqa: E402
import topics  # noqa: E402

# Roughly the 4% exception rate from the problem statement, split so both demo
# exception types appear often enough to watch.
P_CREDIT_HOLD = 0.02
P_STOCK_SHORTFALL = 0.03


def _message(service, body: dict, dmq_eligible: bool = True):
    """Build an outbound message.

    correlation_id carries the exceptionId so a resolution can be tied back to
    the exception that caused it without parsing the payload.
    """
    builder = service.message_builder().with_property(
        # THE flag. Set it or the DMQ never receives anything.
        #
        # The value is an INT, not a bool and not a string. The library
        # validates message properties against its own type map
        # (_solace_utilities.validate_message_props), where
        # PERSISTENT_DMQ_ELIGIBLE is [int] — passing "true" raises
        # InvalidDataTypeError. Worth knowing because the natural thing to write
        # is a string and the error names types rather than the property.
        msgprops.PERSISTENT_DMQ_ELIGIBLE, 1 if dmq_eligible else 0
    )
    if "exceptionId" in body:
        builder = builder.with_correlation_id(body["exceptionId"])
    return builder.build(json.dumps(body))


def publish_order(service, direct, persistent, order: dict, verbose: bool = True,
                  script: str = None) -> None:
    """Publish one order's lifecycle, raising exceptions where they occur.

    The sequence mirrors the business: an order is created, credit is checked,
    then it is allocated to a warehouse. A credit hold therefore fires BEFORE
    allocation — which is why no warehouse appears anywhere in this flow.
    """
    import random

    def send_lifecycle(topic: str, body: dict, tag: str):
        # Direct: fire and forget. No spool, no ack, no redelivery.
        direct.publish(destination=Topic.of(topic), message=_message(service, body))
        if verbose:
            print(f"  {tag:16} [direct]     {topic}")

    def send_exception(topic: str, body: dict):
        # Guaranteed: spooled by the broker, redelivered until acked, and
        # DMQ-eligible so an exhausted message lands somewhere visible.
        persistent.publish(destination=Topic.of(topic), message=_message(service, body))
        if verbose:
            print(f"  {'EXCEPTION':16} [guaranteed] {topic}")
            print(f"  {'exceptionId':16} {body['exceptionId']}")

    send_lifecycle(topics.lifecycle("created", order["orderId"]), order, "created")

    # --- credit check, before allocation -----------------------------------
    # `script` names the exception this order must raise. With script=None the
    # ambient probabilities below apply instead (--random).
    hold = script == "credit-hold" if script is not None else random.random() < P_CREDIT_HOLD
    if hold:
        body = payloads.credit_hold(order)
        send_exception(topics.order_exception("credit-hold", order["orderId"]), body)
        return  # a held order does not allocate

    send_lifecycle(topics.lifecycle("allocated", order["orderId"]), order, "allocated")

    # --- allocation, per line ----------------------------------------------
    # One event per affected LINE, not one per order. A shortfall on line 3 says
    # nothing about lines 1, 2, 4 and 5, and each is resolved separately.
    shorted = False
    if script == "stock-shortfall":
        # Deterministic: short the LAST line, so the topic ends in a line id the
        # audience can read off the screen and match to the consumer output.
        line = order["lines"][-1]
        body = payloads.stock_shortfall(order, line)
        send_exception(
            topics.line_exception("stock-shortfall", order["orderId"], line["lineId"]),
            body)
        return
    if script is not None:
        # Scripted and not an exception order: clean lifecycle, nothing raised.
        for verb in ("picked", "shipped", "invoiced"):
            send_lifecycle(topics.lifecycle(verb, order["orderId"]), order, verb)
        return
    for line in order["lines"]:
        if random.random() < P_STOCK_SHORTFALL:
            body = payloads.stock_shortfall(order, line)
            send_exception(
                topics.line_exception("stock-shortfall", order["orderId"], line["lineId"]),
                body)
            shorted = True

    if shorted:
        return  # the rest of the flow waits on resolution

    for verb in ("picked", "shipped", "invoiced"):
        send_lifecycle(topics.lifecycle(verb, order["orderId"]), order, verb)


def publish_forced(service, persistent, kind: str) -> None:
    """Publish exactly one exception of the requested kind, on cue.

    Prints the topic and the payload highlights, because during the demo this
    output is the thing being pointed at.
    """
    order = payloads.new_order()

    if kind == "credit-hold":
        body = payloads.credit_hold(order)
        topic = topics.order_exception("credit-hold", order["orderId"])
        detail = (f"order value ${body['orderValue']:,.2f}  "
                  f"limit ${body['creditLimit']:,.2f}  "
                  f"over by ${body['overBy']:,.2f}")
        scope = "ORDER-scoped — the topic ends at the order"
    else:
        # Pick a line deliberately rather than at random, so the lineId in the
        # topic is predictable enough to point at.
        line = order["lines"][-1]
        body = payloads.stock_shortfall(order, line)
        topic = topics.line_exception("stock-shortfall", order["orderId"],
                                      line["lineId"])
        detail = (f"line {line['lineId']} of {len(order['lines'])}  "
                  f"{line['sku']}  ordered {body['quantityOrdered']}  "
                  f"on hand {body['quantityOnHand']}  short {body['quantityShort']}")
        scope = "LINE-scoped — the topic ends at the order AND the line"

    persistent.publish(destination=Topic.of(topic), message=_message(service, body))
    print(f"  {order['orderId']}  {order['distributor']}")
    print(f"  topic     {topic}")
    print(f"  scope     {scope}")
    print(f"  detail    {detail}")


def publish_duplicate(service, persistent) -> None:
    """Publish one exception TWICE with the same exceptionId — Beat 5.

    WHY THIS FLAG EXISTS RATHER THAN RESTARTING A CONSUMER.
    Beat 5 shows a consumer recognising an exception it has already handled.
    The obvious way to trigger that is to restart a consumer so the broker
    redelivers an unacked message - but the dedupe set lives in the consumer
    PROCESS, so a restart begins with an empty set and the redelivered copy
    looks new. The beat cannot fire that way.

    Publishing the same exceptionId twice exercises exactly the claim being
    made: at-least-once delivery means a consumer may see the same exception
    more than once, and this one dedupes on (orderId, exceptionId). Whether the
    second copy came from a broker redelivery or a duplicate publish is
    immaterial to the consumer, which is the point of idempotency.

    Say it that way in the demo. "Here is the same exception arriving twice -
    however that happens - and here is the consumer noticing" is honest and is
    the actual guarantee. Claiming the broker redelivered it when we published
    it twice would not be.
    """
    order = payloads.new_order()
    body = payloads.credit_hold(order)
    topic = topics.order_exception("credit-hold", order["orderId"])

    for attempt in (1, 2):
        persistent.publish(destination=Topic.of(topic), message=_message(service, body))
        print(f"  send {attempt}          {topic}")
    print(f"  exceptionId      {body['exceptionId']}")
    print("  both copies carry the SAME exceptionId — the consumer should")
    print("  process the first and recognise the second.")


def publish_poison(service, direct, persistent) -> None:
    """Publish a credit hold the consumer cannot process.

    Deliberately still DMQ-eligible: the point is that it lands in the dead
    message queue rather than vanishing. Set that flag to False and the same
    message disappears with no trace, which is the failure worth showing.

    On the wire and on screen this is an ordinary credit-hold exception. It has
    to be: the consumer is what discovers the message cannot be processed, and
    labelling it at publish time would give that away.
    """
    order = payloads.new_order()
    body = payloads.credit_hold(order)
    body["creditLimit"] = None          # the consumer will fail on this
    topic = topics.order_exception("credit-hold", order["orderId"])

    print(f"{order['orderId']}  {order['distributor']}"
          f"  ({len(order['lines'])} lines)")
    lifecycle = topics.lifecycle("created", order["orderId"])
    direct.publish(destination=Topic.of(lifecycle), message=_message(service, order))
    print(f"  {'created':16} [direct]     {lifecycle}")
    persistent.publish(destination=Topic.of(topic), message=_message(service, body))
    print(f"  {'EXCEPTION':16} [guaranteed] {topic}")
    print(f"  {'exceptionId':16} {body['exceptionId']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    # The defaults are the demo's opening sequence, so a bare run needs no
    # arguments. --orders 0 runs until Ctrl-C.
    ap.add_argument("--orders", type=int, default=8,
                    help="publish N orders then exit (default 8 = Beat 1; "
                         "use 0 to run until Ctrl-C)")
    ap.add_argument("--rate", type=float, default=3.0,
                    help="orders per second (default 3 = Beat 1; "
                         "0 publishes as fast as the broker accepts)")
    ap.add_argument("--credit-hold", action="store_true",
                    help="force one order-scoped credit-hold exception and exit")
    ap.add_argument("--shortfall", action="store_true",
                    help="force one line-scoped stock-shortfall exception and exit")
    ap.add_argument("--poison", action="store_true",
                    help="publish a single un-processable message and exit")
    ap.add_argument("--duplicate", action="store_true",
                    help="publish the SAME exception twice, to exercise Beat 5")
    ap.add_argument("--seed", type=int, default=None,
                    help="replay a previous run exactly. Omit for fresh data — "
                         "a random seed is minted and printed either way, so "
                         "any run can be reproduced after the fact")
    ap.add_argument("--random", action="store_true",
                    help="use the ambient ~4%% exception rates instead of the "
                         "scripted set. Honest rates, unpredictable output - "
                         "for a load run, not for a demo beat")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    # Always seed. Omitting --seed mints a random one, so every run differs but
    # every run is also replayable — the seed is printed below.
    used_seed = payloads.seed(args.seed)

    print(f"seed {used_seed}"
          f"{'  (replayed)' if args.seed is not None else '  — rerun with --seed to reproduce'}")

    with broker.session("publisher") as service:
        # Two publishers on one connection — the mixed-mode argument, in code.
        direct = service.create_direct_message_publisher_builder().build()
        persistent = service.create_persistent_message_publisher_builder().build()
        direct.start()
        persistent.start()
        try:
            if args.duplicate:
                publish_duplicate(service, persistent)
                return 0
            if args.credit_hold:
                publish_forced(service, persistent, "credit-hold")
                return 0
            if args.shortfall:
                publish_forced(service, persistent, "stock-shortfall")
                return 0
            if args.poison:
                publish_poison(service, direct, persistent)
                return 0

            sent = 0
            interval = 1.0 / args.rate if args.rate > 0 else 0
            if args.orders:
                print(f"publishing {args.orders} orders at ~{args.rate}/s\n")
            else:
                print(f"publishing at ~{args.rate}/s — Ctrl-C to stop\n")
            # The last two orders raise one exception each, so every run ends
            # the same way. Everything before them is a clean lifecycle.
            #   n-1  credit-hold      order-scoped  -> credit desk
            #   n    stock-shortfall  line-scoped   -> inventory desk
            # The unprocessable message is not part of this set; publish it on
            # its own with --poison.
            plan = {}
            if args.orders >= 2 and not args.random:
                plan = {args.orders - 1: "credit-hold",
                        args.orders: "stock-shortfall"}

            while args.orders == 0 or sent < args.orders:
                n = sent + 1
                step = plan.get(n)
                order = payloads.new_order()
                if not args.quiet:
                    print(f"{order['orderId']}  {order['distributor']}"
                          f"  ({len(order['lines'])} lines)")
                publish_order(service, direct, persistent, order,
                              verbose=not args.quiet,
                              # "clean", not None: None means "use the ambient
                              # rates", which would add unplanned exceptions.
                              script=((step or "clean") if plan else None))
                sent += 1
                if interval:
                    time.sleep(interval)
            print(f"\npublished {sent} orders")
            return 0
        except KeyboardInterrupt:
            print("\nstopped")
            return 0
        finally:
            direct.terminate()
            persistent.terminate()


if __name__ == "__main__":
    sys.exit(main())
