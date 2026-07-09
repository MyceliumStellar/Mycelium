"""
Startup banner shared by the Mycelium CLI and SDK.

Renders the MYCELIUM wordmark as FILLED (solid-block) glyphs in green, with the
tagline "Give your agent a wallet". Printed to stderr so it never pollutes
machine-readable stdout (e.g. `mycelium compile` output). ANSI colour is only
emitted to a TTY, and the whole banner can be suppressed with the
MYCELIUM_NO_BANNER environment variable.
"""

import os
import shutil
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

# Smaller ASCII art fallback (width 50) for medium terminals.
_ART_SMALL = r"""
 __  __  _  _  ___  ___  _     ___  _   _  __  __ 
|  \/  |\ \/ // __|| __|| |   |_ _|| | | ||  \/  |
| |\/| | \  / | (__| _| | |__  | | | |_| || |\/| |
|_|  |_|  \/  \___||___||____||___| \___/ |_|  |_|
"""

_TAGLINE = "🐝  Give your agent a wallet"

_shown = False


def get_terminal_columns() -> int:
    """Return the terminal window width, handling Windows-specific window vs buffer differences."""
    if sys.platform == "win32":
        try:
            import ctypes

            class COORD(ctypes.Structure):
                _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

            class SMALL_RECT(ctypes.Structure):
                _fields_ = [
                    ("Left", ctypes.c_short),
                    ("Top", ctypes.c_short),
                    ("Right", ctypes.c_short),
                    ("Bottom", ctypes.c_short)
                ]

            class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
                _fields_ = [
                    ("dwSize", COORD),
                    ("dwCursorPosition", COORD),
                    ("wAttributes", ctypes.c_ushort),
                    ("srWindow", SMALL_RECT),
                    ("dwMaximumWindowSize", COORD)
                ]

            # STD_ERROR_HANDLE = -12, STD_OUTPUT_HANDLE = -11
            for std_handle in [-12, -11]:
                h = ctypes.windll.kernel32.GetStdHandle(std_handle)
                csbi = CONSOLE_SCREEN_BUFFER_INFO()
                if ctypes.windll.kernel32.GetConsoleScreenBufferInfo(h, ctypes.byref(csbi)):
                    window_width = csbi.srWindow.Right - csbi.srWindow.Left + 1
                    if window_width > 0:
                        return window_width
        except Exception:
            pass

    columns, _ = shutil.get_terminal_size(fallback=(80, 24))
    return columns


def _has_ansi_support() -> bool:
    """Check if the environment supports ANSI escape codes, enabling them on Windows if needed."""
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # Try to enable virtual terminal processing on stderr (STD_ERROR_HANDLE = -12)
            h_err = kernel32.GetStdHandle(-12)
            mode_err = ctypes.c_ulong()
            if kernel32.GetConsoleMode(h_err, ctypes.byref(mode_err)):
                # 0x0004 is ENABLE_VIRTUAL_TERMINAL_PROCESSING
                if kernel32.SetConsoleMode(h_err, mode_err.value | 0x0004):
                    return True
            # Also try stdout (STD_OUTPUT_HANDLE = -11)
            h_out = kernel32.GetStdHandle(-11)
            mode_out = ctypes.c_ulong()
            if kernel32.GetConsoleMode(h_out, ctypes.byref(mode_out)):
                if kernel32.SetConsoleMode(h_out, mode_out.value | 0x0004):
                    return True
            return False
        except Exception:
            return False
    # On non-Windows platforms, we assume ANSI is supported if it is a TTY
    return True


def render(color: bool = True, version: str | None = None) -> str:
    """Return the banner string, with or without ANSI green colouring,
    dynamically scaled to fit the terminal width.
    """
    columns = get_terminal_columns()

    if columns >= 67:
        art = _ART.strip("\n")
        tagline_pad = "   "
    elif columns >= 50:
        art = _ART_SMALL.strip("\n")
        tagline_pad = "   "
    else:
        # Fallback for extremely narrow terminals
        art = "[ MYCELIUM ]"
        tagline_pad = " "

    version_line = f"\n{tagline_pad}📦  v{version}" if version else ""

    if color:
        return f"{_GREEN}{art}\n{tagline_pad}{_TAGLINE}{version_line}{_RESET}\n"
    return f"{art}\n{tagline_pad}{_TAGLINE}{version_line}\n"


def print_banner(stream=None, version: str | None = None) -> None:
    """Unconditionally write the banner to `stream` (default stderr)."""
    stream = stream or sys.stderr
    color = hasattr(stream, "isatty") and stream.isatty() and _has_ansi_support()
    stream.write(render(color=color, version=version))
    stream.flush()


def show_startup_banner(stream=None, version: str | None = None) -> None:
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
    print_banner(stream=stream, version=version)
