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

    JUDGE = "G" + "J" * 55
    job_id = client.post_bounty(
        title="Write a haiku", description="Write a haiku about graphs",
        checks=[("imagery", "vivid imagery", 50), ("form", "5-7-5 structure", 50)],
        judge_models=["nvidia:meta/llama-3.3-70b-instruct"],
        bounty_xlm=Decimal("2"), judge=JUDGE, mode="single",
    )
    assert job_id == 7

    post = [c for c in ctx.calls if c["function"] == "post_job"][0]
    poster, title, description, spec, rh, bounty, token, mode, escrow, judge, deadline = post["args"]
    assert poster == ctx.keypair.public_key
    assert title == b"Write a haiku"
    assert description == b"Write a haiku about graphs"
    assert b'"judges"' in spec and b"llama-3.3" in spec   # checks + chosen panel on-chain
    import hashlib
    assert rh == hashlib.sha256(spec).digest()            # rubric_hash anchors the spec
    assert bounty == 20_000_000                            # 2 XLM in stroops
    assert mode == "single"
    assert escrow == "CESCROW"
    assert judge == JUDGE


def test_post_bounty_rejects_bad_mode():
    with pytest.raises(ValueError):
        JobBoardClient(_FakeContext(), BOARD).post_bounty(
            "t", "d", checks=[("a", "x", 100)], judge_models=["nvidia:m"],
            bounty_xlm=Decimal("1"), judge="G" + "J" * 55, mode="bogus")


def test_post_bounty_requires_judge_models():
    with pytest.raises(ValueError):
        JobBoardClient(_FakeContext(), BOARD).post_bounty(
            "t", "d", checks=[("a", "x", 100)], judge_models=[],
            bounty_xlm=Decimal("1"), judge="G" + "J" * 55)


# ── P3: bounty + share validation (reject before any escrow/tx) ──────────────
@pytest.mark.parametrize("bad_bounty", ["0", "-5", "0.00000001"])
def test_post_bounty_rejects_non_positive_or_substroop_bounty(monkeypatch, bad_bounty):
    ctx = _FakeContext()
    import mycelium_sdk.jobs as jobs_mod
    # If validation failed to fire, this stub would let a tx through — so the
    # test also proves nothing was posted.
    monkeypatch.setattr(jobs_mod.EscrowPaymentRouter, "create_locked_escrow",
                        lambda self, **kw: "CESCROW")
    with pytest.raises(ValueError):
        JobBoardClient(ctx, BOARD).post_bounty(
            "t", "d", checks=[("a", "x", 100)], judge_models=["nvidia:m"],
            bounty_xlm=Decimal(bad_bounty), judge="G" + "J" * 55, mode="single")
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


def test_submit_evidence_marshals_submitter():
    ctx = _FakeContext()
    root = b"\x22" * 32
    JobBoardClient(ctx, BOARD).submit_evidence(5, root, "ipfs://bundle")
    call = [c for c in ctx.calls if c["function"] == "submit_evidence"][0]
    submitter, job_id, evidence_root, evidence_uri = call["args"]
    # the wallet keypair signs as the claimant — contract gates on submitter auth
    assert submitter == ctx.keypair.public_key
    assert evidence_root == root
    assert evidence_uri == b"ipfs://bundle"
    from stellar_sdk import scval
    assert scval.to_native(job_id) == 5


def test_submit_evidence_rejects_bad_root_length():
    ctx = _FakeContext()
    with pytest.raises(ValueError):
        JobBoardClient(ctx, BOARD).submit_evidence(5, b"too-short")
    assert ctx.calls == []


def test_record_verdict_marshals_judge_score_and_pass():
    ctx = _FakeContext()
    root = b"\x33" * 32
    JobBoardClient(ctx, BOARD).record_verdict(5, True, 88, root)
    call = [c for c in ctx.calls if c["function"] == "record_verdict"][0]
    judge, job_id, passed, score, evidence_root = call["args"]
    assert judge == ctx.keypair.public_key  # judge wallet signs
    assert passed is True
    from stellar_sdk import scval
    assert scval.to_native(score) == 88     # numeric verdict score recorded on-chain
    assert evidence_root == root


def test_settle_pass_records_then_releases_single(monkeypatch):
    ctx = _FakeContext()
    root = b"\x44" * 32
    ctx.reads["get_job"] = {
        "escrow": "CESCROW", "mode": "single", "agent": "G" + "C" * 55,
        "poster": "G" + "A" * 55, "judge": ctx.keypair.public_key,
        "bounty": 10_000_000, "status": "submitted",
    }
    client = JobBoardClient(ctx, BOARD)

    captured = {}
    import mycelium_sdk.jobs as jobs_mod
    monkeypatch.setattr(
        jobs_mod.EscrowPaymentRouter, "split_release",
        lambda self, escrow, shares, evidence_root: captured.update(
            escrow=escrow, shares=shares, evidence_root=evidence_root),
    )

    client.settle(5, True, 91, root)
    # verdict recorded by the judge...
    assert any(c["function"] == "record_verdict" for c in ctx.calls)
    # ...then the bounty released: single mode → one recipient at 100%
    assert captured["escrow"] == "CESCROW"
    assert captured["shares"] == [("G" + "C" * 55, 10000)]
    assert captured["evidence_root"] == root


def test_settle_fail_records_no_release(monkeypatch):
    ctx = _FakeContext()
    client = JobBoardClient(ctx, BOARD)
    import mycelium_sdk.jobs as jobs_mod
    called = {"split": False}
    monkeypatch.setattr(jobs_mod.EscrowPaymentRouter, "split_release",
                        lambda *a, **k: called.update(split=True))

    client.settle(7, False, 40, b"\x55" * 32)
    assert any(c["function"] == "record_verdict" for c in ctx.calls)
    assert called["split"] is False  # a failing verdict never pays out


def test_release_bounty_swarm_splits(monkeypatch):
    ctx = _FakeContext()
    root = b"\x66" * 32
    ctx.reads["get_job"] = {
        "escrow": "CESCROW", "mode": "swarm", "agent": "G" + "A" * 55,
        "poster": "G" + "A" * 55, "judge": ctx.keypair.public_key,
        "bounty": 10_000_000, "status": "verified",
    }
    ctx.reads["get_swarm"] = ["G" + "D" * 55, "G" + "E" * 55]
    ctx.reads["get_shares"] = [6000, 4000]
    client = JobBoardClient(ctx, BOARD)

    captured = {}
    import mycelium_sdk.jobs as jobs_mod
    monkeypatch.setattr(
        jobs_mod.EscrowPaymentRouter, "split_release",
        lambda self, escrow, shares, evidence_root: captured.update(shares=shares),
    )

    client.release_bounty(9, root)
    assert captured["shares"] == [("G" + "D" * 55, 6000), ("G" + "E" * 55, 4000)]


def test_finalize_is_poster_bookkeeping_only():
    ctx = _FakeContext()
    JobBoardClient(ctx, BOARD).finalize(5)
    call = [c for c in ctx.calls if c["function"] == "finalize"][0]
    from stellar_sdk import scval
    (job_id,) = call["args"]
    assert scval.to_native(job_id) == 5
