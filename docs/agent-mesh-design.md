# Agent Mesh Design (Act 3)

_The Act 3 design deliverable, written to the same standard as [messaging-design.md](messaging-design.md): decisions with their rejected alternatives, and mechanics verified against the installed package rather than recalled._

**Act 3 is the differentiator, and it is the half that can fail as an argument even when it works as software.** Act 2 proves competence on Solace's revenue core. Act 3 answers the question their SEs actually face in the field — *why Solace and not a lightweight agent framework* — and that question is not answered by agents running. It is answered by a contrast that holds up under probing.

_Companion docs: [messaging-design.md](messaging-design.md) (Act 2), [problem-statement.md](problem-statement.md) (the brief), [`RUNBOOK.md`](../RUNBOOK.md) (how to run it)._

_SAM mechanics below were read from `solace_agent_mesh` 1.28.8 as installed, principally `common/a2a/protocol.py` and `agent/sac/app.py`. Findings that changed the design are in **Constraints discovered**._

---

## 0. The requirements this document answers

Act 3 is written against the customer brief. Two of the six requirements drive it, and they are restated here so this document stands on its own rather than assuming the deck:

| | The customer's requirement |
|---|---|
| **R2** | Adding a new consumer must not mean changing the order system. We have a decade of point to point interfaces and we are not adding an eleventh |
| **R5** | Some of these need human judgment. Escalation has to be deliberate, and arrive with the work already gathered |

The full set of six is carried in the slides. R1, R3, R4 and R6 are answered by Act 2 and are not revisited here.

---

## 1. What Act 3 has to prove

It answers **R5**: *some of these need human judgment; escalation must be deliberate, and arrive with the work already gathered.*

That framing matters more than it looks. Opening on R5 makes the agentic tier the answer to something the customer asked for. Opening on "and now, agents" makes it technology being pushed, which invites the fair objection that Agent Mesh is the nascent half of the portfolio while the broker is the substance.

Three things must be true at the end of Act 3:

| | |
|---|---|
| **The deterministic cases resolve themselves** | Act 2's rules already handle most exceptions. Agents are for the residue |
| **The judgment cases escalate with context assembled** | A human receives a decision to make, not a research task |
| **The transport choice is visibly load-bearing** | Remove the broker and something specific breaks. If nothing breaks, there is no argument |

---

## 2. The agent set

Four participants. Three agents plus the human gateway.

| Participant | Decides | Escalates when |
|---|---|---|
| **OrchestratorAgent** | Which agent handles this exception, in what order, and when the work is done | The plan fails or no agent claims the capability |
| **InventoryAgent** | Transfer from another site, substitute, split-ship, or backorder | The customer has a stated no-substitution preference, or value exceeds the auto-resolve threshold |
| **CreditAgent** | Release within tolerance, release against payment in flight, or hold | Over tolerance, or the account has prior disputes |
| **Human gateway** | Nothing. It presents | — |

**Why these three and not more.** Each maps to a decision a human actually makes today, and each is the *last mile* of an Act 2 exception stream that already exists. Adding a fourth agent would add surface without adding argument. The 8/14 cut order puts agent count first on the chopping block, so three is already the reduced set.

**Why the orchestrator is not "just a router."** It is the participant that decides a shortfall on three lines is three sub-tasks, dispatches them, and assembles one answer. That fan-out is what the transport comparison measures — a router with one downstream call would demonstrate nothing.

### What each agent is *not* allowed to do

Stated because any serious audience will ask where the boundary is, and "the LLM decides" is a losing answer:

- **No agent writes to SAP.** They publish a `resolution` event on the Act 2 topic architecture. Whatever applies it is unchanged and unaware agents exist.
- **No agent invents inventory or credit facts.** Both read live state through tools; the model chooses among options, it does not supply data.
- **Escalation is a first-class outcome, not a failure path.** An escalation that arrives with the alternatives already priced is the product, and R5 says so.

---

## 3. A2A topic architecture — SAM's, read from the source

SAM builds its own topics. This is not something we design; it is something we must be able to *explain*, because it is on the same broker as our design and both are visible at once.

```
{namespace}/a2a/v1/discovery/agentcards
{namespace}/a2a/v1/agent/request/{agent_name}
{namespace}/a2a/v1/agent/response/{delegating_agent_name}/{sub_task_id}
{namespace}/a2a/v1/agent/status/{delegating_agent_name}/{sub_task_id}
{namespace}/a2a/v1/gateway/response/{gateway_id}/{task_id}
{namespace}/a2a/v1/gateway/status/{gateway_id}/{task_id}
```

Our namespace is `meridian/`, so these render as `meridian/a2a/v1/…`.

