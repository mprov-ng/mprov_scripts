#!/bin/sh
''''true; p=/usr/libexec/mprov/bin/python3; [ -x "$p" ] || p=/usr/bin/python3; exec "$p" "$0" "$@" #'''
# The two lines above pick the interpreter (#86, #89): sh runs line 2, which
# execs the job server's bundled python (/usr/libexec/mprov/bin/python3, in
# images built by mprov_jobserver 0.7.0 and later) if it's there, or else the
# system /usr/bin/python3 (images built by 0.6.x).  Python reads line 2 as a
# plain string and skips it.  Keep both lines exactly as they are.

# Script Type: post-boot

"""
Sync this node's network interfaces to the mPCC (#104).

Reads every physical Ethernet and InfiniBand interface from /sys/class/net,
and for each one:
- matches it to one of this system's network interfaces in the mPCC, by MAC
  (Ethernet) or port GUID (InfiniBand), and updates its name, MTU and GUID
  there to what the node has; an interface the mPCC doesn't have yet is
  added to this system;
- on an Ethernet interface that's up, listens for an LLDP frame from the
  switch; on an InfiniBand one, asks the switch at the other end of the
  link with smpquery (infiniband-diags), if it's installed;
- finds that switch and port in the mPCC, adding them if they're new (a new
  switch by its hostname only: its management address feeds DHCP and DNS,
  so only an admin sets it), and links the interface to the port.

The interface's IP address, network and MAC in the mPCC are never changed,
so DHCP and network booting are left as the admin set them up.  It runs on
the node's own certificate; an mPCC that doesn't let nodes do this yet
refuses it, and the script says so and exits 0.  A write the mPCC refuses
because of what it would change (a MAC already on another system, a port on
another network) is reported as a warning and the rest carries on.

Optional config params:
- nic_sync_lldp_timeout: seconds to wait for LLDP on each interface
  (default 35; switches send it every 30s).  0 turns LLDP off.
- nic_sync_skip: space-separated interface names to leave alone.
"""

import glob
import json
import os
import re
import select
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from urllib.parse import urlencode

from mprov_jobserver.script import MProvScript

# mprov-ng/mprov_control_center#452: sent as the mProv-Version header once
# mprov_jobserver's MProvScript grows a script_version hook (visibility/
# logging only, no enforcement). Keep in sync with the repo-root VERSION
# file; these scripts are copied standalone into each mPCC's Scripts
# catalogue, so there's no build step to pull the version from VERSION at
# runtime.
MPROV_SCRIPTS_VERSION = "0.7"

SYSFS_NET = "/sys/class/net"
ENTITY_FILE = "/tmp/mprov/entity.json"
DEFAULT_LLDP_TIMEOUT = 35

# /sys/class/net/<if>/type (include/uapi/linux/if_arp.h)
ARPHRD_ETHER = 1
ARPHRD_INFINIBAND = 32

# LLDP (IEEE 802.1AB)
ETH_P_LLDP = 0x88cc
LLDP_MULTICAST = bytes.fromhex("0180c200000e")
SOL_PACKET = 263
PACKET_ADD_MEMBERSHIP = 1
PACKET_MR_MULTICAST = 0
# a frame this host sent itself (e.g. its own lldpd), not one from the switch
PACKET_OUTGOING = 4
TLV_END = 0
TLV_CHASSIS_ID = 1
TLV_PORT_ID = 2
TLV_PORT_DESC = 4
TLV_SYSTEM_NAME = 5
TLV_MGMT_ADDR = 8
# Chassis/Port ID subtype: a MAC address.  Port ID subtype 3 and chassis ID
# subtype 4 are both "MAC address".
CHASSIS_MAC = 4
PORT_MAC = 3
# Management address subtypes (IANA address family numbers)
ADDR_IPV4 = 1
ADDR_IPV6 = 2

