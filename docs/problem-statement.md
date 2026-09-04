# Problem Statement and Current State

_The Act 1 deliverable: the problem this demo exists to solve, written **before any code**. If the problem statement cannot survive a hard question standing on its own, no amount of working software rescues it._

_Companion docs: [messaging-design.md](messaging-design.md) (Act 2), [agent-mesh-design.md](agent-mesh-design.md) (Act 3)._

---

## The one-paragraph version (say this)

> "Meridian Components sells industrial parts through distributors. About 4% of their orders hit an exception — stock shortfall, address validation failure, credit hold, carrier rejection — and none of those exceptions are *discovered* until the overnight batch runs. So an order that broke at 9:15 in the morning sits untouched for fourteen hours before a human sees it, and then gets worked over email, which adds another day. The information needed to resolve most of them existed inside the business the moment the order broke. The problem isn't that they lack the data. It's that the data and the exception never meet while anyone can still do anything about it."

That is the whole problem. Everything below is support and defence.

---

## The company (illustrative, and labelled as such)

| Attribute | Value |
|---|---|
| Business | Industrial components distributor — sells through a dealer/distributor channel, not direct to consumer |
| Scale | ~2,800 orders/day |
| Order system of record | SAP |
| Downstream systems | Warehouse management, transportation/carrier booking, billing, customer notifications |
| Integration today | Nightly batch extracts plus a handful of point-to-point interfaces accumulated over a decade |

**Say "illustrative" out loud.** A fabricated customer presented as real is a credibility problem the moment someone asks which account it was. Naming it as a composite is honest, costs nothing, and lets the audience engage with the architecture instead of the provenance.

Two reasons this profile and not another: it sits on the GTM surface Solace actually sells through, where the SAP and Boomi OEM relationships already pre-paper Solace into most enterprises, and warehouse/logistics workflow is squarely in scope for the platform rather than an exotic edge case.

---

## Current state — how it actually works today

An order is captured in SAP and moves through fulfilment. Four things commonly break it:

| Exception | What happened | What resolving it requires |
|---|---|---|
| **Stock shortfall** *(line-scoped)* | An ordered **line** exceeds available inventory at the assigned warehouse. A five-line order can have one line short and four fine | Live inventory across other warehouses; substitution rules; customer's split-shipment preference |
| **Address validation failure** *(order-scoped)* | Ship-to address fails carrier validation | Address history for that account; the account's prior corrections |
| **Credit hold** *(order-scoped)* | The **whole order** pushes the account past its credit limit — one decision, taken once | Current balance, payment in flight, account standing, tolerance policy |
| **Carrier rejection** *(line- or order-scoped)* | Booked carrier refuses the shipment — dimensions, hazmat class, lane | Alternate carrier rates and capacity; service-level commitment on the order |

**Scope differs by exception, and it matters.** A credit hold is a fact about an order; a stock shortfall is a fact about a *line*. That distinction survives all the way into the topic architecture, where it is carried by two Object-types rather than forced into a payload — see [messaging-design.md](messaging-design.md) §1. The question that forces it: *is this the whole order, or part of the order?* The answer changes the topic taxonomy.

**None of these are detected in-flight.** They surface when the nightly reconciliation job runs, which writes an exception report. The next morning, a fulfilment coordinator opens it, works down the list, and emails whoever they think owns each one — inventory planning, credit, the carrier desk, sometimes the account rep. Those people reply when they reply. The coordinator applies the answer back into SAP by hand.

### The clock, stated plainly

| Stage | Elapsed |
|---|---|
| Exception occurs (say 9:15 AM) → nightly batch detects it (11:00 PM) | ~14 hours |
| Detection → coordinator opens the report and triages | ~9 hours |
| First email out → useful reply back | typically same day, sometimes not |
| Reply → correction applied in SAP → order resumes | ~1 hour |
| **Total, exception to resumption** | **~24–36 hours, and the tail is much worse** |

The arithmetic is the point: **most of that elapsed time is waiting to be *noticed*, not waiting to be *solved*.** The actual resolution — check another warehouse, confirm a payment cleared, pick a different carrier — takes minutes once the right person has the right context. That gap between "how long it takes" and "how long the work takes" is the entire business case, and it is worth saying in exactly those terms.

### What it costs

Derived from the stated assumptions above, not cited from an industry report — show the arithmetic and it survives scrutiny:

- 2,800 orders/day × 4% ≈ **110 exceptions/day**
- Each consumes ~25 minutes of coordinator time across triage, chasing and re-keying ≈ **46 hours/day of pure coordination** — roughly six full-time people doing nothing but shepherding broken orders
- Orders delayed a day or more risk service-level penalties in distributor contracts and drive an expedite-freight bill nobody budgets for
- The soft cost is worse and harder to argue with: distributors learn that Meridian is slow when something goes wrong, and that shows up at renewal

