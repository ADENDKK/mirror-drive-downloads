from __future__ import annotations

import os
import queue
import argparse
import threading
import tkinter as tk

from .clone import ClonePlan, new_mapfile, run_clone, validate_or_write_map_metadata, verify_sample
from .models import Disk, disk_capacity, human_size
from .system import MirrorDriveError, ensure_log_dir, find_disk, list_disks, require_commands, unmount_children
from .ui import target_choices


NAVY = "#111A4D"
INK = "#273052"
MUTED = "#687083"
MAGENTA = "#DF003F"
MAGENTA_DARK = "#C90039"
PALE_PINK = "#FFF0F4"
PAGE = "#F5F6F8"
WHITE = "#FFFFFF"
BORDER = "#DFE2E8"
DISABLED = "#EEF0F3"
SUCCESS = "#1F7A55"
FONT = "DM Sans"


class MirrorDriveApp:
    def __init__(self, root: tk.Tk, disks: list[Disk] | None = None, *, fullscreen: bool = True) -> None:
        self.root = root
        self.root.title("Mirror Drive | Plainloop Labs")
        self.root.configure(bg=PAGE)
        self.root.attributes("-fullscreen", fullscreen)
        if not fullscreen:
            self.root.geometry("1280x800")
        self.root.minsize(900, 650)
        self.canvas = tk.Canvas(root, bg=PAGE, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.render())
        self.root.bind("<Escape>", self.go_back)
        self.root.bind("<Up>", lambda _event: self.move(-1))
        self.root.bind("<Down>", lambda _event: self.move(1))
        self.root.bind("<Return>", lambda _event: self.advance())
        self.root.bind("<KP_Enter>", lambda _event: self.advance())

        self.disks: list[Disk] = disks or []
        self.source: Disk | None = None
        self.target: Disk | None = None
        self.screen = "source"
        self.active_index = 0
        self.message = ""
        self.status = "Preparing Mirror Drive..."
        self.progress = 0.08
        self.progress_direction = 1
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        if disks is None:
            self.refresh_disks()
        else:
            self.active_index = self._first_enabled_index()
            self.render()
        self.root.after(80, self.poll_events)

    @staticmethod
    def rounded_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, radius: float, **kwargs) -> int:
        points = (
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        )
        return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)

    def refresh_disks(self) -> None:
        try:
            self.disks = list_disks()
            self.message = ""
        except (MirrorDriveError, OSError) as error:
            self.disks = []
            self.message = str(error)
        self.active_index = self._first_enabled_index()
        self.render()

    def source_disabled_reason(self, disk: Disk) -> str | None:
        if disk.protected:
            return "Mirror Drive system"
        if disk.size <= 0:
            return "Unavailable"
        return None

    def visible_choices(self) -> list[tuple[Disk, str | None]]:
        if self.screen == "source":
            return [(disk, self.source_disabled_reason(disk)) for disk in self.disks]
        if self.screen == "target" and self.source is not None:
            return [(choice.disk, choice.disabled_reason or None) for choice in target_choices(self.disks, self.source)]
        return []

    def _first_enabled_index(self) -> int:
        for index, (_disk, reason) in enumerate(self.visible_choices()):
            if reason is None:
                return index
        return 0

    def move(self, direction: int) -> None:
        choices = self.visible_choices()
        if not choices or self.screen not in ("source", "target"):
            return
        for offset in range(1, len(choices) + 1):
            index = (self.active_index + direction * offset) % len(choices)
            if choices[index][1] is None:
                self.active_index = index
                self.render()
                return

    def choose(self, index: int) -> None:
        choices = self.visible_choices()
        if index >= len(choices) or choices[index][1] is not None:
            return
        self.active_index = index
        if self.screen == "source":
            self.source = choices[index][0]
        else:
            self.target = choices[index][0]
        self.render()

    def advance(self) -> None:
        if self.screen == "source":
            choices = self.visible_choices()
            if choices and choices[self.active_index][1] is None:
                self.source = choices[self.active_index][0]
                self.screen = "target"
                self.active_index = self._first_enabled_index()
                self.render()
        elif self.screen == "target":
            choices = self.visible_choices()
            if choices and choices[self.active_index][1] is None:
                self.target = choices[self.active_index][0]
                self.screen = "confirm"
                self.render()
        elif self.screen == "confirm" and self.target is not None:
            self.start_clone()
        elif self.screen in ("success", "error"):
            self.power_off()

    def go_back(self, _event=None) -> None:
        if self.screen == "target":
            self.screen = "source"
            self.active_index = self._first_enabled_index()
        elif self.screen == "confirm":
            self.screen = "target"
            self.active_index = self._first_enabled_index()
        elif self.screen == "error":
            self.screen = "source"
            self.active_index = self._first_enabled_index()
        elif self.screen in ("source", "success"):
            return
        self.render()

    def power_off(self) -> None:
        if os.geteuid() == 0:
            os.system("systemctl poweroff")
        else:
            self.root.destroy()

    def start_clone(self) -> None:
        if self.source is None or self.target is None:
            return
        self.screen = "clone"
        self.status = "Checking the selected drives..."
        self.progress = 0.08
        self.render()
        threading.Thread(target=self.clone_worker, daemon=True).start()
        self.animate_progress()

    def clone_worker(self) -> None:
        assert self.source is not None and self.target is not None
        try:
            require_commands(("ddrescue", "sync", "blockdev", "partprobe", "udevadm", "cmp"))
            fresh_disks = list_disks()
            source = find_disk(fresh_disks, self.source.path)
            target = find_disk(fresh_disks, self.target.path)
            for before, after, role in ((self.source, source, "source"), (self.target, target, "target")):
                if before.size != after.size or (before.serial and before.serial != after.serial):
                    raise MirrorDriveError(f"The {role} drive changed after confirmation. Nothing was written.")
            log_dir = ensure_log_dir("/var/log/mirror-drive")
            plan = ClonePlan(source, target, new_mapfile(log_dir, source, target), 3)
            plan.validate()
            validate_or_write_map_metadata(plan)
            if target.child_mountpoints or target.mountpoints:
                self.events.put(("status", "Unmounting the target drive..."))
                unmount_children(target)
            self.events.put(("status", "Creating an exact one-to-one copy..."))
            run_clone(plan)
            self.events.put(("status", "Verifying samples across the new drive..."))
            verify_sample(source.path, target.path, source.size)
            self.events.put(("success", "The drive was cloned and verified successfully."))
        except (MirrorDriveError, OSError) as error:
            self.events.put(("error", str(error)))
        except Exception as error:  # keep the appliance usable if a platform tool fails unexpectedly
            self.events.put(("error", f"Unexpected error: {error}"))

    def poll_events(self) -> None:
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "status":
                    self.status = text
                    self.render()
                elif kind == "success":
                    self.status = text
                    self.progress = 1.0
                    self.screen = "success"
                    self.render()
                elif kind == "error":
                    self.message = text
                    self.screen = "error"
                    self.render()
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def animate_progress(self) -> None:
        if self.screen != "clone":
            return
        self.progress += 0.012 * self.progress_direction
        if self.progress >= 0.88:
            self.progress_direction = -1
        elif self.progress <= 0.08:
            self.progress_direction = 1
        self.render()
        self.root.after(80, self.animate_progress)

    def render(self) -> None:
        if not self.canvas.winfo_exists():
            return
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 900)
        height = max(self.canvas.winfo_height(), 650)
        content_width = min(1060, width - 80)
        left = (width - content_width) / 2
        right = left + content_width
        if self.screen in ("source", "target", "confirm"):
            self.render_selection(left, right, height)
        elif self.screen == "clone":
            self.render_clone(left, right, height)
        elif self.screen == "success":
            self.render_result(left, right, height, success=True)
        elif self.screen == "error":
            self.render_result(left, right, height, success=False)

    def render_selection(self, left: float, right: float, height: float) -> None:
        step = {"source": 1, "target": 2, "confirm": 3}[self.screen]
        headings = {
            "source": ("Choose the source drive", "This drive will only be read. Nothing on it will be changed."),
            "target": ("Choose the destination drive", "Everything on the destination drive will be permanently overwritten."),
            "confirm": ("Review before cloning", "Check both drives carefully before you begin."),
        }
        title, subtitle = headings[self.screen]
        y = 40
        self.pill(left, y, "MIRROR DRIVE", MAGENTA, PALE_PINK)
        self.canvas.create_text(right, y + 13, text=f"STEP {step} OF 3", anchor="e", fill=MUTED, font=(FONT, 11, "bold"))
        heading_x = (left + right) / 2 if self.screen == "confirm" else left
        heading_anchor = "center" if self.screen == "confirm" else "w"
        self.canvas.create_text(heading_x, y + 62, text=title, anchor=heading_anchor, fill=NAVY, font=(FONT, 31, "bold"))
        self.canvas.create_text(heading_x, y + 104, text=subtitle, anchor=heading_anchor, fill=INK, font=(FONT, 15))
        track_y = y + 142
        self.canvas.create_line(left, track_y, right, track_y, fill=BORDER, width=4)
        self.canvas.create_line(left, track_y, left + (right - left) * step / 3, track_y, fill=MAGENTA, width=4)

        card_top = track_y + 30
        if self.screen in ("source", "target"):
            choices = self.visible_choices()
            if not choices:
                self.empty_state(left, right, card_top)
            else:
                for index, (disk, reason) in enumerate(choices[:4]):
                    self.disk_card(left, right, card_top + index * 91, disk, reason, index)
            self.footer(left, right, height, show_back=self.screen == "target", enabled=bool(choices and choices[self.active_index][1] is None))
        else:
            self.confirmation_card(left, right, card_top, height)

    def disk_card(self, left: float, right: float, top: float, disk: Disk, reason: str | None, index: int) -> None:
        selected = index == self.active_index
        fill = PALE_PINK if selected and reason is None else (DISABLED if reason else WHITE)
        outline = MAGENTA if selected and reason is None else BORDER
        tag = f"disk-card-{index}"
        self.rounded_rect(self.canvas, left, top, right, top + 74, 13, fill=fill, outline=outline, width=2 if selected else 1, tags=(tag,))
        model = disk.model or "Unknown drive"
        self.canvas.create_text(left + 22, top + 24, text=model, anchor="w", fill=NAVY if reason is None else MUTED, font=(FONT, 15, "bold"), tags=(tag,))
        details = "  •  ".join(part for part in (disk.path, human_size(disk.size), disk.transport.upper()) if part)
        self.canvas.create_text(left + 22, top + 51, text=details, anchor="w", fill=MUTED, font=(FONT, 11), tags=(tag,))
        if reason:
            self.pill(right - 18, top + 24, reason.upper(), MUTED, WHITE, anchor="e")
        elif selected:
            self.pill(right - 18, top + 24, "SELECTED", MAGENTA, WHITE, anchor="e")
        self.canvas.tag_bind(tag, "<Button-1>", lambda _event, value=index: self.choose(value))

    def empty_state(self, left: float, right: float, top: float) -> None:
        self.rounded_rect(self.canvas, left, top, right, top + 160, 14, fill=WHITE, outline=BORDER)
        self.canvas.create_text((left + right) / 2, top + 54, text="No usable drives found", fill=NAVY, font=(FONT, 18, "bold"))
        detail = self.message or "Connect at least two drives, then refresh the list."
        self.canvas.create_text((left + right) / 2, top + 88, text=detail, fill=MUTED, font=(FONT, 12))
        self.button((left + right) / 2 - 75, top + 112, (left + right) / 2 + 75, top + 151, "Refresh drives", self.refresh_disks, primary=False)

    def confirmation_card(self, left: float, right: float, top: float, height: float) -> None:
        assert self.source is not None and self.target is not None
        self.rounded_rect(self.canvas, left, top, right, top + 150, 14, fill=WHITE, outline=BORDER)
        mid = (left + right) / 2
        column_offset = (right - left) / 4
        self.drive_summary(left + column_offset, top + 24, "SOURCE — READ ONLY", self.source, NAVY)
        self.canvas.create_text(mid, top + 82, text="→", fill=MAGENTA, font=(FONT, 26, "bold"))
        self.drive_summary(right - column_offset, top + 24, "DESTINATION — WILL BE ERASED", self.target, MAGENTA)
        self.footer(left, right, height, show_back=True, enabled=True, label="Start cloning")

    def drive_summary(self, center_x: float, top: float, label: str, disk: Disk, color: str) -> None:
        self.canvas.create_text(center_x, top, text=label, anchor="n", fill=color, font=(FONT, 10, "bold"))
        self.canvas.create_text(center_x, top + 37, text=disk.model or "Unknown drive", anchor="n", fill=NAVY, font=(FONT, 16, "bold"))
        self.canvas.create_text(center_x, top + 68, text=disk_capacity(disk.size), anchor="n", fill=MUTED, font=(FONT, 11, "bold"))

    def render_clone(self, left: float, right: float, height: float) -> None:
        y = 50
        self.pill(left, y, "MIRROR DRIVE", MAGENTA, PALE_PINK)
        self.canvas.create_text(left, y + 72, text="Cloning your drive", anchor="w", fill=NAVY, font=(FONT, 34, "bold"))
        self.canvas.create_text(left, y + 116, text="Keep the computer connected to power and do not remove either drive.", anchor="w", fill=INK, font=(FONT, 15))
        top = y + 168
        self.rounded_rect(self.canvas, left, top, right, top + 248, 16, fill=WHITE, outline=BORDER)
        assert self.source is not None and self.target is not None
        self.canvas.create_text(left + 30, top + 34, text=f"{self.source.model or self.source.path}  →  {self.target.model or self.target.path}", anchor="nw", fill=NAVY, font=(FONT, 17, "bold"))
        self.canvas.create_text(left + 30, top + 77, text=self.status, anchor="nw", fill=INK, font=(FONT, 14))
        bar_left, bar_right, bar_y = left + 30, right - 30, top + 132
        self.rounded_rect(self.canvas, bar_left, bar_y, bar_right, bar_y + 16, 8, fill=DISABLED, outline="")
        progress_right = bar_left + max(18, (bar_right - bar_left) * self.progress)
        self.rounded_rect(self.canvas, bar_left, bar_y, progress_right, bar_y + 16, 8, fill=MAGENTA, outline="")
        self.canvas.create_text(left + 30, top + 178, text="A recovery map is saved automatically, so an interrupted clone can be resumed safely.", anchor="nw", fill=MUTED, font=(FONT, 11), width=right - left - 60)
        self.canvas.create_text(left, height - 40, text="Plainloop Labs  •  Simple, transparent and controlled", anchor="w", fill=MUTED, font=(FONT, 10))

    def render_result(self, left: float, right: float, height: float, *, success: bool) -> None:
        top = 62
        tone = SUCCESS if success else MAGENTA
        badge = "CLONE COMPLETE" if success else "CLONE STOPPED"
        self.pill(left, top, badge, tone, PALE_PINK if not success else "#EBF7F1")
        title = "Your new drive is ready" if success else "Mirror Drive could not continue"
        self.canvas.create_text(left, top + 76, text=title, anchor="w", fill=NAVY, font=(FONT, 34, "bold"))
        body = self.status if success else self.message
        self.canvas.create_text(left, top + 130, text=body, anchor="nw", fill=INK, font=(FONT, 15), width=right - left)
        if success:
            detail = "Shut down, disconnect the source drive, then boot from the new drive first."
        else:
            detail = "No further writes will be started. You can go back and review the connected drives."
        self.rounded_rect(self.canvas, left, top + 196, right, top + 306, 14, fill=WHITE, outline=BORDER)
        self.canvas.create_text(left + 24, top + 228, text="NEXT STEP", anchor="nw", fill=tone, font=(FONT, 10, "bold"))
        self.canvas.create_text(left + 24, top + 260, text=detail, anchor="nw", fill=NAVY, font=(FONT, 14), width=right - left - 48)
        if not success:
            self.button(right - 330, height - 76, right - 170, height - 30, "Back to drives", lambda: self.go_back(), primary=False)
        self.button(right - 154, height - 76, right, height - 30, "Shut down", self.power_off, primary=True)

    def footer(self, left: float, right: float, height: float, *, show_back: bool, enabled: bool, label: str = "Continue") -> None:
        y1, y2 = height - 76, height - 30
        self.canvas.create_text(left, (y1 + y2) / 2, text="↑↓  Select    ENTER  Continue    ESC  Back", anchor="w", fill=MUTED, font=(FONT, 10))
        if show_back:
            self.button(right - 330, y1, right - 170, y2, "Back", lambda: self.go_back(), primary=False)
        self.button(right - 154, y1, right, y2, label, self.advance, primary=True, enabled=enabled)

    def button(self, x1: float, y1: float, x2: float, y2: float, text: str, command, *, primary: bool, enabled: bool = True) -> None:
        fill = MAGENTA if primary and enabled else (WHITE if enabled else DISABLED)
        color = WHITE if primary and enabled else (NAVY if enabled else MUTED)
        outline = MAGENTA if primary and enabled else BORDER
        item = self.rounded_rect(self.canvas, x1, y1, x2, y2, 10, fill=fill, outline=outline)
        label = self.canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=text, fill=color, font=(FONT, 11, "bold"))
        if enabled:
            self.canvas.tag_bind(item, "<Button-1>", lambda _event: command())
            self.canvas.tag_bind(label, "<Button-1>", lambda _event: command())

    def pill(self, x: float, y: float, text: str, color: str, fill: str, *, anchor: str = "w") -> None:
        width = max(92, len(text) * 7.1 + 24)
        if anchor == "e":
            x1, x2 = x - width, x
        else:
            x1, x2 = x, x + width
        self.rounded_rect(self.canvas, x1, y, x2, y + 27, 13, fill=fill, outline="")
        self.canvas.create_text((x1 + x2) / 2, y + 13, text=text, fill=color, font=(FONT, 9, "bold"))


def demo_disks() -> list[Disk]:
    return [
        Disk("nvme0n1", "/dev/nvme0n1", 512 * 1000**3, model="Samsung SSD 980", serial="PLAIN-SOURCE", transport="nvme"),
        Disk("sda", "/dev/sda", 1000 * 1000**3, model="Crucial MX500", serial="PLAIN-TARGET", transport="usb"),
        Disk("sdb", "/dev/sdb", 256 * 1000**3, model="Kingston DataTraveler", serial="LIVE-USB", transport="usb", protected=True, removable=True),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mirror Drive graphical interface")
    parser.add_argument("--demo", action="store_true", help="show safe demonstration disks")
    parser.add_argument("--windowed", action="store_true", help="open a resizable development window")
    args = parser.parse_args(argv)
    root = tk.Tk()
    MirrorDriveApp(root, demo_disks() if args.demo else None, fullscreen=not args.windowed)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
