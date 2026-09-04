# Demo Runbook — Meridian Components order exceptions

How to stand this up, and the order to run it in during the presentation.

Design and rationale live in [`docs/`](docs/): [problem-statement.md](docs/problem-statement.md)
(the problem), [messaging-design.md](docs/messaging-design.md) (the architecture and its
rejected alternatives), and [agent-mesh-design.md](docs/agent-mesh-design.md) (the agentic tier).

---

## 0. Two brokers, and which one you are on

The demo runs against **either** a Solace Cloud service or a local Docker broker. Same code, same topic architecture, same queues — the difference is one environment variable.

| | Cloud | Local |
|---|---|---|
| Broker | `meridian-demo`, AWS us-east-1, Developer / Standard 100 | Docker container `solace` |
| Local RAM | **0** | ~1.5 GB broker + ~2.5 GB Docker/WSL |
| Transport | `tcps://…:55443` (TLS mandatory) | `tcp://localhost:55555` |
| Start time | Instant — it is already running | ~90 s warm, ~3 min cold |
| Fails if | The network does | Nothing external |
| Profile file | `.env.cloud` (gitignored) | `.env.local` (committed) |

**Cloud is the default working environment. Local is the demo-day fallback**, kept working deliberately: the 2026-08-14 decision chose local precisely to avoid a network dependency in front of a live audience, and that reasoning has not changed. Rehearse both; decide on the morning.

Switching is one variable, honoured by every script, publisher, consumer and the provisioning:

```powershell
$env:DEMO_PROFILE = "cloud"    # or "local"
```

Every entry point prints which broker it is on. Publishing to the wrong one mid-demo is a confusing failure, and one line prevents it.

---

## 0a. Cloud — the normal path

Nothing to start. The service is already running.

```powershell
.\demo-up.ps1
```

That verifies the credentials are filled in, confirms SEMP answers, and provisions the topology. No Docker, no container, no WSL VM.

**Credentials live in `.env.cloud`**, which is gitignored and kept separate from `.env` because `sam init` overwrites `.env` without backup — that is how a Gemini API key was lost on 2026-09-01.

Two things about those values that cost time:

**Messaging and management are different accounts.** `solace-cloud-client` connects and publishes; `mission-control-manager` is what SEMP wants. On the local broker both were `admin/admin`, so it is natural to assume they are the same. They are not.

**Take the Secured SMF URI.** Solace Cloud states on the service creation form that "all unsecure ports are disabled by default", so plain `tcp://` is refused outright.

### The trust store, because it will bite anyone who clones this

`src/broker.py` handles it automatically by creating a `certs/` directory and copying in the CA bundle that ships with `requests`. It is documented here because the failure modes are misleading and cost roughly an hour:

| Mistake | What it looks like |
|---|---|
| No trust store | `SESSION CREATION UNSUCCESSFUL. Failed to load trust store` — reads like a missing file, is a missing setting. Solace's client does **not** read the Windows certificate store |
| Pointing at a `.pem` file | `Untrusted certificate` — sends you investigating the server's certificate rather than your own argument. `trust_store_file_path` wants a **directory** despite the name |
| Pointing at a directory with other files in it | Handshake succeeds, **publisher works**, and long-lived receivers die in a loop of `SSL 'SSL-client' cannot read, sslErr = 1`. A working publisher next to a failing consumer is the most misleading signal of the three |

Certificate validation is left **on**. Turning it off would also work and is deliberately not done.

---

## 0b. Local — the fallback

```powershell
.\demo-up.ps1
```

Creates the container if absent, starts it if stopped, waits for the broker to answer, and provisions. Roughly 3 minutes cold, 90 seconds warm.

The `docker run` it issues includes `--shm-size=2g`, which is **mandatory** — the broker uses shared memory for its spool and will not start on Docker's 64 MB default. That is the most common first-run failure.

### Readiness — do not read the logs

Startup emits hundreds of lines like `unknownEarlyBirdLogPrefix … ERROR 'ASSERT: Process::isAddrStaticShared(this)'`. **These are cosmetic**, appear on every start, and look catastrophic on a `docker logs --tail`. The deterministic check is SEMP answering:

```powershell
curl.exe -s -u admin:admin http://localhost:8080/SEMP/v2/config/about/api
```

That URL is the **local** broker only. On cloud, `demo-up.ps1` polls the cloud SEMP endpoint from `.env.cloud` instead — you do not run this by hand.

A 200 with JSON means the broker is up. `docker inspect solace --format '{{.RestartCount}}'` returning `0` is what separates log noise from a real fault — a genuine failure here crash-loops the container.

> PowerShell aliases `curl` to `Invoke-WebRequest`, which rejects Unix flags. Use `curl.exe`. Also `&&` is a parser error in PowerShell 5.1.

---

## 0c. Reclaiming memory

```powershell
.\demo-down.ps1          # stop broker + demo processes
.\demo-down.ps1 -Deep    # also stop Docker Desktop and its WSL backend VM
```

Measured on this machine, 2026-09-02:

| State | `vmmemWSL` | Docker procs |
|---|---|---|
| Local broker running | 4,097 MB | ~866 MB |
| After `.wslconfig` cap | 1,574 MB | ~866 MB |
| Broker in cloud, Docker stopped | **658 MB** | **0** |

`%USERPROFILE%\.wslconfig` caps the VM at 3 GB and sets `autoMemoryReclaim=gradual`. That second setting is the one that matters: without it WSL never hands freed memory back to Windows, so stopping the broker frees nothing Windows can see.

