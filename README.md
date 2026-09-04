# Meridian Components: order exceptions on Solace PubSub+

A working event-driven demo built on Solace PubSub+, with an agentic tier on
Solace Agent Mesh. It was built for a Solutions Engineer presentation to Solace
in September 2026, and the design documents were written before the code.

Meridian Components is a fictional industrial-parts distributor. Everything here
is illustrative, and labelled as such.

---

## The problem

Meridian sells through distributors. About 4% of orders hit an exception, such as
a stock shortfall, an address validation failure, a credit hold or a carrier
rejection. None of those exceptions are *discovered* until the overnight batch
runs, so an order that broke at 9:15 in the morning sits untouched for fourteen
hours before a human sees it, and is then worked over email, which adds another
day.

The information needed to resolve most of them existed inside the business the
moment the order broke. The problem is not missing data. It is that the data and
the exception never meet while anyone can still do anything about it.

Full version, including why it has not already been fixed and the hard questions
it invites: [`docs/problem-statement.md`](docs/problem-statement.md).

---

## What is here

| Act | What it shows | Status |
|---|---|---|
| **Act 2** | Event-driven exception handling on PubSub+: topic taxonomy, mixed delivery modes, consumer outage and catch-up, poison message into the DMQ, duplicate handling, replay | **Runs live** |
| **Act 3** | An agentic tier on Solace Agent Mesh, contrasted against a good-faith synchronous HTTP arm | **Partially working, narrated** |

Act 1 is the problem statement above. There is no Act 1 code by design: the
framing is the deliverable.

### Act 3, honestly

The mesh is real. Three agents register on durable queues, discovery works, the
web UI serves, the orchestrator delegates, and all five agent tools execute. The
task still finalises FAILED on an LLM provider 503.

It is presented as narration rather than a live run, and the repository says so
rather than hiding it. What works, what does not, and the four blockers found
are documented in [`RUNBOOK.md`](RUNBOOK.md) Appendix A.

---

## The topic architecture

One rule governs it: **a topic hierarchy serves routing and access control. A
level serving neither has no justification and becomes noise.** In Solace, ACL
profiles are written as topic patterns, so the hierarchy is not merely described
by the security model, it *is* the security model.

```
mc/order/exception/v1/{exceptionType}/{orderId}              order-scoped
mc/order/exception/v1/{exceptionType}/{orderId}/{lineId}     line-scoped
```

Depth varies within one root deliberately: a shorter topic *means* order-scoped,
and subscribers route on exactly that. A credit hold is a fact about an order; a
stock shortfall is a fact about a line.

`src/topics.py` is the only place topic strings are composed, and it carries the
value spaces plus the record of what was cut and why. The reasoning, every
rejected alternative, and the levels that were removed under the governing rule
are in [`docs/messaging-design.md`](docs/messaging-design.md).

---

## Quickstart

Requires Python 3.10 to 3.13 and either a Solace Cloud service or Docker for a
local broker. Neither needs a paid account.

```powershell
git clone https://github.com/aaugust/solace-order-exceptions.git
cd solace-order-exceptions

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env      # add an LLM key only if you want Act 3
.\demo-up.ps1               # cloud by default; -Local for Docker
```

`demo-up.ps1` provisions the queues, subscriptions and DMQ wiring, then opens
the demo windows. `demo-reset.ps1` puts everything back to a clean state between
runs, and `demo-down.ps1` tears it down and reclaims memory.

Broker selection is one environment variable, `DEMO_PROFILE`, resolved by
`src/profile.py`. Every entry point prints which broker it is on before it does
anything else.

**Act 3 installs separately**, into its own virtualenv, so it can be deleted
without touching the demo that runs:

```powershell
python -m venv .venv-sam
.\.venv-sam\Scripts\Activate.ps1
pip install -r requirements-sam.txt
```

Full setup, the window layout, the demo sequence beat by beat, the reset
procedure and a break-glass table for failures mid-run are in
[`RUNBOOK.md`](RUNBOOK.md).

---

## Repository map

| Path | What it is |
|---|---|
| [`docs/`](docs/) | Design documents, written before the code. Problem statement, messaging design, agent mesh design |
| [`RUNBOOK.md`](RUNBOOK.md) | How to stand it up and run it, plus every failure found during the build |
| [`presentation/`](presentation/) | The slides |
| `src/` | Act 2 messaging and Act 3 agent tooling |
| `scripts/` | Provisioning, queue depths over SEMP, dead-message inspection |
| `configs/` | Agent Mesh agent, gateway and shared configuration |
| `*.ps1` | Environment lifecycle: up, down, reset, window management |

No credentials are committed. `.env.example` lists what is needed; `.env.local`
holds only the `admin/admin` defaults the local Docker container is created with.

---

## A note on what this repository is for

The build kept running into one pattern, and it is the most useful thing in here:
**the symptom names the wrong subsystem.** By the end there were seven
independent cases. A dropped connection property reported as a missing
credential. A byte-order mark in an environment file reported as a YAML
validation error. A TLS trust store that completes the handshake and then kills
long-lived receivers later, so the publisher works while the consumer dies. A
duplicate client name presenting as a flood of SSL errors. `startReplay`
returning 200 while leaving the queue undeliverable. `deleteMsgs` returning 200
and removing nothing. A model name read from a database while the environment
file that appeared to set it was ignored.

Each is written up where it was found, with what it looked like and what it
actually was, rather than quietly fixed. That is deliberate. A demo that only
shows the working path is not evidence of much.