# How the mPCC words a 403 from its per-agent rules (common.permissions):
# "<user> may not <METHOD> this."  Its other 403 (the shared API key refused)
# is an error.  A check on what a write would change (a MAC already on another
# system, a port on another network, a hostname or GUID already taken) is a
# 400 whose body is keyed by the field.
RULE_REFUSAL = re.compile(r"may not (GET|POST|PATCH|PUT|DELETE) this")


class NotAllowed(Exception):
  """ The mPCC's rules don't let a node make this write at all: it's an mPCC
  that doesn't let nodes sync interfaces (yet). """


class Refused(Exception):
  """ The mPCC refused this one write (400) because of what it would change,
  e.g. a MAC already on another system, or a port on another network:
  something only an admin can sort out, so it's reported and the sync
  carries on. """


class Unreachable(Exception):
  """ A request to the mPCC didn't get an answer at all (connection, TLS or
  timeout error): nothing more can be synced this run. """


def fmt_mac(raw):
  """ 6 raw bytes as a lowercase, colon-separated MAC. """
  return ":".join(f"{b:02x}" for b in raw)


def fmt_guid(raw):
  """ 8 raw bytes as a GUID, "0x" and 16 lowercase hex digits. """
  return "0x" + raw.hex()


def normalize_guid(text):
  """ A GUID in any common spelling (0x..., or with colons) as "0x" and 16
  lowercase hex digits, or None if it isn't one. """
  digits = re.sub(r"[^0-9a-fA-F]", "", text[2:] if text.lower().startswith("0x") else text)
  if len(digits) != 16:
    return None
  return "0x" + digits.lower()


# ---- what the node has ----------------------------------------------------

def _read(path):
  try:
    with open(path) as f:
      return f.read().strip()
  except OSError:
    return None


def read_interfaces(sysfs=SYSFS_NET, skip=()):
  """
  The node's physical Ethernet and InfiniBand interfaces, as dicts of name,
  kind ("ether" or "ib"), mac (Ethernet only), guid (InfiniBand only), mtu
  and up.  Virtual interfaces (no device link: bridges, bonds, VLANs, veth,
  tunnels) and loopback are left out.
  """
  found = []
  for path in sorted(glob.glob(os.path.join(sysfs, "*"))):
    name = os.path.basename(path)
    if name == "lo" or name in skip or not os.path.exists(os.path.join(path, "device")):
      continue
    kind = _read(os.path.join(path, "type"))
    address = (_read(os.path.join(path, "address")) or "").lower()
    iface = {"name": name, "mac": None, "guid": None,
             "up": _read(os.path.join(path, "operstate")) == "up"}
    try:
      iface["mtu"] = int(_read(os.path.join(path, "mtu")))
    except (TypeError, ValueError):
      iface["mtu"] = None
    if kind == str(ARPHRD_ETHER):
      if not re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", address) or address == "00:00:00:00:00:00":
        continue
      iface["kind"] = "ether"
      iface["mac"] = address
    elif kind == str(ARPHRD_INFINIBAND):
      # an IPoIB address is 20 bytes: 4 of flags and queue pair number,
      # which change from boot to boot, then the 16-byte port GID, whose
      # last 8 bytes are the port GUID.  Only the GUID is stable, so it's
      # what the interface is known by; no MAC is sent for it.
      octets = address.split(":")
      if len(octets) != 20:
        continue
      iface["kind"] = "ib"
      iface["guid"] = "0x" + "".join(octets[12:])
    else:
      continue
    found.append(iface)
  return found


# ---- LLDP -----------------------------------------------------------------

