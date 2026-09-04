# Messaging Design

_Built 2026-08-23. The Act 2 design deliverable: topic taxonomy, delivery semantics, failure handling and the rejected alternatives behind each. Written **on paper before code**, so the build implements a defended decision rather than the reverse._

**The organising principle: every section carries its rejected alternative.** A topic taxonomy or a delivery-mode choice is only worth presenting if the alternatives were weighed and can be named. Design claims that cannot survive "why not the other way" are decoration.

_Companion docs: [problem-statement.md](problem-statement.md) (Act 1), [agent-mesh-design.md](agent-mesh-design.md) (Act 3)._

_**Deployment, revised 2026-09-02:** the demo now runs against a **Solace Cloud** service (`meridian-demo`, AWS us-east-1, Developer / Standard 100) by default, with the local Docker broker retained as the demo-day fallback. Same code, same topic architecture, same queues — the profile is one environment variable, `DEMO_PROFILE`. The subscriptions on the cloud broker were verified byte-identical to `src/topics.py`, and `#DEAD_MSG_QUEUE` created first try, so nothing in this document changes. Cloud connections are TLS-only; see `RUNBOOK.md` §0a for the trust-store traps._

_**Implementation:** this repository. [`RUNBOOK.md`](../RUNBOOK.md) carries one-time setup, the five-window layout, the six-beat demo sequence with what to say at each beat, the reset procedure, and a break-glass table. `src/topics.py` is the executable form of §1; `scripts/provision.py` is the executable form of §2._

_Solace mechanics below were verified against docs.solace.com on 2026-08-23; findings that materially changed this design are called out in **Constraints discovered** at the end. Revised 2026-08-24 to adopt Solace's published vocabulary throughout — see §0. Revised again 2026-08-29 after design review: exception granularity split across two Object-types, every level justified against routing or access control, `customerTier` cut, and quoted Solace material moved below the design. Locality cut the same day on review — see §1 and **Constraints discovered**._

