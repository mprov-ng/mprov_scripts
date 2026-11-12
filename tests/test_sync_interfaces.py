"""sync_interfaces.py (#104): syncs the node's interfaces, and the switch
ports LLDP or smpquery find for them, to the mPCC.

The tests run the real parsing and sync logic against a fake /sys/class/net
tree, LLDP frames built here byte by byte, and a fake mPCC session that keeps
its rows in memory and answers like the mPCC does for a node's certificate
(404 for an empty list, 403 for a write its rules don't allow).
"""
import importlib.util
import json
import struct
import sys
import types
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "post-boot" / "sync_interfaces.py"
URL = "https://mpcc.example/"
MAC = "aa:bb:cc:dd:ee:01"
IB_GUID = "0x0002c90300a1b2c3"
IB_ADDR = "80:00:02:08:fe:80:00:00:00:00:00:00:00:02:c9:03:00:a1:b2:c3"


@pytest.fixture
def mod(monkeypatch):
  monkeypatch.setitem(sys.modules, "mprov_jobserver", types.ModuleType("mprov_jobserver"))
  base = types.ModuleType("mprov_jobserver.script")
  base.MProvScript = type("MProvScript", (), {})
  monkeypatch.setitem(sys.modules, "mprov_jobserver.script", base)
  spec = importlib.util.spec_from_file_location("sync_interfaces", SCRIPT)
  m = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(m)
  return m


def make_iface(root, name, kind, address, mtu=1500, up=True, physical=True):
  d = root / name
  d.mkdir(parents=True)
  (d / "type").write_text(f"{kind}\n")
  (d / "address").write_text(f"{address}\n")
  (d / "mtu").write_text(f"{mtu}\n")
  (d / "operstate").write_text("up\n" if up else "down\n")
  if physical:
    (d / "device").mkdir()
  return d


# ---- reading interfaces ---------------------------------------------------

def test_reads_physical_ethernet_and_infiniband(mod, tmp_path):
  make_iface(tmp_path, "lo", 772, "00:00:00:00:00:00")
  make_iface(tmp_path, "eno1", 1, MAC.upper(), mtu=9000)
  make_iface(tmp_path, "ib0", 32, IB_ADDR, mtu=2044, up=False)
  make_iface(tmp_path, "br0", 1, "aa:bb:cc:dd:ee:99", physical=False)
  make_iface(tmp_path, "eno2", 1, "00:00:00:00:00:00")
  found = mod.read_interfaces(str(tmp_path))
  assert found == [
    {"name": "eno1", "kind": "ether", "mac": MAC, "guid": None, "mtu": 9000, "up": True},
    {"name": "ib0", "kind": "ib", "mac": None, "guid": IB_GUID, "mtu": 2044, "up": False},
  ]


def test_skip_param_leaves_interfaces_out(mod, tmp_path):
  make_iface(tmp_path, "eno1", 1, MAC)
  assert mod.read_interfaces(str(tmp_path), skip=["eno1"]) == []


# ---- LLDP -----------------------------------------------------------------

def tlv(kind, value):
  return struct.pack("!H", (kind << 9) | len(value)) + value


def lldp_frame(sysname=b"leaf01", port=b"\x05swp12", chassis=b"\x04" + bytes.fromhex("0c42a1000001"),
               mgmt=b"\x05\x01" + bytes([10, 1, 0, 2]) + b"\x02\x00\x00\x00\x01\x00"):
  body = tlv(1, chassis) + tlv(2, port) + tlv(3, b"\x00\x78")
  if sysname is not None:
    body += tlv(5, sysname)
  body += tlv(4, b"to node01")
  if mgmt is not None:
    body += tlv(8, mgmt)
  return body + tlv(0, b"")


def test_parses_lldp(mod):
  assert mod.parse_lldp(lldp_frame()) == {
    "sysname": "leaf01", "chassis_id": "0c:42:a1:00:00:01", "chassis_mac": "0c:42:a1:00:00:01",
    "port": "swp12", "port_desc": "to node01", "mgmt_ip": "10.1.0.2",
  }


