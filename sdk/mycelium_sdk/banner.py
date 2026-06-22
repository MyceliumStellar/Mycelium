"""
Startup banner shared by the Mycelium CLI and SDK.

Renders the MYCELIUM wordmark as FILLED (solid-block) glyphs in green, with the
tagline "Give your agent a wallet". Printed to stderr so it never pollutes
machine-readable stdout (e.g. `mycelium compile` output). ANSI colour is only
emitted to a TTY, and the whole banner can be suppressed with the
MYCELIUM_NO_BANNER environment variable.
"""

import os
import sys

# Bright green, applied to the whole wordmark.
_GREEN = "\033[92m"
_RESET = "\033[0m"

# Filled block wordmark (solid glyph bodies, not an outline font).
_ART = r"""
███╗   ███╗██╗   ██╗ ██████╗███████╗██╗     ██╗██╗   ██╗███╗   ███╗
████╗ ████║╚██╗ ██╔╝██╔════╝██╔════╝██║     ██║██║   ██║████╗ ████║
██╔████╔██║ ╚████╔╝ ██║     █████╗  ██║     ██║██║   ██║██╔████╔██║
██║╚██╔╝██║  ╚██╔╝  ██║     ██╔══╝  ██║     ██║██║   ██║██║╚██╔╝██║
██║ ╚═╝ ██║   ██║   ╚██████╗███████╗███████╗██║╚██████╔╝██║ ╚═╝ ██║
╚═╝     ╚═╝   ╚═╝    ╚═════╝╚══════╝╚══════╝╚═╝ ╚═════╝ ╚═╝     ╚═╝
"""

_TAGLINE = "🐝  Give your agent a wallet"

_shown = False


def render(color: bool = True) -> str:
    """Return the banner string, with or without ANSI green colouring."""
    art = _ART.strip("\n")
    if color:
        return f"{_GREEN}{art}\n   {_TAGLINE}{_RESET}\n"
    return f"{art}\n   {_TAGLINE}\n"


def print_banner(stream=None) -> None:
    """Unconditionally write the banner to `stream` (default stderr)."""
    stream = stream or sys.stderr
    color = hasattr(stream, "isatty") and stream.isatty()
    stream.write(render(color=color))
    stream.flush()


def show_startup_banner(stream=None) -> None:
    """
    Print the banner once per process, unless MYCELIUM_NO_BANNER is set.
    Used as the 'starting' banner for both the CLI and the SDK runtime.
    """
    global _shown
    if _shown:
        return
    _shown = True
    # MYCELIUM_QUIET (set by mycelium_sdk.logging.configure(quiet=True)) silences
    # the banner alongside informational logs, for production agents.
    if os.environ.get("MYCELIUM_NO_BANNER") or os.environ.get("MYCELIUM_QUIET"):
        return
    print_banner(stream=stream)
