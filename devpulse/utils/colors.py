# devpulse/utils/colors.py
# Terminal color and formatting utilities.
# Hand-written ANSI escape codes — no colorama, no rich, no termcolor.
# Includes: Color class, Table renderer, Spinner, progress bar.

import sys
import os
import time
import threading
import shutil
from typing import List, Optional, Tuple


def _supports_color() -> bool:
    """Detect if the terminal supports ANSI color codes."""
    if not hasattr(sys.stdout, "isatty"):
        return False
    if not sys.stdout.isatty():
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return True


_COLOR_ENABLED = _supports_color()


class _Codes:
    """Raw ANSI escape codes."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    ITALIC  = "\033[3m"
    UNDER   = "\033[4m"
    BLINK   = "\033[5m"
    REVERSE = "\033[7m"
    STRIKE  = "\033[9m"

    # Foreground colors
    BLACK   = "\033[30m"
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN    = "\033[36m"
    WHITE   = "\033[37m"

    # Bright foreground
    BRIGHT_BLACK   = "\033[90m"
    BRIGHT_RED     = "\033[91m"
    BRIGHT_GREEN   = "\033[92m"
    BRIGHT_YELLOW  = "\033[93m"
    BRIGHT_BLUE    = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN    = "\033[96m"
    BRIGHT_WHITE   = "\033[97m"

    # Background colors
    BG_BLACK   = "\033[40m"
    BG_RED     = "\033[41m"
    BG_GREEN   = "\033[42m"
    BG_YELLOW  = "\033[43m"
    BG_BLUE    = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN    = "\033[46m"
    BG_WHITE   = "\033[47m"

    # Cursor / screen
    CLEAR_LINE  = "\033[2K\r"
    CURSOR_UP   = "\033[1A"
    HIDE_CURSOR = "\033[?25l"
    SHOW_CURSOR = "\033[?25h"


def _apply(code: str, text: str) -> str:
    if not _COLOR_ENABLED:
        return text
    return f"{code}{text}{_Codes.RESET}"


class Color:
    """Static methods for applying color/style to strings."""

    @staticmethod
    def red(s: str) -> str:        return _apply(_Codes.RED, s)
    @staticmethod
    def green(s: str) -> str:      return _apply(_Codes.GREEN, s)
    @staticmethod
    def yellow(s: str) -> str:     return _apply(_Codes.YELLOW, s)
    @staticmethod
    def blue(s: str) -> str:       return _apply(_Codes.BLUE, s)
    @staticmethod
    def magenta(s: str) -> str:    return _apply(_Codes.MAGENTA, s)
    @staticmethod
    def cyan(s: str) -> str:       return _apply(_Codes.CYAN, s)
    @staticmethod
    def white(s: str) -> str:      return _apply(_Codes.WHITE, s)
    @staticmethod
    def dim(s: str) -> str:        return _apply(_Codes.DIM, s)
    @staticmethod
    def bold(s: str) -> str:       return _apply(_Codes.BOLD, s)
    @staticmethod
    def italic(s: str) -> str:     return _apply(_Codes.ITALIC, s)
    @staticmethod
    def underline(s: str) -> str:  return _apply(_Codes.UNDER, s)
    @staticmethod
    def strike(s: str) -> str:     return _apply(_Codes.STRIKE, s)
    @staticmethod
    def success(s: str) -> str:    return _apply(_Codes.BRIGHT_GREEN, s)
    @staticmethod
    def error(s: str) -> str:      return _apply(_Codes.BRIGHT_RED, s)
    @staticmethod
    def warn(s: str) -> str:       return _apply(_Codes.BRIGHT_YELLOW, s)
    @staticmethod
    def info(s: str) -> str:       return _apply(_Codes.BRIGHT_CYAN, s)
    @staticmethod
    def muted(s: str) -> str:      return _apply(_Codes.BRIGHT_BLACK, s)

    @staticmethod
    def bg_green(s: str) -> str:   return _apply(_Codes.BG_GREEN + _Codes.BLACK, s)
    @staticmethod
    def bg_red(s: str) -> str:     return _apply(_Codes.BG_RED + _Codes.WHITE, s)
    @staticmethod
    def bg_blue(s: str) -> str:    return _apply(_Codes.BG_BLUE + _Codes.WHITE, s)
    @staticmethod
    def bg_yellow(s: str) -> str:  return _apply(_Codes.BG_YELLOW + _Codes.BLACK, s)

    @staticmethod
    def strip(s: str) -> str:
        """Remove all ANSI escape codes from a string."""
        import re
        return re.sub(r"\033\[[0-9;]*m", "", s)

    @staticmethod
    def len(s: str) -> int:
        """Return visible length of string (ignoring escape codes)."""
        return len(Color.strip(s))


def print_success(msg: str) -> None:
    print(f"{Color.success('✓')} {msg}")

def print_error(msg: str) -> None:
    print(f"{Color.error('✗')} {msg}", file=sys.stderr)

def print_warn(msg: str) -> None:
    print(f"{Color.warn('!')} {msg}")

def print_info(msg: str) -> None:
    print(f"{Color.info('→')} {msg}")

def print_header(title: str) -> None:
    width = shutil.get_terminal_size((80, 24)).columns
    print()
    print(Color.bold(title))
    print(Color.muted("─" * min(len(title) + 4, width)))


class Table:
    """
    Terminal table renderer. Handles column alignment, truncation,
    colored headers, and automatic width fitting.

    Usage:
        t = Table(["Name", "Commits", "LOC"])
        t.add_row(["devpulse", "142", "6200"])
        t.print()
    """

    ALIGN_LEFT   = "left"
    ALIGN_RIGHT  = "right"
    ALIGN_CENTER = "center"

    def __init__(self, headers: List[str],
                 alignments: Optional[List[str]] = None,
                 max_width: Optional[int] = None):
        self.headers = headers
        self.alignments = alignments or [self.ALIGN_LEFT] * len(headers)
        self.rows: List[List[str]] = []
        self.max_width = max_width or shutil.get_terminal_size((120, 24)).columns

        # Pad alignments if shorter than headers
        while len(self.alignments) < len(self.headers):
            self.alignments.append(self.ALIGN_LEFT)

    def add_row(self, row: List[str]) -> None:
        # Ensure row has same column count as headers
        padded = list(row) + [""] * max(0, len(self.headers) - len(row))
        self.rows.append(padded[:len(self.headers)])

    def add_separator(self) -> None:
        self.rows.append(["---SEP---"])

    def _col_widths(self) -> List[int]:
        widths = [Color.len(h) for h in self.headers]
        for row in self.rows:
            if row == ["---SEP---"]:
                continue
            for i, cell in enumerate(row):
                if i < len(widths):
                    widths[i] = max(widths[i], Color.len(str(cell)))
        return widths

    def _fit_widths(self, widths: List[int]) -> List[int]:
        """Scale column widths to fit terminal if needed."""
        total = sum(widths) + (3 * (len(widths) - 1)) + 4
        if total <= self.max_width:
            return widths
        # Shrink the widest column iteratively
        widths = list(widths)
        while sum(widths) + (3 * (len(widths) - 1)) + 4 > self.max_width:
            max_i = widths.index(max(widths))
            widths[max_i] -= 1
            if widths[max_i] < 4:
                break
        return widths

    def _cell(self, text: str, width: int, align: str) -> str:
        visible = Color.strip(text)
        padding = width - len(visible)
        if padding < 0:
            # Truncate — but preserve color codes before truncating
            text = Color.strip(text)[:width - 1] + "…"
            padding = 0
        if align == self.ALIGN_RIGHT:
            return " " * padding + text
        if align == self.ALIGN_CENTER:
            left = padding // 2
            right = padding - left
            return " " * left + text + " " * right
        return text + " " * padding

    def render(self) -> str:
        widths = self._fit_widths(self._col_widths())
        sep_char = Color.muted("│")
        h_line = Color.muted("┼".join("─" * (w + 2) for w in widths))

        lines = []

        # Header row
        header_cells = []
        for i, h in enumerate(self.headers):
            cell = self._cell(Color.bold(h), widths[i], self.ALIGN_LEFT)
            header_cells.append(f" {cell} ")
        lines.append(sep_char.join(header_cells))
        lines.append(h_line)

        # Data rows
        for row in self.rows:
            if row == ["---SEP---"]:
                lines.append(h_line)
                continue
            cells = []
            for i, cell in enumerate(row):
                align = self.alignments[i] if i < len(self.alignments) else self.ALIGN_LEFT
                formatted = self._cell(str(cell), widths[i], align)
                cells.append(f" {formatted} ")
            lines.append(sep_char.join(cells))

        return "\n".join(lines)

    def print(self) -> None:
        print(self.render())
        print()


class ProgressBar:
    """
    Simple terminal progress bar.
    Usage:
        bar = ProgressBar(total=100, label="Analyzing")
        for i in range(100):
            bar.update(i + 1)
        bar.done()
    """

    def __init__(self, total: int, label: str = "",
                 width: int = 40, show_percent: bool = True):
        self.total = max(total, 1)
        self.label = label
        self.width = width
        self.show_percent = show_percent
        self.current = 0

    def update(self, current: int) -> None:
        self.current = min(current, self.total)
        self._render()

    def _render(self) -> None:
        pct = self.current / self.total
        filled = int(self.width * pct)
        bar = Color.green("█" * filled) + Color.muted("░" * (self.width - filled))
        pct_str = f" {int(pct * 100):3d}%" if self.show_percent else ""
        label = f"{self.label} " if self.label else ""
        line = f"\r{label}[{bar}]{pct_str} {self.current}/{self.total}"
        sys.stdout.write(_Codes.CLEAR_LINE + line)
        sys.stdout.flush()

    def done(self, message: str = "") -> None:
        self._render()
        suffix = f" — {message}" if message else ""
        print(f"{suffix}")


class Spinner:
    """
    Animated terminal spinner for long-running operations.
    Runs in a background thread.

    Usage:
        with Spinner("Analyzing repo..."):
            do_slow_thing()
    """

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    INTERVAL = 0.08

    def __init__(self, message: str = "Working...", color_fn=None):
        self.message = message
        self.color_fn = color_fn or Color.cyan
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _spin(self) -> None:
        if not _COLOR_ENABLED:
            print(f"{self.message}")
            return
        sys.stdout.write(_Codes.HIDE_CURSOR)
        sys.stdout.flush()
        i = 0
        while not self._stop_event.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(
                f"\r{self.color_fn(frame)} {self.message}"
            )
            sys.stdout.flush()
            time.sleep(self.INTERVAL)
            i += 1
        sys.stdout.write(_Codes.CLEAR_LINE)
        sys.stdout.write(_Codes.SHOW_CURSOR)
        sys.stdout.flush()

    def start(self) -> "Spinner":
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def stop(self, final_message: str = "", success: bool = True) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if final_message:
            icon = Color.success("✓") if success else Color.error("✗")
            print(f"{icon} {final_message}")

    def __enter__(self) -> "Spinner":
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type:
            self.stop(f"{self.message} failed", success=False)
        else:
            self.stop(f"{self.message} done", success=True)
        return False


def banner(title: str, version: str, subtitle: str = "") -> None:
    """Print a startup banner for the CLI or server."""
    width = shutil.get_terminal_size((80, 24)).columns
    box_width = max(len(title) + len(version) + 6, 40)

    top    = Color.cyan("╔" + "═" * box_width + "╗")
    bottom = Color.cyan("╚" + "═" * box_width + "╝")
    middle_title = (
        Color.cyan("║ ")
        + Color.bold(Color.white(title))
        + "  "
        + Color.muted(f"v{version}")
        + " " * (box_width - len(title) - len(version) - 4)
        + Color.cyan(" ║")
    )

    print(top)
    print(middle_title)
    if subtitle:
        sub_line = (
            Color.cyan("║ ")
            + Color.muted(subtitle)
            + " " * (box_width - len(subtitle) - 1)
            + Color.cyan("║")
        )
        print(sub_line)
    print(bottom)
    print()