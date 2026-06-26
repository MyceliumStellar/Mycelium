# Mycelium — Scaling to Millions of Agents

> **The questions you asked:**
> 1. *"How will we scale this for millions of agents?"*
> 2. *"We can't sponsor fees for mainnet — so how do new agents afford to exist?"*
> 3. *"For stateful agents, on-chain memory will be so costly — we can't put memory on chain, right?"* (answered fully in `02-persistent-agent-memory.md`; summarized here.)
>
> This doc tackles the **four real bottlenecks** between "works for 2 agents on testnet" and "works for millions on mainnet," and gives you the answer to each — including the one you're worried about: **you do NOT have to sponsor anyone's fees.**

---

## The four bottlenecks (and the honest status of each)

| # | Bottleneck | Without a fix | The fix | Cost to *you* |
|---|---|---|---|---|
| 1 | **Discovery** is an O(N) event-scan, retention-bounded | Can't list/search a large agent set | Off-chain indexer (`vision/01`) | You host it (or it's self-hosted) — **not** per-agent gas |
| 2 | **Account funding** — every agent needs an XLM reserve | "Who pays for a million wallets?" | Account abstraction patterns — you **don't** pay (see §2) | **Zero** if designed right |
| 3 | **Transaction throughput** — one key can't pipeline txns | Agents serialize, look flaky | Channel accounts + sequence manager | Minimal |
| 4 | **State/memory cost** — on-chain memory is metered | Cost explodes per stateful agent | Off-chain memory + tiny on-chain anchor (`vision/02`) | ~Constant per agent |

The throughline: **push volume off-chain (discovery, memory, indexing), keep only tiny commitments on-chain, and never make *yourself* the payer of users' fees.**

---

## 1. Discovery at scale → the indexer (summary; full design in `vision/01`)

Today `HiveClient.discover_agents` walks the RPC's `getEvents` over a bounded ledger window with a per-agent `resolve_agent` simulation. That's **O(N) RPC round-trips, retention-bounded, unsearchable** — fine for a demo, impossible for millions.

**Fix:** the off-chain indexer turns discovery into an **O(1) indexed database lookup over the complete history**, with capability/reputation/geo search. The chain stays the source of truth; the indexer is a verifiable cache anyone can run or self-host. **No new on-chain code** — the registry already emits the events.

→ Full design, schema, and build plan in **`vision/01-offchain-indexer.md`**.

---

## 2. Account funding at scale → "we can't sponsor fees" — and you DON'T have to

This is the concern you raised most directly, so here's the full answer.

### The problem
Every Stellar account needs a **minimum balance reserve** (a base reserve, currently ~1 XLM, ~0.5 XLM × entries) just to exist, plus tiny per-transaction fees. On **testnet**, Friendbot funds everyone for free (that's what `mycelium fund` uses). On **mainnet**, Friendbot doesn't exist. So: *"who funds a million agent wallets?"* You're right that **you sponsoring it yourself doesn't scale** — a million reserves is real money you don't have.

### The answer: you are NOT the payer. Pick the model per use-case.

There are **four** ways agents get funded on mainnet, and in **none of them do you, the protocol, eat the cost at scale**:

**(a) The agent's owner funds it (the default — and it's *fine*).**
An agent is deployed by *someone* — a developer, a company, a user. They fund their own agent's wallet, the same way you fund any wallet you create on mainnet. A reserve of ~1–2 XLM (cents to low dollars) is a trivial cost for *anyone deploying a real agent that handles money*. **You don't need to sponsor agents that are economically active** — they can afford their own reserve out of the value they transact. This alone answers "how do agents afford to exist": *their operator pays, just like any account.*

