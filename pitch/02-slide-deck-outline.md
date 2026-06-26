# Mycelium — Slide Deck Outline (Investor / VC)

> **8 slides, ~90-second pitch.** Each slide: **headline** (read in 2 sec) + **body** (what you say/show) + **why it's here**.
> Design principle: one idea per slide, demo is the centerpiece (slides 4–5), lead with the agent economy and the take-rate — not the compiler.
> **This is a product pitch, not a fundraise ask.** No dollar figure, no use-of-funds slide. The working on-chain product and the revenue model are the pitch. Mainnet is already funded; the indexer and agent memory are shipped.

---

### Slide 1 — Title / Thesis
- **Headline:** `Mycelium — The Settlement Layer for the Agent Economy`
- **Subhead:** *Agents that discover each other, hire each other, and pay each other — no human in the loop. Live on-chain.*
- **Show:** Logo, `mycelium.isriz.xyz`, "Live on Stellar · Funded to mainnet" badge, two names (Ansh & Rohan).
- **Why:** Frames it as *the rails of a market* (investable category), not a dev tool. First impression decides how they read everything after.

---

### Slide 2 — The Problem & The Market
- **Headline:** `Billions of agents are coming. They have no economy.`
- **Body:** Soon agents won't just talk — they'll **hire and pay each other**. But there's nowhere for those transactions to settle: no way to discover a counterparty, settle a payment they trust, or prove who did what. The demand is here; the infrastructure underneath it isn't. *Whoever ships those rails owns the settlement layer.*
- **Why:** Names the gap as a market someone will own. The VC needs to feel the missing infrastructure and the size of the prize.

---

### Slide 3 — The Product
- **Headline:** `One Python-first stack: from code to on-chain economy.`
- **Body (layered, lead with the economy):**
  - **Economic layer** *(the company)* — Hive Registry (discovery) · x402 escrow (trustless pay) · Job Boards (post/claim/prove/split) · **off-chain indexer** (instant, searchable discovery) · **persistent agent memory** (portable, verifiable).
  - **Compiler + SDK + CLI** *(the wedge)* — Python → Soroban WASM, sovereign wallets, agent loop. No Rust.
- **Show:** Clean stack diagram, economic layer highlighted. Tag indexer + memory as **shipped**.
- **Why:** Shows a coherent platform that's *already complete*, not a roadmap. The wedge brings Python devs on-chain; the economy is where value flows.

---

### Slide 4 — DEMO (part 1): Discover & contract
- **Headline:** `Two agents. Two wallets. They find each other on-chain.`
- **Show:** Screen recording — Agent A finds Agent B *by capability* via the **on-chain indexer** (instant, no hard-coded address) → A posts a task and **locks the bounty in x402 escrow**.
- **Why:** The proof. Searchable discovery + trustless lock = the two hardest primitives, shown working live.

---

### Slide 5 — DEMO (part 2): Settle → fee → receipts
- **Headline:** `Proof in → payment out → a fee lands with us.`
- **Show:** Agent B submits proof → escrow **releases & splits** (exact swarm split, zero dust) → **a basis-point protocol fee routes to Mycelium** → **explorer with real tx hashes**.
- **Why:** Closes the loop *and* shows the business model executing on-chain. "Don't trust us — check the chain." This is the slide that converts.

---

### Slide 6 — Business Model
- **Headline:** `We monetize the rails, not the road.`
- **Body (lead with the take-rate you just saw fire):**
  1. **Protocol take-rate** *(primary)* — a basis-point fee on every settlement through escrow/job-boards. ~100% margin, collected on-chain. **Revenue = settled volume × fee → we grow as the economy grows.**
  2. **Managed infra** *(open-core SaaS)* — hosted indexer, compile, RPC, agent memory for teams who don't self-host.
- **Why:** This is what a VC underwrites. Revenue scales with the network, not with seats. The demo already proved it's real.

---

### Slide 7 — Traction & Why Now
- **Headline:** `Shipped, on-chain, and funded to mainnet.`
- **Body:**
  - ✅ Compiler, SDK, 18-cmd CLI, Web IDE — open-source, on PyPI
  - ✅ Registry · x402 escrow · Job Boards — validated E2E on-chain
  - ✅ **Off-chain indexer + persistent agent memory — shipped**
  - ✅ **Mainnet path funded by the Stellar Development Foundation**
  - Why now: 2025–26 made *agents paying agents* a real category (x402, agentic commerce). Thesis proven; we're already on the rails.
- **Why:** Neutralizes "no traction" and "too early" in one slide. For infra, working on-chain code + external funding *is* the traction.

---

### Slide 8 — Team & Close
- **Headline:** `Two builders. A full stack, shipped. Come build the layer.`
- **Body:** Ansh & Rohan — a compiler, SDK, CLI, IDE, on-chain economy, indexer, and agent memory, built by two people and live on-chain. Velocity is the signal.
- **Close line:** *"Mycelium is the settlement layer for the agentic internet — it's live, it's funded to mainnet, and it earns as the network grows. Come build it with us."*
- **Show:** Logo · `mycelium.isriz.xyz` · GitHub · PyPI.
- **Why:** Reframes a small team as capital-efficient velocity, and closes on an invitation to the category — not a dollar ask.

---

## Deck design notes
- **Color/feel:** Stellar black + a Mycelium accent. Code/terminal screenshots carry the credibility — keep text minimal.
- **Slide discipline:** 8 max for 90 sec (~11 sec/slide; demo slides 4–5 get the most air). If you run long, merge 2+3.
- **The two slides that win it:** 4–5 (the live demo where the fee fires) and 7 (shipped + funded). Everything else supports those.
- **Every claim verifiable.** Put the Hive Registry contract ID and tx hashes on-screen.
- **What changed from the grant deck:** dropped the dedicated Why-Stellar, Roadmap, and Ask slides (12→8); indexer + memory moved from roadmap to shipped; the ask became an invitation; the fee now appears *in the demo*, not just on a model slide.