_Primary sources: [Event Messaging Overview](https://docs.solace.com/Messaging/messaging-overview.htm) · [Understanding Topics](https://docs.solace.com/Get-Started/what-are-topics.htm) · [Wildcard Characters in Topic Subscriptions](https://docs.solace.com/Messaging/Wildcard-Charaters-Topic-Subs.htm) · [Topic Architecture Best Practices](https://docs.solace.com/Messaging/Topic-Architecture-Best-Practices.htm) · [Topic Architecture Case Studies](https://docs.solace.com/Messaging/topic-use-cases.htm) · [Topic Hierarchy best-practices blog](https://solace.com/blog/topic-hierarchy-best-practices/)._

---

## Decisions this document closes

| Open question | Decision |
|---|---|
| Which exception type carries the live demo | **Stock shortfall** carries Act 3 — it is the one with genuine judgment in it (substitute, split-ship, or backorder). **Credit hold** carries the Act 2 deterministic walkthrough — the resolution is a rule, which is exactly the point |
| How many exception types to build | **Two.** Four is scope creep, one is thin. The 8/14 cut order puts scenario breadth second on the chopping block, so two leaves a cut available that is not the transport comparison |
| Company name | **Meridian Components**, kept. Obviously fictional, no collision risk |

---

## 0. Terminology — use Solace's, not ours

Solace has published vocabulary for every concept here. Using it is not pedantry: inventing parallel terms for a well-documented product is the tell that reads as *hasn't actually worked with this*, and the audience is Solace SEs. This section exists so the words in the presentation are theirs.

| Concept | **Solace's term** | Terms to avoid |
|---|---|---|
| The string on a message | **topic** | — |
| A slash-separated segment | **level** | node, segment, token |
| The static leading fields, fixed for an event stream | **event topic root** | prefix |
| The per-message fields the publisher substitutes | **event topic properties** | discriminators, variables |
| An individual component | **field** | discriminator |
| The matching expression a consumer registers | **topic subscription** | filter, pattern |
| `*` and `>` | **wildcards** | — |
| The whole design discipline | **topic architecture** / **topic hierarchy** | — |

Two specific corrections to earlier drafts of this document:

**"Prefix" was wrong, and it collides.** There is no prefix concept at runtime — the broker matches level by level and has no notion that a leading portion is special. Solace *does* use "topic prefix", but it means a character prefix **within one level** (`credit` in `credit*`). The distinction actually wanted is **root vs. properties**, which is a design-time distinction about which fields are static and which the publisher fills per message.

**"Discriminator" was ours, and was used two ways** — once for every field, once for one specific field. Solace's word is **field** (or **property** for the dynamic ones). The term is removed throughout.

---

## 1. Topic architecture

### The design

Everything in this section is **our topic architecture**. Solace's own published example appears further down, in a clearly marked callout, and nowhere else — see *Reading-order note* at the end of this section for why that placement matters.

```
Root                          Properties
────────────────────────────  ──────────────────────────────────────────────
mc/order/{verb}/v1            {orderId}
mc/order/exception/v1         {exceptionType}/{orderId}[/{lineId}]
mc/order/resolution/v1        {exceptionType}/{orderId}[/{lineId}]
```

**Worked examples**

```
mc/order/exception/v1/credit-hold/SO-4471955
mc/order/created/v1/SO-4471801
mc/order/shipped/v1/SO-4471801
mc/order/exception/v1/stock-shortfall/SO-4471932/3
mc/order/resolution/v1/stock-shortfall/SO-4471932/3
```

| Field | Solace field | Nature | Values |
|---|---|---|---|
| `mc` | Domain | static | fixed — see the ACL justification below |
| `order` | Object-type | **enumerated** | the subject of the event. `shipment`, `invoice` would be later siblings |
| `{verb}` | Verb | enumerated | `created`, `allocated`, `picked`, `shipped`, `invoiced`, `exception`, `resolution` |
| `v1` | Version | static | schema version, above every wildcard boundary |
| `{exceptionType}` | property | enumerated | order-scoped: `credit-hold`, `address-invalid`. Line-scoped: `stock-shortfall`, `carrier-rejected` |
| `{orderId}` | ObjectId | identifier | e.g. `SO-4471932` |
| `{lineId}` | ObjectId | identifier | line number within the order — **present only on line-scoped events**, and its absence is what makes an event order-scoped |

### Exception granularity — one root, variable depth

**The two exception types carried by this demo have different natural scope, and that changes the taxonomy.** A credit hold is a fact about an order: one decision, taken once. A stock shortfall is a fact about a *line* — the resolution is substitute *this item*, split-ship *this item*, or backorder *this item*. A shortfall on a five-line order is not one event.

Publishing both at order granularity would force line detail into the payload, where no consumer can route on it or apply access control to it. So the **address** carries the distinction: a line-scoped event appends `{lineId}`, and an order-scoped event simply ends at `{orderId}`.

Both share the root `mc/order/exception/v1/`. An earlier design gave line-scoped events their own Object-type, `mc/orderLine/`, and that was changed on 2026-09-02 after the split failed the governing rule — it won nothing on routing and cost the all-exceptions consumer a permanent second subscription. Constraint 11 has the full comparison.

The absence of `{lineId}` is not a null level. Solace's guidance for a level that is not always present is an explicit defined value **or** removal; this takes removal, and the resulting depth difference is itself the routing signal:

| Subscription | Receives |
|---|---|
| `mc/order/exception/v1/*/*` | order-scoped only — `*` is single-level, so length is exact |
| `mc/order/exception/v1/*/*/>` | line-scoped only — `>` matches one or more levels, never zero |
| `mc/order/exception/v1/>` | both, in one subscription |

Verified against the broker on 2026-09-02 rather than assumed, because the whole design rests on `>` not matching zero levels.

**The four exception types, and why each sits where it does**

| Exception | Description | Scope | In the demo |
|---|---|---|---|
| `credit-hold` | Customer is over their credit limit | **order** — one decision for the whole order, one resolution releases it | **Live** |
| `stock-shortfall` | Not enough stock to fill an item | **line** — substitute, split-ship or backorder is a per-item call | **Live** |
| `carrier-rejected` | Carrier refuses items, or a box containing them | **line** | Modelled |
| `address-invalid` | Delivery address fails validation | **order** — one fact about the order, resolved once | Modelled |

**`carrier-rejected` is line-scoped, and the reasoning is worth having ready.** A carrier refuses specific items before shipment, or belatedly refuses a box once it realises what is inside it. It never refuses an *order*: a carrier's business is boxes and the items in them, and an order is not a unit it transacts in. Modelling it order-scoped would push the rejected item into the payload, where the carrier desk can neither route on it nor have access control applied to it — the same failure the shortfall case avoids.

Two live and two modelled is deliberate: the live pair carries one of each scope, so the granularity question is *demonstrated*; the modelled pair shows the value space is not merely the two things that happen to be built.

Three consequences worth stating:

- **Object-type is enumerated, not constant.** It is the field that answers "what is this event *about*", and different exceptions are about different things.
- **Depth varies within one root, deliberately.** The earlier draft argued depth must be consistent within a root and used that to justify two roots. That is stricter than Solace requires: removing an inapplicable level is sanctioned, and here the removal is meaningful rather than incidental — a shorter topic *means* order-scoped, and subscribers route on exactly that.
- **The pairing is deliberate.** One order-scoped and one line-scoped exception was chosen so the demo shows the granularity question being asked and answered, rather than quietly assumed.

_Origin: the review question that forces it. An order will not always have one product, so is this the whole order or part of the order? The answer changes the topic taxonomy._

### Every level justified — routing or access control

The governing rule this section applies: **a topic hierarchy serves routing and ACL. A level serving neither has no justification and becomes noise.** Earlier drafts of this document argued routing exhaustively and never applied access control at all. This table is the correction.

Access control matters here in a specific, mechanical way: in Solace, **ACL profiles are written as topic patterns** — publish and subscribe exceptions expressed as topic subscriptions, wildcards included, attached to a client username. The topic hierarchy is therefore not merely described by the security model; it *is* the security model. A level whose job is to be a security boundary is justified even when nothing routes on it.

| Level | Serves routing? | Serves ACL? | Verdict |
|---|---|---|---|
| `mc` (Domain) | No | **Yes** — one rule, `mc/>`, isolates this application on a shared broker | **Keep.** See below |
| Object-type | **Yes** — consumers subscribe by subject | Yes — line-scoped and order-scoped consumers can be separated | Keep |
| `{verb}` | **Yes** — the stream selector | Yes — publishers restricted to the verbs they own, so a consumer cannot forge a resolution | Keep |
| `v1` (Version) | **Yes** — during a parallel run, v1 and v2 consumers route to different code | No | **Keep.** See below |
| `{exceptionType}` | **Yes** — the primary subscription axis | Yes — the credit desk is restricted to `credit-hold` and sees no other exception type | Keep |
| `{orderId}` / `{lineId}` (ObjectId) | Weakly — the short-lived order-scoped UI subscription | No | Keep — but this is the level to defend honestly; see below |
| ~~`{customerTier}`~~ | **No** — no consumer in this design subscribes on it | **No** — no access rule needs it | **CUT** |
| ~~`{locality}`~~ | **No** — no consumer subscribes on it | **No** — see below; the argument does not survive contact with the business | **CUT** |

**On `mc` — the ACL argument, stated properly.** The original justification was that it namespaces the application so a second domain could share the broker. That is future-proofing, and future-proofing satisfies neither test. The real argument is access control:

| Without `mc` | With `mc` |
|---|---|
| Isolating this application means enumerating every Object-type — `order/>`, `shipment/>`, `invoice/>` … | One rule: `mc/>` |
| Every new Object-type needs an ACL change, and forgetting one is a silent security hole | New Object-types are covered automatically |

The honest framing, including the cost: *three bytes per message buys the application's entire event space as a single access-control expression, with no failure mode where someone forgets to add a rule.* On a 100-byte payload those three bytes are real overhead — so the answer names the price rather than pretending there isn't one.

**On `v1` — it looks unjustified until you name the mechanism.** A version level appears to serve neither test, which is exactly why it needs saying out loud: during a parallel run it *is* routing. v1 consumers and v2 consumers are different code, and the version level is what sends each message to the right one. Without it there is no staged migration, only a big-bang cutover — which is the thing the problem statement says this customer cannot do.

**On `{customerTier}` — cut, and here is when it comes back.** It was carried for "priority handling without opening the payload", but no consumer in this design subscribes on it and no access rule needs it, so by the governing rule it is noise. It earns a place the moment either becomes true: a platinum-only escalation consumer, or a rule restricting which clients may see high-value accounts. Not before.

**Why Locality was cut — and it is the more instructive cut of the two.** An earlier draft called Locality the strongest-justified level, on regional dashboards for routing and warehouse-staff isolation for access control. Neither holds.

*Routing:* no consumer in this design subscribes on it. That is exactly the position `customerTier` was in when it was cut — the rule was being applied strictly to one level and generously to another.

*Access control:* the argument assumed exception handling is site-local. It is not. Credit holds go to a central credit desk, address failures to customer service, carrier rejections to the carrier desk. And the one exception that looked site-shaped is the clearest case against it: **resolving a stock shortfall requires live inventory across other warehouses**, as the problem statement itself says — so a site-scoped consumer could not resolve the very exception the level was supposed to route to it.

A level kept by twisting for a justification is worse than no level, because the justification is what gets probed.

**Where departmental access control lives instead.** It rides on `{exceptionType}`, which is already justified on routing grounds and therefore costs nothing extra:

```
credit desk client    subscribe exception:  mc/order/exception/v1/credit-hold/>
inventory planning    subscribe exception:  mc/order/exception/v1/stock-shortfall/>
```

The credit desk sees credit holds and nothing else, enforced at the broker rather than trusted to the consumer. That is real departmental ACL on a level that was being kept anyway.

**Prepared answer — "what about site- or department-specific requirements?"** Four options, in the order you would actually reach for them:

1. **Use an existing level.** Most departmental rules land on `exceptionType` or Object-type, which are already there.
2. **Append a level.** New fields go on the end, where `>` subscribers absorb them for free and no consumer breaks. This is why append-never-insert is a rule rather than a preference.
3. **Bump the Version** if it genuinely must be inserted mid-sequence — parallel-run v1 and v2, migrate, retire v1.
4. **Separate Message VPN** for hard isolation, when the requirement is tenancy rather than filtering.

The framing: *"I don't add a level for a requirement I can't name a consumer for. Here is what I'd do the day one appears, and none of it is a rewrite."*

**On ObjectId — the honest one.** `{orderId}` is weakly justified. It serves the rare short-lived order-scoped UI subscription and nothing else, and no access rule needs it. It stays because it makes per-entity subscription possible without payload inspection, and because it is Solace's own trailing convention. **If anyone pushes on a level, this is the one with the thinnest defence — say so before they find it.**

### The tradeoff this shape carries

Making verb a root field means there is no single subscription for "all lifecycle events" — `mc/order/*/v1/>` would sweep in exceptions and resolutions too. The cost is real and the mitigation is ordinary: a queue may hold several topic subscriptions, so a lifecycle consumer registers one per verb. Taken knowingly, because the alternative — a synthetic `lifecycle` grouping level above the verb — adds a level that exists only for subscription convenience and describes nothing that happened, which is precisely what the governing rule forbids.

### Corroboration — Solace's published example

> **QUOTED FROM SOLACE — NOT OUR DESIGN.**
>
> Solace's own published order example, from *Topic Architecture Case Studies*:
>
> `store/order/created/v1/{productGroup}/{area}/{productID}/{customerID}/{orderID}`
>
> Their canonical field sequence is *Domain / Object-type / Verb / Version*, then properties ordered least-specific to most-specific, ending in the object identifier. The design above follows that sequence, including the object identifier last.

This is corroboration, not source material: the design was reached from the primitives and then checked against their guidance. It matters because it means the design is an application of Solace's framework rather than a private invention to be defended.

### Reading-order note — why the quotation sits here and not above

An earlier draft placed this quotation *before* the design. In review, most of a short session went to challenging a `store` node, a `productID`, and a one-product-per-order assumption. **None of the three were in the design**; all three are features of the quoted example.

The lesson is about reading order, not labelling. A topic string is visually self-announcing; it is what the eye lands on, and a caption underneath arrives too late. So: our design first and visually dominant, quoted material below and unmistakably marked.

**This applies with more force to the deck.** The presentation shows **exactly one topic architecture — ours**. Solace's example belongs in an appendix, deployed only if someone challenges the shape, at which point *"and this matches their own published example"* is a strong close. On a live slide it competes with the thing being presented, in front of an audience with less time and less context than a reviewer reading closely.

### Value spaces, and the null case

**Every level declares its value space, including its null case where one exists.** A level whose value is sometimes absent is a design defect, not an edge case — the taxonomy has to say what gets published when the value is not known, or the first such event produces a malformed topic.

No level in the current design has a null case. The level that did — `{locality}` — was removed on 2026-08-29; see *Why Locality was cut* below. The rule still governs anything added later, and Solace's own guidance is the standard to apply: when a level may be null, first ask whether the field is still useful for routing, access control or governance — if not, remove it from the taxonomy; if it is, define an explicit null value so applications can subscribe to it and access control can be applied to it.

### The rule that makes the ordering defensible

Solace's `*` matches exactly one level and may appear at any level, including several times. `>` matches one or more levels but **only in the final position**. The consequence, and this is the load-bearing point under questioning:

> **Level order does not determine what is *filterable* — every level is reachable with `*`. It determines what is *cheap and readable*.** Put the axis most subscriptions pin first, so the common case is a `>` subscription rather than a string of `*`s.

That reframes "why is exception type above the order id?" from a guess into a stated optimisation with a known cost. The standing consumers pin exception type, so each gets a clean `>` subscription; the rare consumer that wants one order across all exception types pays a `*`, and still works perfectly.

Solace also supports `*` as a **topic prefix within** a level — this is the only sense in which "prefix" is a Solace term (`credit*` would match `credit-hold` and `credit-review`), but not as an arbitrary infix — `*` in the middle of a level is treated as a literal character. Worth knowing, and worth not relying on.

### Subscribing is not looking up — and the order ID position depends on it

The most natural objection to this ordering is: *most people care about a specific order — so why is the order id last, behind exception type?* It is the right question, and the answer is that it conflates two different operations.

| | Subscription | Lookup |
|---|---|---|
| Who performs it | A system, once, at startup | A person or an API call, ad hoc |
| Lifetime | Months | Milliseconds |
| How many exist | ~10 | Millions possible |
| Served by | Topic subscription on a queue | The audit store, or replay |

**"Where is order SO-4471932?" is a query, not a subscription.** It is answered by the read model fed from `Q/audit/all-exceptions`, or by replay. A topic hierarchy is a *routing* structure, not an *index* — nobody subscribes in order to find something; they subscribe to receive things as they happen.

So the population that filters on order ID never touches a subscription at all. The handful of standing subscribers — inventory planning, credit desk, carrier desk, audit — filter on exception type, and on scope where they care about it. Those are the consumers the ordering principle is about.

This matches Solace's published guidance directly: order properties "from least specific to most specific", and their own worked example places lower-cardinality fields above the order ID because "there are likely more orders placed than cities in which the enterprise operates". Exception-type-before-orderId is structurally the same call.

**The order-scoped subscription case is real, and is still served.** A UI following one order live is a legitimate subscriber: `mc/order/*/v1/*/SO-4471932` — one `*` per skipped level. Verbose to read, entirely functional, and created by a single short-lived UI session rather than standing infrastructure. The verbosity is paid by the rare case, which is the correct place to pay it. Request-reply, the other apparent order-scoped case, does not use this topic space at all — it uses the `ReplyTo` destination and correlation ID.

### Rejected alternatives

| Rejected | Why |
|---|---|
| **Version at the end** (`…/{orderId}/v1`) | Breaks every `>` subscription the moment v2 ships, because `>` swallows the version level. Version must sit above anything a subscriber wildcards past |
| **No version level at all** | Leaves no way to run v1 and v2 consumers side by side during a migration. The only alternative is a big-bang cutover, which is precisely what a distributor with a decade of accumulated interfaces cannot do |
| **Order ID early** (`mc/order/{orderId}/…`) | Highest-cardinality value at the top destroys wildcard utility — every subscription becomes `mc/order/*/exception/…`, and the topic stops being a routing structure and becomes an address |
| **Payload data in the topic** (quantity short, hold amount) | Topics are for *routing*, not for carrying state. Values that consumers filter on belong in the topic; values they merely read belong in the payload. Putting the shortfall quantity in the topic would create an unbounded level and force subscribers to parse topics as data |
| **Flat topics per consumer** (`mc/inventory-planning/exceptions`) | This is the point-to-point interface problem re-created inside the broker. The producer would again need to know who its consumers are — which is the exact thing the problem statement says has failed for a decade |

---

### Evolving it — blast radius, and the append-never-insert rule

The reasonable objection to a shared composition rule is maintenance: if every publisher and subscriber in a domain follows one rule, does a change for one consumer disturb all of them? Mostly no, and the exceptions are specific.

First, scope. **The rule is per event topic root, not per broker.** `mc/order/…` is one contract; `mc/shipment/…` and `mc/invoice/…` are different shapes that share only a Domain field. A change to one does not reach the others.

Within a root, the blast radius depends entirely on the kind of change:

| Change | Who breaks |
|---|---|
| **New value** in an existing field — a fifth exception type | **Nobody.** Subscribers pinning other values never see it; new subscribers opt in. New values are free |
| **Append** a field at the end | `>` subscribers are **unaffected** — `>` matches one *or more* levels, so an extra level still matches. Only exact or `*`-terminated subscriptions stop matching |
| **Insert or reorder** a field mid-sequence | Breaks every subscription pinning a level *after* the insertion point — and breaks it **silently**, as non-matching rather than as an error |
| **Change a field's value space** — rename `credit-hold` to `credit_hold` | Breaks subscribers pinning that value |
| **New Object-type** — `mc/shipment/…` | Nobody. Different root, different contract |

So a queue is affected only if its subscription pins a level at or past the change point, and most do not. The genuinely dangerous move is **inserting** a field, not adding one.

Two rules follow, and the second is already in the design:

**Append, never insert.** New fields go on the end, where `>` subscribers absorb them for free. This is a discipline, not a broker feature — nothing enforces it but review.

**The Version field is the escape hatch for when you must insert.** Publishers begin emitting `v2` while continuing `v1`; consumers migrate independently; `v1` retires when nobody is on it. This is also the answer to the migration probe — moving off the batch job without a big-bang cutover is the same mechanism.

**The framing to use if this is raised.** The coupling is the price of the decoupling. Publishers that do not know their consumers must still share *something*, and the topic architecture is where that necessary agreement is concentrated — deliberately, into one visible versioned artifact, instead of scattered across a decade of point-to-point interfaces. Compare the maintenance shapes: today a field change means touching eleven interfaces, each with its own owner and deploy window; here it is one rule, versioned, with a documented value space per field. Larger apparent blast radius, materially smaller real cost, and a staged migration rather than a simultaneous one.

This is also precisely what Solace sells **Event Portal** for — the topic architecture as a governed, versioned, reviewed artifact rather than tribal knowledge living in publisher source code. Naming it is the commercially literate close to this question.

---

## 2. Delivery semantics — direct vs guaranteed, per stream

Not everything deserves the same guarantee, and saying so is the difference between a design and a default.

| Stream | Mode | Reasoning |
|---|---|---|
| `mc/order/{verb}/v1/>` — the lifecycle verbs | **Direct** | High volume, informational, consumed by dashboards. A dashboard that misses a "picked" event during a reconnect is not wrong — it refreshes. Spooling this would buy nothing and cost spool depth |
| `mc/order/exception/v1/>` | **Guaranteed** | This is the business event. Losing one means an order silently stops. Non-negotiable |
| `mc/order/resolution/v1/>` | **Guaranteed** | The audit trail of what was decided and by whom. Losing it means the exception looks unresolved forever |
| Context lookups (inventory across warehouses, credit balance) | **Request-reply**, guaranteed | A synchronous-shaped question with a bounded wait. Uses the `ReplyTo` destination and correlation ID rather than a hand-rolled correlation scheme |

**The point to make out loud:** mixing modes on one broker, per stream, is itself the argument. The alternative architectures force one guarantee across everything — either pay spool cost for dashboard chatter, or accept loss on business events.

**Built as specified — and it very nearly was not.** `src/publisher.py` opens *two* publishers on one connection: a direct publisher for the lifecycle verbs and a persistent one for exceptions and resolutions. The first cut used a single persistent publisher for everything, which would have left this section asserting an argument the running demo did not actually make. Caught on 2026-08-29 while reconciling the documents against the code — the drift was invisible from either side alone.

### Queue topology

Durable queues, one per consuming service, each with its own topic subscription:

| Queue | Subscription | Max redelivery |
|---|---|---|
| `Q/inventory-planning/exceptions` | `mc/order/exception/v1/stock-shortfall/>` | 2 |
| `Q/credit-desk/exceptions` | `mc/order/exception/v1/credit-hold/>` | 2 |
| `Q/audit/all-exceptions` | `mc/order/exception/v1/>` | 0 (retry forever) |

The audit queue carries **one** subscription and receives both scopes, because they share a root. It carried two until 2026-09-02, one per Object-type, and a new object type would have meant a third — a change to shared infrastructure every time the domain grew. A queue may still hold several subscriptions, and that remains the mechanism a lifecycle consumer uses to gather the verbs it cares about; this consumer no longer needs it.

**Rejected alternative — one shared queue with consumers filtering client-side.** It works, and it is what a team reaching for the simplest thing would build. It fails on three counts: a slow consumer applies back-pressure to every other consumer on the queue; each consumer burns network and CPU discarding messages it does not want; and adding the twelfth consumer means touching the shared queue. Per-consumer queues with topic subscriptions is the whole reason the broker has a topic layer above the queue layer.

**Rejected alternative — topic endpoints instead of queues.** Topic endpoints bind one subscription each. Queues can hold several, and the operational tooling around queues is better. For this design the flexibility is worth more.

---

## 3. Failure handling

### Dead message queue

Default `#DEAD_MSG_QUEUE`, max-redelivery 2 on the service queues.

**The gotcha, and it is worth demonstrating rather than describing:** a message is only moved to the DMQ if it was published with the **DMQ-Eligible** flag set. Without it, a message that exhausts its redelivery count is *silently discarded*. Max-redelivery is set on the queue; DMQ-eligibility is set by the publisher — the two halves live on opposite sides of the system, and getting one without the other produces a system that looks correct and quietly loses poison messages.

Valid max-redelivery range is 0–255, where **0 means retry forever** — which is why the audit queue is set to 0 and the service queues are not. A poison message on the audit queue should block and be noticed; a poison message on a service queue should step aside so the other 109 exceptions that day keep flowing.

TTL interacts with the same flag: a message that exceeds its TTL on a queue configured to respect TTL is also moved to the DMQ, again only if DMQ-eligible.

### Consumer outage — the four-hour scenario

Success criterion 3 from the problem statement: *a warehouse system offline for four hours catches up; it does not drop the four hours.*

Guaranteed messaging on a durable queue delivers this without any application code. The queue spools; the consumer reconnects; delivery resumes from where it stopped. The demo shows it by stopping the inventory-planning consumer, letting exceptions accumulate on `Q/inventory-planning/exceptions`, and restarting it — and, critically, showing the **other** consumers unaffected throughout, which is what per-consumer queues buy and a shared queue would not.

### At-least-once, and the honest answer about exactly-once

Guaranteed delivery is **at-least-once**. Redelivery happens on consumer reconnect and on unacknowledged messages. There is no exactly-once, here or anywhere else in this class of system.

The design's answer is idempotent consumers keyed on `(orderId, exceptionId)`, carried in the payload, with the resolution write conditioned on the exception not already being resolved.

**Say it that way when asked.** "We get exactly-once" is a claim an SE audience will take apart in one question. "At-least-once delivery with idempotent consumers, keyed on the exception ID, and here is the dedup check" is the answer that holds — and it is the same answer their own field team gives.

---

## 4. Replay

`Q/audit/all-exceptions` is replay-enabled. Replay serves two purposes in the demo:

1. **The onboarding story.** A twelfth consumer arrives and needs the last 24 hours of exceptions to build its initial state. It replays rather than being backfilled from a database export.
2. **The forensic story.** "What did we know about SO-4471932 and when?" — replay from a timestamp rather than reconstructing from logs.

Replay accepts all logged messages, everything after a given replication group message ID, or everything from a start time (`YYYY-MM-DDThh:mm:ssTZD`, e.g. `2026-08-23T09:15:00-05:00`), which must not be in the future.

---

## 5. Ordering, and the constraint that shapes it

Per-order ordering matters: `allocated` must not arrive after `shipped` for the same order. Across orders it does not matter at all.

**Decision: non-partitioned queues, single consumer flow per service queue.** Order is preserved, and replay works.

The alternative — partitioned queues with `orderId` as the partition key — is the right answer at real throughput. It preserves per-order ordering *within* a partition while allowing many consumers, and it is how this would actually be built at 2,800 orders/day scaled up tenfold. It is not the right answer here, for a specific and checkable reason:

> **Message replay is available for non-partitioned queues and topic endpoints.** Partitioning the queue would cost the replay demo.

The mitigation, if throughput ever demanded partitioning, is to partition the *service* queues and keep `Q/audit/all-exceptions` non-partitioned, so replay survives where it is actually needed. That is the answer to give if anyone raises partitioned queues — which they may well, since it is a natural "why didn't you…" and the honest answer is a tradeoff rather than an oversight.

Two further partitioned-queue behaviours worth having ready: rebalancing triggers when a consumer binds to a queue with fewer consumers than partitions, after a default five-second delay; and **changing the partition count changes the key-to-partition mapping**, so consumers start receiving a different set of keys. That second one is the operational trap and the reason partition count is not a knob to turn casually.

---

## Constraints discovered while designing this

Three findings changed the design rather than decorating it. All are good answers under probing, because each is a decision taken knowingly rather than a default left in place.

**1. Replay and partitioned queues are mutually exclusive.** Replay works on non-partitioned queues and topic endpoints. Since a replay demo is a stated success criterion and partitioning is not needed at demo scale, the queues are non-partitioned — with the split-topology mitigation above ready as the scale answer.

**2. DMQ eligibility is publisher-side, redelivery limits are queue-side.** Setting max-redelivery without setting the DMQ-Eligible flag at publish produces silent message loss that looks like correct operation. This is worth *showing* in the failure-scenario build rather than mentioning — it is precisely the kind of detail that distinguishes someone who has designed with these primitives from someone who has read about them.

**7. TLS trust store — three traps in one setting, found 2026-09-02 moving to Solace Cloud.** Not a topic-architecture finding, but the same pattern as 1 and 2 and the sharpest example yet.

Solace's Python API wraps a C client that does **not** read the operating system certificate store. With no trust store it fails `SESSION CREATION UNSUCCESSFUL. Failed to load trust store` — which reads like a missing file rather than a missing setting. `trust_store_file_path` then wants a **directory** despite its name; a `.pem` path gives `Untrusted certificate`, sending you after the server's certificate instead of your own argument. And the directory must contain **only** certificates: pointing at `certifi`'s package directory makes the handshake succeed, so a short-lived **publisher works**, while long-lived **receivers** die in a loop of `SSL 'SSL-client' cannot read, sslErr = 1`.

That last one is the most misleading signal in this entire build. A working publisher beside a failing consumer sends you looking at the consumer, at queue permissions, at the client profile — anywhere except the transport both share.

**This belongs to the dominant pattern in this build: the symptom names the wrong subsystem.** By 2026-09-03 there were seven independent cases — a dropped connection property reported as a missing credential; a byte-order mark in an environment file reported as a YAML validation error; this trust store, which completes the handshake and kills long-lived receivers later; a duplicate client name that presented as a flood of SSL errors; `startReplay` returning 200 while leaving the queue undeliverable; `deleteMsgs` returning 200 and removing nothing; and a model name read from a database while the environment file that appeared to set it was ignored.

A **narrower and rarer** class sits inside that one: two settings that must agree with nothing validating the pair. DMQ eligibility is the clean example (publisher-side flag, queue-side redelivery limit), and the `.env`/`platform.db` split is the only other genuine member. `respect-ttl` on the DMQ, cited here in an earlier draft, belongs to **neither** — it is refused loudly, immediately, in one place, which is the opposite of the point. Removed 2026-09-03 when the claim was checked rather than repeated.

Seven independent cases is an observation about the platform rather than a list of gotchas, and it is the honest answer to "what surprised you".

**3. The first draft of this taxonomy had no null case for its Locality level — caught in review, 2026-08-23; the level itself was then cut on 2026-08-29, see 6 below.** An order is created, and a credit hold fires, before any warehouse is assigned. The original design assumed every level always carries a value, and would have broken on the first credit-hold event published. Fixed with an explicit `unassigned` token, per Solace's documented null-value guidance.

Keep this one in the decisions log deliberately: **"here is a design question I initially got wrong, and here is why the fix is better than the original" is stronger under probing than a design that was never challenged.** The related conceptual point — that subscribing and looking up are different operations, and only one of them is served by the topic hierarchy — is the reasoning that keeps the order ID at the end, and it is the more valuable half of the exchange.

**4. The first draft used invented vocabulary — corrected 2026-08-24.** "Topic pattern", "discriminator" and "prefix" were ours, not Solace's; "discriminator" was additionally used two ways in one document, and "prefix" collided with Solace's real and narrower meaning (a character prefix within a level). Solace's published terms — **level**, **event topic root**, **event topic properties**, **field**, **topic subscription**, **topic architecture** — are now used throughout, per §0.

The substantive gain is larger than the cosmetic one. Solace's canonical field sequence is *Domain / Object-type / Verb / Version* plus properties, and their published order example is `store/order/created/v1/{productGroup}/{area}/{productID}/{customerID}/{orderID}` — structurally the same design this document had reached independently, including the object identifier last. Adopting their names converts the design from a private construction that must be defended into an application of their own framework, and it independently corroborates the order-ID-last decision that prompted the review.

Adopting the root/properties split also removed a real muddle: the original had a synthetic `{stage}` level above a `{discriminator}` level, when `lifecycle` versus `exception` was never a per-message variable. Verb is a root field, so each verb is simply its own event stream.

---

**5. Four design changes from review, 2026-08-26.** The topic architecture was reviewed live against one governing rule: *a topic hierarchy serves routing and ACL; a level serving neither has no justification and becomes noise.*

Three of the four challenges turned out to describe Solace's quoted example rather than this design: there was no `store` node, no `productID`, and "fixed" was a column label rather than a topic value. **That is a finding about the artefact, not a reprieve.** An experienced reader, reading in good faith with the author present, could not tell quoted material from designed material in the time available, and a live audience has less time still. Hence the reading-order fix in §1.

But two of the challenges carry real substance underneath the misread, and both changed the design:

- **Exception granularity.** The general point, *is this the whole order or part of the order?*, lands regardless of what was being read. A stock shortfall is line-scoped and a credit hold is order-scoped, and the original taxonomy could express only the latter. Now carried by the Object-type field, which also settles whether that field is constant or enumerated: it is enumerated.
- **The Domain level.** The challenge pivoted from the quoted `store` to a general question, *do you need the org name?*, and the live answer was accepted on condition it be defensible in writing. The written justification was future-proofing, which satisfies neither test. Re-argued on access control.

Two further changes follow directly from the governing rule: **`customerTier` is cut** (nothing routes on it, no access rule needs it), and **every remaining level now carries an explicit routing-or-ACL verdict**, including an honest admission that ObjectId is the weakest.

The access-control gap was conceded live, and it was a real hole: the document argued routing exhaustively and never once applied ACL as a justification.

---

**6. Locality cut, 2026-08-29 — a level removed by applying the governing rule to our own work.** An earlier draft called Locality the strongest-justified level in the design. Re-examined against the governing rule it failed both legs: no consumer subscribes on it, and the access-control argument assumed exception handling is site-local when it is central. The decisive point came from this project's own problem statement — resolving a stock shortfall requires live inventory *across* warehouses, so a site-scoped consumer could not resolve the one exception that looked site-shaped.

The instructive part is the inconsistency it exposed: `customerTier` had been cut for exactly this reason a few hours earlier, while Locality was kept on the strength of consumers that did not exist. The rule was being applied strictly to one level and generously to another.

Two things came out of it. Departmental access control moved onto `{exceptionType}`, which was being kept on routing grounds anyway and so carries the ACL story for free. And the design now has a prepared answer for site- or department-scoped requirements — use an existing level, append, version-bump, or separate VPN — rather than a level held in reserve against a requirement nobody had named.

**8. Replay is accepted by the control plane and never happens on the data plane — found 2026-09-02 on the trial cloud service, and it takes the queue down with it.** This is the strongest instance yet of the pattern in 2 and 7, and the only one that caused an outage rather than a wrong result.

Every configuration step succeeds. Creating a replay log returns 200. The log demonstrably fills — message bytes accumulate in its spool usage. `startReplay` on a queue returns 200. Then nothing is delivered: `replayState` sits at `active` indefinitely, `replayedTxMsgCount` stays at zero, and the queue stops delivering to bound consumers altogether. A consumer binds successfully and receives silence.

Reproduced on two queues, with a consumer bound before the replay as well as after. Bouncing egress does not clear it. Recovery required deleting the queue and re-provisioning.

Two things follow. Operationally, Beat 5 is now narrate-only and the replay log has been deleted from the service, because the failure mode is not "replay does not run" but "the queue stops working". Architecturally, it is the sharpest available answer to *how do you know a managed service actually does what its API says*: a 200 on a config endpoint is not evidence of a working data path, and the only way to find that out was to build it and run it. Constraint 1 — replay and partitioned queues being mutually exclusive — still stands as a design point and still shaped the topology; what changed is that the demonstration became a claim we can defend rather than a thing we show.

---

**9. A second endpoint that returns 200 and does nothing — found 2026-09-02, and it is the same failure as 8.** Clearing a queue's messages without deleting the queue has an obvious SEMP action, `deleteMsgs`. It returns 200 and removes nothing: six messages before the call, HTTP 200 back, six messages still there ten seconds later.

This matters less operationally than 8 — nothing breaks, the call is simply inert — but it matters more as evidence, because it is the *second independent instance* of the same thing on the same service. One endpoint accepting a request it will not honour is a bug. Two is a property of the environment, and it changes what a reasonable engineer should assume: on a managed service, a 200 from the control plane is a receipt for the request, not a statement about the data plane. The check is always the same and always cheap — read the state back afterwards.

The workaround is to delete the queue and let provisioning recreate it, which works because the topology is defined in the repository rather than on the broker. That is the payoff for a decision made much earlier for unrelated reasons: nothing on the broker is a source of truth, so destroying and rebuilding any part of it is a safe move rather than a data-loss event.

---

**10. A client-name collision that impersonated a TLS failure — found 2026-09-02, and the most instructive bug in the build.** Consumers connected as `meridian-<role>`. Client names are unique per message VPN, and that name collided whenever a force-killed consumer left a ghost connection holding it, or the window set was started twice. Two connections with one name displace each other continuously, and the churn loses acknowledgements in flight.

What it looked like was a certificate problem: a flood of `SSL 'SSL-client' cannot read`. What it actually was had nothing to do with TLS. Downstream, the consequences inverted the system's central claim — messages were processed correctly, redelivered because their acks were lost, and climbed to max-redelivery until **successfully handled messages landed in the dead message queue**. A demo built to show that the DMQ catches what cannot be processed was instead filling it with work that had been done.

Three things worth carrying from it. First, **the loudest symptom named the wrong subsystem**, which is the recurring shape of every constraint in this list. Second, **redelivery was correct behaviour throughout** — at-least-once did exactly what it promises, and the consumers' idempotency is what made the damage visible rather than silent. Third, the fix is a one-line naming change (a short random suffix, role kept at the front so the console stays legible), but finding it required isolating one variable at a time against a live broker: same code, same queue, 15 redeliveries under the shared name and 0 under a unique one.

It is also the strongest available answer to *"what went wrong when you built this?"* — a real failure, correctly diagnosed, with the misleading symptom named honestly.

---

**11. The two-root split was tested against the governing rule on 2026-09-02 and did not survive it — the roots were merged.** The question, raised in review: why `mc/order/exception/v1/{type}/{orderId}` and `mc/orderLine/exception/v1/{type}/{orderId}/{lineId}` rather than one root where the line id is simply an extra level when it applies?

Wildcard behaviour was verified against the live broker rather than assumed, because the whole question turns on it. `>` matches **one or more** levels and never zero:

```
mc/order/exception/v1/*/*     -> order-scoped ONLY  (exactly 6 levels)
mc/order/exception/v1/*/*/>   -> line-scoped ONLY   (7 or more)
mc/order/exception/v1/>       -> both
```

Counting the subscriptions each consumer actually needs settles it:

| Consumer wants | Two roots | One root |
|---|---|---|
| One exception type | 1 | 1 |
| All line-scoped | 1 | 1 |
| All order-scoped | 1 | 1 |
| **Everything** | **2** | **1** |
| One order, both scopes | 2 | 2 |

**The merged root wins one case and ties every other. The split therefore had no routing justification at all**, which is the leg the governing rule tests. ACL is also a wash: ACL topic exceptions take the same wildcards, so `mc/order/exception/v1/*/*/>` restricts line-level access exactly as `mc/orderLine/>` did.

The objection that variable-length topics break Solace's guidance does **not** hold, and this was the strongest-sounding argument for keeping the split. Solace's documented advice for a level that is not always present is an explicit defined value *or removal*. Removal is sanctioned, and here it is better than sanctioned: the absence of `{lineId}` is not a gap but a scope distinction subscribers route on directly.

What the split bought was modelling rather than routing — an order line is a distinct object with a distinct payload schema. That is a real cost of merging and is worth conceding aloud. It was not worth a permanent extra subscription on every all-exceptions consumer, and a new object type under the old scheme would have meant a *third*: a change to shared infrastructure every time the domain grew.

**The condition under which this flips.** If line-level *lifecycle* events are ever published — allocated, picked, per line — merging makes every order topic variable-depth rather than just the exception ones, and the separate object type earns its place. That is the trigger to revisit, and naming it is more useful than defending the decision.

Two things worth carrying from the exercise. First, **the level that had been called well-justified was the one that failed** — the same shape as the `{locality}` cut in 6, and the second time applying the rule strictly to our own work changed the design. Second, the analysis was only decidable by testing wildcard semantics on a real broker; reasoning from documentation would have left the central premise unverified.

---

---

## Depth probes this design invites, and where the answers live

Rehearsal material for a62.

| Probe | Answer |
|---|---|
| "Why is exception type above the order id?" | Level order sets cost, not capability — every level is reachable with `*`. Exception type is where the standing subscriptions pin, so it goes first; a consumer wanting one order across all exception types pays a `*` |
| **"Most people care about a specific order and may not know the warehouse. Why is warehouse above order ID?"** | Subscribing and looking up are different operations. Lookup is served by the audit read model or replay, not by a subscription — a topic hierarchy is a routing structure, not an index. The ~10 standing subscribers filter on exception type and warehouse. Also matches Solace's own least-specific-to-most-specific guidance, whose worked example puts city above order ID |
| **"How would you handle a site-specific or department-specific access rule?"** | Four options in order: use an existing level (most departmental rules land on `exceptionType`); append a level, where `>` subscribers absorb it for free; bump the Version if it must be inserted mid-sequence; or a separate Message VPN when the requirement is tenancy rather than filtering. None is a rewrite |
| **"You have no location in the topic. Why not?"** | No consumer subscribes on one, and exception handling here is central rather than site-local. A stock shortfall is the clearest case against it — resolving one requires inventory *across* warehouses, so a site-scoped consumer could not resolve the exception the level would route to it. A level kept by twisting for a justification is worse than no level, because the justification is what gets probed |
| **"Does an order only ever have one product?"** | No — which is why exception granularity is carried by the Object-type field. A credit hold is order-scoped; a stock shortfall is line-scoped and publishes one event per affected line, with the line id appended. The two demo exceptions were chosen to have different scope precisely so the question gets answered rather than assumed |
| **"Do you need the org name? What does it buy you?"** | Access control. One rule, `mc/>`, isolates the application on a shared broker; without it, isolation means enumerating every Object-type and every new one is a silent hole. It costs three bytes per message, which is real overhead on a small payload — that is the price, stated |
| **"What does the version level route?"** | During a parallel run, v1 and v2 consumers are different code, and the version level is what sends each message to the right one. Without it there is no staged migration, only a big-bang cutover — the thing this customer cannot do |
| **"Which of your levels is weakest?"** | ObjectId. It serves only the rare short-lived order-scoped UI subscription and no access rule needs it. It stays for per-entity subscription without payload inspection, and because it is Solace's own trailing convention. Volunteer this before they find it |
| **"One shared composition rule across every publisher — isn't that a maintenance problem?"** | Scope is per event topic root, not per broker. New values are free; appending is safe for `>` subscribers; only insert/reorder breaks, and silently — which is what the Version field exists for. The coupling is the price of the decoupling, concentrated into one governed artifact instead of eleven point-to-point interfaces. Event Portal is the governance answer |
| "What happens when the WMS is down for four hours?" | Nothing is lost; the queue spools and delivery resumes. Demonstrated live in a50, including that other consumers are unaffected |
| "Exactly-once or at-least-once? What did you do about duplicates?" | At-least-once with idempotent consumers keyed on `(orderId, exceptionId)`. No exactly-once claim |
| **"Why one root with a variable-length topic? Why not a separate `orderLine` object type?"** | It was two roots until 2026-09-02. The split cost the all-exceptions consumer a second subscription and won nothing on routing — verified on the broker: `>` matches one or more levels, so `*/*/>` still isolates line-scoped events cleanly and `*/*` isolates order-scoped ones. What the split bought was modelling, since an order line has its own schema, and that is a real concession. If line-level lifecycle events were ever added it would earn its place back. Constraint 11 |
| **"Have you had a message end up somewhere it should not?"** | Yes, and it inverted the DMQ story: successfully processed messages reached the dead message queue because their acknowledgements were being lost to a client-name collision, so they were redelivered until max-redelivery. The symptom presented as a TLS error and TLS was never involved. Constraint 10 |
| **"How do you know the managed service does what its API says?"** | You do not, from the API. Two endpoints on this service returned 200 and did nothing — message replay and `deleteMsgs`. Both were found by reading the state back after the call, which is now the habit: the control plane gives you a receipt for the request, not a statement about the data plane. See constraints 8 and 9 |
| **"Can you show us replay?"** | Not on this service, and say why: the replay log was created, it filled, `startReplay` returned 200, and no message was ever transmitted — while the queue stopped delivering to its bound consumers until it was deleted and re-provisioned. Tried it, it does not work on the trial tier, and the failure is silent on the control plane. The design point survives intact: replay needs non-partitioned queues, which is why the audit queue is not partitioned |
| "Why not partitioned queues?" | They are correct at scale and cost the replay demo. Split topology is the mitigation. Also: changing partition count remaps keys |
| "Why not one queue and filter client-side?" | Slow-consumer back-pressure across all consumers, wasted work discarding, and the twelfth consumer means touching shared infrastructure |
| "How would you migrate them off the batch job without a big bang?" | The version level exists for exactly this. Run the batch and the event path in parallel, reconcile, then retire the batch — the version level lets v1 and v2 consumers coexist during it |
| "What is in the topic vs the payload?" | Anything a consumer routes on is in the topic; anything it merely reads is in the payload. Shortfall quantity is payload, not topic |

---

_Solace mechanics verified 2026-08-23 against docs.solace.com: topic wildcard syntax and restrictions; message replay availability and from-date format (documented behaviour; **not reproducible on the trial cloud service** — see constraint 8); partitioned queue key hashing, rebalancing and remapping; dead message queue naming, DMQ-eligibility, max-redelivery range and TTL interaction. All business specifics are illustrative, per the problem statement._
