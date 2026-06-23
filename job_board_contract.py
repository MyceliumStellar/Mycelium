"""
JobBoard — the Sovereign Job Boards contract for Mycelium agents.

A poster publishes a task (a spec URI + its SHA-256 hash) with a bounty that is
locked off-chain into an `Escrow` instance (see `escrow_contract.py`). Agents
either self-`claim_job` (single mode) or `join_swarm` with an agreed bounty
share (swarm mode). When a claimant publishes a proof whose SHA-256 matches the
task's `spec_hash`, `submit_proof` records completion; `finalize` then marks the
job done while the SDK releases the escrow — splitting the bounty across swarm
members per their recorded shares via `EscrowPaymentRouter.split_release`.

The escrow holds the funds and enforces the proof + balanced-split invariants on
release, so this contract stays free of cross-contract calls: it is the
discovery + coordination ledger, and emits `job_posted` / `job_claimed` /
`swarm_joined` / `job_completed` events for the off-chain indexer and `/jobs` UI.

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


@contract
class JobBoard:
    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def post_job(
        self,
        poster: Address,
        spec_uri: Bytes,
        spec_hash: Bytes,
        bounty: I128,
        token: Address,
        mode: Symbol,
        escrow: Address,
        deadline: U64,
    ) -> U64:
        """
        Record a new job and return its id. The bounty is expected to already be
        locked in the `escrow` instance (the SDK deploys + locks it first). `mode`
        is the symbol `single` or `swarm`.
        """
        poster.require_auth()

        job_id = self.storage.get("job_count", U64(0)) + U64(1)
        self.storage.set("job_count", job_id)

        jid = str(job_id)
        self.storage.set("poster:" + jid, poster)
        self.storage.set("spec_uri:" + jid, spec_uri)
        self.storage.set("spec_hash:" + jid, spec_hash)
        self.storage.set("bounty:" + jid, bounty)
        self.storage.set("token:" + jid, token)
        self.storage.set("mode:" + jid, mode)
        self.storage.set("escrow:" + jid, escrow)
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
    def submit_proof(self, job_id: U64, proof: Bytes) -> Bool:
        """Record a completion proof whose SHA-256 matches the job's spec_hash."""
        jid = str(job_id)
        if self.env.crypto().sha256(proof) != self.storage.get("spec_hash:" + jid):
            raise ContractError.INVALID_PROOF
        self.storage.set("proof:" + jid, proof)
        self.storage.set("status:" + jid, Symbol("submitted"))
        self.env.emit_event("job_submitted", {"job_id": job_id})
        return True

    @external
    def finalize(self, job_id: U64) -> Bool:
        """
        Mark a submitted job complete. The SDK releases the escrow (single payout
        or N-way swarm split) around this call. Reverts unless a proof was
        submitted.
        """
        jid = str(job_id)
        if self.storage.get("status:" + jid, Symbol("none")) != Symbol("submitted"):
            raise ContractError.NOT_SUBMITTED
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
        """Return a job's current state for off-chain inspection."""
        jid = str(job_id)
        poster = self.storage.get("poster:" + jid)
        details = Map()
        details.set(Symbol("poster"), poster)
        details.set(Symbol("bounty"), self.storage.get("bounty:" + jid))
        details.set(Symbol("token"), self.storage.get("token:" + jid))
        details.set(Symbol("mode"), self.storage.get("mode:" + jid))
        details.set(Symbol("escrow"), self.storage.get("escrow:" + jid))
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
