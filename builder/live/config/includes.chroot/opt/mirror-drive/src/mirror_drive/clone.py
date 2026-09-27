from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import Disk
from .system import MirrorDriveError


@dataclass(frozen=True)
class ClonePlan:
    source: Disk
    target: Disk
    mapfile: Path
    retries: int = 3

    def validate(self) -> None:
        if os.path.realpath(self.source.path) == os.path.realpath(self.target.path):
            raise MirrorDriveError("Source and target are the same disk.")
        if self.source.size <= 0:
            raise MirrorDriveError("Source disk reports an invalid size.")
        if self.target.size < self.source.size:
            raise MirrorDriveError("Target disk is smaller than the source disk.")
        if self.target.read_only:
            raise MirrorDriveError("Target disk is read-only.")
        if self.target.protected:
            raise MirrorDriveError("Target contains the running Mirror Drive system and is protected.")

    @property
    def commands(self) -> tuple[tuple[str, ...], ...]:
        common = (self.source.path, self.target.path, str(self.mapfile))
        return (
            ("ddrescue", "--force", "--no-scrape", *common),
            ("ddrescue", "--force", f"--retry-passes={self.retries}", *common),
        )


def run_clone(
    plan: ClonePlan,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    plan.validate()
    plan.mapfile.parent.mkdir(parents=True, exist_ok=True)
    for command in plan.commands:
        result = runner(command, check=False)
        if result.returncode != 0:
            raise MirrorDriveError(f"Cloning failed (exit code {result.returncode}). Resume with mapfile: {plan.mapfile}")
    runner(("sync",), check=True)
    runner(("blockdev", "--flushbufs", plan.target.path), check=False)
    runner(("partprobe", plan.target.path), check=False)
    runner(("udevadm", "settle"), check=False)


def _chunk_positions(size: int, chunk_size: int, samples: int) -> list[int]:
    if size <= chunk_size:
        return [0]
    samples = max(2, samples)
    last = size - chunk_size
    return sorted({round(last * index / (samples - 1)) for index in range(samples)})


def verify_sample(source: str, target: str, size: int, *, samples: int = 64, chunk_size: int = 4 * 1024 * 1024) -> None:
    positions = _chunk_positions(size, min(chunk_size, size), samples)
    with open(source, "rb", buffering=0) as source_file, open(target, "rb", buffering=0) as target_file:
        for index, position in enumerate(positions, start=1):
            source_file.seek(position)
            target_file.seek(position)
            source_data = source_file.read(min(chunk_size, size - position))
            target_data = target_file.read(min(chunk_size, size - position))
            if hashlib.sha256(source_data).digest() != hashlib.sha256(target_data).digest():
                raise MirrorDriveError(f"Verification failed near byte {position} (sample {index}/{len(positions)}).")


def verify_full(source: str, target: str, size: int, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
    result = runner(("cmp", "--bytes", str(size), source, target), check=False)
    if result.returncode != 0:
        raise MirrorDriveError("Full verification found a difference between source and target.")


def new_mapfile(log_dir: Path, source: Disk, target: Disk) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return log_dir / f"{stamp}-{source.name}-to-{target.name}.map"


def _disk_identity(disk: Disk) -> dict[str, str | int]:
    return {
        "path": disk.path,
        "name": disk.name,
        "serial": disk.serial,
        "model": disk.model,
        "size": disk.size,
    }


def validate_or_write_map_metadata(plan: ClonePlan) -> Path:
    """Bind a recovery map to the selected disks so it cannot be reused accidentally."""
    metadata_path = Path(f"{plan.mapfile}.json")
    expected = {
        "format": 1,
        "source": _disk_identity(plan.source),
        "target": _disk_identity(plan.target),
    }
    if plan.mapfile.exists():
        if not metadata_path.exists():
            raise MirrorDriveError("Existing recovery map has no Mirror Drive identity file; refusing unsafe resume.")
        try:
            actual = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise MirrorDriveError(f"Could not read recovery-map identity: {error}") from error
        for role in ("source", "target"):
            old = actual.get(role, {})
            new = expected[role]
            if old.get("size") != new["size"]:
                raise MirrorDriveError(f"Recovery map belongs to a different {role} disk size.")
            if old.get("serial") and new["serial"]:
                if old["serial"] != new["serial"]:
                    raise MirrorDriveError(f"Recovery map belongs to a different {role} disk serial.")
            elif old.get("path") != new["path"] or old.get("model") != new["model"]:
                raise MirrorDriveError(f"Recovery map belongs to a different {role} disk identity.")
        return metadata_path
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    return metadata_path
