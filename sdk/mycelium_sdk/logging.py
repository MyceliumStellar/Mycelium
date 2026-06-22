"""
Structured logging for the Mycelium SDK.

The SDK used to narrate itself with bare `print("[SDK] ...")` calls, which a
production agent can neither silence nor route. This replaces that with a single
`mycelium` logger so callers can control verbosity:

    from mycelium_sdk import logging as mlog
    mlog.configure(quiet=True)            # production: warnings/errors only
    mlog.configure(level="DEBUG")         # development: full RPC narration

Configuration is also environment-driven, so an agent process needs no code
change:

    MYCELIUM_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR   (default INFO)
    MYCELIUM_QUIET=1                              (alias for WARNING + no banner)

Log lines go to stderr so they never pollute machine-readable stdout.
"""

import logging
import os
import sys

_LOGGER_NAME = "mycelium"
_configured = False


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the `mycelium` logger, or a `mycelium.<name>` child."""
    _ensure_configured()
    return logging.getLogger(_LOGGER_NAME if not name else f"{_LOGGER_NAME}.{name}")


def _resolve_env_level() -> int:
    if os.environ.get("MYCELIUM_QUIET"):
        return logging.WARNING
    name = (os.environ.get("MYCELIUM_LOG_LEVEL") or "INFO").upper()
    return getattr(logging, name, logging.INFO)


def _ensure_configured() -> None:
    """Attach a stderr handler to the `mycelium` logger once, honoring env vars."""
    global _configured
    if _configured:
        return
    _configured = True
    logger = logging.getLogger(_LOGGER_NAME)
    # Messages already carry their own ✓/❌/[tag] context, so keep the format
    # bare — this matches the old print() look while remaining filterable by level.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(_resolve_env_level())
    # Don't double-emit through the root logger if the host app configured one.
    logger.propagate = False


def configure(level: "str | int | None" = None, quiet: bool = False) -> None:
    """
    Set the SDK log level explicitly (overrides env).

    - quiet=True  -> WARNING and above only (production agents).
    - level="DEBUG"/"INFO"/... or a logging.* int -> that threshold.
    Also flips MYCELIUM_QUIET so the startup banner stays suppressed in quiet mode.
    """
    _ensure_configured()
    logger = logging.getLogger(_LOGGER_NAME)
    if quiet:
        os.environ["MYCELIUM_QUIET"] = "1"
        logger.setLevel(logging.WARNING)
        return
    if level is None:
        logger.setLevel(_resolve_env_level())
    elif isinstance(level, str):
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    else:
        logger.setLevel(level)


def is_quiet() -> bool:
    """True when the SDK is configured to suppress informational output."""
    return bool(os.environ.get("MYCELIUM_QUIET")) or get_logger().level > logging.INFO
