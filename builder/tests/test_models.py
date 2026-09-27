from mirror_drive.models import disk_capacity


def test_disk_capacity_uses_advertised_decimal_gigabytes():
    assert disk_capacity(480_000_000_000) == "480GB"
    assert disk_capacity(512_110_190_592) == "512GB"


def test_disk_capacity_uses_terabytes_for_large_disks():
    assert disk_capacity(1_000_204_886_016) == "1TB"
    assert disk_capacity(1_920_000_000_000) == "1.92TB"
