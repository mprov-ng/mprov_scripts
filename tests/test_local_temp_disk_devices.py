"""localTempDisk.py formats and RAIDs the device each partition really got (#53, #34).

Device names were built as '{diskname}{layout partnum}': wrong for NVMe disks
(which need a 'p' before the number) and whenever the fill partition isn't
numbered last (it's created last).  RAID members were looked up by a loop
that left 'dev' unset, or set to the previous member's disk, when a member
belonged to a layout not attached to the system.  And the fill partition's
raid check read the loop's last partition instead of the fill one (#34).

The tests run the real buildDisks(), buildRAIDs() and _makeFS() with parted,
the commands (sh) and /etc/fstab stubbed, and check the device each command
gets.
"""
import builtins
import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "post-boot" / "localTempDisk.py"


class FakeSh:
  """Records every command as 'name arg ...'."""

  def __init__(self):
    self.calls = []

  def __getattr__(self, name):
    def run(*args):
      if len(args) == 1 and isinstance(args[0], list):
        args = args[0]
      self.calls.append(" ".join([name, *map(str, args)]))
    return run

  def ran(self, name):
    return [c for c in self.calls if c.split()[0] == name]


@pytest.fixture
def formatter(monkeypatch, tmp_path):
  for name in ("parted", "sh", "requests", "yaml"):
    monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
  sys.modules["requests"].Session = lambda: types.SimpleNamespace(headers={})
  sys.modules["parted"].PARTITION_NORMAL = 1
  spec = importlib.util.spec_from_file_location("localTempDisk", SCRIPT)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)

  fake_sh = FakeSh()
  monkeypatch.setattr(mod, "sh", fake_sh)
  monkeypatch.setattr(mod, "parted", types.SimpleNamespace(
    getDevice=lambda name: types.SimpleNamespace(sectorSize=512),
    freshDisk=lambda device, label: object(),
  ))
  monkeypatch.setattr(mod.os, "unlink", lambda path: None)
  fstab = tmp_path / "fstab"
  real_open = builtins.open
  monkeypatch.setattr(builtins, "open", lambda path, *a, **k: real_open(
    str(fstab) if path == "/etc/fstab" else path, *a, **k))

  f = object.__new__(mod.mProvStatelessDiskFormatter)
  f._getDiskLayout = lambda: None
  # partitioning touches real devices; parted's numbering is what
  # buildDisks() tracks, so only creation is stubbed.
  f._createPartition = lambda device, disk, part, sectorsize, start, parttype=1: 0
  f.sh = fake_sh
  return f


def part(partnum, mount, filesystem, fill=False, layout=None):
  return {"partnum": partnum, "mount": mount, "filesystem": filesystem,
          "size": 1024, "fill": fill, "disklayout": layout}


def disk(slug, diskname, *partitions):
  return {"slug": slug, "diskname": diskname, "dtype": "scsi", "mount": None,
          "partitions": [dict(p, disklayout=slug) for p in partitions]}


def raid(diskname, mount, *members):
  return {"slug": diskname.rsplit("/", 1)[1], "diskname": diskname, "dtype": "mdrd",
          "raidlevel": "raid1", "filesystem": "xfs", "mount": mount, "partitions": [],
          "members": [part(n, "raid", "raid", layout=slug) for slug, n in members]}


def mkfs_devices(f):
  return [c.split()[-1] for c in f.sh.ran("mkfs")]


def test_nvme_partitions_get_the_p_separator(formatter):
  formatter.disklayout = [disk("nv", "/dev/nvme0n1", part(1, "/scratch", "xfs", fill=True))]
  formatter.buildDisks()
  formatter.buildRAIDs()
  assert mkfs_devices(formatter) == ["/dev/nvme0n1p1"]


def test_raid_over_nvme_uses_the_nvme_partition_names(formatter):
  formatter.disklayout = [
    disk("n0", "/dev/nvme0n1", part(1, "raid", "raid", fill=True)),
    disk("n1", "/dev/nvme1n1", part(1, "raid", "raid", fill=True)),
    raid("/dev/md0", "/data", ("n0", 1), ("n1", 1)),
  ]
  formatter.buildDisks()
  formatter.buildRAIDs()
  assert formatter.sh.ran("mdadm")[-1] == \
    "mdadm --create /dev/md0 -R --force --level=raid1 --raid-devices=2 /dev/nvme0n1p1 /dev/nvme1n1p1"
  assert mkfs_devices(formatter) == ["/dev/md0"]


def test_fill_partition_numbered_first_is_formatted_on_the_last_device(formatter):
  # layout partition 1 is the fill partition, so it's created second: sdb2.
  # Partition 2 (a RAID member) is created first: sdb1.
  formatter.disklayout = [
    disk("d-sdb", "/dev/sdb", part(1, "/scratch", "xfs", fill=True), part(2, "raid", "raid")),
    disk("d-sdc", "/dev/sdc", part(1, "raid", "raid")),
    raid("/dev/md0", "/data", ("d-sdb", 2), ("d-sdc", 1)),
  ]
  formatter.buildDisks()
  formatter.buildRAIDs()
  # the fill partition is formatted (#34: it was skipped because the last
  # partition in the loop was the RAID member), on the device it got.
  assert mkfs_devices(formatter) == ["/dev/sdb2", "/dev/md0"]
  assert formatter.sh.ran("mdadm")[-1].endswith("--raid-devices=2 /dev/sdb1 /dev/sdc1")


def test_raid_fill_partition_is_not_formatted(formatter):
  # the other half of #34: a RAID fill partition numbered before a normal one
  # was formatted, because the check read the normal partition (the loop's
  # last).  Partition 2 is created first, so it's sdb1.
  formatter.disklayout = [
    disk("d-sdb", "/dev/sdb", part(1, "raid", "raid", fill=True), part(2, "/var/tmp", "xfs")),
  ]
  formatter.buildDisks()
  assert mkfs_devices(formatter) == ["/dev/sdb1"]


def test_raid_member_on_an_unattached_layout_stops_the_script(formatter, capsys):
  formatter.disklayout = [raid("/dev/md0", "/data", ("other-sdb", 1), ("other-sdc", 1))]
  formatter.buildDisks()
  with pytest.raises(SystemExit) as exit:
    formatter.buildRAIDs()
  assert exit.value.code == 1
  assert "RAID /dev/md0 member partition 1 of other-sdb wasn't created" in capsys.readouterr().err
  assert formatter.sh.ran("mdadm") == ["mdadm --stop --scan"]


def test_later_raid_with_a_missing_member_doesnt_reuse_the_previous_disk(formatter):
  formatter.disklayout = [
    disk("d-sdb", "/dev/sdb", part(1, "raid", "raid"), part(2, "raid", "raid")),
    disk("d-sdc", "/dev/sdc", part(1, "raid", "raid")),
    raid("/dev/md0", "/a", ("d-sdb", 1), ("d-sdc", 1)),
    raid("/dev/md1", "/b", ("d-sdb", 2), ("missing-sdd", 2)),
  ]
  formatter.buildDisks()
  with pytest.raises(SystemExit):
    formatter.buildRAIDs()
  creates = [c for c in formatter.sh.ran("mdadm") if "--create" in c]
  assert creates == ["mdadm --create /dev/md0 -R --force --level=raid1 --raid-devices=2 /dev/sdb1 /dev/sdc1"]