def parse_lldp(payload):
  """
  The switch and port from an LLDP frame's payload (after the Ethernet
  header): a dict of sysname, chassis_id, chassis_mac, port, port_desc and
  mgmt_ip, each None if the switch didn't send it.
  """
  info = dict.fromkeys(("sysname", "chassis_id", "chassis_mac", "port", "port_desc", "mgmt_ip"))
  while len(payload) >= 2:
    header, = struct.unpack("!H", payload[:2])
    tlv_type, length = header >> 9, header & 0x1ff
    value = payload[2:2 + length]
    payload = payload[2 + length:]
    if tlv_type == TLV_END or len(value) < length:
      break
    if tlv_type == TLV_CHASSIS_ID and value:
      subtype, data = value[0], value[1:]
      if subtype == CHASSIS_MAC and len(data) == 6:
        info["chassis_mac"] = fmt_mac(data)
        info["chassis_id"] = info["chassis_mac"]
      else:
        info["chassis_id"] = data.decode("utf-8", "replace")
    elif tlv_type == TLV_PORT_ID and value:
      subtype, data = value[0], value[1:]
      if subtype == PORT_MAC and len(data) == 6:
        info["port"] = fmt_mac(data)
      else:
        info["port"] = data.decode("utf-8", "replace")
    elif tlv_type == TLV_PORT_DESC:
      info["port_desc"] = value.decode("utf-8", "replace")
    elif tlv_type == TLV_SYSTEM_NAME:
      info["sysname"] = value.decode("utf-8", "replace")
    elif tlv_type == TLV_MGMT_ADDR and len(value) >= 2 and info["mgmt_ip"] is None:
      # address string length (subtype byte + address), subtype, address
      addr_len, subtype = value[0], value[1]
      addr = value[2:1 + addr_len]
      if subtype == ADDR_IPV4 and len(addr) == 4:
        info["mgmt_ip"] = socket.inet_ntop(socket.AF_INET, addr)
      elif subtype == ADDR_IPV6 and len(addr) == 16:
        info["mgmt_ip"] = socket.inet_ntop(socket.AF_INET6, addr)
  return info


def listen_lldp(ifname, timeout):
  """
  Wait up to timeout seconds for an LLDP frame on ifname, and return what
  parse_lldp() makes of it, or None if none came.  Joins the LLDP multicast
  group on the socket instead of putting the interface in promiscuous mode;
  the membership goes when the socket is closed.
  """
  try:
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_LLDP))
  except OSError as err:
    print(f"Warning: can't listen for LLDP on {ifname}: {err}", file=sys.stderr)
    return None
  try:
    sock.bind((ifname, ETH_P_LLDP))
    index = socket.if_nametoindex(ifname)
    mreq = struct.pack("iHH8s", index, PACKET_MR_MULTICAST, 6, LLDP_MULTICAST)
    sock.setsockopt(SOL_PACKET, PACKET_ADD_MEMBERSHIP, mreq)
    end = time.monotonic() + timeout
    while True:
      left = end - time.monotonic()
      if left <= 0:
        return None
      ready, _, _ = select.select([sock], [], [], left)
      if not ready:
        return None
      frame, addr = sock.recvfrom(65535)
      if addr[2] == PACKET_OUTGOING:
        continue
      # 14-byte Ethernet header; a VLAN tag would put the type 4 bytes on
      if len(frame) > 14 and struct.unpack("!H", frame[12:14])[0] == ETH_P_LLDP:
        info = parse_lldp(frame[14:])
        if info["port"] and (info["sysname"] or info["chassis_id"]):
          return info
  except OSError as err:
    print(f"Warning: listening for LLDP on {ifname} failed: {err}", file=sys.stderr)
    return None
  finally:
    sock.close()


# ---- InfiniBand -----------------------------------------------------------

def parse_smpquery(text):
  """ smpquery's "Key:.....value" lines as a dict. """
  out = {}
  for line in text.splitlines():
    key, sep, value = line.partition(":")
    if sep:
      out[key.strip()] = value.lstrip(".").strip()
  return out