**Stopping Docker Desktop needs its own CLI, not a process kill.** `Stop-Process` does not work — Docker Desktop has a restart supervisor and comes straight back, taking the WSL VM with it. `docker desktop stop` is the supported shutdown. `-Deep` uses it.

`-Deep` also refuses when other containers are running, and names them, rather than quietly taking down unrelated work.

---

## 0d. First-time setup

Only needed on a fresh clone.

```powershell
cd solace-order-exceptions
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

For cloud, fill in `.env.cloud` from the Solace Cloud console: **Connect** tab → *Solace Messaging* for host, VPN, username, password; **Manage** tab → *SEMP - REST API* for the SEMP base path and its separate credentials.

> The Solace Cloud service is on a **15-day trial from 2026-09-02**, so it expires around 2026-09-17. After that, use the local path or create a new service.

---

## 0e. Agent Mesh (Act 3)

Agent Mesh is installed **natively on Windows** — undocumented by Solace, who list macOS, Linux and Windows-via-WSL, but it works on Python 3.13.12 and saves the WSL detour entirely.

It lives in its own virtualenv, `.venv-sam`, deliberately separate from `.venv` so Act 2's `requirements.txt` stays small and the whole thing can be deleted without touching the working demo.

```powershell
python -m venv .venv-sam
.\.venv-sam\Scripts\Activate.ps1
pip install -r requirements-sam.txt
```

### Point it at a broker

```powershell
.\configure-sam.ps1 -Cloud     # Solace Cloud
.\configure-sam.ps1            # local Docker broker
```

This edits only the broker settings in SAM's `.env`, leaving the LLM configuration, the API key and the generated YAML alone. Use `setup-sam.ps1` instead only for first-time scaffolding — it re-runs `sam init`, which regenerates every config file and overwrites `.env` without backup.

### Run it

```powershell
.\.venv-sam\Scripts\solace-agent-mesh.exe run
```

Verify it actually connected rather than trusting the absence of a traceback:

```powershell
# should list meridian/q/a2a/meridian_orchestrator with durable=True
curl.exe -s -u "<SOLACE_ADMIN_USER>:<SOLACE_ADMIN_PASSWORD>" `
  "<SOLACE_SEMP>/../monitor/msgVpns/<VPN>/queues?count=50"
```

### The two settings that matter, and why

**`USE_TEMPORARY_QUEUES=false`.** This is the setting Act 3's central claim depends on and it is **not** the default.

`shared_config.yaml` carries `temporary_queue: ${USE_TEMPORARY_QUEUES, true}`, and each agent binds to `{namespace}/q/a2a/{agent_name}`. A temporary queue is destroyed when its client disconnects — so with the default, the kill-an-agent beat **loses the message** and the argument collapses live. Durable queues make the queue outlive the agent, the broker spools while it is gone, and the message is delivered on reconnect.

Confirm with `durable=True` on `meridian/q/a2a/meridian_orchestrator` before rehearsing that beat.

**`TRUST_STORE`.** SAM's connector defaults to `os.path.dirname(certifi.where())` — see `solace_ai_connector/common/messaging/solace_messaging.py`. That is the broken configuration described in §0a: certifi's package directory holds `.py` files alongside the bundle, which makes the handshake succeed while long-lived receivers die in an SSL read loop. **Agents are long-lived receivers.** `configure-sam.ps1` overrides it to `certs/`, which holds the bundle and nothing else.

### Three failures worth recognising

Each cost time on 2026-09-02, and each reports something other than its cause:

| Symptom | Cause |
|---|---|
| `ValidationError … namespace / Field required` | A **UTF-8 BOM** on `.env`. PowerShell's `Set-Content -Encoding utf8` writes one, so SAM reads the first key as `\ufeffNAMESPACE` and `${NAMESPACE}` resolves to nothing. The error names the YAML; the fault is the encoding of a different file. `configure-sam.ps1` now writes BOM-free |
| `SOLCLIENT_SUBCODE_FAILED_LOADING_TRUSTSTORE` | A Windows path with **backslashes** in `.env`. Use forward slashes: `C:/Users/.../certs` |
| `sam init --skip` silently ignoring `--llm-*` flags | Its `shared_config` template carries `__PLANNING_MODEL_CONFIG__` placeholders; when substitution does not happen the whole models block is dropped (98 template lines become 35) and three configs reference model roles nothing defines. `setup-sam.ps1` writes the block itself |

### Act 3 status

**Act 3 is NARRATED, not run.** The agentic path does not complete a task. What running it would involve, what already works, and the four blockers are in **Appendix A**.

The mesh itself is real: three agents register on durable queues, discovery works, the web UI serves, and as of 2026-09-03 evening the orchestrator delegates and **all five agent tools execute**. The task still finalises FAILED on a provider 503.

**The model name does not come from `.env`.** It is persisted in `platform.db` and seeded once; editing `.env` changes nothing and SAM logs the stale value as though it were configuration. Change it with a direct update to `model_configurations`, or the run silently uses whatever the first run wrote. This is the fourth instance of this build's recurring pattern.

**Current model configuration:** `gemini/gemini-flash-lite-latest`, `api_base` NULL, `model_params` `{"cache_strategy": "none"}`. The native `gemini/` provider and the disabled cache are both required — see Appendix A for why.

**Benign startup noise:** five lines of `Async loop not available` on a `discovery/gatewaycards` message during boot, once, non-fatal. Do not react to it.

---

## 1. What each script is

**Act 2 — the demo that runs**

