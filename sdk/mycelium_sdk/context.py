"""
AgentContext — the sovereign execution context that maps Python calls to live
Stellar/Soroban transactions (signing + RPC submission).

`stellar_sdk` is imported lazily inside methods rather than at module import
time. This is deliberate: the contract-authoring DSL (`mycelium`) re-exports
`AgentContext`, and the compiler imports `mycelium` while building WASM in an
environment that has no `stellar_sdk` installed. Merely importing this module
must therefore stay free of the heavy on-chain dependency; only *constructing*
an `AgentContext` pulls it in.
"""

import json
import os
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, List, Optional

from mycelium_sdk import crypto
from mycelium_sdk import rpc as rpc_helpers
from mycelium_sdk.logging import get_logger
from mycelium_sdk.constants import (
    SOROBAN_RPC_URLS,
    HORIZON_URLS,
    NETWORK_PASSPHRASES,
    normalize_network,
)

log = get_logger()

# Process-wide accumulator of dry-run records across every AgentContext, so a
# driver (e.g. `mycelium test`) that runs an agent script — and never sees the
# context the script builds internally — can still report what it would submit.
DRY_RUN_LOG: List[dict] = []


def reset_dry_run_log() -> None:
    """Clear the global dry-run accumulator (call before driving an agent)."""
    DRY_RUN_LOG.clear()

# Soroban Symbols are limited to [a-zA-Z0-9_] and <= 32 chars; longer/other
# strings are encoded as Soroban Strings instead.
_SYMBOL_RE = re.compile(r"^[a-zA-Z0-9_]{1,32}$")
# Polling cadence while waiting for a submitted transaction to settle.
_POLL_INTERVAL_SECONDS = 2
_POLL_TIMEOUT_SECONDS = 60


class StellarNetwork(Enum):
    TESTNET = "testnet"
    MAINNET = "mainnet"
    LOCAL = "local"


@dataclass
class TxResult:
    """Result of a state-changing contract invocation."""
    hash: str
    status: str
    return_value: Any = None


def _require_stellar_sdk():
    try:
        import stellar_sdk  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(
            "AgentContext requires the Stellar SDK. Install it with:\n"
            "    pip install 'stellar-sdk>=14,<15'"
        ) from exc
    return stellar_sdk


