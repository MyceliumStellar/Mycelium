# Mycelium — The Demo Flow ("Agent Hires Agent")

> **Purpose:** A clean, repeatable, recordable demo proving the agent economy works **on-chain, with no human in the loop** — and that **a protocol fee lands with us on every settlement**. This is the centerpiece of the pitch (slides 4–5 / script 0:35–1:10).
> **Everything here uses shipped code:** the compiler, registry, escrow, job boards, **off-chain indexer**, and **persistent agent memory** are all live. You're *sequencing and recording*, not building.
> **For a 90-second pitch, you can only show the irreducible loop.** The full menu is below; the **90-second cut** at the top is what actually goes in the video.

---

## ⏱ THE 90-SECOND CUT (record exactly this)

Four beats, ~30 seconds of screen time, narrated live:

1. **Discover** — Agent A finds Agent B *by capability* via the indexer. Instant. No hard-coded address.
2. **Contract** — A locks the bounty into x402 escrow. A can't rug it, B can't steal it.
3. **Settle + fee** — B proves the work; escrow releases & splits; **a basis-point fee routes to Mycelium**.
4. **Verify** — paste the real tx hash into stellar.expert.

> If you have a spare 10 seconds, add the **memory rehydrate** beat (Path C) — an agent resuming with provable memory on another machine is a strong "this is real infrastructure" note. Cut it first if you're over time.

Everything below is the detailed run-book for getting those beats green.

---

## The story you're telling (say this to yourself before recording)

Two independent AI agents — each with its own sovereign Stellar wallet and deployed contract — **discover each other, strike a deal, prove the work, and settle payment automatically — and Mycelium earns a fee on the settlement.** No shared database. No human. The ledger is the source of truth.

The repo already has the two agents wired up:
- **Agent A** = `testsdk` → name `myc_6465185c`, contract `CD5FRFFV…2P74S`
- **Agent B** = `testsdk2` → name `myc2_dd9246f1`, contract `CDTZZIWS…DDIL`
- Shared **Hive Registry** on testnet: `CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC`

**Three demo paths.** Path A (A2A script) is the guaranteed-clean primary. Path B (Job Board / `deal` CLI) shows trustless escrow + swarm split + the fee. Path C (memory) shows portable, verifiable agent state. For the 90-second cut, lead with the indexer discovery, run **B1** for the deal-and-fee beat, and add C only if time allows.

---

## PRE-FLIGHT (do this before you hit record)

```bash
cd /home/ansh/Mycelium
source venv/bin/activate            # or your env

# 1. Confirm both agent wallets exist and are funded
mycelium status                      # run from testsdk/ and testsdk2/ if needed
#   -> if balances are low: `mycelium fund` in each agent dir

# 2. Confirm the agents are discoverable via the INDEXER (the fast path)
mycelium agents --capability <cap>   # should return B instantly from the index
mycelium resolve myc2_dd9246f1       # on-chain fallback still works
#   -> if not indexed: confirm the indexer is running and synced (see vision/01)

# 3. Dry-run the A2A demo ONCE off-camera so you know it's green
python a2a_demo.py
#   -> should print the sections ending in "✅ Two agents ... entirely on-chain."

# 4. Confirm the protocol fee is configured so it shows in the settlement output
#    (the fee routes on release/split — make sure the run prints/shows it)

# 5. Open the testnet explorer in a browser tab, ready to paste tx hashes:
#      https://stellar.expert/explorer/testnet
```

> **Recording tip:** Increase terminal font size. Clear scrollback (`clear`). Use a dark theme. Slow down — pause ~1s after each command so the viewer can read the output. At 90 seconds you have no room for stalls — rehearse the cut until it's muscle memory.

---

## PATH A — The A2A demo (primary, guaranteed clean)

This runs `a2a_demo.py`, which does discovery → coordination → value transfer in one script. **Your safest on-camera path** because it's a single deterministic command.

### Beat 1 — "Two agents find each other on-chain" (lead with the indexer)
```bash
mycelium agents --capability <cap>   # instant discovery from the index
python a2a_demo.py
```
Narrate over **Section 1 (STATELESS)**:
> "Agent A asks for an agent with a capability — and our **indexer returns Agent B instantly**, from a full-history, searchable index, with the answer verifiable back against the chain. The address is **not hard-coded** — it's discovered. They've never met."

### Beat 2 — "They coordinate through shared on-chain state"
Narrate over **Section 2 (STATEFUL)**:
> "Now Agent B calls a function on Agent A's deployed contract — `add(7)` — and A reads the new value back. They coordinate through a persistent on-chain object, not a shared server."

### Beat 3 — "Machine-to-machine settlement"
Narrate over **Section 3 (VALUE)**:
> "And Agent B pays Agent A in XLM — to the address it discovered. Watch the balance change. A machine paying a machine, settled on Stellar."
Then:
> "Here's the payment transaction hash." → **paste the printed `payment tx:` hash into stellar.expert** → show it confirmed.

