"""
Resilient RPC helpers: retry/backoff + idempotent submit.

Soroban testnet RPCs routinely return transient failures — `TRY_AGAIN_LATER`
when the ledger is congested, plus the usual 429/502/503/504 and dropped
connections. A naive `send_transaction` that gives up on the first hiccup makes
agents look flaky when the chain is fine.

`with_retry` wraps any RPC thunk in exponential backoff over *transient* errors
only (permanent errors — bad auth, contract reverts — propagate immediately).
`submit_transaction` adds idempotency on top: a built-and-signed transaction has
a deterministic hash, so on `TRY_AGAIN_LATER`/`DUPLICATE` we re-send the SAME
signed tx (never rebuild/re-sign) and then poll that hash — there is no risk of
double-submitting a second, different transaction.
"""

import time

from mycelium_sdk.logging import get_logger

log = get_logger("rpc")

# Substrings that mark a *transient* failure worth retrying. Anything else
# (auth, malformed tx, contract error) is permanent and must not be retried.
_TRANSIENT_MARKERS = (
    "try_again_later",
    "timeout",
    "timed out",
    "connection",
    "temporarily unavailable",
    "429",
    "502",
    "503",
    "504",
    "rate limit",
)


def is_transient(error: Exception) -> bool:
    """True if `error` looks like a retryable transient RPC failure."""
    return any(marker in str(error).lower() for marker in _TRANSIENT_MARKERS)


def with_retry(
    fn,
    *,
    retries: int = 5,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    retryable=is_transient,
    label: str = "rpc call",
):
    """
    Call `fn()` with exponential backoff over transient errors.

    Re-raises immediately on a non-transient error or once `retries` is
    exhausted. Backoff is base_delay, 2x, 4x, ... capped at max_delay.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as error:  # noqa: BLE001 - predicate decides what to swallow
            attempt += 1
            if attempt > retries or not retryable(error):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            log.warning(
                f"{label}: transient error ({error}); "
                f"retry {attempt}/{retries} in {delay:.1f}s"
            )
            time.sleep(delay)


def submit_transaction(soroban_rpc, signed_tx, *, retries: int = 5, base_delay: float = 1.0):
    """
    Submit an already-prepared, already-signed transaction idempotently.

    Returns the SendTransactionResponse once the RPC accepts it (PENDING) or
    reports it already in the mempool (DUPLICATE — same hash, safe to poll).
    Retries `send_transaction` on transient network errors (via with_retry) and
    loops on a `TRY_AGAIN_LATER` *status* with backoff, always re-sending the
    SAME signed tx so the on-chain hash never changes.
    """
    from stellar_sdk.soroban_rpc import SendTransactionStatus

    attempt = 0
    while True:
        send = with_retry(
            lambda: soroban_rpc.send_transaction(signed_tx),
            retries=retries,
            base_delay=base_delay,
            label="send_transaction",
        )
        if send.status in (SendTransactionStatus.PENDING, SendTransactionStatus.DUPLICATE):
            return send
        if send.status == SendTransactionStatus.TRY_AGAIN_LATER:
            attempt += 1
            if attempt > retries:
                raise RuntimeError(
                    f"Transaction still TRY_AGAIN_LATER after {retries} resubmits "
                    f"(hash {send.hash})."
                )
            delay = min(8.0, base_delay * (2 ** (attempt - 1)))
            log.warning(
                f"send_transaction: TRY_AGAIN_LATER (hash {send.hash}); "
                f"resubmitting same tx {attempt}/{retries} in {delay:.1f}s"
            )
            time.sleep(delay)
            continue
        # ERROR or any other status is permanent — surface it.
        raise RuntimeError(
            f"Transaction submission rejected: {send.status} "
            f"{getattr(send, 'error_result_xdr', '') or ''}"
        )