### Two properties of that scheme worth naming out loud

**Responses are routed by who ASKED, not by who ANSWERED.** `agent/response/{delegating_agent_name}/{sub_task_id}` — the delegator subscribes `agent/response/{self}/>` and receives every reply to everything it dispatched, on one subscription. There is no queue per agent-pair and no correlation table.

That is the single cleanest illustration of why this is on a broker. Point-to-point HTTP has no equivalent: each caller holds a connection per callee and correlates by response object.

**Discovery is a repeating broadcast, not a registry — and the repetition is the whole mechanism.** Agents publish their agent card to `discovery/agentcards`; everyone subscribes `discovery/>`.

The obvious question is how a *late-joining* agent learns about agents that announced themselves before it had a queue. The answer is that cards are not published once. They are published **on a timer**, and our generated orchestrator config sets it explicitly:

```yaml
agent_card_publishing:
  interval_seconds: 10
agent_discovery:
  enabled: true
```

So every participant re-announces every ten seconds. A late joiner subscribes and has a complete picture of the mesh within one interval, with no replay, no last-value queue, and no bootstrap query. Nothing had to be retained on its behalf, because the state is continuously reasserted.

The reverse direction works the same way: the registry ages entries out against a health-check TTL (`agent_registry.check_ttl_expired`), so an agent that stops publishing disappears from everyone's view without anyone being told. Liveness and membership are the same signal.

**This is soft state, and it is worth naming as a deliberate trade.**

| Gained | Paid |
|---|---|
| No registry that can be stale, partitioned, or become a dependency | Continuous background traffic: one card per agent per interval, forever |
| Membership and liveness are one mechanism, not two | Up to `interval_seconds` before a new agent is addressable |
| A dead agent leaves the mesh without cleanup | The interval is a tuning decision nobody thinks about until it matters |

At three agents and ten seconds this is free. At three hundred agents it is a real traffic decision, and the honest answer to a scaling question is that the interval is the knob and the cost is linear in agent count.

**Relevant to the demo specifically:** after the kill-an-agent beat, the killed agent ages out of the orchestrator's registry rather than lingering as a phantom. That is worth pointing at, because it is the difference between a mesh that knows what is alive and a config file that claims to.

**This is R2 again, one layer up.** R2 was *adding a new consumer must not mean changing the order system* — the requirement that came out of a decade of accumulated point to point interfaces. At the agent layer the identical property appears: adding an agent does not mean changing the orchestrator, or restarting it, or editing a registry.

Saying that connects Act 3 back to the brief rather than leaving it as a separate act. It is also the strongest available answer to *"why is this on a broker at all"*: the same architectural property the customer asked for at the integration layer is what makes the agent layer extensible, and they get it from one piece of infrastructure rather than two.

### It corroborates our own rules

| Our rule (Act 2 §1) | SAM's A2A scheme |
|---|---|
| Version above every wildcard boundary | `v1` at level 3, above everything wildcarded |
| Coarse to fine, left to right | domain → a2a → version → object → verb → identifiers |
| Highest cardinality last | `sub_task_id` / `task_id` in final position |
| Route on it or leave it out | Every level is subscribed on by something |

Useful under questioning: the topic architecture in Act 2 was not a private invention. Solace's own product, designed by the people in the room, follows the same shape.

---

## 4. Two topic architectures, one broker

`mc/...` carries order events. `meridian/a2a/v1/...` carries agent coordination. Same broker, no overlap.

**That is deliberate and it is the point.** The alternative — agents on their own transport — would make Act 3 a bolt-on rather than an extension, and would forfeit the argument entirely.

**The separation is at the Domain level**, which is exactly the access-control justification given for keeping `mc` in Act 2. One rule isolates order events; another isolates agent traffic. If agents should never publish order lifecycle events, that is now one ACL rule rather than a code review.

**Rejected: putting agent traffic under `mc/`.** It would blur the two contracts and imply agents are part of the order domain. They are consumers of it.

**Rejected: a second broker or VPN for agents.** Correct if the requirement were tenancy or hard isolation. Here it would cost the demo its central claim — that this runs on infrastructure the enterprise already has.

---

## 5. Durability — the finding that would have broken the demo live

**Act 3's central claim is: kill an agent mid-workflow, and the broker version replays and finishes while the HTTP version loses the request.**

Read from `agent/sac/app.py`, that claim is **false by default**:

```
generated_queue_name = f"{namespace.strip('/')}/q/a2a/{agent_name}"
broker_config["queue_name"] = generated_queue_name
broker_config["temporary_queue"] = app_info.get("broker", {}).get("temporary_queue", True)
```

