"""#29: non-RAID partitions must be mounted at the partition's own mount point.

buildDisks() writes the partition's mount point to /etc/fstab, then mounted
pdisk['mount'] instead -- the mPCC serializer returns None for that field on
every disk whose dtype is not 'mdrd', so mount(8) was handed the string
'None' and the formatter crashed.  The test stubs the script's environment
(parted, sh, requests, yaml are not installed on the test runner), feeds one
non-RAID disk through buildDisks() and asserts mount() is called with the
partition mount point that was just written to fstab.
"""
import builtins
import importlib.util
import sys
import types
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "post-boot" / "localTempDisk.py"


class FakeSh:
  """Records invocations; mount() only accepts a real mount point, the way
  mount(8) fails on anything else."""

  def __init__(self, mountable):
    self.mountable = set(mountable)
    self.mounts = []
    self.mdadm_calls = []

  def mdadm(self, args):
    self.mdadm_calls.append(args)

  def mount(self, args):
    # 'mount UUID=... <dir>' since #33: the mount point is the last argument.
    path = args[-1] if isinstance(args, (list, tuple)) else args
    self.mounts.append(path)
    if path not in self.mountable:
      raise RuntimeError(f"mount: {path}: mount point does not exist.")


class FakeOS:
  """Delegates to the real os, except unlink() is recorded, not executed
  (buildDisks() unlinks /etc/fstab)."""

  def __init__(self, real):
    self._real = real
    self.unlinked = []

  def unlink(self, path):
    self.unlinked.append(path)

  def __getattr__(self, name):
    return getattr(self._real, name)


def load_formatter_module(monkeypatch):
  """Import post-boot/localTempDisk.py with its environment deps stubbed."""
  for name in ("parted", "sh", "requests", "yaml"):
    monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
  sys.modules["requests"].Session = lambda: types.SimpleNamespace(
    headers={}, get=lambda *a, **k: None)
  sys.modules["parted"].PARTITION_NORMAL = 1
  spec = importlib.util.spec_from_file_location("localTempDisk", SCRIPT)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def test_build_disks_mounts_partition_not_disk(monkeypatch, tmp_path):
  mod = load_formatter_module(monkeypatch)

  part_mount = tmp_path / "var-tmp"
  part_mount.mkdir()
  fstab = tmp_path / "fstab"

  disk = {
    "slug": "scratch-sdb",
    "diskname": "/dev/sdb",
    # Non-RAID disk: DiskLayoutAPISerializer.getMount returns None for every
    # dtype that is not 'mdrd'.
    "dtype": "part",
    "mount": None,
    "partitions": [
      {"partnum": 1, "size": 1024, "filesystem": "xfs",
       "mount": str(part_mount), "fill": False},
    ],
  }

  partuuid = uuid.UUID(int=1)
  formatter = object.__new__(mod.mProvStatelessDiskFormatter)
  formatter.disklayout = [disk]
  formatter._getDiskLayout = lambda: None
  # Partitioning/mkfs touch real devices; stub them out to exercise only the
  # fstab-write + mount path this bug lives on.
  formatter._createPartition = (
    lambda device, disk, part, sectorsize, start, parttype=1: 0)
  formatter._makeFS = lambda part, pdisk: partuuid

  fake_sh = FakeSh(mountable=[str(part_mount)])
  fake_os = FakeOS(mod.os)
  fake_parted = types.SimpleNamespace(
    getDevice=lambda name: types.SimpleNamespace(sectorSize=512),
    freshDisk=lambda device, label: object(),
  )
  monkeypatch.setattr(mod, "sh", fake_sh)
  monkeypatch.setattr(mod, "os", fake_os)
  monkeypatch.setattr(mod, "parted", fake_parted)

  real_open = builtins.open
  def redirect_open(path, *args, **kwargs):
    if path == "/etc/fstab":
      path = str(fstab)
    return real_open(path, *args, **kwargs)
  monkeypatch.setattr(builtins, "open", redirect_open)
  formatter.fstabfile = str(fstab)

  formatter.buildDisks()
  formatter.writeFstab()

  # The mount point written to fstab is the one that must be mounted.
  assert f"UUID={partuuid}\t{part_mount}" in fstab.read_text()
  assert fake_sh.mounts == [str(part_mount)]
