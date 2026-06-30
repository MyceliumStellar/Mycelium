"""
JobBoard — the Sovereign Job Boards contract for Mycelium agents.

A poster publishes a task as a **rubric** (a structured set of acceptance
criteria — see `PROOF_SYSTEM.md`) identified by `rubric_uri` + its SHA-256
`rubric_hash`, with a bounty locked off-chain into an `Escrow` instance
(`escrow_contract.py`). Agents either self-`claim_job` (single mode) or
`join_swarm` with an agreed bounty share (swarm mode).

The claimant does the work and publishes an **evidence bundle** — the real
deliverable, not the spec echoed back — via `submit_evidence`, anchoring its
`evidence_root` on-chain. A `judge` (fixed per job) evaluates the bundle against
the rubric off-chain and records the outcome with `record_verdict`. On a pass the
SDK has the judge release the escrow (single payout or N-way swarm split). The
poster closes the record with `finalize`.

This is the validity layer that replaces the old `SHA256(proof) == spec_hash`
gate: that only proved a claimant could echo the agreed bytes, never that the
work met the rubric. Now release follows a judge's verdict.

The escrow holds the funds and enforces judge-authorized + balanced release, so
this contract stays free of cross-contract calls: it is the discovery +
coordination ledger, emitting `job_posted` / `job_claimed` / `swarm_joined` /
`job_submitted` / `job_verified` / `job_completed` events for the off-chain
indexer and `/jobs` UI.

Authored in the Mycelium DSL and compiled with this repo's own compiler:

    python -m mycelium_compiler.main job_board_contract.py -o build/job_board.wasm

Deploy once per network and record the contract id in `mycelium.toml`
(`[jobs].board_address`). The SDK's `JobBoardClient` drives every external here.
"""

from mycelium import (
    contract, external, view,
    Address, U64, U32, I128, Bytes, Bool, Map, Vec, Env, Symbol,
)


class ContractError:
    NOT_FOUND = 1
    NOT_OPEN = 2
    NOT_POSTER = 3
    INVALID_PROOF = 4
    NOT_SUBMITTED = 5
    BAD_SHARE = 6
    NOT_CLAIMANT = 7
    NOT_JUDGE = 8
    NOT_VERIFIED = 9


