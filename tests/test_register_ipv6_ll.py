"""register_IPv6_ll.py: main() returns run()'s status, so a failure exits
nonzero (#37).

The tests run the real run() and main() with the mPCC session, netifaces
and pyroute2 stubbed.
"""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "post-boot" / "register_IPv6_ll.py"
URL = "http://mpcc.example/"
MAC = "aa:bb:cc:dd:ee:ff"
LL = "fe80::a8bb:ccff:fedd:eeff"


class Response:
  def __init__(self, status_code, body):
    self.status_code = status_code
    self.body = body
    self.text = repr(body) if isinstance(body, Exception) else json.dumps(body)

  def json(self):
    if isinstance(self.body, Exception):
      raise self.body
    return self.body


class Session:
  def __init__(self, ipv6ll=None):
    self.get_reply = Response(200, [{"id": 5, "mac": MAC, "ipv6ll": ipv6ll}])
    self.patch_reply = Response(200, {})
    self.patches = []

  def get(self, url):
    assert url == f"{URL}networkinterfaces/?hostname=node01"
    return self.get_reply

  def patch(self, url, data=None):
    self.patches.append((url, json.loads(data)))
    return self.patch_reply


@pytest.fixture
def script(monkeypatch):
  monkeypatch.setitem(sys.modules, "mprov_jobserver", types.ModuleType("mprov_jobserver"))
  base = types.ModuleType("mprov_jobserver.script")
  base.MProvScript = type("MProvScript", (), {})
  monkeypatch.setitem(sys.modules, "mprov_jobserver.script", base)
  netifaces = types.ModuleType("netifaces")
  netifaces.interfaces = lambda: ["lo", "ens18"]
  monkeypatch.setitem(sys.modules, "netifaces", netifaces)
  pyroute2 = types.ModuleType("pyroute2")
  pyroute2.IPDB = lambda: types.SimpleNamespace(
    interfaces={"ens18": types.SimpleNamespace(operstate="UP")})
  monkeypatch.setitem(sys.modules, "pyroute2", pyroute2)
  spec = importlib.util.spec_from_file_location("register_IPv6_ll", SCRIPT)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  netifaces.ifaddresses = lambda iface: {
    mod.AF_PACKET: [{"addr": MAC}],
    mod.AF_INET6: [{"addr": "2001:db8::5"}, {"addr": f"{LL}%ens18"}],
  }
  monkeypatch.setattr(mod.platform, "node", lambda: "node01.cluster")

  s = object.__new__(mod.MScript)
  s.mprovURL = URL
  s.session = Session()
  s.startSession = lambda: None
  return s


def test_registers_the_link_local_address(script):
  assert script.main() == 0
  assert script.session.patches == [(f"{URL}networkinterfaces/5/", {"ipv6ll": LL})]


def test_already_registered_is_success(script):
  script.session = Session(ipv6ll=LL)
  assert script.main() == 0
  assert script.session.patches == []


def test_failed_interface_lookup_fails_the_script(script, capsys):
  script.session.get_reply = Response(500, "boom")
  assert script.main() == 1
  assert "Unable to get interfaces" in capsys.readouterr().err


def test_unparsable_reply_fails_the_script(script):
  script.session.get_reply = Response(200, ValueError("not json"))
  assert script.main() == 1


def test_failed_update_fails_the_script(script, capsys):
  # main() dropped run()'s 1, so this exited 0 (#37).
  script.session.patch_reply = Response(400, {"ipv6ll": ["bad"]})
  assert script.main() == 1
  assert 'Unable to update mPCC, 400: {"ipv6ll": ["bad"]}' in capsys.readouterr().err