| Script | Role |
|---|---|
| `src/topics.py` | The **only** place topic strings are composed. Also carries the value spaces and the record of what was cut and why |
| `src/broker.py` | Connection in one place — profile, TLS trust store, retry strategy, unique client name |
| `src/profile.py` | Loads `.env.cloud` / `.env.local` by `DEMO_PROFILE`. Every entry point prints which broker it is on |
| `src/payloads.py` | Faker-backed orders and exception bodies |
| `src/publisher.py` | Order lifecycle (direct) and exceptions (guaranteed). A bare run is the opening sequence; `--credit-hold`, `--shortfall`, `--poison`, `--duplicate` force one on cue |
| `src/consumer.py` | The three desks — `desk-credit`, `desk-inventory`, `desk-audit` |
| `scripts/provision.py` | Queues, subscriptions, DMQ wiring. The deployable form of the design's section 2 |
| `scripts/queue_depths.py` | Queue depths + DMQ over SEMP, no browser. The window-5 fallback |
| `scripts/dmq_peek.py` | Reads dead messages, including correlation id and payload — which the console cannot show. `--drain` to remove them |

**Running the environment**

| Script | Role |
|---|---|
| `demo-up.ps1` | Bring it up. Cloud and windows are the defaults; `-Local`, `-NoWindows`, `-Fresh` |
| `demo-down.ps1` | Take it down and reclaim memory. `-Destroy`, `-Deep` |
| `demo-reset.ps1` | **One call: stop, wipe every queue, start again.** Use between rehearsals and once before the session |
| `start-demo.ps1` | Opens the four demo windows (called by `demo-up`) |
| `stop-demo.ps1` | Closes those windows; superseded by `demo-down.ps1` |

**Act 3 — narrated, not run** (see Appendix A)

| Script | Role |
|---|---|
| `src/agent_tools.py` | The deterministic data layer both arms call. No agent invents inventory or credit facts |
| `src/mesh_tools.py` | Thin wrappers exposing those tools to Agent Mesh |
| `src/http_arm.py` | The synchronous point-to-point control arm, built in good faith — pooling, keep-alive, retries |
| `src/comparison.py` | The transport comparison. **Only `kill` is worth running**; `fanout` and `backpressure` narrate the broker side rather than exercising it |
| `configure-sam.ps1` | Points Agent Mesh at a broker. Sets the trust store and durable agent queues |
| `setup-sam.ps1` | First-time Agent Mesh scaffolding only — re-runs `sam init` and overwrites `.env` |

---

## 2. Window layout for the live demo

### One command opens everything

```powershell
.\demo-up.ps1                      # DEFAULT: cloud broker, four windows
.\demo-up.ps1 -Local               # fallback: local Docker broker
.\demo-up.ps1 -NoWindows           # provision only, open nothing
```

or double-click **`start-demo.cmd`** (bypasses execution policy for that one script).

It checks the broker is answering, re-provisions the topology, then opens four titled PowerShell windows with the virtualenv already active. **Window 1 is the PUBLISHER** - the only one you type into - and windows 2, 3 and 4 are the three desk consumers. The publisher is opened last despite being numbered first, so it lands on top and focused:

| Window | State |
|---|---|
| `1 PUBLISHER` | **idle, cwd set, ready to paste into** |
| `2 desk-credit` | consumer running |
| `3 desk-inventory` | consumer running |
| `4 desk-audit` | consumer running |

Open the **broker console** yourself as window 5 - see *The console on cloud* below. It is not opened for you, because it needs a browser login.

### The console on cloud

Window 5 is the broker console. On cloud it is **not** `localhost:8080` — there is no local broker to serve it. Two ways in:

**Direct (use this).** The cloud service serves the same PubSub+ Broker Manager on its management port:

```
https://mr-connection-zlmtq5pi7n8.messaging.solace.cloud:943
```

Sign in with the **management** credentials — `SOLACE_ADMIN_USER` / `SOLACE_ADMIN_PASSWORD` from `.env.cloud`. Those are a different account from the messaging credentials; the messaging ones will not log you in.

**Via the console.** `console.solace.cloud` → **Cluster Manager** → the `meridian-demo` service → **Manage** tab → **Broker Manager**. Slower, and it is three clicks you do not want to be making live.

**Once you are in, everything is identical to the local console.** It is the same application, so **Queues** → pick a queue → **Messages Queued** works exactly as rehearsed, and `#DEAD_MSG_QUEUE` appears in the ordinary queue list.

**Bookmark the direct URL and sign in before the session starts.** The session cookie is what stands between Beat 3 and a login form in front of the room. If it has gone stale by the time you get there, the fallback is SEMP from the publisher window, which proves the same thing without a browser:

```powershell
python scripts\queue_depths.py
```

**One caveat, stated plainly:** the URL is verified to serve the Manager application, but a browser sign-in has not been tested. Do that once before Friday — it is a thirty-second check that removes the only unverified step in the demo path.

**The preflight is the point.** It finds a stopped broker here rather than mid-demo, and it checks SEMP rather than the container logs — which are full of cosmetic startup errors that look like failures. If the broker is down it tells you exactly what to run.

To close down:

```powershell
.\stop-demo.ps1           # close the four windows
.\stop-demo.ps1 -Reset    # also clear queue backlogs
```

`stop-demo.ps1` matches on the window titles it set, so it will not touch your other PowerShell sessions.

### Doing it by hand

| Window | Command |
|---|---|
| 1 — publisher | *(idle — every beat is typed here)* |
| 2 — credit desk | `python src\consumer.py desk-credit` |
| 3 — inventory | `python src\consumer.py desk-inventory` |
| 4 — audit | `python src\consumer.py desk-audit` |
| 5 — browser | broker console → Queues (see *The console on cloud*) |