**(b) Fee-bump / fee-sponsorship — paid by whoever benefits, NOT by you.**
Stellar's **fee-bump transactions** and **sponsored reserves** let a *third party* pay another account's fees/reserve. The key insight: **the sponsor is whoever has a business reason to onboard that agent — not Mycelium.**
- A *platform built on Mycelium* (e.g. a marketplace) sponsors its own users' agent reserves as a customer-acquisition cost — their economics, not yours.
- An *employer agent* sponsors the reserves of worker agents it spawns (it profits from their work).
- Mycelium *ships the SDK support* for sponsorship (so any platform can do it in one call) but is not itself the bank. **You sell the shovel; you don't dig.**

**(c) Sponsored reserves with reclaim — the reserve is recoverable.**
Sponsored reserves on Stellar can be **revoked/reclaimed** when the sponsored entry is removed. So a platform that sponsors an agent's reserve gets it *back* when the agent is decommissioned. The "cost" is a recoverable deposit, not a sunk expense — which is why even at scale it's bounded, and again, it's the *platform's* deposit, not yours.

**(d) Stateless / ephemeral agents that never hold an account.**
Many agents don't need their own funded account at all (see §3 channel accounts + §4 stateless memory). An agent that only ever *acts on behalf of* a funded parent (via fee-bump + auth) can be **account-light** — it has a keypair for signing but its operations are paid by the parent. Spin up a million of these for ~free because most of them never create a reserved on-chain account.

### What Mycelium actually builds here (the SDK surface)
You don't fund anyone. You **make funding-by-the-right-party a one-liner**:
- `ctx.sponsor_reserve(child_address)` → builds the sponsored-reserve op (parent pays).
- Fee-bump helpers so a paymaster/parent wraps a child's tx and pays the fee.
- `mycelium fund` stays testnet-only (Friendbot); on mainnet it points the operator at "fund this address" / sponsorship flows.

### The crisp pitch answer
> "We don't sponsor a million wallets — that wouldn't scale and it's not our cost to bear. Funding follows the value: an agent's operator funds it, or a platform built on Mycelium sponsors its own users via Stellar's fee-bump and sponsored-reserve primitives — which are *reclaimable deposits*, not sunk costs. Most lightweight agents never hold a reserved account at all; they act on behalf of a funded parent. **We ship the SDK so the right party can pay in one call — we're the rails, not the bank.**"

---

## 3. Transaction throughput → channel accounts + a sequence manager

### The problem
A Stellar account has **one sequence number**. If an agent fires many transactions concurrently from the same key, they collide on the sequence number and fail. A busy agent (or a swarm coordinating fast) serializes and looks flaky — and your `mycelium_sdk/rpc.py` already has to fight `TRY_AGAIN_LATER`.

### The fix
- **Channel accounts:** a pool of lightweight accounts whose *only* job is to carry the sequence number for a transaction, while the agent's main account remains the *source of funds / authority*. N channels → N concurrent in-flight transactions from one logical agent. Standard Stellar high-throughput pattern.
- **Local sequence manager:** an SDK component that (a) caches and increments sequence numbers locally instead of round-tripping to the RPC for each, (b) hands out channel accounts from a pool, (c) enqueues and retries on collision. This eliminates the GIL-bound, one-at-a-time submission bottleneck the ROADMAP flags.
- **Async RPC + connection pooling:** native async submission so high-concurrency agent loops don't block.

### Cost to you: minimal
Channel accounts need tiny reserves too — but they're **per-busy-agent infrastructure**, pooled and reused, funded by the same party that funds the agent (§2). Most agents are low-throughput and need zero channels.

---

## 4. State/memory cost → off-chain memory, on-chain anchor (summary; full design in `vision/02`)

You're right: **you cannot put agent memory on-chain** — it's metered, public, rent-bearing, and memory is large/mutable/private. The fix is the *commitment* pattern:

- **Off-chain:** all the actual memory (vectors, logs, docs). App-speed reads/writes, ~free.
- **On-chain:** a tiny `MemoryAnchor` per agent — `(memory_root, uri, version, acl)`, a few hundred bytes, written **lazily** at checkpoints. Constant, tiny footprint *regardless of memory size*.

