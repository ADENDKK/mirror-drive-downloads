from __future__ import annotations

from dataclasses import dataclass

try:
    import curses
except ModuleNotFoundError:  # Windows development hosts do not ship _curses.
    curses = None  # type: ignore[assignment]

from .models import Disk, human_size
from .system import MirrorDriveError


class UserCancelled(MirrorDriveError):
    pass


@dataclass(frozen=True)
class Choice:
    disk: Disk
    disabled_reason: str | None = None


def target_choices(disks: list[Disk], source: Disk) -> list[Choice]:
    choices: list[Choice] = []
    for disk in disks:
        reason = None
        if disk.path == source.path:
            reason = "source disk"
        elif disk.protected:
            reason = "Mirror Drive system"
        elif disk.read_only:
            reason = "read-only"
        elif disk.size < source.size:
            reason = "too small"
        choices.append(Choice(disk, reason))
    return choices


class MirrorDriveUI:
    HEADER = 1
    ACCENT = 2
    MUTED = 3
    WARNING = 4
    DANGER = 5
    SELECTED = 6
    SUCCESS = 7

    def __init__(self, screen: curses.window, disks: list[Disk]):
        self.screen = screen
        self.disks = disks
        curses.curs_set(0)
        screen.keypad(True)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(self.HEADER, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(self.ACCENT, curses.COLOR_CYAN, -1)
            curses.init_pair(self.MUTED, curses.COLOR_WHITE, -1)
            curses.init_pair(self.WARNING, curses.COLOR_YELLOW, -1)
            curses.init_pair(self.DANGER, curses.COLOR_RED, -1)
            curses.init_pair(self.SELECTED, curses.COLOR_BLACK, curses.COLOR_WHITE)
            curses.init_pair(self.SUCCESS, curses.COLOR_GREEN, -1)

    def _text(self, row: int, col: int, value: str, style: int = 0, width: int | None = None) -> None:
        height, columns = self.screen.getmaxyx()
        if row < 0 or row >= height or col >= columns:
            return
        available = max(0, columns - col - 1)
        if width is not None:
            available = min(available, width)
        try:
            self.screen.addnstr(row, col, value, available, curses.color_pair(style) if style else 0)
        except curses.error:
            pass

    def _frame(self, step: str, title: str, subtitle: str) -> tuple[int, int]:
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        if height < 22 or width < 72:
            raise MirrorDriveError("Mirror Drive needs a terminal of at least 72x22 characters.")
        header = " PLAINLOOP  /  MIRROR DRIVE "
        self._text(0, 0, header.ljust(width - 1), self.HEADER)
        self._text(2, 3, step.upper(), self.ACCENT)
        self._text(3, 3, title, self.ACCENT)
        self._text(4, 3, subtitle, self.MUTED)
        self._text(height - 2, 3, "↑↓ Move    ENTER Select    Q Quit", self.MUTED)
        return height, width

    @staticmethod
    def _disk_detail(disk: Disk) -> str:
        parts = [human_size(disk.size)]
        if disk.transport:
            parts.append(disk.transport.upper())
        if disk.serial:
            parts.append(f"S/N {disk.serial}")
        return "  •  ".join(parts)

    def choose(self, choices: list[Choice], step: str, title: str, subtitle: str, *, danger: bool = False) -> Disk:
        active = next((index for index, choice in enumerate(choices) if not choice.disabled_reason), 0)
        while True:
            height, width = self._frame(step, title, subtitle)
            visible_rows = max(1, height - 10)
            start = max(0, min(active - visible_rows + 1, len(choices) - visible_rows))
            for line, choice in enumerate(choices[start:start + visible_rows]):
                index = start + line
                row = 7 + line * 3
                if row + 1 >= height - 2:
                    break
                selected = index == active
                style = self.SELECTED if selected else (self.MUTED if choice.disabled_reason else (self.DANGER if danger else self.ACCENT))
                marker = "▶" if selected else " "
                name = choice.disk.model or "Unknown disk"
                disabled = f"  —  {choice.disabled_reason}" if choice.disabled_reason else ""
                self._text(row, 3, f"{marker} {choice.disk.path}  {name}{disabled}", style, width - 7)
                self._text(row + 1, 7, self._disk_detail(choice.disk), self.MUTED, width - 11)
            self.screen.refresh()
            key = self.screen.getch()
            if key in (ord("q"), ord("Q"), 27):
                raise UserCancelled("Cancelled; nothing was changed.")
            if key in (curses.KEY_UP, ord("k")):
                active = (active - 1) % len(choices)
            elif key in (curses.KEY_DOWN, ord("j")):
                active = (active + 1) % len(choices)
            elif key in (10, 13, curses.KEY_ENTER) and not choices[active].disabled_reason:
                return choices[active].disk

    def confirm(self, source: Disk, target: Disk) -> None:
        while True:
            height, width = self._frame("STEP 3 OF 3", "Review and confirm", "Nothing has been written yet.")
            self._text(7, 4, "SOURCE", self.ACCENT)
            self._text(8, 6, source.label, self.MUTED, width - 12)
            self._text(10, 4, "TARGET — ALL DATA WILL BE ERASED", self.DANGER)
            self._text(11, 6, target.label, self.DANGER, width - 12)
            self._text(14, 4, "Press ENTER to start cloning.", self.WARNING, width - 8)
            self._text(height - 2, 3, "ENTER Start cloning    ESC Cancel", self.MUTED)
            self.screen.refresh()
            key = self.screen.get_wch()
            if key == "\x1b":
                raise UserCancelled("Cancelled; nothing was changed.")
            if key in ("\n", "\r"):
                return

    def run(self) -> tuple[Disk, Disk]:
        source = self.choose(
            [Choice(disk) for disk in self.disks],
            "STEP 1 OF 3",
            "Choose the source disk",
            "Mirror Drive reads from this disk. It will not be modified.",
        )
        target = self.choose(
            target_choices(self.disks, source),
            "STEP 2 OF 3",
            "Choose the target disk",
            "The selected target will be overwritten completely.",
            danger=True,
        )
        self.confirm(source, target)
        return source, target


def interactive_selection(disks: list[Disk]) -> tuple[Disk, Disk]:
    if curses is None:
        raise MirrorDriveError("The full-screen interface requires a terminal with curses support.")
    return curses.wrapper(lambda screen: MirrorDriveUI(screen, disks).run())