Start the three consumers **first** and leave them running for the whole session. You never touch windows 2–4 again except to stop one deliberately in Beat 2. They bind to durable queues, so they receive nothing until you publish — an empty console is the correct starting state and is worth saying out loud.

**Roles are named `desk-*` on purpose.** `desk-credit` is *who does the work*; `credit-hold` is *what went wrong*. The old names (`credit-desk`, `inventory-planning`) sat one character from the exception types and were a live-typing hazard.

**UI navigation is for answering questions, not for making points.** Every point below is made from terminal output you caused. Window 5 is held in reserve for "show me the actual message" — a strong answer to a challenge, a bad thing to go hunting in unprompted.

---

## 3. The demo sequence

**Paste these, do not type them.** Keep this section open in a second window during the session.

Each beat has a thing to run, a thing to show, and a thing to say. Do not run ahead of the narration — the point is the architecture, not the throughput.

### The whole sequence, at a glance

**40 minutes of prepared material**, inside a 90-minute block. With four attendees and depth-probing expected, 40 minutes of planned content runs closer to 55—60 elapsed once questions land mid-flow. The remainder is discussion.

| Time | Segment | Beats |
|---|---|---|
| 0—3 | Framing | — |
| 3—9 | The problem | — |
| 9—20 | Architecture with Solace | — |
| **20—32** | **Act 2 live** — the broker doing the work | 1, 2, 3, 4 |
| 32—38 | **Act 3 — narrated, not run** | — |
| 38—40 | Synthesis, hand to discussion | — |

| Beat | Runs? | Budget | The point |
|---|---|---|---|
| **1** | live | 3 min | Two delivery modes on one broker; order-scoped vs line-scoped granularity |
| **2** | live | 2.5 min | A consumer dies, the queue spools, nothing is lost, other desks unaffected |
| **3** | live | 3.5 min | Poison message → DMQ. Two settings on opposite sides must agree, nothing validates the pair |
| **4** | live | 2 min | Duplicates, and the honest at-least-once claim |
| **5** | narrate | — | Replay. Broken on this service — never run it |
| **Act 3** | **narrate** | 6 min | Agents over the mesh, and the transport argument |

**Act 3 is narrated because the agentic path does not work.** The orchestrator fails on its first tool call with `Function call is missing a thought_signature` — a Gemini thinking-model contract that LiteLLM's OpenAI-compatibility path does not round-trip. Verified 2026-09-03 on two different models, and the same error appears 8 times in earlier logs: **it has never completed a task.** What *is* real and verified is in Appendix A, along with what running it live would take.

Narration is a deliberate choice, not a shortfall. One tier demonstrated live and one tier talked through beats two tiers half-run, and it lets Act 3 say precisely what was built and precisely what broke.

**If Act 2 runs long, cut Beat 4.** Never cut Beat 3 — it carries the strongest single point in the demo.

### Beat 1 — normal flow, two delivery modes, and exception granularity

```powershell
python src\publisher.py          # 10 orders at ~3/s are the defaults
```

**Show:** lifecycle events tagged `[direct]` streaming past in window 1. The first seven orders are clean and the desk windows stay quiet — most orders do not break. Then the last three fire, one of each failure mode, and all three desks light up.

**Say:** two delivery modes on one broker, chosen per stream. Dashboard chatter goes direct because a missed "picked" event refreshes away; exceptions go guaranteed because losing one means an order silently stops. The alternatives force one guarantee across everything — pay spool cost for dashboard traffic, or accept loss on business events.

**The set is scripted, not random.** Ten orders, and the last three always break the same way:

| Order | What happens | Lands on |
|---|---|---|
| 1—7 | clean lifecycle | nothing |
| 8 | credit hold — **order-scoped** | window 2, desk-credit |
| 9 | stock shortfall — **line-scoped** | window 3, desk-inventory |
| 10 | unprocessable — five failed attempts | **#DEAD_MSG_QUEUE** |

`--random` restores the ambient ~4% rates for a load run.

**Say the rate out loud anyway** — "about four percent of orders break; this set is arranged so you see each way it breaks." The scripting is a demo convenience, not a claim about the business, and saying so costs nothing.

**Then stop on the two exception lines still on screen.** They are the granularity point, and they arrived inside a realistic stream rather than being summoned one at a time:

| Window | What landed |
|---|---|
| 2 — desk-credit | `mc/order/exception/v1/credit-hold/SO-…` — ends at the **order** |
| 3 — desk-inventory | `mc/order/exception/v1/stock-shortfall/SO-…/4` — ends at the order **and a line** |

**Say:** a credit hold is a fact about an order — one decision, taken once. A stock shortfall is a fact about a *line*: a five-line order can have one line short and four perfectly fine, and each short line is resolved separately — substitute, split-ship, or backorder *that item*.

So the taxonomy carries the distinction **in the address**, rather than burying it in a payload where nothing can route on it or apply access control to it. **The two live exception types were chosen precisely because they differ in scope** — so the granularity question gets answered rather than assumed.

**Same root, different depth.** Both topics start `mc/order/exception/v1/`; the line-scoped one simply carries one more level. That is what lets the audit desk subscribe **once**, with `mc/order/exception/v1/>`, and still receive both — point at window 4, where the two scopes arrive side by side under a single subscription.

Scope-selective routing survives intact, and this is the non-obvious half:

| Subscription | Receives |
|---|---|
| `mc/order/exception/v1/*/*` | order-scoped **only** — `*` is single-level, so the length is exact |
| `mc/order/exception/v1/*/*/>` | line-scoped **only** — `>` matches one or more levels, never zero |
| `mc/order/exception/v1/>` | both |

