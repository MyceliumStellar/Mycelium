# Mycelium — Pitch-Readiness Plan (Before the Recording)

> **Goal:** Maximize impact of a 90-second **investor** pitch with the *least* new work. You're two people — focus is the weapon.
> **Rule:** Don't build new features for the pitch. **Polish what proves the story, and pre-empt the two attacks** (will this scale, and how do you make money). The product is already shipped — the indexer and persistent memory are done, and mainnet is funded. The pitch's job is to *show* that, tightly.

---

## Priority 0 — The 90-second demo must be bulletproof (non-negotiable, do first)

The demo *is* the pitch. At 90 seconds there's no room to recover from a stall.

- [ ] **Run the 90-second cut green, end-to-end, in a clean session** — indexer discovery → `deal open` → `deal release` (with the fee routing) → explorer. See `03-demo-flow.md`.
- [ ] **Confirm the protocol fee actually routes and is visible** in the settlement output — this is the beat that sells the business model. If it isn't visible, make it visible (log line / status field).
- [ ] **Run one swarm split (B2) and one memory rehydrate (Path C) green** as optional add-on beats; save the tx hashes.
- [ ] **Record a clean screen-capture of the cut** off-camera, in case the live run hiccups during recording.
- [ ] **Verify each tx hash loads on `stellar.expert/explorer/testnet`.** Bookmark them.

> If only ONE thing works before recording, it's this. A working on-chain demo where the fee fires beats every slide.

---

## Priority 1 — Pre-empt "will this scale / is it real?" (worry #1)

For infra, traction = working on-chain code + verifiable usage, not MAU. And the scaling answer is now *shipped*, not promised.

- [ ] **A one-screen "Proof on-chain" reference** (`pitch/proof.md` or a slide): the Hive Registry contract ID, the two agent contract IDs, and real tx hashes (discovery, escrow lock, proof release **with fee**, swarm split, memory anchor) — each a clickable explorer link.
- [ ] **Lead the scaling answer with shipped facts:** the **off-chain indexer** makes discovery O(1) and searchable over full history; **persistent memory** keeps per-agent on-chain footprint tiny and constant. These are *done*, not roadmap — say so.
- [ ] **PyPI download / install count** if non-trivial — even modest numbers say "real package, real users."
- [ ] **Reframe the language everywhere:** *"live on-chain,"* *"funded to mainnet,"* *"open-source, on PyPI."* That **is** the traction story for infra.
- [ ] **(Optional, high-leverage) One design-partner quote** — anyone building an agent who'll say "we'd use this." One quote kills the traction objection.

---

## Priority 2 — Make the business model land in the demo (worry #2)

Investors underwrite the take-rate. You don't need revenue yet — you need to *show the model executing on-chain*.

**The model (one breath, after the fee fires in the demo):**

1. **Protocol take-rate (primary).** A basis-point fee on value settled through escrow/job-boards. ~100% margin, collected on-chain. *Revenue = settled volume × fee → we grow as the agent economy grows.* **They just saw it fire.**
2. **Managed infrastructure (open-core SaaS).** Hosted indexer, compile, RPC, agent memory for teams who don't self-host.

- [ ] **Make the fee visible in the settlement output** (covered in P0 — it's that important).
- [ ] **One-line answer ready:** *"We take a basis-point cut of every dollar agents settle through us — so we grow exactly as the agent economy grows."*
- [ ] **Bypass follow-up ready:** *"Why won't agents settle peer-to-peer?"* → *the value is the coordination + trust layer (discovery, escrow, proof, swarm split). A raw `mycelium pay` is free; the fee buys trustless settlement. Bypassing means rebuilding it — and the fee is small enough that it never pays to."*

---

## Priority 3 — Repo / first impression (investors WILL open GitHub)

The repo is your live resume. Make the first impression match "shipped, funded, real."

- [ ] **Fix any README typos** (`Launcehs`→`Launches`, `Broadcats`→`Broadcasts`, etc.) — sloppiness reads as immaturity.
- [ ] **Lead the README hero with the agent economy + the settlement layer**, compiler as the enabler. Add up top: *"Mycelium is the settlement layer for the agent economy on Stellar."*
- [ ] **Add a "Live on-chain — verify it yourself" section** near the top: contract IDs + explorer links. Front-load the proof.
- [ ] **Reflect what's shipped:** make sure the README/README-of-`vision/` shows the indexer and persistent memory as *done*, and mainnet as *funded* — don't let stale "roadmap" language undersell you.
- [ ] **Decide on `pitch/` visibility** — you may not want investors reading internal pitch notes. Gitignore `pitch/` if the repo is public, or keep it — your call.

---

## Priority 4 — Be ready for the hard questions

Investors reward candor. Have crisp answers; don't hide the gaps.

- [ ] **"What's NOT ready?"** → mainnet deployment itself (testnet-validated, path funded, hardening in progress); the flagged auth-hardening on Job Boards (known, scoped, on the mainnet checklist); broader compiler coverage (core DSL solid; long-tail fixtures climbing).
- [ ] **"Why Stellar?"** → micropayment economics make a basis-point fee viable; stablecoins, ~5s finality, fee-sponsorship + channel accounts for million-agent scale. A take-rate on micropayments only works on a chain this cheap.
- [ ] **"What stops a big lab from building this?"** → the moat is the *network of registered agents + reputation in our registry/indexer*, the Python wedge, and an already-shipped, Stellar-native head start.
- [ ] **"Two people — can you execute?"** → "We already shipped a compiler, SDK, CLI, IDE, on-chain economy, indexer, and agent memory, and got mainnet funded. Velocity is the proof."
- [ ] **"How big can this get?"** → revenue tracks total agent-to-agent settled volume across the economy — uncapped as the agent population scales into the billions.

---

## What's DONE (say "shipped," not "coming")

These were roadmap in the grant pitch. They're built now — lean on them as proof of velocity:

- ✅ **Off-chain indexer** — O(1), searchable, full-history discovery (`vision/01`)
- ✅ **Persistent agent memory** — off-chain memory, on-chain commitment, portable + verifiable (`vision/02`)
- ✅ **Mainnet funding** — secured from the Stellar Development Foundation

## Explicitly DO NOT build before the recording

Real roadmap items that won't move an investor decision and will eat demo-polish time. Mention as "next," don't build now:

- ❌ Full mainnet deployment + audit (in progress — don't block the pitch on it)
- ❌ Capability marketplace / multi-sig swarm orchestration
- ❌ Multiplayer IDE, Monaco IntelliSense, fuzzer, SSA gas optimizer
- ❌ Broader compiler fixture coverage push

> Every hour here is an hour not spent making the 90-second demo flawless. **The demo is the pitch.**

---

## Suggested time-box (if you have ~3 days)

| Day | Focus |
|---|---|
| **Day 1** | Priority 0: get the 90-second cut green; confirm the fee is visible; record clean capture; save tx hashes. |
| **Day 2** | Priority 1 + 2: build the "proof on-chain" reference; rehearse the business-model line + objections; README polish. |
| **Day 3** | Build the 8-slide deck from `02-slide-deck-outline.md`; rehearse the script (`01`) out loud 5×; time it to 1:30; record, multiple takes, cut the best. |

---

## The single highest-leverage action

If you do nothing else: **record the 90-second cut clean — agent discovers agent via the indexer, contracts, settles on proof, the protocol fee routes to you, here are the tx hashes — verifiable on stellar.expert.** That one artifact, narrated well, is worth more than the entire deck — because it proves the thesis *and the business* are real on Stellar, today.
