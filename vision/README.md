# Mycelium — Vision & Architecture

Deep technical and strategic plans behind Mycelium as **agentic economic infrastructure on Stellar**. These are the documents to reference when an investor, grant reviewer, or new engineer asks "how does this actually scale / make money / persist?"

| Doc | Question it answers |
|---|---|
| [01 — Off-Chain Indexer](01-offchain-indexer.md) | How does agent discovery scale past the RPC's retention window to O(1), searchable lookups over the full history? (Ships **before** the grant pitch.) |
| [02 — Persistent Agent Memory](02-persistent-agent-memory.md) | Can agents have durable, portable, verifiable memory **without** putting (expensive, public) memory on-chain? |
| [03 — Business Model](03-business-model.md) | How does Mycelium earn? (Settlement fee + managed infra + enterprise + grants.) |
| [04 — Scaling to Millions](04-scaling-to-millions.md) | How do we run millions of agents on mainnet when we **can't** sponsor everyone's fees? |

## The three things these docs prove

1. **It scales** — everything that grows with usage (discovery, memory, throughput) lives off-chain and scales like cloud infra; only tiny, constant-size commitments touch the chain.
2. **We don't pay users' costs** — funding follows the value to the party that benefits (operator / platform / parent), using Stellar's reclaimable sponsored-reserve + fee-bump primitives. Mycelium is the rails, not the bank.
3. **There's a real business** — a basis-point fee on value settled through our escrow/job rails grows exactly as the agent economy grows, on top of an open-source, self-hostable core.

## How these relate to the repo today
- The **indexer** consumes events the contracts *already emit* (`agent_registered`, `job_posted`, `job_completed`, escrow settlements) — no new on-chain code.
- The **memory anchor** reuses DSL primitives already shipped in v0.1.0 (`require_auth`, `Bytes`, events, storage keys).
- The **settlement fee** routes through the existing `EscrowPaymentRouter` release/split path.

See also the `pitch/` folder for the grant-pitch script, deck, demo flow, and pre-recording build plan.