Verified on the broker, not assumed. **If asked why not a separate `orderLine` object type:** it was that way until 2026-09-02 and it cost the audit consumer a second subscription while winning nothing on routing — see constraint 11 in the design doc. The counter-argument is real and worth conceding: an order line is a distinct object with a distinct schema, and if line-level *lifecycle* events were ever added, the separate root would earn its place.

*If challenged to prove a topic:* window 5 → **Queues** → `Q/credit-desk/exceptions` → the **Messages Queued** tab → click a message; its destination topic is on the message detail.

**Note the dead message queue is NOT empty after this beat** — order 10 put a message there. Beat 3 explains what already happened rather than producing it fresh.

### Beat 2 — a consumer goes down

`Ctrl-C` window 3 (desk-inventory). Leave it dead. Then:

```powershell
python src\publisher.py --shortfall
```
```powershell
python src\publisher.py --shortfall
```
```powershell
python src\publisher.py --shortfall
```

**Show:** window 5 → **Queues** → `Q/inventory-planning/exceptions`, with a non-zero **Messages Queued** count. Windows 2 and 4 are entirely unaffected.

**Do not then point at that number to show it draining** → it rises correctly and never falls on this service (see *Queue depth does not go down*). Narrate the drain from window 3 instead, which prints each message as the restarted consumer works through the backlog.

Restart window 3:

```powershell
python src\consumer.py desk-inventory
```

**Show:** it drains the backlog immediately. Nothing lost.

**Say:** the subscription lives on the queue, not on the consumer. That is why a warehouse system offline for four hours catches up instead of dropping four hours — and it is why per-consumer queues matter. On one shared queue, that outage would have applied back-pressure to every other desk.

### Beat 3 — the poison message and the dead message queue

**This already happened in Beat 1.** Order 10 of the scripted set was the unprocessable one, so window 2 has the five `FAILED` lines on screen and the dead message queue already holds a message. Scroll back to it rather than publishing again — it is the same demonstration and it costs no time.

Publish another only if you want it to happen live while they watch:

```powershell
python src\publisher.py --poison
```

**Show:** window 2 failing the same message repeatedly — three attempts, because `max-redelivery` is 2 — then window 5 → **Queues** → `#DEAD_MSG_QUEUE` holding it. The DMQ is listed among the ordinary queues, not on a separate screen.

**Say:** the consumer acknowledges *after* the work, not on receipt. So a failure redelivers rather than disappears, and after the redelivery limit the message steps aside into the dead message queue instead of blocking the other 109 exceptions that day.

**Then the point that is actually worth making:** this only works because two settings on opposite sides of the system agree. The queue sets `max-redelivery` and names a DMQ. The **publisher** sets the DMQ-eligible flag on the message. Set one without the other and exhausted messages are silently discarded while every piece of configuration still looks correct — and nothing validates the pair.

*Two settings that must agree, on opposite sides of the system, with nothing validating the pair — and the only other genuine instance we hit was two configuration sources for the same value, where the database silently beat the environment file. That class is rare and it is worth naming as a design hazard rather than a gotcha.*

*The broader pattern in this build is different and more common: **the symptom names the wrong subsystem.** A dropped connection property reported as a missing credential. A byte-order mark in an environment file reported as a YAML validation error. A trust store that completes the handshake and kills long-lived receivers later. A duplicate client name that presented as a flood of SSL errors. Two management endpoints that return 200 and do nothing. **DMQ eligibility is the one case that is both**, which is why it is the point worth spending time on.*

### Beat 4 — duplicates, and the honest claim

```powershell
python src\publisher.py --duplicate
```

**Show:** the desk processes the first copy and makes a decision, then:

```
[desk-credit] duplicate 386bf422 — already handled, acking
```

**Say:** guaranteed delivery is at-least-once. There is no exactly-once — here or anywhere else in this class of system. Consumers dedupe on `(orderId, exceptionId)`. That is the honest answer, and it is the same one Solace's own field team gives.

*"We get exactly-once" is a claim a room of SEs takes apart in one question.*

**Why a flag rather than restarting a consumer.** The obvious way to trigger this beat is to restart a consumer so the broker redelivers an unacked message. That does not work: the dedupe set lives in the consumer **process**, so a restart begins with an empty set and the redelivered copy looks new. The beat cannot fire that way, and it was written that way until 2026-09-02.

`--duplicate` publishes the same `exceptionId` twice, which exercises exactly the claim being made — a consumer may see the same exception more than once, and this one notices. Whether the second copy came from a redelivery or a duplicate publish is immaterial to the consumer, which is the whole point of idempotency.

**Narrate it honestly:** *"here is the same exception arriving twice, however that happens, and here is the consumer recognising it."* Claiming the broker redelivered it when you published it twice would not survive a follow-up question.

### Beat 5 — replay *(DO NOT RUN — narrate only)*

**Tested 2026-09-02 against the cloud service. Replay does not work there, and attempting it breaks the queue.** Do not run this beat live under any circumstances.

What happens: the config plane accepts everything. Creating a replay log returns 200, the log fills (message bytes accumulate in `msgSpoolUsage`), and `startReplay` returns 200. The data plane then does nothing. `replayState` sits at `active` indefinitely, `replayedTxMsgCount` stays at 0, and — this is the part that matters — **the queue stops delivering to bound consumers entirely**. A consumer binds successfully and receives nothing.

Reproduced twice, on `Q/credit-desk/exceptions` and on `Q/audit/all-exceptions`, with a consumer bound before the replay as well as after. Disabling and re-enabling egress does not clear it. **The only recovery is to delete the queue and re-run `provision.py`**, which is fast but would be a visible scramble mid-demo.

