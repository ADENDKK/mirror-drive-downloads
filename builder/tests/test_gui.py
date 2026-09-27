from mirror_drive.gui import MirrorDriveApp
from mirror_drive.models import Disk


def test_confirm_screen_starts_clone_without_a_typed_phrase():
    app = MirrorDriveApp.__new__(MirrorDriveApp)
    app.screen = "confirm"
    app.source = Disk("nvme0n1", "/dev/nvme0n1", 1024)
    app.target = Disk("sdb", "/dev/sdb", 2048)
    started = []
    app.start_clone = lambda: started.append(True)

    app.advance()

    assert started == [True]


def test_confirm_screen_does_not_start_without_a_target():
    app = MirrorDriveApp.__new__(MirrorDriveApp)
    app.screen = "confirm"
    app.source = Disk("nvme0n1", "/dev/nvme0n1", 1024)
    app.target = None
    started = []
    app.start_clone = lambda: started.append(True)

    app.advance()

    assert started == []
