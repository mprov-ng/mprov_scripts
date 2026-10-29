"""localTempDisk.py refuses to repartition a disk the running system uses (#43).

buildDisks() wrote a fresh partition table to every disk in the layout, with
nothing checking that a disk wasn't the one the node booted from: attached to a
stateful system, the script repartitioned its root disk.  Now every layout disk
is checked first, against fake /proc/mounts, /proc/swaps and /sys/class/block
trees here, and if any is in use nothing is touched.
"""
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "post-boot" / "localTempDisk.py"


class FakeSh:
  def __init__(self):
    self.calls = []

  def __getattr__(self, name):
    def run(*args):
      self.calls.append(name)
    return run


@pytest.fixture
def formatter(monkeypatch, tmp_path):
  for name in ("parted", "sh", "requests", "yaml"):
    monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
  sys.modules["requests"].Session = lambda: types.SimpleNamespace(headers={})
  sys.modules["parted"].PARTITION_NORMAL = 1
  spec = importlib.util.spec_from_file_location("localTempDisk", SCRIPT)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)

  wiped = []
  monkeypatch.setattr(mod, "sh", FakeSh())
  monkeypatch.setattr(mod, "parted", types.SimpleNamespace(
    getDevice=lambda name: wiped.append(name) or types.SimpleNamespace(sectorSize=512),
    freshDisk=lambda device, label: object(),
  ))

  f = object.__new__(mod.mProvStatelessDiskFormatter)
  f._getDiskLayout = lambda: None
  f._createPartition = lambda device, disk, part, sectorsize, start, parttype=1: 0
  f._makeFS = lambda part, pdisk: "uuid"
  f._activate = lambda part, partuuid: None
  f._waitForPartition = lambda device: None
  f.procMounts = str(tmp_path / "mounts")
  f.procSwaps = str(tmp_path / "swaps")
  f.sysBlock = str(tmp_path / "block")
  f.wiped = wiped
  f.tmp = tmp_path
  (tmp_path / "mounts").write_text("proc /proc proc rw 0 0\ntmpfs / tmpfs rw 0 0\n")
  (tmp_path / "swaps").write_text("Filename\tType\tSize\tUsed\tPriority\n")
  return f


def block(f, disk, *parts, holders=()):
  """ A disk (and its partitions) in the fake /sys/class/block. """
  (f.tmp / "block" / disk / "holders").mkdir(parents=True)
  for part in parts:
    (f.tmp / "block" / disk / part).mkdir()
    (f.tmp / "block" / disk / part / "partition").write_text("1\n")
    (f.tmp / "block" / part / "holders").mkdir(parents=True)
  for dev, holder in holders:
    (f.tmp / "block" / dev / "holders" / holder).mkdir()


def layout(*disknames):
  return [{"diskname": d, "dtype": "scsi", "slug": d.split("/")[-1], "partitions": [
    {"partnum": 1, "mount": "/scratch", "filesystem": "xfs", "size": 1024, "fill": True}]}
    for d in disknames]


def refused(f, capsys):
  with pytest.raises(SystemExit) as exit:
    f.buildDisks()
  assert exit.value.code == 1
  assert f.wiped == []  # no disk was touched
  return capsys.readouterr().err


def test_unused_disks_are_built(formatter):
  block(formatter, "sda", "sda1")
  block(formatter, "nvme0n1", "nvme0n1p1")
  formatter.disklayout = layout("/dev/sda", "/dev/nvme0n1")
  formatter.buildDisks()
  assert formatter.wiped == ["/dev/sda", "/dev/nvme0n1"]


def test_stateful_root_disk_refused(formatter, capsys):
  block(formatter, "sda", "sda1", "sda2", "sda3")
  (formatter.tmp / "mounts").write_text("/dev/sda3 / xfs rw 0 0\n/dev/sda2 /boot/efi vfat rw 0 0\n")
  formatter.disklayout = layout("/dev/sda")
  err = refused(formatter, capsys)
  assert "/dev/sda: /dev/sda2 is mounted on /boot/efi" in err
  assert "/dev/sda: /dev/sda3 is mounted on /" in err


def test_every_disk_checked_before_any_is_touched(formatter, capsys):
  # sda is free, but sdb comes later and is in use: sda isn't wiped either.
  block(formatter, "sda", "sda1")
  block(formatter, "sdb", "sdb1")
  (formatter.tmp / "mounts").write_text("/dev/sdb1 /var/tmp xfs rw 0 0\n")
  formatter.disklayout = layout("/dev/sda", "/dev/sdb")
  err = refused(formatter, capsys)
  assert "/dev/sdb: /dev/sdb1 is mounted on /var/tmp" in err
  assert "/dev/sda:" not in err


def test_whole_disk_mount_refused(formatter, capsys):
  block(formatter, "sdb")
  (formatter.tmp / "mounts").write_text("/dev/sdb /mnt ext4 rw 0 0\n")
  formatter.disklayout = layout("/dev/sdb")
  assert "/dev/sdb: /dev/sdb is mounted on /mnt" in refused(formatter, capsys)


def test_active_swap_refused(formatter, capsys):
  block(formatter, "sda", "sda1", "sda2")
  (formatter.tmp / "swaps").write_text(
    "Filename\tType\tSize\tUsed\tPriority\n/dev/sda1\tpartition\t1048572\t0\t-2\n")
  formatter.disklayout = layout("/dev/sda")
  assert "/dev/sda: /dev/sda1 is active swap" in refused(formatter, capsys)


def test_running_raid_or_lvm_member_refused(formatter, capsys):
  # an array still running after mdadm --stop --scan (it's mounted), or LVM.
  block(formatter, "sda", "sda1", "sda2", holders=[("sda1", "md0"), ("sda2", "dm-0")])
  formatter.disklayout = layout("/dev/sda")
  err = refused(formatter, capsys)
  assert "/dev/sda: /dev/sda1 is in use by /dev/md0" in err
  assert "/dev/sda: /dev/sda2 is in use by /dev/dm-0" in err


def test_mount_by_symlink_is_resolved(formatter, capsys):
  # a mount source and a layout disk named through symlinks, as in
  # /dev/disk/by-id/, match the kernel's names.
  block(formatter, "sdc", "sdc1")
  links = formatter.tmp / "dev"
  links.mkdir()
  os.symlink("/dev/sdc", links / "disk-by-id")
  os.symlink("/dev/sdc1", links / "disk-by-id-part1")
  # only /dev/ sources count, so the link is named through /dev/.. here.
  (formatter.tmp / "mounts").write_text(f"/dev/..{links}/disk-by-id-part1 /data xfs rw 0 0\n")
  formatter.disklayout = layout(str(links / "disk-by-id"))
  assert "/dev/sdc1 is mounted on /data" in refused(formatter, capsys)


def test_missing_disk_refused(formatter, capsys):
  block(formatter, "sda", "sda1")
  formatter.disklayout = layout("/dev/sda", "/dev/sdz")
  assert "/dev/sdz: no such disk" in refused(formatter, capsys)


def test_raid_layouts_not_checked(formatter):
  block(formatter, "sda", "sda1")
  formatter.disklayout = layout("/dev/sda") + [
    {"diskname": "/dev/md0", "dtype": "mdrd", "slug": "md0", "partitions": []}]
  formatter.buildDisks()
  assert formatter.wiped == ["/dev/sda"]