The replay log has been deleted from the service so that nothing can trip this accidentally on the day.

**What to say if replay comes up:** describe it rather than show it — a new consumer arrives and needs the last 24 hours, so it replays from the broker's own log instead of being backfilled from a database export. Then the design point, which is genuinely useful: replay works only on **non-partitioned** queues, which is why these are not partitioned. At real throughput you would partition the service queues for consumer scale and deliberately keep the audit queue non-partitioned, so replay survives exactly where it is actually needed.

**If asked whether you tried it:** yes, and it did not work on a trial-tier cloud service — the control plane accepted it and the data plane never delivered. That is a better answer than silence, and it is the same pattern as the other five findings in this build: configuration accepted in one place, failing in another, with the error pointing somewhere other than the cause.

---

---

## Act 3 — narrated

Six minutes, no terminal. Nothing here is run. The full design is in `demo-agentmesh-design-2026-09-02.md`; this is the spoken version.

**Open on R5, not on "and now, agents."** *Some of these need human judgment; escalation must be deliberate and arrive with the work already gathered.* That makes the agentic tier the answer to something the customer asked for. Opening on the technology invites the fair objection that Agent Mesh is the nascent half of the portfolio and the broker is the substance. Leading with the requirement avoids it.

**What it is.** Three agents and a human gateway — an orchestrator that decomposes an exception into sub-tasks, an inventory agent, a credit agent. They address each other over the *same broker* as Act 2, on a second topic architecture (`{namespace}/a2a/v1/...`) alongside the business one. Agents find each other by discovery, a repeating broadcast of agent cards, so a fourth agent is a deployment rather than a config change to the other three.

**Three claims, in order of how much they matter:**

| Claim | Why it holds |
|---|---|
| Deterministic cases resolve themselves | Act 2's rules already handle most exceptions. Agents are for the residue |
| Judgment cases escalate **with context assembled** | A human receives a decision to make, not a research task. That is R5 |
| The transport choice is load-bearing | Remove the broker and something specific breaks |

**The boundary, volunteered before they ask.** No agent writes to SAP — they publish a `resolution` event on the Act 2 topic architecture, and whatever applies it is unchanged and unaware agents exist. No agent invents inventory or credit facts either; both read live state through tools, so the model chooses among options and does not supply data. *"The LLM decides where the boundary is" is a losing answer; a named threshold is defensible.*

**The transport argument.** Three failure shapes, and what each transport does:

| | Broker | Synchronous HTTP |
|---|---|---|
| Fan-out — one order, three short lines | Sub-tasks dispatched; replies arrive on **one** subscription, routed by who asked | Three sequential calls, or hand-rolled concurrency the caller owns |
| Back-pressure — a burst | Queue absorbs it; agents drain at their own rate | Caller blocks or sheds. There is no third option |
| **An agent dies mid-workflow** | Queue spools; the agent restarts and finishes | **Request lost. No queue, no retry target** |

**What you can say you measured, because you did.** The synchronous arm was built in good faith — pooling, keep-alive, timeouts, retry with backoff — and it loses 3 of 3 requests when the callee is down. Both arms import the same tools module, so the business logic is byte-identical and the only variable is the transport.

**And say the durability finding.** SAM binds agents to `{namespace}/q/a2a/{agent_name}` with `temporary_queue` defaulting to **true**, and a temporary queue dies with its client. Out of the box, a dead agent takes its queue and its in-flight messages with it — the durability claim fails. One line, `USE_TEMPORARY_QUEUES=false`, changes it. *The capability is real; the default is not it, and we found that by building.*

**If asked "did you get it running?"** Answer plainly: the mesh runs, three agents register on durable queues, discovery works, and the control arm is real. The orchestrator's tool-calling path fails against this LLM provider on a thinking-model contract the client library does not round-trip — a library incompatibility, found and diagnosed, not designed around. That is a better answer than a demo that dies live, and it is the honest one.

---

### Running a beat twice

Safe, and worth knowing what you will see.

**Order ids differ every run — and every run is still replayable.** Each run mints a random seed and prints it as its first line:

```
seed 415390  — rerun with --seed to reproduce
  SO-4479725  Castro, Padilla and Odonnell
```

So the data is fresh every time, and if a rehearsal run produced a particularly clean example you can reproduce it exactly with `--seed 415390`. There is no magic number — 42 was only a placeholder in an earlier draft.

An earlier version seeded a constant at import and emitted `SO-4479068` every single time. Fine for development, wrong in a live demo: a repeated order number makes an audience wonder what else is canned.

**It is not treated as a duplicate.** Consumers dedupe on `(orderId, exceptionId)`, and `exceptionId` is a uuid4 drawn from `os.urandom` — unaffected by the seed. So even a seeded re-run of the same order is a genuinely new exception and is processed again. Beat 4's duplicate line comes from broker *redelivery*, not from re-running the publisher.

**Backlogs accumulate.** Nothing clears between runs. Before the live session run `.\stop-demo.ps1 -Reset` so no window opens holding a rehearsal backlog.

### Script faults found on 2026-09-02, and what they looked like

All four were found by running the scripts rather than reading them. Each reported something other than its cause, which is the recurring theme of this build.