Each agent binds to `meridian/q/a2a/{agent_name}` — but **`temporary_queue` defaults to `True`**, and `shared_config.yaml` propagates `${USE_TEMPORARY_QUEUES, true}`. A temporary queue is destroyed when its client disconnects. Kill an agent and its queue goes with it, taking anything in flight and anything published while it is down.

**Run the demo as conceived and the message would have been lost.** The argument would have collapsed live, on the one beat it exists to make.

### The fix, and why it is better than a fix

Set `temporary_queue: false` on the agent's broker config so the queue is durable. Then the queue survives the agent, the broker spools while it is gone, and the message is delivered on reconnect. The claim becomes true.

**This is the third instance of one pattern, and now the strongest.** Act 2 found DMQ eligibility split between publisher and queue, and `respect-ttl` refused on the DMQ. Both were silent misconfigurations that looked correct. This is the same shape at the agent layer: **a durability property that the transport can provide, does not provide by default, and does not announce.**

That is a much better thing to say than "Solace gives you durability." It is:

> *"The transport can make agent-to-agent calls durable. It does not do it by default — agent queues are temporary, so a dead agent takes its queue with it. One config line changes that, and here is the difference on screen. The capability is real; the default is not it."*

Volunteering a product's default being wrong for your use case, and showing you found it by building, is worth more than any claim that everything works.

**Rejected: leaving the default and softening the claim to "decoupling and scale."** Weaker argument, and it walks away from the only failure mode that visibly separates the two transports.

**Rejected: durable queues for every participant.** The gateway and discovery paths genuinely want temporary queues — a disconnected browser session should not accumulate a spool. Durability goes where the work is, which is the agent request queues.

---

## 6. The control arm

The comparison is only worth showing if the HTTP version is built in good faith. A strawman is worse than no comparison, because an SE audience will recognise one.

**Same agents, same prompts, same tools, same model.** The only variable is transport. The HTTP arm is a small FastAPI service per agent, with the orchestrator calling them directly and awaiting responses.

**What the HTTP arm is allowed to have**, so nobody can say it was hobbled:

- Connection pooling and keep-alive
- Sensible timeouts
- Retries with backoff on connection errors

**What it cannot have, because HTTP structurally cannot:**

- Delivery to a callee that is not currently listening
- Recovery of a request whose caller died after sending
- Discovery of a new agent without configuration

**The honest framing:** HTTP is not bad. It is *synchronous and point-to-point*, and those two properties have consequences that only appear under failure and fan-out. Most of the time it works fine, which is precisely why teams pick it and then hit the wall on day two.

**Rejected: comparing against a named framework.** Building a deliberately poor CrewAI implementation and beating it would be dishonest and transparent. The comparison is against the *pattern* those frameworks use, which is the fair target.

---

## 7. What the comparison measures

Three scenarios, in ascending order of how much they matter.

| Scenario | Broker | HTTP | Proves |
|---|---|---|---|
| **Parallel fan-out** — one order, three short lines | Three sub-tasks dispatched, replies arrive on one subscription | Three sequential calls, or hand-rolled concurrency | Decoupling |
| **Back-pressure** — burst of exceptions | Queue absorbs; agents drain at their own rate | Caller blocks or sheds load | Elasticity |
| **Agent dies mid-workflow** | Queue spools; agent restarts and finishes the task | Request lost, no retry target | **Durability. This is the beat** |

**Keep the model out of the load path.** In the back-pressure scenario the agents use a fixed-latency stub, not a real model call. Otherwise the harness measures Gemini's 15 requests/minute free-tier limit rather than the transport, and the whole comparison is invalid. Use the real model for the narrative walkthrough where correctness is what is being shown.

That is also the more honest demo, and it is a good answer in its own right: *isolate the variable you are arguing about.*

---

## 8. Rejected alternatives

| Rejected | Why |
|---|---|
| **A queue per agent pair** | O(n²) queues, and every new agent means provisioning against every existing one. SAM's response-topic-per-delegator gets the same routing with one subscription per agent |
| **Agents subscribing directly to `mc/order/exception/...`** | Skips the orchestrator, so there is no fan-out, no plan, and nothing for the comparison to measure. It also couples agents to the order contract instead of to the orchestrator |
| **Direct (non-guaranteed) messaging for A2A** | Cheaper and faster, and it forfeits the durability beat. Wrong trade for this demo and for the real use case |
| **One "do everything" agent** | No delegation means no fan-out and no discovery story. Multi-agent is not decoration here; it is the thing being argued about |
| **Letting agents write to SAP** | Makes the blast radius of a bad model decision unbounded. Publishing a resolution event keeps the existing system of record in charge |
| **Skipping the human gateway** | R5 names deliberate escalation as a requirement. Without it Act 3 answers a question nobody asked |

