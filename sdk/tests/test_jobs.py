"""Offline tests for the Sovereign Job Boards SDK (no network)."""

from decimal import Decimal

import pytest

from mycelium_sdk.jobs import JobBoardClient


class _FakeKP:
    public_key = "G" + "A" * 55


class _FakeContext:
    """Records call_contract invocations; returns canned values for reads."""

    def __init__(self, network_type="testnet"):
        self.network_type = network_type
        self.keypair = _FakeKP()
        self.calls = []
        self.reads = {}  # function_name -> canned return

    def call_contract(self, contract_id, function_name, args, read_only=False):
        self.calls.append(
            {"contract_id": contract_id, "function": function_name, "args": args, "read_only": read_only}
        )
        if read_only:
            return self.reads.get(function_name)
        # state-changing: emulate a TxResult-ish object carrying the post return
        return type("TxResult", (), {"hash": "tx", "status": "SUCCESS", "return_value": self.reads.get(function_name)})()


BOARD = "C" + "B" * 55


def test_requires_board_address():
    with pytest.raises(ValueError):
        JobBoardClient(_FakeContext(), "")


def test_post_job_locks_escrow_and_records(monkeypatch):
    ctx = _FakeContext()
    # post_job returns the new job id as the contract return value
    ctx.reads["post_job"] = 7
    client = JobBoardClient(ctx, BOARD)

    # Stub the escrow deploy/lock so no network is touched.
    import mycelium_sdk.jobs as jobs_mod
    monkeypatch.setattr(
        jobs_mod.EscrowPaymentRouter, "create_locked_escrow",
        lambda self, **kw: "CESCROW",
    )

    spec_hash = b"\x11" * 32
    job_id = client.post_job(
        spec_uri="ipfs://spec", spec_hash=spec_hash, bounty_xlm=Decimal("2"), mode="single"
    )
    assert job_id == 7

    post = [c for c in ctx.calls if c["function"] == "post_job"][0]
    poster, spec_uri, sh, bounty, token, mode, escrow, deadline = post["args"]
    assert poster == ctx.keypair.public_key
    assert spec_uri == b"ipfs://spec"
    assert sh == spec_hash
    assert bounty == 20_000_000      # 2 XLM in stroops
    assert mode == "single"
    assert escrow == "CESCROW"


def test_post_job_rejects_bad_mode():
    with pytest.raises(ValueError):
        JobBoardClient(_FakeContext(), BOARD).post_job("u", b"h", Decimal("1"), mode="bogus")


# ── P3: bounty + share validation (reject before any escrow/tx) ──────────────
@pytest.mark.parametrize("bad_bounty", ["0", "-5", "0.00000001"])
def test_post_job_rejects_non_positive_or_substroop_bounty(monkeypatch, bad_bounty):
    ctx = _FakeContext()
    import mycelium_sdk.jobs as jobs_mod
    # If validation failed to fire, this stub would let a tx through — so the
    # test also proves nothing was posted.
    monkeypatch.setattr(jobs_mod.EscrowPaymentRouter, "create_locked_escrow",
                        lambda self, **kw: "CESCROW")
    with pytest.raises(ValueError):
        JobBoardClient(ctx, BOARD).post_job("u", b"h", Decimal(bad_bounty), mode="single")
    assert [c for c in ctx.calls if c["function"] == "post_job"] == []


@pytest.mark.parametrize("bad_share", [0, -1, 10001])
def test_join_swarm_rejects_out_of_range_share(bad_share):
    ctx = _FakeContext()
    with pytest.raises(ValueError):
        JobBoardClient(ctx, BOARD).join_swarm(3, "vision", bad_share)
    assert ctx.calls == []


def test_join_swarm_marshals_share():
    ctx = _FakeContext()
    JobBoardClient(ctx, BOARD).join_swarm(3, "vision", 4000)
    call = [c for c in ctx.calls if c["function"] == "join_swarm"][0]
    agent, job_id, cap, share = call["args"]
    assert agent == ctx.keypair.public_key
    assert cap == b"vision"
    # share marshalled via the u32 helper (width-correct SCVal, not a bare int)
    from stellar_sdk import scval
    assert scval.to_native(share) == 4000
    assert share.type == scval.to_uint32(4000).type


def test_submit_proof_marshals_submitter():
    ctx = _FakeContext()
    JobBoardClient(ctx, BOARD).submit_proof(5, b"proof-bytes")
    call = [c for c in ctx.calls if c["function"] == "submit_proof"][0]
    submitter, job_id, proof = call["args"]
    # the wallet keypair signs as the claimant — contract gates on submitter auth
    assert submitter == ctx.keypair.public_key
    assert proof == b"proof-bytes"
    from stellar_sdk import scval
    assert scval.to_native(job_id) == 5


def test_finalize_single_pays_claimant(monkeypatch):
    ctx = _FakeContext()
    ctx.reads["get_job"] = {
        "escrow": "CESCROW", "mode": "single", "agent": "G" + "C" * 55,
        "poster": "G" + "A" * 55, "bounty": 10_000_000, "status": "submitted",
    }
    client = JobBoardClient(ctx, BOARD)

    captured = {}
    import mycelium_sdk.jobs as jobs_mod
    monkeypatch.setattr(
        jobs_mod.EscrowPaymentRouter, "split_release",
        lambda self, escrow, shares, proof: captured.update(escrow=escrow, shares=shares, proof=proof),
    )

    client.finalize(5, b"proof")
    assert captured["escrow"] == "CESCROW"
    # single mode → one recipient (the claimant) at 100%
    assert captured["shares"] == [("G" + "C" * 55, 10000)]
    # and the contract finalize was called
    assert any(c["function"] == "finalize" for c in ctx.calls)


def test_finalize_swarm_splits(monkeypatch):
    ctx = _FakeContext()
    ctx.reads["get_job"] = {
        "escrow": "CESCROW", "mode": "swarm", "agent": "G" + "A" * 55,
        "poster": "G" + "A" * 55, "bounty": 10_000_000, "status": "submitted",
    }
    ctx.reads["get_swarm"] = ["G" + "D" * 55, "G" + "E" * 55]
    ctx.reads["get_shares"] = [6000, 4000]
    client = JobBoardClient(ctx, BOARD)

    captured = {}
    import mycelium_sdk.jobs as jobs_mod
    monkeypatch.setattr(
        jobs_mod.EscrowPaymentRouter, "split_release",
        lambda self, escrow, shares, proof: captured.update(shares=shares),
    )

    client.finalize(9, b"proof")
    assert captured["shares"] == [("G" + "D" * 55, 6000), ("G" + "E" * 55, 4000)]