def test_parses_lldp_without_optional_tlvs(mod):
  info = mod.parse_lldp(lldp_frame(sysname=None, mgmt=None, chassis=b"\x07sw-a",
                                   port=b"\x03" + bytes.fromhex("0c42a10000ff")))
  assert info["chassis_id"] == "sw-a" and info["chassis_mac"] is None
  assert info["port"] == "0c:42:a1:00:00:ff"
  assert info["sysname"] is None and info["mgmt_ip"] is None


def test_truncated_lldp_frame_stops_cleanly(mod):
  info = mod.parse_lldp(lldp_frame()[:20])
  assert info["port"] == "swp12" and info["sysname"] is None


# ---- InfiniBand -----------------------------------------------------------

NODEINFO = """# Node info: DR path slid 65535; dlid 65535; 0,1
BaseVers:........................1
NodeType:........................Switch
NumPorts:........................36
NodeGuid:........................0x248a070300f1e2d3
LocalPort:.......................17
"""
NODEDESC = "Node Description:................MF0;ib-sw01:MSB7800/U1\n"


def test_ib_neighbor_from_smpquery(mod, tmp_path, monkeypatch):
  d = make_iface(tmp_path, "ib0", 32, IB_ADDR)
  (d / "device" / "infiniband" / "mlx5_0").mkdir(parents=True)
  (d / "dev_port").write_text("0\n")
  monkeypatch.setattr(mod.shutil, "which", lambda cmd: "/usr/sbin/smpquery")
  calls = []

  def run(cmd, **kw):
    calls.append(cmd)
    out = NODEINFO if "nodeinfo" in cmd else NODEDESC
    return types.SimpleNamespace(returncode=0, stdout=out, stderr="")

  iface = {"name": "ib0"}
  assert mod.ib_neighbor(iface, str(tmp_path), run) == {
    "guid": "0x248a070300f1e2d3", "sysname": "MF0;ib-sw01:MSB7800/U1", "port": "17"}
  assert calls[0][:6] == ["smpquery", "-C", "mlx5_0", "-P", "1", "-D"]


def test_ib_neighbor_without_infiniband_diags(mod, monkeypatch):
  monkeypatch.setattr(mod.shutil, "which", lambda cmd: None)
  assert mod.ib_neighbor({"name": "ib0"}) is None


def test_ib_neighbor_that_isnt_a_switch(mod, tmp_path, monkeypatch):
  d = make_iface(tmp_path, "ib0", 32, IB_ADDR)
  (d / "device" / "infiniband" / "mlx5_0").mkdir(parents=True)
  monkeypatch.setattr(mod.shutil, "which", lambda cmd: "/usr/sbin/smpquery")
  run = lambda cmd, **kw: types.SimpleNamespace(
    returncode=0, stdout=NODEINFO.replace("Switch", "Channel Adapter"), stderr="")
  assert mod.ib_neighbor({"name": "ib0"}, str(tmp_path), run) is None


# ---- the sync against a fake mPCC ----------------------------------------

class Response:
  def __init__(self, status_code, body=None):
    self.status_code = status_code
    self.body = body
    self.text = json.dumps(body)

  def json(self):
    return self.body