**Deliberately not claimed:** a revenue-loss figure, a churn percentage, or an industry benchmark. Every one of those invites "where did that number come from", and none of them is needed — the coordination-hours number is derived in front of the audience and is sufficient on its own.

---

## Why it hasn't already been fixed

**This is the section that separates a real problem statement from a strawman, and it is the first place a skeptical audience will push.** If the answer to "why don't they just fix it" is obvious, the problem was not worth a demo.

| The obvious fix | Why it hasn't worked |
|---|---|
| "Run the batch more often" | Shortens the detection window but doesn't close it, and every increase in frequency raises load on SAP during business hours — which is exactly when it is least welcome. It also does nothing about the email-based resolution, which is the larger half of the delay |
| "Add point-to-point interfaces for the exception cases" | This is what they already did, and it is why they have a decade of accumulated interfaces. Each new consumer means a new integration, and each one is a change to the producer. The cost of adding the *eleventh* consumer is what stopped them |
| "Buy a workflow tool" | Routes the work; doesn't shorten detection, and doesn't bring the resolving context with it. The coordinator still chases people for data |
| "Have the WMS call the inventory service directly" | Couples fulfilment availability to inventory-service availability. When inventory is down for maintenance, orders stop. They have been burned by this and are now reasonably conservative about synchronous coupling |

The honest summary: **each individual fix is defensible and none of them addresses the actual shape of the problem**, which is that exception information needs to reach several different consumers, at different speeds, with different reliability requirements, without the producer needing to know who they are.

That is a messaging problem. Saying so at this point in the presentation is earned rather than asserted — which is the whole reason Act 1 exists before Act 2.

---

## What "solved" looks like — the success criteria

State these before showing anything. They are what the demo is then measured against, and defining them yourself is how the demo gets judged on the right axis.

1. **Detection is immediate.** An exception is known the moment it occurs, not at 11 PM.
2. **The right parties learn about it without the order system knowing who they are.** Adding a twelfth consumer must not be a change to SAP.
3. **Nothing is lost when a consumer is down.** A warehouse system offline for four hours catches up; it does not drop the four hours.
4. **Resolution context travels with the exception.** Whoever picks it up — human or agent — gets what they need to act, rather than starting a hunt.
5. **The escalation to a human is deliberate, not a fallback.** Some of these genuinely need judgment. The system should be good at *knowing which*, and should hand those over with everything already gathered.

Criteria 1–4 are Act 2 and are satisfied by event-driven architecture on PubSub+. **Criterion 5 is what Act 3 is for**, and framing it here — before any agent has been mentioned — is what keeps the agentic layer from reading as chasing the growth narrative. It arrives as the answer to a requirement the audience already accepted.

---

## Hard questions this statement invites, and the answers

These are the ones that come at the *problem*, not the architecture.

**"Isn't 4% low enough to just live with?"**
110 exceptions a day, six people's worth of coordination, and it is the 4% that distributors remember. Volume is not the argument; asymmetry of impact is.

**"Why is this a Solace problem rather than an SAP problem?"**
SAP knows the order broke. It does not know, and should not need to know, who cares. The moment you make the order system responsible for the distribution list, you have built the eleventh point-to-point interface again.

**"Couldn't Kafka do this?"**
Yes, partly — and answer it that way rather than defensively. Then be specific about where the differences actually show up for *this* problem: topic hierarchy and wildcard routing without a consumer-side filtering layer, the request-reply pattern for the resolution round-trip, dead-message handling, and an operational profile that does not require a platform team. That is a defensible answer; "Kafka is worse" is not.

**"Is this a real customer?"**
No — a composite, and say so immediately and without hedging. Then move: the mechanics of the batch window, the coordination cost and the failure modes are drawn from how distribution businesses actually run, and the architecture would be the same for any of them.

## Decisions this statement left open — all now closed

Closed by the messaging design and its post-review revision.

| Question | Resolution |
|---|---|
| Which exception type carries the live demo | **Both, deliberately.** Stock shortfall carries Act 3 — it has the real judgment in it (substitute, split-ship, or backorder). Credit hold carries the Act 2 deterministic walkthrough, because its resolution is a rule |
| How many exception types | **Two.** Four is scope creep, one is thin, and two leaves a cut available that is not the transport comparison |
| Company name | **Meridian Components**, kept |

**The pairing is load-bearing.** The two are not merely one-hard and one-easy — they have *different granularity*, one line-scoped and one order-scoped. That was chosen so the demo visibly asks and answers the granularity question rather than assuming past it.

---

_Revised after design review: exception scope annotated per type, and the open decisions closed. All company figures are illustrative and derived in-document; none are cited from external sources._