---

## 9. Constraints discovered

**1. Agent queues are temporary by default.** Covered in §5. It would have broken the demo's central claim live. Fixed with `temporary_queue: false` on agent broker config, and it is now the strongest talking point in Act 3.

**2. `sam init --skip` accepts `--llm-*` flags and discards them.** The `shared_config.yaml` template carries a models block with `__PLANNING_MODEL_CONFIG__` / `__GENERAL_MODEL_CONFIG__` placeholders; when substitution does not happen, the entire block is dropped — 98 template lines become 35 generated. The result is three config files referencing model roles that nothing defines, and the failure appears at run time rather than at init. Worked around in `setup-sam.ps1`, which writes the models block and the `LLM_SERVICE_*` variables itself.

**3. `sam init` overwrites `.env` with no backup.** Cost a Gemini API key mid-setup. The project now keeps credentials in `.env.key`, which init does not touch, and the setup script restores them afterwards.

**4. `gemini-2.5-flash` is closed to new API keys.** Returns 404 with a pointer to `gemini-3.6-flash`. The build plan's model-fitness assessment had been reasoning about a generation the key cannot reach — which in turn undermined the case for a second paid provider. Both roles now run on `gemini-3.6-flash`; buy Sonnet credit only if the orchestrator misbehaves in rehearsal.

**5. Discovery is heartbeat-driven, and the interval is a real config decision.** Agent cards republish every `agent_card_publishing.interval_seconds` (10 in the generated config), and the registry ages entries against a health-check TTL. This is what makes late-joining agents work without any retained state, and it is why the mesh notices a dead agent without being told. It also means background traffic scales linearly with agent count: fine at three, a decision at three hundred. Worth knowing before someone asks how discovery scales, because "it broadcasts" without the interval attached sounds naive.

**6. Native Windows works, though it is undocumented.** SAM lists macOS, Linux and Windows-via-WSL. `pip install` and the CLI both work natively on Windows with Python 3.13.12. Worth stating in the README, since a reviewer on Windows will assume otherwise.

---

## 10. Depth probes

| Probe | Answer |
|---|---|
| **"Why Solace and not CrewAI?"** | Those frameworks wire agents with synchronous point-to-point calls. That is fine until an agent is down, until you need fan-out, or until you add an agent. Put A2A on a broker and durability, back-pressure and discovery come from infrastructure the enterprise already runs. Then show the kill-an-agent beat |
| **"Isn't this just a message queue with extra steps?"** | Yes, deliberately. The claim is not that agents need novel infrastructure. It is that they need the infrastructure integration already solved, and most frameworks reinvent a worse version |
| **"What happens when two agents claim the same capability?"** | Discovery is a broadcast of agent cards; the orchestrator selects. That is a real ambiguity and the honest answer is that it is a policy question, not a transport one |
| **"How do you stop the model doing something stupid?"** | It cannot write to SAP. It publishes a resolution event, and it chooses among options supplied by tools rather than inventing facts. Escalation thresholds are explicit |
| **"Your agent queues are durable — isn't that the default?"** | No, and that is worth knowing. Default is temporary, so a dead agent takes its queue with it. One config line changes it. Found by building |
| **"Is the HTTP comparison rigged?"** | It has pooling, keep-alive, timeouts and retries. What it does not have is delivery to something not currently listening, which is structural rather than an implementation gap |
| **"Why not a second broker for agents?"** | Right answer if the requirement is tenancy. Here it would forfeit the claim that this runs on infrastructure already in place |
| **"How much of this is Solace and how much is Google ADK?"** | ADK is the agent runtime, A2A is the protocol, and Solace is the transport underneath both. The transport is the part being argued about, and the other two are open standards deliberately |

---

## 11. Build order, and the gate

Nothing here changes the a69 go/no-go on Wednesday evening. It sharpens what the gate is testing.

| Item | Must be true to go live |
|---|---|
| **a52** SAM running | ✅ installed and initialised; models block and durable-queue config still to land |
| **a53** Agent set | Three agents responding, with the orchestrator fanning out |
| **a54** HTTP control arm | Reachable and honest. **This is the one to protect** |
| **a55** Comparison harness | The kill-an-agent beat working with durable queues |

**If a54 is not clearly reachable by Wednesday evening, narrate.** A partial Act 3 — agents over the mesh, no control arm — demonstrates that agents can use a broker, which is not an argument. The whole value is in the contrast, and narrating the contrast from this document is stronger than showing half of it.