class FakeMPCC:
  """ The three tables, filtered by exact match like MProvView; a node gets 404
  for an empty list, and 403 for anything in `refuse` ((method, path)). """

  def __init__(self):
    self.tables = {"networkinterfaces": [], "switches": [], "switchports": []}
    self.writes = []
    self.refuse = set()
    self.conflict = set()
    self.fail = set()
    self.next_id = 100

  def add(self, table, **row):
    self.next_id += 1
    row["id"] = self.next_id
    self.tables[table].append(row)
    return row

  def get(self, url):
    parts = urlsplit(url[len(URL):])
    table = parts.path.strip("/")
    if table == "systems":
      return Response(200, [{"id": 7, "hostname": "node01"}])
    query = dict(parse_qsl(parts.query))
    rows = [r for r in self.tables[table]
            if all(str(r.get(k)) == v for k, v in query.items())]
    return Response(200, rows) if rows else Response(404, None)

  def _write(self, method, url, data):
    path = url[len(URL):]
    body = json.loads(data)
    self.writes.append((method, path, body))
    table = path.split("/")[0]
    if (method, table) in self.refuse:
      # the mPCC's rules (common.permissions)
      return Response(403, {"detail": f"node01 may not {method.upper()} this."})
    if (method, table) in self.conflict:
      # a check on what the write would change
      return Response(400, {"mac": ["Another system already has this MAC address."]})
    if method == "post" and table == "switches" and any(
        r["hostname"] == body.get("hostname") for r in self.tables["switches"]):
      return Response(400, {"hostname": ["switch with this Host Name already exists."]})
    if (method, table) in self.fail:
      return Response(500, {"detail": "boom"})
    if method == "post":
      return Response(201, self.add(table, **body))
    row_id = int(path.split("/")[1])
    row = next(r for r in self.tables[table] if r["id"] == row_id)
    row.update(body)
    return Response(200, row)

  def post(self, url, data=None):
    return self._write("post", url, data)

  def patch(self, url, data=None):
    return self._write("patch", url, data)


@pytest.fixture
def node(mod, tmp_path, monkeypatch):
  sysfs = tmp_path / "net"
  sysfs.mkdir()
  monkeypatch.setattr(mod, "SYSFS_NET", str(sysfs))
  monkeypatch.setattr(mod, "ENTITY_FILE", str(tmp_path / "entity.json"))
  real_read = mod.read_interfaces
  monkeypatch.setattr(mod, "read_interfaces", lambda sysfs=str(sysfs), skip=(): real_read(sysfs, skip))
  monkeypatch.setattr(mod.shutil, "which", lambda cmd: None)
  lldp = {}
  monkeypatch.setattr(mod, "listen_lldp", lambda name, timeout: lldp.get(name))
  s = object.__new__(mod.MScript)
  s.mprovURL = URL
  s.session = FakeMPCC()
  s.startSession = lambda: None
  s.sysfs = sysfs
  s.lldp = lldp
  s.entity = tmp_path / "entity.json"
  return s


def writes(node, method=None):
  return [w for w in node.session.writes if method is None or w[0] == method]


def test_renames_and_links_a_known_interface(mod, node):
  make_iface(node.sysfs, "eno1", 1, MAC, mtu=9000)
  nic = node.session.add("networkinterfaces", system=7, name="eth0", mac=MAC.upper(), mtu=1500,
                         switch_port=None)
  node.lldp["eno1"] = mod.parse_lldp(lldp_frame())
  assert node.main() == 0
  switch = node.session.tables["switches"][0]
  # by hostname only: a node never sets a switch's management address
  assert switch == {"id": switch["id"], "hostname": "leaf01"}
  port = node.session.tables["switchports"][0]
  assert port == {"id": port["id"], "switch": switch["id"], "name": "swp12"}
  assert writes(node, "patch") == [
    ("patch", f"networkinterfaces/{nic['id']}/", {"name": "eno1", "mtu": 9000}),
    ("patch", f"networkinterfaces/{nic['id']}/", {"switch_port": port["id"]})]


def test_adds_an_unknown_interface_without_touching_ip_or_network(mod, node):
  make_iface(node.sysfs, "eno1", 1, MAC)
  assert node.main() == 0
  assert writes(node) == [("post", "networkinterfaces/", {"system": 7, "name": "eno1", "mtu": 1500, "mac": MAC})]


def test_infiniband_interface_is_matched_and_added_by_guid(mod, node):
  make_iface(node.sysfs, "ib0", 32, IB_ADDR, mtu=2044)
  assert node.main() == 0
  assert writes(node) == [
    ("post", "networkinterfaces/", {"system": 7, "name": "ib0", "mtu": 2044, "guid": IB_GUID})]
  # a second boot finds it by GUID and changes nothing
  node.session.writes.clear()
  assert node.main() == 0
  assert writes(node) == []


