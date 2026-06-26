# Mycelium — Business Model: How We Earn

> **The question you asked:** *"How will we do the business? How will we earn?"*
> **The one-sentence answer:** *We take a small fee on the value that flows through the agent economy we enable, and we sell managed infrastructure on top of an open-source core. We grow exactly as the agent economy grows.*

This is the **"AWS / Stripe of the agent economy"** model: the protocol and SDK are open-source and free (that's how you win adoption and an SDF grant), and you monetize the **rails** (settlement) and the **managed services** (hosting) — not the toll-free road.

---

## 1. The principle: monetize value-flow and convenience, never the open core

Two hard rules that keep you fundable *and* adoptable:

1. **The open-source core is always free and self-hostable.** Compiler, SDK, CLI, contracts — MIT, on PyPI, run it yourself forever. This is non-negotiable for ecosystem credibility and for the SDF grant. Charging for the SDK would kill adoption and contradict the grant thesis.
2. **You earn where you add ongoing value:** when agents *settle value* through your escrow rails, and when teams want *managed infrastructure* instead of self-hosting. Both scale with usage, not with seats.

---

## 2. The four revenue lines (in priority order)

### Line 1 — Protocol settlement fee (the primary, long-term engine) 💰
A small **basis-point fee** on value settled through Mycelium's escrow / job-board / deal flows.

- **Where:** the `EscrowPaymentRouter` release/split path (`release_funds`, `split_release`) and `mycelium deal` / `mycelium job finalize`. When a bounty or deal settles, a configurable fee (e.g. **25–100 bps**, i.e. 0.25%–1%) routes to a protocol address before the payout.
- **Why it's the right model:** the value you provide is the **coordination + trust layer** — discovery, escrow, proof-of-work enforcement, dispute/refund, swarm splitting. The fee is for *that*, not for moving the tokens. An agent that wanted to settle peer-to-peer would have to rebuild all of it.
- **Why it scales beautifully:** revenue = (settled volume) × (fee). As the number of agents and jobs grows, settled volume grows, and you earn more **with zero marginal cost** — the contract collects automatically on-chain. This is the line that justifies a venture/grant-scale outcome.
- **Stellar makes it viable:** sub-cent base fees mean a 0.5% protocol fee on even tiny machine-to-machine payments is meaningful to you and negligible to the agent. On a high-fee chain this model breaks.

> **The objection to be ready for:** *"Why won't agents bypass you?"* → "They can settle a raw payment peer-to-peer anytime — `mycelium pay` is free and unconditional. They use the escrow/job rails *because* they don't trust the counterparty. The fee buys trustless settlement, proof enforcement, and swarm coordination. Bypassing it means rebuilding the trust layer and giving up discovery + reputation. The fee is small precisely so it's never worth bypassing."

### Line 2 — Managed infrastructure (the near-term cash, open-core SaaS) ☁️
Everything in the OSS stack is self-hostable; most teams will pay you to not bother. Usage-based:

| Service | Free (self-host) | Paid (managed) |
|---|---|---|
| **Hosted compile** (`/compile` Docker backend) | run your own Docker | metered hosted compiles, higher concurrency, priority queue |
| **Off-chain indexer** (`vision/01`) | run your own | hosted, SLA'd, full-history search API, capability/reputation queries |
| **RPC / submission infra** | bring your own RPC | managed RPC pool, channel-account sequencing (see `04-scaling`), higher rate limits |
| **Managed agent memory** (`vision/02`) | `LocalVectorBackend` | hosted `SupermemoryBackend`, anchoring throughput, backups |

- **Model:** free tier (generous, drives adoption) → usage-based paid tiers (per-compile, per-query, per-GB-memory, per-1k-tx). Classic open-core / Vercel-style.
- **Why it works:** the people building agents want to ship, not run Postgres + indexers + RPC pools. You already host the IDE backend — this is the same muscle.

### Line 3 — Enterprise & custom (today + bespoke) 🏢
- **Support & SLAs** for teams running Mycelium in production.
- **Custom agent-swarm deployments** — build/operate bespoke agent economies for a customer (a marketplace, a DAO, a logistics swarm).
- **Security audits & certified contracts** — co-sell audited contract templates.
- **Priority:** opportunistic now, real revenue once you have logos.

### Line 4 — Ecosystem & grants (the bootstrap fuel, today) 🌱
- **SDF / ecosystem grants** fund the build to mainnet (this is what you're pitching for now).
- **Ecosystem partnerships / co-marketing** with Stellar projects that need agentic automation.
- **Not a long-term business** — it's the runway that gets you to Line 1 + 2 revenue. Be explicit with reviewers that grants bootstrap, settlement fees + managed infra sustain.

---

## 3. The revenue flywheel

```
   More agents register (free SDK)
            │
            ▼
   More jobs/deals posted ──► more settled volume ──► Line 1 settlement fees ▲
            │                                                   │
            ▼                                                   ▼
   More demand for fast discovery / memory / RPC ──► Line 2 managed infra ▲
            │                                                   │
            └──────────► reputation + network effects ◄─────────┘
                 (agents go where the other agents are)
```

The network effect is the moat: agents register where the *other* agents are (discovery + reputation live in your registry/indexer). Once a critical mass of agents and jobs is on Mycelium, settled volume compounds, and both revenue lines compound with it.

---

## 4. Unit economics sketch (illustrative — fill with real numbers as you get data)

- Say the network settles **$X/month** in agent-to-agent value through escrow.
- At a **0.5%** settlement fee → **$0.005·X/month** revenue, ~100% margin (collected on-chain, no marginal cost).
- Managed infra adds usage revenue on top (compiles, indexer queries, memory GB).
- **The lever:** every initiative (better SDK, indexer, memory, more templates) increases agents → increases settled volume → increases Line-1 revenue. Growth and revenue are the same motion.

> For the pitch, you don't need real revenue. You need to show the *model is sound and scales with the network*, and that grants bootstrap it to the point where settlement fees take over.

---

## 5. What to charge later vs. free now (adoption-first sequencing)

| Phase | Free | Paid |
|---|---|---|
| **Now (grant/testnet)** | Everything. Maximize agents + integrations. | Nothing — adoption is the only metric. |
| **Mainnet launch** | Full OSS stack, self-host everything, `mycelium pay`. | Settlement fee on escrow/job flows (Line 1); hosted indexer free tier. |
| **Scale** | Generous free tiers everywhere. | Managed infra usage tiers (Line 2); enterprise (Line 3). |

Charge **only** once there's real value flowing — never tax adoption. The settlement fee is invisible until agents are already transacting *because* the rails are useful.

---

## 6. How to present this in the pitch (slide 9 / `pitch/02` + `pitch/04`)

- **Headline:** *"We monetize the rails, not the road."*
- **Three lines, one sentence each:** settlement fee (grows with the economy) · managed infra (open-core SaaS) · enterprise/grants (bootstrap + bespoke).
- **The killer line:** *"Our revenue is a basis-point cut of the value agents settle through us — so we grow exactly as the agent economy on Stellar grows."*
- **Have ready:** the bypass objection answer (§2, Line 1) and the "grants bootstrap, settlement sustains" framing.

---

## 7. Relationship to the other docs
- Line 1 (settlement fee) is collected on the escrow path — the **`settlements` table in `01-offchain-indexer.md`** gives you the volume dashboard that proves traction.
- Line 2 managed services *are* the hosted **indexer (`01`)**, **memory (`02`)**, compile, and RPC.
- The cost structure that keeps margins ~100% on-chain is enabled by the **fee strategy in `04-scaling-to-millions.md`** (you don't eat users' gas — see that doc).