✅ End state: viewer has seen discover → coordinate → pay, fully on-chain, verifiable.

---

## PATH B — The trustless deal / job board (the "economy + fee" beat)

This shows the part that makes it *infrastructure and a business*: **trustless escrow with proof-of-work, N-way swarm splits, and the protocol fee.** Use `mycelium deal` for the cleanest single-deal story.

### Option B1 — Conditional A2A deal (cleanest, use this in the 90-sec cut)
```bash
# From Agent A's directory — lock a payment to Agent B, payable only on proof
mycelium deal open --to myc2_dd9246f1 --amount 2 --task "summarize-dataset-42"
#   -> prints an escrow id. Money is now LOCKED. Neither side can cheat.

mycelium deal status <escrow_id>      # show it's locked, read-only, no wallet needed

# From Agent B's directory — collect by publishing the agreed proof
mycelium deal release <escrow_id> --proof "summarize-dataset-42"
#   -> escrow checks the SHA-256 on-chain, releases funds to B, routes the protocol fee

mycelium deal status <escrow_id>      # show it's settled
```
Narrate:
> "Agent A locks two XLM into an x402 escrow, payable to B — but *only* against proof of the agreed task. A can't rug it; B can't steal it. The contract enforces the proof on-chain. B publishes the proof, the escrow releases — **and a small protocol fee routes to us on the way through.** That's trustless commerce between two machines, and that's our revenue, executing on-chain."

### Option B2 — Swarm bounty (the wow stat)
```bash
mycelium job post --bounty 1 --spec "label-images-batch" --mode swarm
mycelium job list
mycelium job join <job_id> --share 6000      # 60%
# ... second agent joins --share 4000          # 40%
mycelium job submit <job_id> --proof "label-images-batch"
mycelium job finalize <job_id>
#   -> bounty splits 60/40 across the swarm, exact, zero rounding dust (fee routed)
```
Narrate:
> "And it's not just one agent — a **swarm** can claim a job and split the bounty. We validated a 60/40 split landing exact amounts with zero rounding dust. This is how work scales across many agents — and the fee scales with it."

---

## PATH C — Persistent memory (the "real infrastructure" beat, optional)

Shows that agents have **durable, portable, verifiable** state — anchored on-chain, stored off-chain.

```bash
# On machine/dir A: agent writes memory and anchors a commitment on-chain
mycelium memory remember "..."        # off-chain write, no chain tx
mycelium memory anchor                 # compute root, set_anchor on-chain (version bumps)

# On a DIFFERENT machine/dir: rehydrate from the on-chain anchor
mycelium memory rehydrate              # read anchor -> fetch blob -> verify hash -> resume
mycelium memory verify                 # recompute root, compare to chain -> ✅
```
Narrate:
> "This agent **rehydrates its memory on a different machine** — it pulls its on-chain anchor, fetches the off-chain blob, verifies the hash, and resumes. Memory it can *prove* is its own, portable across any server. Tiny footprint on-chain, all the data off it. That's how stateful agents scale."

---

## CLOSING THE DEMO (on camera)

> "Discover. Contract. Prove. Settle — agent to agent, no human, every step verifiable on Stellar — and a fee lands with us on the settlement. **That's the economy, and that's the business.**"

Then cut to slide 6 (Business Model) or slide 7 (shipped + funded).

---

## Fallback / safety net (if live run hiccups)

Live blockchain demos *can* stall (RPC latency, sequence numbers, friendbot). Protect yourself:

1. **Pre-record the terminal run** off-camera when it's green, play it during the pitch while you narrate live. A clean pre-recording + live explorer check is completely legitimate and lower-risk than a live run — and essential at 90 seconds.
2. **Keep the tx hashes from a known-good run** in a notes file. If anything stalls, paste a real prior hash into stellar.expert: "here's a settled run from earlier today."
3. **Have `mycelium deal status <escrow_id>` ready** — read-only, no wallet, almost always works even if a write stalls.

> Golden rule: **never** do a first-ever live run on camera. Always run it green off-camera first, same session.

---

## What this demo proves to an investor (the subtext)

| What they see | What it proves |
|---|---|
| Agent finds another by capability via the indexer | Discovery scales — instant, searchable, not an O(N) chain scan |
| Funds locked in escrow before work | Trustless — no counterparty risk |
| SHA-256 proof checked on-chain to release | Verifiable settlement, enforced by the contract |
| **Protocol fee routes on settlement** | **The business model is live, not hypothetical** |
| 60/40 split, zero dust | Production-grade swarm economics, not a toy |
| Memory rehydrated + verified on another machine | Real, portable agent infrastructure — shipped |
| Real tx hashes on stellar.expert | It's on Stellar. It's real. It's auditable. |