def test_reuses_an_existing_switch_and_port(mod, node):
  make_iface(node.sysfs, "eno1", 1, MAC)
  sw = node.session.add("switches", hostname="leaf01", mgmt_mac=None, mgmt_ip=None, guid=None)
  port = node.session.add("switchports", switch=sw["id"], name="swp12")
  nic = node.session.add("networkinterfaces", system=7, name="eno1", mac=MAC, mtu=1500, switch_port=None)
  node.lldp["eno1"] = mod.parse_lldp(lldp_frame())
  assert node.main() == 0
  assert writes(node) == [("patch", f"networkinterfaces/{nic['id']}/", {"switch_port": port["id"]})]


def test_already_in_sync_writes_nothing(mod, node):
  make_iface(node.sysfs, "eno1", 1, MAC)
  sw = node.session.add("switches", hostname="leaf01", mgmt_mac="0c:42:a1:00:00:01")
  port = node.session.add("switchports", switch=sw["id"], name="swp12")
  node.session.add("networkinterfaces", system=7, name="eno1", mac=MAC, mtu=1500, switch_port=port["id"])
  node.lldp["eno1"] = mod.parse_lldp(lldp_frame())
  assert node.main() == 0
  assert writes(node) == []


def test_two_interfaces_on_one_switch_add_it_once(mod, node):
  make_iface(node.sysfs, "eno1", 1, MAC)
  make_iface(node.sysfs, "eno2", 1, "aa:bb:cc:dd:ee:02")
  node.lldp["eno1"] = mod.parse_lldp(lldp_frame())
  node.lldp["eno2"] = mod.parse_lldp(lldp_frame(port=b"\x05swp13"))
  assert node.main() == 0
  assert len(node.session.tables["switches"]) == 1
  assert sorted(p["name"] for p in node.session.tables["switchports"]) == ["swp12", "swp13"]


def test_switch_found_by_management_mac(mod, node):
  make_iface(node.sysfs, "eno1", 1, MAC)
  sw = node.session.add("switches", hostname="leaf01.mgmt", mgmt_mac="0c:42:a1:00:00:01")
  node.lldp["eno1"] = mod.parse_lldp(lldp_frame())
  assert node.main() == 0
  assert len(node.session.tables["switches"]) == 1
  assert node.session.tables["switchports"][0]["switch"] == sw["id"]


def test_switch_added_by_another_node_meanwhile_is_found_again(mod, node):
  make_iface(node.sysfs, "eno1", 1, MAC)
  node.lldp["eno1"] = mod.parse_lldp(lldp_frame())
  real_post = node.session.post

  def racing_post(url, data=None):
    if url.endswith("switches/") and not node.session.tables["switches"]:
      # the other node gets there first: hostname is unique, so this one fails
      node.session.add("switches", hostname="leaf01")
    return real_post(url, data)

  node.session.post = racing_post
  assert node.main() == 0
  assert len(node.session.tables["switches"]) == 1
  assert len(node.session.tables["switchports"]) == 1


def test_refused_port_link_is_a_warning(mod, node, capsys):
  make_iface(node.sysfs, "eno1", 1, MAC, mtu=9000)
  nic = node.session.add("networkinterfaces", system=7, name="eno1", mac=MAC, mtu=1500, switch_port=None)
  node.lldp["eno1"] = mod.parse_lldp(lldp_frame())
  real_patch = node.session.patch

  def patch(url, data=None):
    if "switch_port" in json.loads(data):
      return Response(400, {"switch_port": [
        "This switch port is on a different network from this interface."]})
    return real_patch(url, data)

  node.session.patch = patch
  assert node.main() == 0
  # the MTU still went through
  assert nic["mtu"] == 9000
  assert "wouldn't set switch_port on eno1" in capsys.readouterr().err


def test_mac_already_on_another_system_is_a_warning(mod, node, capsys):
  make_iface(node.sysfs, "eno1", 1, MAC)
  make_iface(node.sysfs, "eno2", 1, "aa:bb:cc:dd:ee:02")
  nic = node.session.add("networkinterfaces", system=7, name="eth1", mac="aa:bb:cc:dd:ee:02", mtu=1500)
  node.session.conflict.add(("post", "networkinterfaces"))
  assert node.main() == 0
  assert "wouldn't add eno1" in capsys.readouterr().err
  assert nic["name"] == "eno2"


