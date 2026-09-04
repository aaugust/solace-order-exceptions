"""Read the dead message queue without consuming it.

    python scripts/dmq_peek.py            # show what is in the DMQ
    python scripts/dmq_peek.py --drain    # read AND remove them

WHY THIS EXISTS
The admin console cannot show you a dead message's contents. SEMP exposes only
metadata for spooled messages - msgId, spooledTime, redeliveryCount - and
returns 400 for payload. The Manager UI is built on SEMP, so the correlation id
and the payload are not hidden behind a button; they are not available to the
console at all. The only way to see them is to bind a client.

That matters live: "show me the actual dead message" is a fair challenge and
window 5 cannot answer it. This can.

NON-DESTRUCTIVE BY DEFAULT
Messages are received and deliberately NOT settled, so they return to the queue
when the receiver terminates. Safe because #DEAD_MSG_QUEUE has
maxRedeliveryCount 0 (unlimited) - nothing is discarded for being redelivered.
The redelivery counter does increment, which is cosmetic.

CORRELATION
msgId is the broker's internal spool sequence number and means nothing to the
application. The application key is the correlation id, which the publisher sets
to the exceptionId (see publisher._message), and which also appears in the JSON
payload. That is the field to match against what the publisher printed.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import broker  # noqa: E402
from solace.messaging.resources.queue import Queue  # noqa: E402

DMQ = "#DEAD_MSG_QUEUE"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drain", action="store_true",
                    help="acknowledge the messages, removing them permanently")
    ap.add_argument("--seconds", type=float, default=8.0,
                    help="how long to keep reading (default 8)")
    args = ap.parse_args()

    with broker.session("dmq-peek") as service:
        receiver = (service.create_persistent_message_receiver_builder()
                    .build(Queue.durable_exclusive_queue(DMQ)))
        receiver.start()
        print(f"\n  reading {DMQ} "
              f"({'DRAINING - messages will be removed' if args.drain else 'peek only'})\n")
        seen = 0
        t0 = time.time()
        while time.time() - t0 < args.seconds:
            m = receiver.receive_message(timeout=1000)
            if m is None:
                continue
            seen += 1
            corr = m.get_correlation_id()
            raw = m.get_payload_as_string() or ""
            try:
                body = json.loads(raw)
            except ValueError:
                body = {}
            print(f"  [{seen}] correlationId {corr}")
            print(f"       exceptionId   {body.get('exceptionId')}")
            print(f"       orderId       {body.get('orderId')}   "
                  f"type={body.get('exceptionType')}  scope={body.get('scope')}")
            if body.get("_poison"):
                print(f"       poison        {body['_poison']}")
            print()
            if args.drain:
                receiver.ack(m)
        # Not settling is the point: unacked messages go back on the queue.
        receiver.terminate()
        print(f"  {seen} message(s) read"
              f"{' and removed' if args.drain else '; left on the queue'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