class AgentContext:
    """
    Manages an agent's signing keypair and Stellar/Soroban RPC clients, and
    invokes on-chain contracts on its behalf.
    """

    def __init__(
        self,
        keypair_path: str = ".mycelium/wallet.json",
        network_type: str = "testnet",
        passphrase: Optional[str] = None,
        dry_run: bool = False,
    ):
        from mycelium_sdk.banner import show_startup_banner
        show_startup_banner()

        self.network_type = normalize_network(network_type)
        self._passphrase = passphrase
        # Dry-run: state-changing calls are simulated, logged, and NOT submitted
        # (no signature, no fee). Also enabled via MYCELIUM_DRY_RUN=1 so
        # `mycelium test` can flip it without touching the agent script.
        self.dry_run = dry_run or bool(os.environ.get("MYCELIUM_DRY_RUN"))
        self.dry_run_log: List[dict] = []
        self.keypair = self._load_and_decrypt_keypair(keypair_path)
        self._init_network_clients()

    def _init_network_clients(self):
        """Wire up the Soroban/Horizon RPC clients and the network passphrase."""
        _require_stellar_sdk()
        from stellar_sdk import Server, Network
        from stellar_sdk import SorobanServer

        self.soroban_rpc = SorobanServer(SOROBAN_RPC_URLS[self.network_type])
        self.horizon_server = Server(HORIZON_URLS[self.network_type])
        # Use stellar_sdk's canonical passphrase constants (normalize_network
        # guarantees network_type is exactly "testnet" or "mainnet").
        if self.network_type == "testnet":
            self.network_passphrase = Network.TESTNET_NETWORK_PASSPHRASE
        else:
            self.network_passphrase = Network.PUBLIC_NETWORK_PASSPHRASE

    @classmethod
    def read_only(cls, network_type: str = "testnet") -> "AgentContext":
        """
        Build a wallet-free context for read-only (simulated) contract calls.

        Discovery and `resolve_agent`-style views require no signature and no
        funded account, so this skips wallet loading entirely and signs nothing.
        A throwaway keypair is used purely as the simulation source account.
        Calling a state-changing path on this context will fail at submit time.
        """
        stellar_sdk = _require_stellar_sdk()
        from stellar_sdk import Keypair

        self = cls.__new__(cls)
        self.network_type = normalize_network(network_type)
        self._passphrase = None
        self.dry_run = False
        self.dry_run_log = []
        self.keypair = Keypair.random()
        self._init_network_clients()
        return self

    # ── back-compat constructor ──────────────────────────────────────────────
    @classmethod
    def from_keypair(cls, keypair_path: str, network: "StellarNetwork | str" = StellarNetwork.TESTNET):
        """Construct from a wallet path and a StellarNetwork enum (or string)."""
        net = network.value if isinstance(network, StellarNetwork) else str(network)
        return cls(keypair_path=keypair_path, network_type=net)

    # ── wallet ───────────────────────────────────────────────────────────────
    def _load_and_decrypt_keypair(self, path: str):
        """Load the encrypted wallet, decrypt the seed, return a signing Keypair."""
        stellar_sdk = _require_stellar_sdk()
        from stellar_sdk import Keypair

        with open(path, "r") as f:
            wallet_data = json.load(f)
        decrypted_seed = self._decrypt_aes_gcm(
            wallet_data["encrypted_secret"],
            wallet_data["nonce"],
            wallet_data["salt"],
        )
        return Keypair.from_secret(decrypted_seed)

    def _decrypt_aes_gcm(self, ciphertext_hex: str, nonce_hex: str, salt_hex: str) -> str:
        """Decrypt the wallet secret seed (AES-GCM). Returns the 'S...' seed string."""
        passphrase = crypto.resolve_passphrase(self._passphrase)
        return crypto.decrypt_secret(ciphertext_hex, nonce_hex, salt_hex, passphrase)

    # ── argument marshalling ─────────────────────────────────────────────────
    def _to_scval(self, arg: Any):
        """
        Convert a Python value to a Soroban SCVal. Pass a pre-built
        `stellar_sdk.xdr.SCVal` to control exact integer widths / types.
        """
        from stellar_sdk import scval
        from stellar_sdk import xdr as stellar_xdr

        if isinstance(arg, stellar_xdr.SCVal):
            return arg
        if isinstance(arg, bool):
            return scval.to_bool(arg)
        # Mycelium DSL typed-int wrappers (U64(40), u32(3), I128(...), ...) are
        # int subclasses; honor their declared width so calls to u64/u32/i64
        # parameters don't get a default i128 and trap in the VM.
        typed = self._typed_int_scval(arg, scval)
        if typed is not None:
            return typed
        if isinstance(arg, int):
            return scval.to_int128(arg)
        if isinstance(arg, bytes):
            return scval.to_bytes(arg)
        if isinstance(arg, str):
            from stellar_sdk import StrKey

            if StrKey.is_valid_ed25519_public_key(arg) or StrKey.is_valid_contract(arg):
                return scval.to_address(arg)
            if _SYMBOL_RE.match(arg):
                return scval.to_symbol(arg)
            return scval.to_string(arg)
        if isinstance(arg, list):
            return scval.to_vec([self._to_scval(a) for a in arg])
        raise TypeError(
            f"Cannot convert {type(arg).__name__} to SCVal; "
            "pass a width-correct value via mycelium_sdk.scval (e.g. u64(x))."
        )

    # Map Mycelium DSL int-type class names -> the matching SCVal constructor.
    _TYPED_INT_CTORS = {
        "u32": "to_uint32", "U32": "to_uint32",
        "u64": "to_uint64", "U64": "to_uint64",
        "U128": "to_uint128",
        "i32": "to_int32", "I32": "to_int32",
        "i64": "to_int64",
        "i128": "to_int128", "I128": "to_int128",
    }

    @classmethod
    def _typed_int_scval(cls, arg: Any, scval):
        """Return an SCVal for a DSL typed-int wrapper, or None for a plain int."""
        if not isinstance(arg, int) or type(arg) is int or isinstance(arg, bool):
            return None
        ctor = cls._TYPED_INT_CTORS.get(type(arg).__name__)
        return getattr(scval, ctor)(int(arg)) if ctor else None

    def _marshal_args(self, contract_id: str, function_name: str, args: List[Any], scval):
        """
        Convert Python args to SCVals, using the contract spec to pick integer
        widths automatically when available. Falls back to `_to_scval` per value
        if the spec can't be fetched, so a plain int still works (as i128) and
        DSL wrappers (U64(x)) keep working regardless.
        """
        from mycelium_sdk import spec as spec_mod

        return spec_mod.marshal_args(
            self.soroban_rpc, contract_id, function_name, args, scval, self._to_scval
        )

    # ── invocation ───────────────────────────────────────────────────────────
    def call_contract(
        self,
        contract_id: str,
        function_name: str,
        args: List[Any],
        read_only: bool = False,
    ) -> Any:
        """
        Invoke `function_name(args)` on `contract_id`.

        - read_only=True: simulate only (no fee, no signature) and return the
          decoded Python value. Use for view/getter calls (e.g. resolve_agent).
        - read_only=False: simulate → assemble footprint/fees → sign → submit →
          poll until settled, returning a TxResult(hash, status, return_value).
        """
        stellar_sdk = _require_stellar_sdk()
        from stellar_sdk import TransactionBuilder, scval
        from stellar_sdk import xdr as stellar_xdr
        from stellar_sdk.exceptions import BaseRequestError
        from stellar_sdk.soroban_rpc import GetTransactionStatus

        log.info(f"[SDK] Invoking {function_name} on {contract_id} (read_only={read_only})...")
        try:
            if read_only:
                # Simulation needs only a syntactically valid source account, not
                # a funded/existing one — build it locally so views (resolve,
                # discovery, getters) work without a wallet or RPC round-trip.
                from stellar_sdk import Account

                source = Account(self.keypair.public_key, 0)
            else:
                source = self.soroban_rpc.load_account(self.keypair.public_key)
            sc_args = self._marshal_args(contract_id, function_name, args, scval)

            tx = (
                TransactionBuilder(source, self.network_passphrase, base_fee=100)
                .append_invoke_contract_function_op(contract_id, function_name, sc_args)
                .set_timeout(300)
                .build()
            )

            # Always simulate first to surface contract errors cheaply. The
            # simulation also yields the function's return value deterministically
            # — we use it as the authoritative return for state-changing calls
            # too, since post-submit TransactionMetaV4 (protocol 23) is not
            # decodable by stellar-sdk 12.x. Retried on transient RPC errors.
            sim = rpc_helpers.with_retry(
                lambda: self.soroban_rpc.simulate_transaction(tx),
                label="simulate_transaction",
            )
            if sim.error:
                raise RuntimeError(f"Simulation failed: {sim.error}")

            sim_return = self._decode_sim_result(sim, scval, stellar_xdr)
            if read_only:
                return sim_return

            # Dry-run: record what we WOULD submit (incl. estimated fee from the
            # simulation) and return without signing or spending.
            if self.dry_run:
                est_fee = getattr(sim, "min_resource_fee", None)
                record = {
                    "contract_id": contract_id,
                    "function": function_name,
                    "args": list(args),
                    "sim_return": sim_return,
                    "est_fee_stroops": int(est_fee) if est_fee is not None else None,
                }
                self.dry_run_log.append(record)
                DRY_RUN_LOG.append(record)
                log.info(
                    f"[dry-run] would submit {function_name} on {contract_id} "
                    f"(est fee {est_fee} stroops) — not signing or submitting."
                )
                return TxResult(hash="DRY-RUN", status="SIMULATED", return_value=sim_return)

            # State-changing: assemble (footprint + fees), sign, submit, poll.
            # prepare/submit/poll are all retried on transient RPC failures;
            # submit re-sends the SAME signed tx on TRY_AGAIN_LATER (idempotent).
            prepared = rpc_helpers.with_retry(
                lambda: self.soroban_rpc.prepare_transaction(tx),
                label="prepare_transaction",
            )
            prepared.sign(self.keypair)
            send = rpc_helpers.submit_transaction(self.soroban_rpc, prepared)
            tx_hash = send.hash

            deadline = time.time() + _POLL_TIMEOUT_SECONDS
            while True:
                get = rpc_helpers.with_retry(
                    lambda: self.soroban_rpc.get_transaction(tx_hash),
                    label="get_transaction",
                )
                if get.status != GetTransactionStatus.NOT_FOUND:
                    break
                if time.time() > deadline:
                    raise TimeoutError(
                        f"Transaction {tx_hash} did not settle within "
                        f"{_POLL_TIMEOUT_SECONDS}s."
                    )
                time.sleep(_POLL_INTERVAL_SECONDS)

            if get.status == GetTransactionStatus.FAILED:
                raise RuntimeError(f"Transaction {tx_hash} FAILED: {get.result_xdr}")

            # Prefer the post-settlement meta return value when decodable
            # (meta v3); otherwise fall back to the simulated return value.
            settled_return = self._decode_tx_result(get, scval, stellar_xdr)
            return TxResult(
                hash=tx_hash,
                status="SUCCESS",
                return_value=settled_return if settled_return is not None else sim_return,
            )
        except BaseRequestError as error:
            log.error(f"[SDK ERROR] Soroban Contract Invocation Failed: {error}")
            raise

    # ── deployment (pure-Python, no stellar-cli) ─────────────────────────────
    def deploy_contract(self, wasm_bytes: bytes, salt: Optional[bytes] = None) -> str:
        """
        Deploy a contract WASM and return the new contract id — entirely via
        signed Soroban transactions, with NO `stellar-cli` / Rust dependency.

        Delegates to the module-level `deploy_contract` using this context's
        already-wired Soroban RPC client, signing keypair, and passphrase.
        """
        return deploy_contract(
            self.soroban_rpc,
            self.keypair,
            self.network_passphrase,
            wasm_bytes,
            salt=salt,
        )

    async def acall_contract(
        self,
        contract_id: str,
        function_name: str,
        args: List[Any],
        read_only: bool = False,
    ) -> Any:
        """
        Async wrapper around `call_contract`.

        Runs the (blocking) RPC submit/poll on a worker thread via
        `asyncio.to_thread`, so an agent can `await` many contract calls
        concurrently without the GIL-bound sync path serializing them. Same
        return contract as `call_contract` (decoded value for read-only, a
        `TxResult` for state-changing).
        """
        import asyncio

        return await asyncio.to_thread(
            self.call_contract, contract_id, function_name, args, read_only
        )

    # ── typed contract client ────────────────────────────────────────────────
    def contract(self, contract_id: str) -> "Any":
        """
        Return a typed client for `contract_id`: `ctx.contract(cid).add(40)`.

        Methods are discovered from the contract's on-chain spec, so calls read
        like native method invocations. `client.add(40)` signs+submits;
        `client.read.get_count()` simulates; `client.aio.*` returns awaitables.
        See `mycelium_sdk.contract_client.ContractClient`.
        """
        from mycelium_sdk.contract_client import ContractClient

        return ContractClient(self, contract_id)

    # ── return-value decoding ────────────────────────────────────────────────
    @staticmethod
    def _decode_sim_result(sim, scval, stellar_xdr):
        if not sim.results:
            return None
        xdr = sim.results[0].xdr
        if not xdr:
            return None
        return scval.to_native(stellar_xdr.SCVal.from_xdr(xdr))

    @staticmethod
    def _decode_tx_result(get, scval, stellar_xdr):
        """Decode the return value from settled tx meta (v3 or protocol-23 v4).

        Returns None if the meta is missing/undecodable, in which case the caller
        falls back to the simulated return value.
        """
        if not get.result_meta_xdr:
            return None
        try:
            meta = stellar_xdr.TransactionMeta.from_xdr(get.result_meta_xdr)
            soroban_meta = None
            if getattr(meta, "v3", None) is not None:
                soroban_meta = meta.v3.soroban_meta
            elif getattr(meta, "v4", None) is not None:
                soroban_meta = meta.v4.soroban_meta
            if soroban_meta is None or soroban_meta.return_value is None:
                return None
            return scval.to_native(soroban_meta.return_value)
        except (AttributeError, ValueError):
            return None


