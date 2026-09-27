import json
from types import SimpleNamespace

from mirror_drive import system


def test_list_disks_ignores_tiny_pseudo_disks(monkeypatch):
    payload = {
        "blockdevices": [
            {"name": "/dev/fd0", "path": "/dev/fd0", "type": "disk", "size": 4096},
            {"name": "/dev/sda", "path": "/dev/sda", "type": "disk", "size": 64 * 1024 * 1024},
        ]
    }

    monkeypatch.setattr(system, "require_commands", lambda _commands: None)
    monkeypatch.setattr(system, "_protected_backing_devices", lambda: set())
    monkeypatch.setattr(
        system.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps(payload)),
    )

    assert [disk.path for disk in system.list_disks()] == ["/dev/sda"]