@contract
class JobBoard:
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def post_job(
        self,
        poster: Address,
        title: Bytes,
        description: Bytes,
        spec: Bytes,
        rubric_hash: Bytes,
        bounty: I128,
        token: Address,
        mode: Symbol,
        escrow: Address,
        judge: Address,
        deadline: U64,
    ) -> U64:
        """
        Record a new job and return its id. The job is self-describing on-chain:
        `title` (heading), `description`, and `spec` (the full acceptance rubric
        JSON — checks + their weights + the chosen judge panel) are stored so
        anyone can read the bounty's details straight from this contract via
        `get_job`, with no off-chain dependency. `rubric_hash` is the SHA-256 of
        `spec` for integrity.

        The bounty is expected to already be locked in the `escrow` instance (the
        SDK deploys + locks it first, naming the same `judge` as the release
        authority). `mode` is the symbol `single` or `swarm`.
        """
        poster.require_auth()

        job_id = self.storage.get("job_count", U64(0)) + U64(1)
        self.storage.set("job_count", job_id)

        jid = str(job_id)
        self.storage.set("poster:" + jid, poster)
        self.storage.set("title:" + jid, title)
        self.storage.set("description:" + jid, description)
        self.storage.set("spec:" + jid, spec)
        self.storage.set("rubric_hash:" + jid, rubric_hash)
        self.storage.set("bounty:" + jid, bounty)
        self.storage.set("token:" + jid, token)
        self.storage.set("mode:" + jid, mode)
        self.storage.set("escrow:" + jid, escrow)
        self.storage.set("judge:" + jid, judge)
        self.storage.set("deadline:" + jid, deadline)
        self.storage.set("status:" + jid, Symbol("open"))

        self.env.emit_event("job_posted", {"job_id": job_id, "poster": poster, "bounty": bounty})
        return job_id

    @external
    def claim_job(self, agent: Address, job_id: U64) -> Bool:
        """Single-mode self-claim. Reverts unless the job is open."""
        agent.require_auth()
        jid = str(job_id)
        if self.storage.get("status:" + jid, Symbol("none")) != Symbol("open"):
            raise ContractError.NOT_OPEN
        self.storage.set("agent:" + jid, agent)
        self.storage.set("status:" + jid, Symbol("claimed"))
        self.env.emit_event("job_claimed", {"job_id": job_id, "agent": agent})
        return True

    @external
    def assign_agent(self, job_id: U64, agent: Address) -> Bool:
        """Poster-side assignment of a specific agent to an open job."""
        jid = str(job_id)
        poster = self.storage.get("poster:" + jid)
        poster.require_auth()
        if self.storage.get("status:" + jid, Symbol("none")) != Symbol("open"):
            raise ContractError.NOT_OPEN
        self.storage.set("agent:" + jid, agent)
        self.storage.set("status:" + jid, Symbol("claimed"))
        self.env.emit_event("job_claimed", {"job_id": job_id, "agent": agent})
        return True

    @external
    def join_swarm(self, agent: Address, job_id: U64, capability_tag: Bytes, share_bps: U32) -> Bool:
        """
        Join a swarm job with an agreed bounty share (basis points). Shares across
        all members are expected to sum to 10000; the balanced split is enforced
        on release by the escrow. Records the member and moves the job to claimed.
        """
        agent.require_auth()
        jid = str(job_id)
        status = self.storage.get("status:" + jid, Symbol("none"))
        if status != Symbol("open") and status != Symbol("claimed"):
            raise ContractError.NOT_OPEN
        if share_bps > U32(10000):
            raise ContractError.BAD_SHARE

        members = self.storage.get("members:" + jid, Vec())
        shares = self.storage.get("shares:" + jid, Vec())
        members.append(agent)
        shares.append(share_bps)
        self.storage.set("members:" + jid, members)
        self.storage.set("shares:" + jid, shares)
        self.storage.set("status:" + jid, Symbol("claimed"))

        self.env.emit_event("swarm_joined", {"job_id": job_id, "agent": agent, "share": share_bps})
        return True

    @external
    def submit_evidence(self, submitter: Address, job_id: U64, evidence_root: Bytes, evidence_uri: Bytes) -> Bool:
        """
        Anchor the claimant's evidence on-chain: `evidence_root` is the 32-byte
        commitment to the off-chain bundle (the real deliverable + per-check
        claims + provenance), and `evidence_uri` is the pointer to where that
        bundle can be fetched and verified against the root. The bulk deliverable
        stays off-chain (cheap), but its commitment and locator are on-chain, so
        the proof is publicly discoverable and tamper-evident — not a bare hash
        with no way to find what it commits to.

        Unlike the old `submit_proof`, there is NO hash-check against the rubric —
        the rubric is satisfied by a judge's verdict, not by echoing bytes.
        Recording evidence opens the verification round. Only the recorded
        claimant may submit: the single-mode `agent:` or a swarm `members:` entry.
        """
        submitter.require_auth()
        jid = str(job_id)

        mode = self.storage.get("mode:" + jid, Symbol("single"))
        if mode == Symbol("swarm"):
            members = self.storage.get("members:" + jid, Vec())
            is_member = False
            n = len(members)
            for i in range(n):
                if members[i] == submitter:
                    is_member = True
            if not is_member:
                raise ContractError.NOT_CLAIMANT
        else:
            agent = self.storage.get("agent:" + jid, self.storage.get("poster:" + jid))
            if agent != submitter:
                raise ContractError.NOT_CLAIMANT

        self.storage.set("evidence_root:" + jid, evidence_root)
        self.storage.set("evidence_uri:" + jid, evidence_uri)
        self.storage.set("status:" + jid, Symbol("submitted"))
        self.env.emit_event("job_submitted", {"job_id": job_id})
        return True

    @external
    def record_verdict(self, judge: Address, job_id: U64, passed: Bool, score: U32, evidence_root: Bytes) -> Bool:
        """
        Record the judge panel's verdict on-chain: the pass/fail AND the numeric
        `score` (0..100, the panel's weighted aggregate) so the judgment is itself
        auditable and can feed agent reputation. Only the `judge` fixed at post
        time may call this, and only once evidence has been submitted. On a pass
        the job moves to `verified` and the SDK has the judge release the escrow;
        on a fail it moves to `rejected` and the depositor can refund after the
        deadline. `evidence_root` must match the anchored submission, so a verdict
        can never be recorded against a different bundle.
        """
        judge.require_auth()
        jid = str(job_id)
        if judge != self.storage.get("judge:" + jid):
            raise ContractError.NOT_JUDGE
        if self.storage.get("status:" + jid, Symbol("none")) != Symbol("submitted"):
            raise ContractError.NOT_SUBMITTED
        if evidence_root != self.storage.get("evidence_root:" + jid):
            raise ContractError.INVALID_PROOF

        self.storage.set("score:" + jid, score)
        if passed:
            self.storage.set("status:" + jid, Symbol("verified"))
            self.env.emit_event("job_verified", {"job_id": job_id, "passed": passed, "score": score})
        else:
            self.storage.set("status:" + jid, Symbol("rejected"))
            self.env.emit_event("job_verified", {"job_id": job_id, "passed": passed, "score": score})
        return True

    @external
    def finalize(self, job_id: U64) -> Bool:
        """
        Mark a verified job complete. The SDK releases the escrow (judge-signed,
        single payout or N-way swarm split) when the verdict is recorded; this is
        the poster closing the record. Reverts unless the job was verified. Only
        the poster may call.
        """
        jid = str(job_id)
        poster = self.storage.get("poster:" + jid)
        poster.require_auth()
        if self.storage.get("status:" + jid, Symbol("none")) != Symbol("verified"):
            raise ContractError.NOT_VERIFIED
        self.storage.set("status:" + jid, Symbol("done"))
        self.env.emit_event("job_completed", {"job_id": job_id})
        return True

    @external
    def cancel_job(self, job_id: U64) -> Bool:
        """Poster cancels an unclaimed job (refund handled via the escrow)."""
        jid = str(job_id)
        poster = self.storage.get("poster:" + jid)
        poster.require_auth()
        if self.storage.get("status:" + jid, Symbol("none")) != Symbol("open"):
            raise ContractError.NOT_OPEN
        self.storage.set("status:" + jid, Symbol("cancelled"))
        self.env.emit_event("job_cancelled", {"job_id": job_id})
        return True

    @view
    def get_job(self, job_id: U64) -> Map:
        """
        Return a job's full state — including its on-chain `title`, `description`,
        and `spec` (the rubric: checks + judge panel) — so a caller can render the
        bounty straight from the contract address with no indexer or off-chain
        fetch required.
        """
        jid = str(job_id)
        poster = self.storage.get("poster:" + jid)
        details = Map()
        details.set(Symbol("poster"), poster)
        details.set(Symbol("title"), self.storage.get("title:" + jid, Bytes(b"")))
        details.set(Symbol("description"), self.storage.get("description:" + jid, Bytes(b"")))
        details.set(Symbol("spec"), self.storage.get("spec:" + jid, Bytes(b"")))
        details.set(Symbol("rubric_hash"), self.storage.get("rubric_hash:" + jid, Bytes(b"")))
        details.set(Symbol("evidence_root"), self.storage.get("evidence_root:" + jid, Bytes(b"")))
        details.set(Symbol("evidence_uri"), self.storage.get("evidence_uri:" + jid, Bytes(b"")))
        details.set(Symbol("score"), self.storage.get("score:" + jid, U32(0)))
        details.set(Symbol("bounty"), self.storage.get("bounty:" + jid))
        details.set(Symbol("token"), self.storage.get("token:" + jid))
        details.set(Symbol("mode"), self.storage.get("mode:" + jid))
        details.set(Symbol("escrow"), self.storage.get("escrow:" + jid))
        details.set(Symbol("judge"), self.storage.get("judge:" + jid, poster))
        details.set(Symbol("deadline"), self.storage.get("deadline:" + jid))
        details.set(Symbol("status"), self.storage.get("status:" + jid))
        # Defaults to the poster when unclaimed, so the SDK always reads an address.
        details.set(Symbol("agent"), self.storage.get("agent:" + jid, poster))
        return details

    @view
    def get_swarm(self, job_id: U64) -> Vec[Address]:
        """Return the swarm members recorded for a job."""
        jid = str(job_id)
        return self.storage.get("members:" + jid, Vec())

    @view
    def get_shares(self, job_id: U64) -> Vec[U32]:
        """Return the swarm members' bounty shares (basis points), index-aligned with get_swarm."""
        jid = str(job_id)
        return self.storage.get("shares:" + jid, Vec())

    @view
    def job_count(self) -> U64:
        """Return the number of jobs posted so far (ids are 1..job_count)."""
        return self.storage.get("job_count", U64(0))
