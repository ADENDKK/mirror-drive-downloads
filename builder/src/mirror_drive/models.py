from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Disk:
    name: str
    path: str
    size: int
    model: str = ""
    serial: str = ""
    transport: str = ""
    read_only: bool = False
    removable: bool = False
    mountpoints: tuple[str, ...] = field(default_factory=tuple)
    child_mountpoints: tuple[str, ...] = field(default_factory=tuple)
    protected: bool = False

    @property
    def label(self) -> str:
        identity = " ".join(value for value in (self.model, self.serial) if value).strip()
        return f"{self.path}  {human_size(self.size)}  {identity or 'Unknown disk'}"

    @property
    def confirmation_id(self) -> str:
        return self.serial.strip() or self.name


def human_size(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def disk_capacity(size: int) -> str:
    """Format a physical disk's advertised decimal capacity for the UI."""
    if size >= 1_000_000_000_000:
        value = size / 1_000_000_000_000
        rounded = round(value)
        if abs(value - rounded) < 0.03:
            return f"{rounded}TB"
        return f"{value:.2f}".rstrip("0").rstrip(".") + "TB"

    value = size / 1_000_000_000
    rounded = round(value)
    if abs(value - rounded) < 0.5:
        return f"{rounded}GB"
    return f"{value:.1f}".rstrip("0").rstrip(".") + "GB"