# ── module-level pure-Python deploy ──────────────────────────────────────────
def deploy_contract(soroban_rpc, keypair, network_passphrase, wasm_bytes: bytes,
                    salt: Optional[bytes] = None) -> str:
    """
    Upload a contract WASM and instantiate it, returning the new contract id —
    entirely via signed Soroban transactions, with NO `stellar-cli` / Rust
    dependency. Reusable from any caller holding a `SorobanServer` + signing
    `Keypair` (the IDE backend deploys from a raw secret key this way).

    Two on-chain steps, each simulated → prepared → signed → submitted → polled
    with the same transient-retry logic as `AgentContext.call_contract`:
      1. Upload the WASM (`append_upload_contract_wasm_op`). The WASM hash is the
         SHA-256 of the bytes, so re-uploading an already-present WASM is a
         harmless no-op.
      2. Instantiate the contract from that hash (`append_create_contract_op`),
         sourced from the keypair's address. The op's return value is the new
         contract address.
    """
    import hashlib

    _require_stellar_sdk()

    wasm_hash = hashlib.sha256(wasm_bytes).digest()
    log.info(
        f"[SDK] Uploading contract WASM ({len(wasm_bytes):,} bytes, "
        f"hash {wasm_hash.hex()[:16]}…)..."
    )
    _build_sign_submit(
        soroban_rpc, keypair, network_passphrase,
        lambda b: b.append_upload_contract_wasm_op(contract=wasm_bytes),
        label="upload_contract_wasm",
    )

    # A random salt keeps each deploy a distinct instance (mirrors stellar-cli).
    salt = salt if salt is not None else os.urandom(32)
    log.info("[SDK] Instantiating contract from uploaded WASM hash...")
    contract_id = _build_sign_submit(
        soroban_rpc, keypair, network_passphrase,
        lambda b: b.append_create_contract_op(
            wasm_id=wasm_hash, address=keypair.public_key, salt=salt
        ),
        label="create_contract",
    )
    if not contract_id:
        raise RuntimeError(
            "Contract creation submitted but no contract address was returned "
            "by the network. Retry the deploy."
        )
    # to_native decodes an address SCVal to a stellar_sdk.Address; normalize to
    # the canonical C... StrKey string callers expect.
    contract_id = getattr(contract_id, "address", contract_id)
    log.info(f"[SDK] Contract deployed: {contract_id}")
    return str(contract_id)


