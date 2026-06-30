"""
EvidenceBundle — what a worker submits instead of the spec echoed back.

The old flow had the worker submit the job spec so ``SHA256(proof)`` would match.
That proves nothing. An evidence bundle is the real submission: the deliverable
artifacts (content-addressed), the worker's per-criterion claims mapping the
rubric to that evidence, and provenance. It is signed by the claimant's Stellar
key and pinned off-chain; only ``evidence_root`` (32 bytes) is anchored on-chain,
reusing the memory-anchor pattern (big private data off-chain, tiny commitment on).

``evidence_root`` binds the submission to (a) the exact artifacts and (b) the
``rubric_hash`` it claims to satisfy — a judge that fetches the bundle can verify
both before scoring.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Artifact:
    """
    One deliverable file or resource. ``sha256`` is the hex digest of the bytes
    at ``uri`` so a judge can confirm it fetched exactly what was submitted.
    ``role`` is e.g. "deliverable" or "preview" (a per-slide PNG render lets a
    judge see what a human would see).
    """

    role: str
    uri: str
    sha256: str
    media_type: str = "application/octet-stream"

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "uri": self.uri, "sha256": self.sha256, "media_type": self.media_type}

    @classmethod
    def from_bytes(cls, role: str, uri: str, data: bytes, media_type: str = "application/octet-stream") -> "Artifact":
        return cls(role=role, uri=uri, sha256=hashlib.sha256(data).hexdigest(), media_type=media_type)


@dataclass
class Claim:
    """The worker's assertion that a given rubric criterion is satisfied, with a
    pointer into the evidence. Advisory — the judge verifies, it does not trust."""

    id: str
    note: str = ""
    value: Any = None
    ref: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"id": self.id}
        if self.note:
            d["note"] = self.note
        if self.value is not None:
            d["value"] = self.value
        if self.ref:
            d["ref"] = self.ref
        return d


@dataclass
class EvidenceBundle:
    job_id: int
    rubric_hash: str  # hex; binds the submission to the agreed rubric
    artifacts: List[Artifact] = field(default_factory=list)
    claims: List[Claim] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[int] = None
    worker_sig: Optional[str] = None  # hex ed25519 sig over the unsigned root

    def to_dict(self, *, include_sig: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "job_id": self.job_id,
            "rubric_hash": self.rubric_hash,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "claims": [c.to_dict() for c in self.claims],
            "provenance": self.provenance,
            "created_at": self.created_at,
        }
        if include_sig and self.worker_sig is not None:
            d["worker_sig"] = self.worker_sig
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvidenceBundle":
        return cls(
            job_id=int(d["job_id"]),
            rubric_hash=d["rubric_hash"],
            artifacts=[
                Artifact(role=a["role"], uri=a["uri"], sha256=a["sha256"],
                         media_type=a.get("media_type", "application/octet-stream"))
                for a in d.get("artifacts", [])
            ],
            claims=[
                Claim(id=c["id"], note=c.get("note", ""), value=c.get("value"), ref=c.get("ref", ""))
                for c in d.get("claims", [])
            ],
            provenance=d.get("provenance", {}) or {},
            created_at=d.get("created_at"),
            worker_sig=d.get("worker_sig"),
        )

    def canonical_json(self, *, include_sig: bool = False) -> bytes:
        """Deterministic serialization. The root is computed over the *unsigned*
        body so the signature can cover the root without a chicken-and-egg loop."""
        return json.dumps(
            self.to_dict(include_sig=include_sig),
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")

    def evidence_root(self) -> bytes:
        """The 32-byte commitment anchored on-chain in ``submit_evidence``.

        v1 is a flat SHA-256 of the canonical unsigned body (which itself pins
        every artifact by its own sha256). A Merkle root over artifacts is a v2
        enhancement, mirroring the memory anchor's roadmap."""
        return hashlib.sha256(self.canonical_json(include_sig=False)).digest()

    def sign(self, keypair) -> "EvidenceBundle":
        """Sign the evidence root with the claimant's Stellar keypair (ed25519).
        ``keypair.sign`` is the same primitive the wallet uses for transactions."""
        if self.created_at is None:
            self.created_at = int(time.time())
        self.worker_sig = keypair.sign(self.evidence_root()).hex()
        return self

    def verify_sig(self, keypair) -> bool:
        """Confirm ``worker_sig`` is a valid signature over the root by ``keypair``
        (typically reconstructed from the claimant's public address)."""
        if not self.worker_sig:
            return False
        try:
            keypair.verify(self.evidence_root(), bytes.fromhex(self.worker_sig))
            return True
        except Exception:
            return False
