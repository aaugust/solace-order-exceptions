"""Queue depths straight from the broker, without a browser.

    python scripts/queue_depths.py

WHY THIS EXISTS
Window 5 is the broker console, and on cloud that is a web app behind a login.
A stale session cookie at the wrong moment turns Beat 3 or Beat 4 into a login
form in front of the room. This reads the same numbers over SEMP from the
publisher window, so the fallback costs one command and no context switch.

It proves exactly what the console screens prove: that the spool holds messages
while a consumer is down (Beat 3), and that an exhausted message ends up in
#DEAD_MSG_QUEUE rather than being discarded (Beat 4).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import profile as _profile  # noqa: E402

_profile.load(quiet=True)

import requests  # noqa: E402
from requests.auth import HTTPBasicAuth  # noqa: E402

QUEUES = [
    "Q/credit-desk/exceptions",
    "Q/inventory-planning/exceptions",
    "Q/audit/all-exceptions",
    "#DEAD_MSG_QUEUE",
]


def main() -> int:
    auth = HTTPBasicAuth(os.environ["SOLACE_ADMIN_USER"], os.environ["SOLACE_ADMIN_PASSWORD"])
    mon = os.environ["SOLACE_SEMP"].replace("/config", "/monitor")
    vpn = os.environ["SOLACE_VPN"]

    print()
    print("  NOTE: on this trial cloud service spooledMsgCount RISES on publish")
    print("        but does not FALL on acknowledgement - a queue proven empty")
    print("        still reported 3 for over two minutes. Trust it for 'messages")
    print("        arrived', never for 'messages remain'. See RUNBOOK.")
    print()
    print(f"  {'queue':34} {'queued*':>7} {'redelivered':>12}")
    print(f"  {'-' * 34} {'-' * 7:>7} {'-' * 12:>12}")
    for q in QUEUES:
        enc = requests.utils.quote(q, safe="")
        try:
            r = requests.get(f"{mon}/msgVpns/{vpn}/queues/{enc}", auth=auth, timeout=20)
            # A queue that does not exist answers 400 with NOT_FOUND on this
            # service, not 404 - reading ["data"] then raised KeyError and the
            # row printed "ERROR" as if the broker were unreachable.
            if r.status_code != 200:
                print(f"  {q:34} {'absent':>7}")
                continue
            d = r.json()["data"]
            print(f"  {q:34} {d.get('spooledMsgCount', '?'):>7} "
                  f"{d.get('redeliveredMsgCount', '?'):>12}")
        except Exception as e:
            # Say what went wrong rather than printing a blank row - a silent
            # zero here would be read as "nothing spooled", which is the
            # opposite of the point being made.
            print(f"  {q:34} {'ERROR':>7}   {type(e).__name__}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