So a stateful agent with gigabytes of memory still costs ~constant on-chain. A million stateful agents = a million tiny, infrequently-updated anchors, not a million on-chain databases. **Anchoring frequency is the cost knob** — anchor at job-completion + heartbeat, not per-thought.

→ Full design, contract, SDK interface, verification flow in **`vision/02-persistent-agent-memory.md`**.

---

## 5. Stateless vs stateful agents (the two scale modes)

| Mode | How it scales | When to use |
|---|---|---|
| **Stateless / serverless** | Rehydrates memory from its on-chain anchor each run; may not even hold a reserved account (acts via parent). Spin up ~unbounded numbers cheaply. | High-fan-out swarms, ephemeral workers, serverless deployments |
| **Stateful** | Holds warm memory + warm RPC connections + channel-account pool for low latency; anchors periodically. | Long-running, latency-sensitive, high-throughput agents |

Same SDK, same contracts — the operator picks the mode. This is what lets the *same* infrastructure serve both a million cheap ephemeral workers and a thousand always-on heavy agents.

---

## 6. Putting it together: the scaling story in one diagram

```
                       ┌─────────────────────────────────────────────┐
                       │             OFF-CHAIN (scales like SaaS)     │
   millions of agents  │  • Indexer: O(1) discovery + search          │
        ───────────────┤  • Memory: vectors/logs/docs (per-agent)     │
                       │  • Sequence mgr + channel pool (throughput)  │
                       └───────────────────┬─────────────────────────┘
                                           │ tiny commitments only
                                           ▼
                       ┌─────────────────────────────────────────────┐
                       │   ON-CHAIN (Stellar — tiny, constant per ag.) │
                       │  • Registry entry (name→identity)             │
                       │  • Memory anchor (root,uri,ver,acl)           │
                       │  • Escrow/job settlement (the value-flow)     │
                       │  funding: operator / platform / parent —      │
                       │           NEVER Mycelium itself               │
                       └─────────────────────────────────────────────┘
```

**The invariant:** everything that grows with usage (search indexes, memory, throughput state) lives **off-chain** where it scales like ordinary cloud infra. Only **tiny, constant-size, value-bearing commitments** touch the chain. And **you never pay users' on-chain costs** — funding follows the value to the party that benefits.

---

## 7. Build sequencing toward mainnet scale

| Phase | Ships |
|---|---|
| **Pre-grant (now)** | Indexer (`vision/01`) — the headline scaling proof. |
| **Mainnet hardening** | Sponsorship/fee-bump SDK helpers (§2); auth hardening on Job Boards (flagged risk); security audit. |
| **Post-funding** | Sequence manager + channel-account pool (§3); async RPC. Memory anchoring + Supermemory (`vision/02`). |
| **Scale** | Indexer HA, partitioning; stateless-agent tooling; multi-network. |

---

## 8. The three crisp answers (memorize for the pitch Q&A)

1. **"Millions of agents — how?"** → "Everything that grows with scale lives off-chain: O(1) discovery via our indexer, off-chain memory with tiny on-chain anchors, and a channel-account/sequence layer for throughput. The chain only holds tiny, constant-size commitments and the actual value settlement. That scales like cloud infra, not like a ledger."
2. **"You can't sponsor a million wallets — who pays?"** → "Not us. Funding follows the value — the agent's operator or the platform built on Mycelium pays, using Stellar's fee-bump and *reclaimable* sponsored-reserve primitives. Most lightweight agents never hold a reserved account; they act for a funded parent. We ship the SDK so the right party pays in one call. We're the rails, not the bank."
3. **"On-chain memory is too costly."** → "Correct — so we don't. Memory lives off-chain; only a few-hundred-byte anchor (hash + URI + version + ACL) goes on-chain, written lazily at checkpoints. Constant tiny footprint no matter how much an agent remembers."
