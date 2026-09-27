from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterable

from .models import Disk

MIN_DISK_BYTES = 1024 * 1024


class MirrorDriveError(RuntimeError):
    pass


def require_commands(commands: Iterable[str]) -> None:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise MirrorDriveError("Missing required tools: " + ", ".join(missing))


def _mountpoints(node: dict) -> tuple[str, ...]:
    values: list[str] = []
    mountpoints = node.get("mountpoints")
    if mountpoints is None:
        mountpoints = [node.get("mountpoint")]
    for value in mountpoints or []:
        if value and value not in values:
            values.append(value)
    return tuple(values)


def _all_child_mountpoints(node: dict) -> tuple[str, ...]:
    result: list[str] = []
    for child in node.get("children") or []:
        result.extend(_mountpoints(child))
        result.extend(_all_child_mountpoints(child))
    return tuple(dict.fromkeys(result))


def _protected_backing_devices() -> set[str]:
    protected: set[str] = set()
    for mountpoint in ("/run/live/medium", "/", "/boot", "/boot/efi"):
        result = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", "--target", mountpoint],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            continue
        source = result.stdout.strip()
        if not source.startswith("/dev/"):
            continue
        parent = subprocess.run(
            ["lsblk", "-n", "-o", "PKNAME", source],
            text=True,
            capture_output=True,
            check=False,
        ).stdout.strip().splitlines()
        protected.add(f"/dev/{parent[-1]}" if parent and parent[-1] else source)
    return protected


def list_disks() -> list[Disk]:
    require_commands(("lsblk", "findmnt"))
    result = subprocess.run(
        [
            "lsblk", "--json", "--bytes", "--paths",
            "--output", "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,TRAN,RO,RM,MOUNTPOINTS",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    protected = _protected_backing_devices()
    disks: list[Disk] = []
    for node in json.loads(result.stdout).get("blockdevices", []):
        if node.get("type") != "disk":
            continue
        if int(node.get("size") or 0) < MIN_DISK_BYTES:
            continue
        path = node.get("path") or node.get("name")
        disks.append(
            Disk(
                name=os.path.basename(path),
                path=path,
                size=int(node.get("size") or 0),
                model=(node.get("model") or "").strip(),
                serial=(node.get("serial") or "").strip(),
                transport=(node.get("tran") or "").strip(),
                read_only=bool(node.get("ro")),
                removable=bool(node.get("rm")),
                mountpoints=_mountpoints(node),
                child_mountpoints=_all_child_mountpoints(node),
                protected=path in protected,
            )
        )
    return sorted(disks, key=lambda disk: disk.path)


def find_disk(disks: list[Disk], path: str) -> Disk:
    canonical = os.path.realpath(path)
    for disk in disks:
        if os.path.realpath(disk.path) == canonical:
            return disk
    raise MirrorDriveError(f"Not a whole disk: {path}")


def unmount_children(target: Disk, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
    for mountpoint in reversed(target.child_mountpoints + target.mountpoints):
        result = runner(["umount", mountpoint], text=True, capture_output=True, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise MirrorDriveError(f"Could not unmount {mountpoint}: {detail}")


def ensure_log_dir(path: str) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory
