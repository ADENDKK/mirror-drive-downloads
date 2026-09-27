from pathlib import Path

import pytest

from mirror_drive.clone import ClonePlan, validate_or_write_map_metadata, verify_sample
from mirror_drive.models import Disk
from mirror_drive.system import MirrorDriveError
from mirror_drive.ui import target_choices


def disk(name: str, size: int, **kwargs) -> Disk:
    return Disk(name, f"/dev/{name}", size, **kwargs)


def test_plan_rejects_same_disk():
    plan = ClonePlan(disk("sda", 100), disk("sda", 100), Path("clone.map"))
    with pytest.raises(MirrorDriveError, match="same disk"):
        plan.validate()


def test_plan_rejects_smaller_target():
    plan = ClonePlan(disk("sda", 101), disk("sdb", 100), Path("clone.map"))
    with pytest.raises(MirrorDriveError, match="smaller"):
        plan.validate()


def test_plan_rejects_protected_target():
    plan = ClonePlan(disk("sda", 100), disk("sdb", 100, protected=True), Path("clone.map"))
    with pytest.raises(MirrorDriveError, match="protected"):
        plan.validate()


def test_ddrescue_commands_have_recovery_map():
    plan = ClonePlan(disk("sda", 100), disk("sdb", 100), Path("clone.map"), retries=5)
    assert plan.commands[0] == ("ddrescue", "--force", "--no-scrape", "/dev/sda", "/dev/sdb", "clone.map")
    assert "--retry-passes=5" in plan.commands[1]


def test_sample_verification_detects_difference(tmp_path):
    source = tmp_path / "source.img"
    target = tmp_path / "target.img"
    source.write_bytes(b"a" * 4096)
    target.write_bytes(b"a" * 2048 + b"b" + b"a" * 2047)
    with pytest.raises(MirrorDriveError, match="Verification failed"):
        verify_sample(str(source), str(target), 4096, samples=4, chunk_size=1024)


def test_sample_verification_accepts_identical_files(tmp_path):
    source = tmp_path / "source.img"
    target = tmp_path / "target.img"
    data = bytes(range(256)) * 32
    source.write_bytes(data)
    target.write_bytes(data)
    verify_sample(str(source), str(target), len(data), samples=5, chunk_size=512)


def test_recovery_map_is_bound_to_disk_serials(tmp_path):
    mapfile = tmp_path / "clone.map"
    original = ClonePlan(
        disk("sda", 100, serial="SOURCE"),
        disk("sdb", 100, serial="TARGET"),
        mapfile,
    )
    validate_or_write_map_metadata(original)
    mapfile.write_text("ddrescue map", encoding="utf-8")
    validate_or_write_map_metadata(original)
    wrong_target = ClonePlan(original.source, disk("sdb", 100, serial="OTHER"), mapfile)
    with pytest.raises(MirrorDriveError, match="different target disk serial"):
        validate_or_write_map_metadata(wrong_target)


def test_existing_map_without_identity_is_rejected(tmp_path):
    mapfile = tmp_path / "clone.map"
    mapfile.write_text("ddrescue map", encoding="utf-8")
    plan = ClonePlan(disk("sda", 100), disk("sdb", 100), mapfile)
    with pytest.raises(MirrorDriveError, match="no Mirror Drive identity"):
        validate_or_write_map_metadata(plan)


def test_target_choices_disable_unsafe_disks():
    source = disk("sda", 200)
    choices = target_choices(
        [source, disk("sdb", 100), disk("sdc", 300, read_only=True), disk("sdd", 300)],
        source,
    )
    assert [choice.disabled_reason for choice in choices] == ["source disk", "too small", "read-only", None]