def _build_sign_submit(soroban_rpc, keypair, network_passphrase, append_op, *, label: str):
    """
    Build a single-op tx via `append_op(builder)`, simulate → prepare → sign →
    submit → poll, and return the decoded native return value (or None). Shares
    the transient-retry + settle-poll behavior of `AgentContext.call_contract`.
    """
    from stellar_sdk import TransactionBuilder, scval
    from stellar_sdk import xdr as stellar_xdr
    from stellar_sdk.soroban_rpc import GetTransactionStatus

    source = soroban_rpc.load_account(keypair.public_key)
    builder = TransactionBuilder(source, network_passphrase, base_fee=100)
    append_op(builder)
    tx = builder.set_timeout(300).build()

    sim = rpc_helpers.with_retry(
        lambda: soroban_rpc.simulate_transaction(tx),
        label=f"simulate_transaction({label})",
    )
    if sim.error:
        raise RuntimeError(f"Simulation failed ({label}): {sim.error}")
    sim_return = AgentContext._decode_sim_result(sim, scval, stellar_xdr)

    prepared = rpc_helpers.with_retry(
        lambda: soroban_rpc.prepare_transaction(tx),
        label=f"prepare_transaction({label})",
    )
    prepared.sign(keypair)
    send = rpc_helpers.submit_transaction(soroban_rpc, prepared)
    tx_hash = send.hash

    deadline = time.time() + _POLL_TIMEOUT_SECONDS
    while True:
        get = rpc_helpers.with_retry(
            lambda: soroban_rpc.get_transaction(tx_hash),
            label=f"get_transaction({label})",
        )
        if get.status != GetTransactionStatus.NOT_FOUND:
            break
        if time.time() > deadline:
            raise TimeoutError(
                f"Transaction {tx_hash} ({label}) did not settle within "
                f"{_POLL_TIMEOUT_SECONDS}s."
            )
        time.sleep(_POLL_INTERVAL_SECONDS)

    if get.status == GetTransactionStatus.FAILED:
        raise RuntimeError(f"Transaction {tx_hash} ({label}) FAILED: {get.result_xdr}")

    settled_return = AgentContext._decode_tx_result(get, scval, stellar_xdr)
    return settled_return if settled_return is not None else sim_return
