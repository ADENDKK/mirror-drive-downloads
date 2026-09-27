from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .clone import ClonePlan, new_mapfile, run_clone, validate_or_write_map_metadata, verify_full, verify_sample
from .models import Disk, human_size
from .system import MirrorDriveError, ensure_log_dir, find_disk, list_disks, require_commands, unmount_children
from .ui import interactive_selection


LOGO = r"""
 __  __ _                     ____       _
|  \/  (_)_ __ _ __ ___  _ _|  _ \ _ __(_)_   _____
| |\/| | | '__| '__/ _ \| '__| | | | '__| \ \ / / _ \
| |  | | | |  | | | (_) | |  | |_| | |  | |\ V /  __/
|_|  |_|_|_|  |_|  \___/|_|  |____/|_|  |_| \_/ \___|
                    by Plainloop
"""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Clone one complete disk to another.")
    result.add_argument("--version", action="version", version=f"Mirror Drive {__version__}")
    result.add_argument("--list", action="store_true", help="list detected whole disks")
    result.add_argument("--source", help="source whole-disk path, e.g. /dev/nvme0n1")
    result.add_argument("--target", help="target whole-disk path; all data will be destroyed")
    result.add_argument("--verify", choices=("none", "sample", "full"), default="sample")
    result.add_argument("--retries", type=int, default=3, help="ddrescue retry passes (default: 3)")
    result.add_argument("--log-dir", default="/var/log/mirror-drive")
    result.add_argument("--mapfile", help="reuse a ddrescue recovery map to resume an interrupted clone")
    result.add_argument("--confirm-target", help="non-interactive confirmation: target serial or device name")
    result.add_argument("--dry-run", action="store_true", help="show the plan without writing")
    return result


def print_disks(disks: list[Disk]) -> None:
    print("Detected disks:\n")
    for index, disk in enumerate(disks, start=1):
        flags: list[str] = []
        if disk.protected:
            flags.append("PROTECTED/RUNNING SYSTEM")
        if disk.read_only:
            flags.append("READ-ONLY")
        if disk.removable:
            flags.append("REMOVABLE")
        if disk.child_mountpoints or disk.mountpoints:
            flags.append("MOUNTED")
        suffix = f"  [{' | '.join(flags)}]" if flags else ""
        print(f"  {index}. {disk.label}{suffix}")


def choose_disk(disks: list[Disk], prompt: str, *, exclude: Disk | None = None) -> Disk:
    while True:
        raw = input(prompt).strip()
        try:
            selected = disks[int(raw) - 1]
        except (ValueError, IndexError):
            print("Enter a disk number from the list.")
            continue
        if exclude and os.path.realpath(selected.path) == os.path.realpath(exclude.path):
            print("Source and target must be different disks.")
            continue
        return selected


def confirm(source: Disk, target: Disk, supplied: str | None) -> None:
    required = f"ERASE {target.confirmation_id}"
    print("\nCLONE PLAN")
    print(f"  Read from : {source.label}")
    print(f"  ERASE     : {target.label}")
    print(f"  Copy      : {human_size(source.size)} sector-for-sector")
    print("\nThe target disk will be overwritten completely.")
    if supplied is not None:
        answer = f"ERASE {supplied}"
    else:
        answer = input(f"Type exactly '{required}' to continue: ").strip()
    if answer != required:
        raise MirrorDriveError("Confirmation did not match; nothing was changed.")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        disks = list_disks()
        print(LOGO)
        print_disks(disks)
        if args.list:
            return 0
        if len(disks) < 2:
            raise MirrorDriveError("At least two whole disks must be connected.")
        graphical_flow = args.source is None and args.target is None and sys.stdin.isatty() and sys.stdout.isatty()
        if graphical_flow:
            source, target = interactive_selection(disks)
        else:
            source = find_disk(disks, args.source) if args.source else choose_disk(disks, "\nSource disk number: ")
            target = find_disk(disks, args.target) if args.target else choose_disk(disks, "Target disk number: ", exclude=source)
        log_dir = ensure_log_dir(args.log_dir)
        mapfile = new_mapfile(log_dir, source, target) if args.mapfile is None else ensure_log_dir(os.path.dirname(os.path.abspath(args.mapfile))) / os.path.basename(args.mapfile)
        plan = ClonePlan(source, target, mapfile, max(0, args.retries))
        plan.validate()
        if not graphical_flow:
            confirm(source, target, args.confirm_target)
        print(f"\nRecovery map: {plan.mapfile}")
        if args.dry_run:
            print("Dry run complete; no disks were changed.")
            for command in plan.commands:
                print("  " + " ".join(command))
            return 0
        if os.geteuid() != 0:
            raise MirrorDriveError("Run as root to clone physical disks.")
        require_commands(("ddrescue", "sync", "blockdev", "partprobe", "udevadm", "cmp"))
        # Re-read identities immediately before the destructive operation. This
        # catches a disk being unplugged and another device taking the same path.
        fresh_disks = list_disks()
        fresh_source = find_disk(fresh_disks, source.path)
        fresh_target = find_disk(fresh_disks, target.path)
        for before, after, role in ((source, fresh_source, "source"), (target, fresh_target, "target")):
            if before.size != after.size or (before.serial and before.serial != after.serial):
                raise MirrorDriveError(f"The {role} disk identity changed after confirmation; nothing was written.")
        plan = ClonePlan(fresh_source, fresh_target, plan.mapfile, plan.retries)
        plan.validate()
        source, target = fresh_source, fresh_target
        validate_or_write_map_metadata(plan)
        if target.child_mountpoints or target.mountpoints:
            print("Unmounting target volumes...")
            unmount_children(target)
        print("\nCloning. Keep power connected; the ddrescue progress display is live.\n")
        run_clone(plan)
        if args.verify == "sample":
            print("\nVerifying 64 evenly distributed samples...")
            verify_sample(source.path, target.path, source.size)
        elif args.verify == "full":
            print("\nVerifying every byte. This can take about as long as the clone...")
            verify_full(source.path, target.path, source.size)
        print("\nSUCCESS: The disk clone is complete.")
        print("Power off, disconnect the source disk, then boot from the new disk first.")
        if target.size > source.size:
            print("Unused space remains at the end of the larger target disk and can be expanded later.")
        return 0
    except (MirrorDriveError, OSError) as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nStopped. Re-run with --mapfile and the printed recovery-map path to resume safely.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