def test_mpcc_without_the_node_rules_is_not_a_failure(mod, node, capsys):
  make_iface(node.sysfs, "eno1", 1, MAC)
  node.session.add("networkinterfaces", system=7, name="eth0", mac=MAC, mtu=1500)
  node.session.refuse.add(("patch", "networkinterfaces"))
  assert node.main() == 0
  assert "doesn't let nodes sync" in capsys.readouterr().out


def test_failed_write_fails_the_script_but_syncs_the_rest(mod, node, capsys):
  make_iface(node.sysfs, "eno1", 1, MAC)
  make_iface(node.sysfs, "eno2", 1, "aa:bb:cc:dd:ee:02")
  node.session.fail.add(("post", "networkinterfaces"))
  nic = node.session.add("networkinterfaces", system=7, name="eth1", mac="aa:bb:cc:dd:ee:02", mtu=1500)
  assert node.main() == 1
  assert ("patch", f"networkinterfaces/{nic['id']}/", {"name": "eno2"}) in writes(node)


def test_port_linked_elsewhere_is_reported(mod, node, capsys):
  make_iface(node.sysfs, "eno1", 1, MAC)
  node.session.add("networkinterfaces", system=7, name="eno1", mac=MAC, mtu=1500, switch_port=None)
  node.lldp["eno1"] = mod.parse_lldp(lldp_frame())
  node.session.patch = lambda url, data=None: Response(400, {"switch_port": [
    "network interface with this Switch Port already exists."]})
  assert node.main() == 0
  assert "Switch Port already exists" in capsys.readouterr().err


def test_lldp_timeout_param(mod, node, monkeypatch):
  make_iface(node.sysfs, "eno1", 1, MAC)
  node.entity.write_text(json.dumps({"config_params": {"nic_sync_lldp_timeout": "0"}}))
  heard = []
  monkeypatch.setattr(mod, "listen_lldp", lambda name, timeout: heard.append(name))
  assert node.main() == 0
  assert heard == []


def test_infiniband_switch_is_added_with_its_guid(mod, node, monkeypatch):
  make_iface(node.sysfs, "ib0", 32, IB_ADDR)
  monkeypatch.setattr(mod, "ib_neighbor", lambda iface: {
    "guid": "0x248a070300f1e2d3", "sysname": "MF0;ib-sw01:MSB7800/U1", "port": "17"})
  assert node.main() == 0
  assert ("post", "switches/", {"hostname": "MF0;ib-sw01:MSB7800/U1", "guid": "0x248a070300f1e2d3"}) \
    in writes(node)
  nic = node.session.tables["networkinterfaces"][0]
  assert nic["switch_port"] == node.session.tables["switchports"][0]["id"]


def test_shared_key_refusal_is_an_error_not_missing_rules(mod, node, capsys):
  make_iface(node.sysfs, "eno1", 1, MAC)
  node.session.post = lambda url, data=None: Response(403, {"detail": (
    "The shared API key isn't accepted here (MPROV_SHARED_KEY_MODE=nodes). "
    "Use this agent's own credential.")})
  assert node.main() == 1
  out = capsys.readouterr()
  assert "doesn't let nodes sync" not in out.out
  assert "shared API key" in out.err


def test_unreachable_mpcc_is_a_clear_error(mod, node, capsys):
  make_iface(node.sysfs, "eno1", 1, MAC)

  def broken(url, data=None):
    # what requests raises for a TLS failure: a subclass of OSError
    raise ConnectionError("certificate verify failed")

  node.session.get = broken
  assert node.main() == 1
  err = capsys.readouterr().err
  assert "couldn't reach the mPCC" in err and "certificate verify failed" in err
  assert "Traceback" not in err


def test_no_interfaces_is_success(mod, node):
  assert node.main() == 0
  assert writes(node) == []