def ib_neighbor(iface, sysfs=SYSFS_NET, run=subprocess.run):
  """
  The switch at the other end of an InfiniBand interface's link, from
  smpquery (infiniband-diags): a dict of guid, sysname and port, or None if
  smpquery isn't installed, or the link doesn't go to a switch.
  """
  if shutil.which("smpquery") is None:
    return None
  cas = glob.glob(os.path.join(sysfs, iface["name"], "device", "infiniband", "*"))
  if len(cas) != 1:
    return None
  ca = os.path.basename(cas[0])
  try:
    port = int(_read(os.path.join(sysfs, iface["name"], "dev_port")) or 0) + 1
  except ValueError:
    port = 1
  base = ["smpquery", "-C", ca, "-P", str(port), "-D"]
  try:
    info = run(base + ["nodeinfo", "0,1"], capture_output=True, text=True, timeout=30)
    desc = run(base + ["nodedesc", "0,1"], capture_output=True, text=True, timeout=30)
  except (OSError, subprocess.SubprocessError) as err:
    print(f"Warning: smpquery on {iface['name']} failed: {err}", file=sys.stderr)
    return None
  if info.returncode or desc.returncode:
    print(f"Warning: smpquery on {iface['name']} failed: {(info.stderr or desc.stderr).strip()}",
          file=sys.stderr)
    return None
  node = parse_smpquery(info.stdout)
  # NodeType 2 is a switch
  if not node.get("NodeType", "").startswith("Switch") and node.get("NodeType") != "2":
    return None
  guid = normalize_guid(node.get("NodeGuid", ""))
  sysname = parse_smpquery(desc.stdout).get("Node Description")
  if not guid or not node.get("LocalPort"):
    return None
  return {"guid": guid, "sysname": sysname or guid, "port": node["LocalPort"]}


# ---- the script -----------------------------------------------------------

def load_params(path=None):
  """ This run's config params from the script runner's entity.json, or {}. """
  try:
    with open(path or ENTITY_FILE) as f:
      params = json.load(f).get("config_params") or {}
  except (OSError, ValueError, AttributeError):
    return {}
  return params if isinstance(params, dict) else {}