| Symptom | Cause |
|---|---|
| `demo-up` dotted for three minutes then blamed the management credentials | `Read-Profile` was defined and never called, so the readiness check requested a relative URI with no credentials. The credentials were always fine |
| `demo-down` said "0 windows closed" while all four stayed open | `MainWindowTitle` is empty under Windows Terminal — the panes belong to `WindowsTerminal.exe`, so no `powershell.exe` owns a window. Now matched on the command line, and title-agnostic so a rename cannot break it again |
| `demo-down` said "0 processes ended" while six consumers kept consuming | The pattern required `<repo>\src\consumer.py`, which never appears: the repo path is on the interpreter and the script argument is relative. **This is why a queue could look empty after a teardown that stopped nothing** |
| `demo-down` exited 1 on a completely successful run | Best-effort `docker` probes set `$LASTEXITCODE` when Docker is not running, and it leaked out as the script's result |

`start-demo.ps1` had the same class of fault on its standalone path: its preflight hardcoded `localhost:8080` and `admin:admin`, so running it directly against cloud reported the broker down and advised `docker start solace`. It reads the profile now. `demo-up.ps1` passes `-SkipChecks`, which is why it never showed there.

**The lesson worth carrying into the room:** a parse check is not a test. Every one of these scripts parsed cleanly for days and none of them worked.

### The client-name collision — the worst bug in the build

**Symptom:** consumer windows flood with `SOLCLIENT_SUBCODE_COMMUNICATION_ERROR` and `SSL 'SSL-client' cannot read`. Messages are processed correctly, then arrive again as `duplicate`, over and over, until they reach max-redelivery and land in the **dead message queue** — successfully handled messages in the DMQ, the exact opposite of what Beat 3 claims.

**It is not TLS.** The trust store was correct throughout. Every SSL warning was a symptom.

**Cause:** every consumer connected as exactly `meridian-<role>`. Client names must be unique per VPN, and that name collided in two ordinary situations:

- a **force-killed** consumer leaves a ghost connection holding the name until broker keepalive expires, so the next consumer collides with a process that no longer exists
- running the window set **twice** starts a second process claiming the same name

Two connections with one name displace each other in a loop, and the churn loses acknowledgements in flight. Unacked messages are redelivered — correctly, by design — which is why the consumers' own dedupe kept reporting duplicates.

**Proof:** identical receiver code, same queue, only the name differing: **15 redeliveries** under the shared name, **0** under a unique one.

**Fix:** client names now carry a short random suffix — `meridian-desk-credit-0fcb`. The role stays at the front so the console still answers "which of these is the credit desk?". A ghost can no longer collide with anything.

**If you ever see that SSL flood again:** it is a name collision or a duplicate window set, not a certificate problem. Check the connected clients before touching TLS.

### Queue depth does not go down

`spooledMsgCount` **rises on publish and never falls on acknowledgement** on this service. A queue proven empty — two consecutive consumers received nothing from it — reported 3 for over two minutes, with `txUnackedMsgCount` at 0.

Consequences for the beats:

| Use | Safe? |
|---|---|
| "messages accumulated while the consumer was down" (**Beat 2**, first half) | **Yes** — the count rises correctly |
| "the dead message queue caught it" (**Beat 3**) | **Yes** — same reason |
| "and now the queue drained" | **No** — the number will not move |

**So narrate the drain from the consumer window, not the console.** The consumer prints each message as it handles it, which is better evidence anyway: it shows the work being done, not just a number changing. `scripts/queue_depths.py` prints the same caveat above its table.

### Two endpoints that return 200 and do nothing

Both on the trial cloud service, both found by reading the state back after the call:

| Call | What happens |
|---|---|
| `startReplay` | Returns 200, transmits nothing, and leaves the queue undeliverable until it is deleted and re-provisioned. See *Beat 5* — narrate only |
| `deleteMsgs` | Returns 200 and removes no messages. `-Fresh` empties the dead message queue by deleting and recreating it instead |

On a managed service a 200 from the control plane is a receipt for the request, not a statement about the data plane. Read the state back.

### If time runs short

**Beat 5 is already cut** — it is narrate-only and must not be run (see above). After that cut **Beat 4**, which is talkable without running. Beats 1–3 are the spine and Beat 3 carries the strongest single point in Act 2.

---

## 4. Reset between runs

```powershell
python scripts\provision.py --teardown
python scripts\provision.py
```

Clears queue contents by removing and recreating the queues. Do this before the live session so no window opens with a stale backlog.

To reset everything including the broker:

```powershell
docker rm -f solace
```

then re-run the `docker run` above and re-provision. Takes about two minutes.

---

## 5. If something breaks live

| Symptom | Cause | Move |
|---|---|---|
| Consumers connect, nothing arrives | Provisioning not run, or queues torn down | `python scripts\provision.py` |
| `argument role: invalid choice` | Typed an exception type where a desk goes | Roles are `desk-credit`, `desk-inventory`, `desk-audit` |
| Broker refuses connections | Container still starting | Check SEMP returns 200; wait 60–90s after `docker run` |
| Wall of red in `docker logs` | Normal startup noise | Ignore. Check `RestartCount` is 0 |
| `curl: Parameter cannot be processed` | PowerShell alias | Use `curl.exe` |
| Publisher runs, one desk silent | That desk's subscription missing | Re-run provisioning; it reconciles |

**If the broker is unrecoverable mid-demo**, keep talking through the architecture from the design doc. The messaging design carries the reasoning and the rejected alternatives, and narrating a tier rather than running it live is a legitimate fallback, not a concession.

---

# Appendix A — Running Act 3 live

What it would take, and what is already true. Written 2026-09-03 so the gap is visible rather than guessed at.

## What already works, verified

