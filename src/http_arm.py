"""The synchronous HTTP control arm — the honest comparison for Act 3.

    python src/http_arm.py serve            # run the two agent services
    python src/http_arm.py orchestrate      # run one exception through them
    python src/http_arm.py kill-test        # the beat: kill an agent mid-workflow

WHY THIS EXISTS
Act 3's argument is not "agents work". It is a CONTRAST: the same multi-agent
workflow over synchronous point-to-point HTTP versus over the broker. Without
this arm there is nothing to compare and the demo shows only that agents can use
a message broker, which is not an argument. The 8/14 cut order says the
transport comparison is the last thing to go, and this is half of it.

BUILT IN GOOD FAITH, DELIBERATELY
A strawman is worse than no comparison, because an SE audience will recognise
one. This arm gets:
  - connection pooling and keep-alive
  - sensible timeouts
  - retries with backoff on connection errors
Same tools, same thresholds, same decisions as the mesh agents - it imports
agent_tools, so the business logic is literally identical. The ONLY variable is
the transport.

WHAT IT STRUCTURALLY CANNOT HAVE, and why that is the point:
  - delivery to a callee that is not currently listening
  - recovery of a request whose caller died after sending
  - discovery of a new agent without a configuration change
Those are not implementation gaps to be fixed with a better client. They are
what "synchronous and point-to-point" means.
"""
import argparse
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))

import agent_tools  # noqa: E402

INVENTORY_PORT = 8101
CREDIT_PORT = 8102


# --------------------------------------------------------------------------
# The agent services. Same decisions as the mesh agents, reached the same way.
# --------------------------------------------------------------------------
def _make_app():
    from fastapi import FastAPI
    from pydantic import BaseModel

    app = FastAPI(title="meridian http arm")

    class ShortfallRequest(BaseModel):
        orderId: str
        lineId: int
        sku: str
        quantityShort: int

    class CreditRequest(BaseModel):
        orderId: str
        account: str
        orderValue: float

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/inventory/resolve")
    def inventory_resolve(r: ShortfallRequest):
        inv = agent_tools.check_inventory(r.sku, r.quantityShort)
        subs = agent_tools.check_substitutes(r.sku)
        escalate, reason = agent_tools.should_escalate_shortfall(r.quantityShort, inv, subs)
        return {"orderId": r.orderId, "lineId": r.lineId,
                "escalate": escalate, "action": reason}

    @app.post("/credit/resolve")
    def credit_resolve(r: CreditRequest):
        credit = agent_tools.check_credit(r.account)
        escalate, reason = agent_tools.should_escalate_credit(r.orderValue, credit)
        return {"orderId": r.orderId, "escalate": escalate, "action": reason}

    return app


def serve():
    """Run both agent services. Two ports, because they are two services."""
    import uvicorn
    app = _make_app()
    for port in (INVENTORY_PORT, CREDIT_PORT):
        t = threading.Thread(
            target=lambda p=port: uvicorn.run(app, host="127.0.0.1", port=p,
                                              log_level="warning"),
            daemon=True)
        t.start()
    print(f"http arm listening on {INVENTORY_PORT} (inventory) and {CREDIT_PORT} (credit)")
    print("Ctrl-C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopped")


# --------------------------------------------------------------------------
# The orchestrator. This is where the transport difference actually shows.
# --------------------------------------------------------------------------
def _session():
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = requests.Session()
    # Pooling, keep-alive and retry on connection errors. Everything a
    # competent HTTP client would have, so nobody can say the arm was hobbled.
    retry = Retry(total=3, backoff_factor=0.3,
                  status_forcelist=[502, 503, 504],
                  allowed_methods=["POST"])
    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=retry)
    s.mount("http://", adapter)
    return s


def orchestrate(lines: int = 3, timeout: float = 5.0, verbose: bool = True) -> dict:
    """Fan out one order's shortfalls, synchronously, and assemble an answer."""
    import requests
    sess = _session()
    order_id = f"SO-{4400000 + int(time.time()) % 99999}"
    started = time.time()
    results, failures = [], []

    for line in range(1, lines + 1):
        payload = {"orderId": order_id, "lineId": line,
                   "sku": f"MC-BRG-104{line}", "quantityShort": 10 * line}
        try:
            # SYNCHRONOUS AND SEQUENTIAL. The caller blocks on each callee in
            # turn. Concurrency here would need threads the caller manages,
            # which is the point: the coordination is the caller's problem.
            r = sess.post(f"http://127.0.0.1:{INVENTORY_PORT}/inventory/resolve",
                          json=payload, timeout=timeout)
            r.raise_for_status()
            results.append(r.json())
            if verbose:
                d = results[-1]
                print(f"  line {line}: {'ESCALATE' if d['escalate'] else 'resolved'} — {d['action']}")
        except requests.RequestException as e:
            # THE FAILURE THAT MATTERS. There is no queue and no retry target:
            # the callee is not listening, so this request is gone. Nothing
            # holds it, nothing replays it, and the caller is the only party
            # that knows it existed.
            failures.append({"lineId": line, "error": type(e).__name__})
            if verbose:
                print(f"  line {line}: LOST — {type(e).__name__}: request not delivered")

    return {"orderId": order_id, "elapsed": round(time.time() - started, 3),
            "resolved": len(results), "lost": len(failures), "failures": failures}


def kill_test():
    """The beat. Fan out with the callee down partway through."""
    print("HTTP ARM — agent dies mid-workflow\n")
    print("1. all agents up:")
    a = orchestrate(lines=3)
    print(f"   -> resolved {a['resolved']}, lost {a['lost']}, {a['elapsed']}s\n")

    print("2. stop the inventory service now (Ctrl-C its window), then press Enter...")
    try:
        input()
    except EOFError:
        pass

    print("3. same workflow with the agent down:")
    b = orchestrate(lines=3, timeout=2.0)
    print(f"   -> resolved {b['resolved']}, LOST {b['lost']}, {b['elapsed']}s\n")

    print("The lost requests are gone. No queue held them, no retry target exists,")
    print("and restarting the agent does not bring them back. Compare with the")
    print("mesh arm, where the queue spooled and the agent finished the work on")
    print("reconnect.")
    return b


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["serve", "orchestrate", "kill-test"])
    ap.add_argument("--lines", type=int, default=3)
    args = ap.parse_args()

    if args.mode == "serve":
        serve()
    elif args.mode == "orchestrate":
        print(json.dumps(orchestrate(lines=args.lines), indent=2))
    else:
        kill_test()