class MScript(MProvScript):
  script_version = MPROV_SCRIPTS_VERSION

  def __init__(self, **kwargs):
    print("Syncing network interfaces with the mPCC.")
    super().__init__(**kwargs)
    self.runonce = True

  # -- API helpers --

  def request(self, method, path, data=None):
    """ One request to the mPCC.  Raises Unreachable if it gets no answer;
    requests' errors are all OSErrors. """
    url = f"{self.mprovURL}{path}"
    try:
      if method == "get":
        return self.session.get(url)
      return getattr(self.session, method)(url, data=data)
    except OSError as err:
      raise Unreachable(f"{method.upper()} {path}: {err}")

  def api_list(self, path):
    """ A list from the mPCC; a 404 is an empty one (the mPCC's reply to a
    node's certificate when nothing matches).  None on any other error. """
    result = self.request("get", path)
    if result.status_code == 404:
      return []
    if result.status_code != 200:
      print(f"Error: GET {path} from the mPCC: {result.status_code}: {result.text}", file=sys.stderr)
      return None
    try:
      data = result.json()
    except ValueError:
      print(f"Error: the mPCC's reply to GET {path} isn't JSON.", file=sys.stderr)
      return None
    if data is None:
      return []
    return data if isinstance(data, list) else [data]

  def api_write(self, method, path, body):
    """ POST or PATCH body; the reply's JSON, or None on failure.  Raises
    NotAllowed on a 403 from the mPCC's rules, Refused on a 400. """
    result = self.request(method, path, json.dumps(body))
    if result.status_code == 403 and RULE_REFUSAL.search(result.text or ""):
      raise NotAllowed(f"{method.upper()} {path}: {result.text}")
    if result.status_code == 400:
      raise Refused(result.text)
    if result.status_code not in (200, 201):
      print(f"Error: {method.upper()} {path} {json.dumps(body)}: {result.status_code}: {result.text}",
            file=sys.stderr)
      return None
    try:
      return result.json()
    except ValueError:
      return {}

  # -- switches and ports --

  def find_switch(self, neighbor):
    """ The mPCC's switch for this neighbor: by GUID (InfiniBand), then
    management MAC, then hostname.  None if it isn't there or on error. """
    for key in ("guid", "mgmt_mac", "hostname"):
      value = neighbor.get(key)
      if not value:
        continue
      found = self.api_list("switches/?" + urlencode({key: value}))
      if found:
        return found[0]
    return None

  def find_or_create_switch(self, neighbor):
    """ The switch's id in the mPCC, adding it if it's new; None on error. """
    cache_key = (neighbor.get("guid"), neighbor.get("mgmt_mac"), neighbor.get("hostname"))
    if cache_key in self.switches:
      return self.switches[cache_key]
    switch = self.find_switch(neighbor)
    if switch is None:
      # its hostname and (InfiniBand) GUID; never a management address, which
      # feeds DHCP and DNS, so only an admin sets it
      body = {"hostname": neighbor["hostname"]}
      if neighbor.get("guid"):
        body["guid"] = neighbor["guid"]
      try:
        switch = self.api_write("post", "switches/", body)
        if switch is not None:
          print(f"Added switch {neighbor['hostname']} to the mPCC.")
      except Refused as err:
        # most likely another node booting at the same time just added it
        # (hostnames and GUIDs are unique), so look again
        switch = self.find_switch(neighbor)
        if switch is None:
          print(f"Warning: the mPCC wouldn't add switch {neighbor['hostname']}: {err}", file=sys.stderr)
    switch_id = switch.get("id") if switch else None
    self.switches[cache_key] = switch_id
    return switch_id

  def find_or_create_port(self, switch_id, name):
    """ The switch port's id in the mPCC, adding it if it's new. """
    query = "switchports/?" + urlencode({"switch": switch_id, "name": name})
    found = self.api_list(query)
    if found is None:
      return None
    if found:
      return found[0].get("id")
    try:
      port = self.api_write("post", "switchports/", {"switch": switch_id, "name": name})
    except Refused as err:
      print(f"Warning: the mPCC wouldn't add port {name}: {err}", file=sys.stderr)
      port = None
    if port is None:
      # another node may have just added it
      found = self.api_list(query)
      return found[0].get("id") if found else None
    return port.get("id")

  def neighbor(self, iface, lldp):
    """ The switch and port at the other end of iface, as a dict of hostname,
    guid and mgmt_mac (to look the switch up by) and port, or None if
    unknown. """
    if iface["kind"] == "ib":
      ib = ib_neighbor(iface)
      if ib is None:
        return None
      return {"hostname": ib["sysname"], "guid": ib["guid"], "mgmt_mac": None, "port": ib["port"]}
    info = lldp.get(iface["name"])
    if not info:
      return None
    return {"hostname": info["sysname"] or info["chassis_id"], "guid": None,
            "mgmt_mac": info["chassis_mac"], "port": info["port"]}

  # -- main work --

  def gather_lldp(self, interfaces, timeout):
    """ LLDP from every up Ethernet interface, listened to at the same time. """
    results = {}
    if timeout <= 0:
      return results

    def listen(name):
      results[name] = listen_lldp(name, timeout)

    threads = [threading.Thread(target=listen, args=(i["name"],), daemon=True)
               for i in interfaces if i["kind"] == "ether" and i["up"]]
    if threads:
      print(f"Listening for LLDP for up to {timeout}s on {len(threads)} interface(s).")
    for t in threads:
      t.start()
    for t in threads:
      t.join(timeout + 5)
    return results

  def own_system(self):
    """ This system's id in the mPCC, or None. """
    systems = self.api_list("systems/?self")
    if not systems:
      print("Error: this system isn't in the mPCC.", file=sys.stderr)
      return None
    return systems[0].get("id")

  def sync(self, iface, known, system_id, lldp):
    """ Sync one interface; True if everything it needed went through. """
    key = "guid" if iface["kind"] == "ib" else "mac"
    match = next((n for n in known if (n.get(key) or "").lower() == iface[key]), None)
    if match is None:
      body = {"system": system_id, "name": iface["name"], "mtu": iface["mtu"]}
      body[key] = iface[key]
      try:
        match = self.api_write("post", "networkinterfaces/", body)
      except Refused as err:
        print(f"Warning: the mPCC wouldn't add {iface['name']} ({iface[key]}): {err}", file=sys.stderr)
        return True
      if match is None:
        return False
      print(f"Added {iface['name']} ({iface[key]}) to the mPCC.")
    changes = {}
    if match.get("name") != iface["name"]:
      changes["name"] = iface["name"]
    if iface["mtu"] and match.get("mtu") != iface["mtu"]:
      changes["mtu"] = iface["mtu"]
    if iface["kind"] == "ib" and match.get("guid") != iface["guid"]:
      changes["guid"] = iface["guid"]

    ok = True
    if changes:
      ok = self.update(iface, match, changes)
    port_id = None
    neighbor = self.neighbor(iface, lldp)
    if neighbor is not None and neighbor["hostname"] and neighbor["port"]:
      switch_id = self.find_or_create_switch(neighbor)
      port_id = self.find_or_create_port(switch_id, neighbor["port"]) if switch_id else None
      if port_id is None:
        print(f"Error: couldn't find or add {neighbor['hostname']} port {neighbor['port']} "
              f"in the mPCC for {iface['name']}.", file=sys.stderr)
        ok = False
    elif iface["kind"] == "ether" and iface["up"] and lldp:
      print(f"No LLDP from the switch on {iface['name']}; leaving its switch port as it is.")

    # on its own, so a port the mPCC won't link doesn't hold up the rest
    if port_id is not None and match.get("switch_port") != port_id:
      # a port linked to another system's interface, or on another
      # network, is refused with a 400 that says so (a warning)
      if not self.update(iface, match, {"switch_port": port_id}):
        ok = False
    return ok

  def update(self, iface, nic, changes):
    """ PATCH changes onto the mPCC's interface; True unless it failed.  A
    write the mPCC refuses for what it would change is a warning. """
    try:
      if self.api_write("patch", f"networkinterfaces/{nic['id']}/", changes) is None:
        return False
    except Refused as err:
      print(f"Warning: the mPCC wouldn't set {', '.join(changes)} on {iface['name']}: {err}",
            file=sys.stderr)
      return True
    print(f"Updated {iface['name']} in the mPCC: {', '.join(changes)}.")
    return True

  def run(self):
    params = load_params()
    skip = str(params.get("nic_sync_skip") or "").split()
    timeout = params.get("nic_sync_lldp_timeout")
    try:
      timeout = DEFAULT_LLDP_TIMEOUT if timeout is None else int(timeout)
    except (TypeError, ValueError):
      print(f"Warning: nic_sync_lldp_timeout isn't a number; using {DEFAULT_LLDP_TIMEOUT}.",
            file=sys.stderr)
      timeout = DEFAULT_LLDP_TIMEOUT

    interfaces = read_interfaces(skip=skip)
    if not interfaces:
      print("No physical network interfaces found, nothing to sync.")
      return 0
    try:
      return self.sync_all(interfaces, timeout)
    except Unreachable as err:
      print(f"Error: couldn't reach the mPCC at {self.mprovURL}, stopping: {err}", file=sys.stderr)
      return 1

  def sync_all(self, interfaces, timeout):
    """ Sync every interface; the script's exit status. """
    system_id = self.own_system()
    if system_id is None:
      return 1
    known = self.api_list(f"networkinterfaces/?system={system_id}")
    if known is None:
      return 1

    lldp = self.gather_lldp(interfaces, timeout)
    self.switches = {}
    failed = False
    try:
      for iface in interfaces:
        if not self.sync(iface, known, system_id, lldp):
          failed = True
    except NotAllowed as err:
      print("This mPCC doesn't let nodes sync their interfaces (or this part of it) yet; "
            f"stopped there. ({err})")
      return 0
    return 1 if failed else 0

  def main(self):
    self.startSession()
    return self.run()


def main():
  script = MScript()
  return script.main()

def __main__():
  return main()

if __name__ == "__main__":
  sys.exit(main())