| | Evidence |
|---|---|
| Agent Mesh runs natively on Windows | Python 3.13.12, no WSL. Undocumented by Solace, who list WSL for Windows |
| Three agents register and are discoverable | `GET /api/v1/agentCards` returns `CreditAgent` (`resolve_credit_hold`), `InventoryAgent` (`resolve_shortfall`), `meridian_orchestrator` |
| Agent queues are **durable** | `meridian/q/a2a/{CreditAgent,InventoryAgent,meridian_orchestrator}`, `durable=True`, because `USE_TEMPORARY_QUEUES=false` |
| A second topic architecture shares the broker | `meridian/a2a/v1/...` alongside `mc/order/...` on one VPN |
| The task API accepts work | `POST /api/v1/message:send` returns a task id and the orchestrator picks it up |
| The synchronous control arm is real | `http_arm.py` loses 3/3 requests when the callee is down, with pooling, keep-alive and retries in place |
| The web UI gateway serves | `http://localhost:8000` |

## What does not work

**The orchestrator fails on its first tool call.**

```
litellm.BadRequestError: OpenAIException - Error code: 400
Function call is missing a thought_signature in functionCall parts.
... function call `default_api:list_artifacts`, position 3
```

Gemini's thinking models require a `thought_signature` to be echoed back on turns following a function call. LiteLLM's OpenAI-compatibility path to `generativelanguage.googleapis.com/v1beta/openai` does not round-trip it. Reproduced on `gemini-3.6-flash` and `gemini-flash-latest`; the same error appears 8 times in earlier logs, so **the orchestrator has never completed a task**.

It is a client-library/provider incompatibility, not a configuration error. Nothing in the agent YAML, the topic architecture or the broker is implicated.

## Progress 2026-09-03 evening — three blockers found and fixed, one external remains

**It is not the native Windows install.** Every failure is an HTTP response from Google's API; everything OS-adjacent works — broker connection, TLS, durable queues, agent discovery, the web UI, and tool execution. Confirmed by calling the model directly from this machine with no SAM, no litellm and no agent plumbing in the path.

| # | Blocker | Fix |
|---|---|---|
| 1 | `thought_signature` missing on function calls | The **OpenAI-compat** path does not round-trip it; litellm only handles it in the `vertex_ai/gemini` path. Switch the model to the native `gemini/` provider and drop `api_base` |
| 2 | `TotalCachedContentStorageTokensPerModelFreeTier limit=0` | SAM defaults `cache_strategy` to `5m`, which asks Gemini to store cached content. The free tier forbids it outright. Set `cache_strategy: none` |
| 3 | `thought_signature` again, orchestrator only | Gemini 3 *thinking* models still trip it on the orchestrator's longer delegation sequence, even natively. A **lite** model avoids it |
| 4 | `ServiceUnavailable` / `MidStreamFallbackError` | **External.** Google capacity. Not fixable here |

**Where it got to.** With `gemini/gemini-flash-lite-latest`, `cache_strategy: none`, native provider: the orchestrator delegates to both peers and **all five agent tools execute** — `check_stock`, `check_substitutes`, `resolve_shortfall`, `check_credit_standing`, `resolve_credit_hold`. The task still finalises FAILED on a 503 during the streamed response.

That is a long way from where it started — it had never completed a single tool call — but it is not a rehearsed demo, and the last blocker is somebody else's capacity.

**Current configuration** (in `platform.db`, which overrides `.env`):

```
model_name    gemini/gemini-flash-lite-latest
api_base      NULL
model_params  {"cache_strategy": "none"}
```

Backups: `platform.db.bak-before-gemini-provider`, `.env.bak-before-model-swap`.

**Next step if resumed:** retry when Google capacity recovers, and consider a paid key or a different provider — the free tier is implicated in two of the four blockers.

## If it were working, the beats

Roughly 12 minutes for the three that matter. Fan-out and back-pressure are narration either way — see the honesty note below.

**Beat 6 — the mesh is up (2 min).** Window 5 → Queues → the three `meridian/q/a2a/` queues, `durable=True`. Say the discovery model, and volunteer the temporary-queue default.

**Beat 7 — resolve, then escalate (5 min).** Two requests through the web UI: one shortfall the agents resolve, one they must escalate — a customer who refuses substitutions, or a value over the auto-resolve ceiling. Show the orchestrator decomposing, delegating, assembling one answer; then an escalation arriving with the alternatives already priced. This is the R5 beat and the only one that makes Act 3 about the customer's problem.

**Beat 9 — an agent dies mid-workflow (5 min).** `python src\comparison.py kill`, then actually stop InventoryAgent and dispatch again. HTTP loses every request; the broker queue spools and the agent finishes on reconnect.

## An honesty note about the comparison harness

`src/comparison.py` is **not** a two-sided live comparison, and must not be presented as one.

| Scenario | HTTP arm | Broker arm |
|---|---|---|
| `fanout` | real calls, real timing | 43 print statements, **no broker traffic** |
| `backpressure` | real sequential loop | 13 print statements, **no broker traffic** |
| `kill` | real — genuinely loses 3/3 | one queue-depth read, then prose |

Running `fanout` or `backpressure` in front of Solace SEs and calling it a comparison invites *"is the broker side actually running?"*, and the answer is no. Narrate those two. `kill` is the one worth running, and only alongside a real agent stop.

Making them genuinely two-sided means dispatching real A2A traffic through the mesh and measuring it — which depends on the orchestrator working, so it is downstream of the fix above.

## Cost estimate

Fixing the LLM path is plausibly under an hour if option 1 works. Getting from there to a rehearsed, timed Act 3 — delegation behaving, escalation content worth showing, a real two-sided comparison — is a day's work, not an evening's.
