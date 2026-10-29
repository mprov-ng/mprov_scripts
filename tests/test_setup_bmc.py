"""setupBMC.py: the password stays off command lines (#35), a failed or empty
systembmcs reply is handled (#36), and the LAN settings are sent once each,
with the gateway (#27).

The tests run the real run() with the mPCC session, subprocess.run and sh
stubbed, and check the commands it runs.
"""
import importlib.util
import json
import os
import stat
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "post-boot" / "setupBMC.py"
URL = "http://mpcc.example/"


class Response:
  def __init__(self, status_code, body):
    self.status_code = status_code
    self.body = body
    self.text = json.dumps(body)

  def json(self):
    return self.body


class Session:
  def __init__(self, bmcs_status=200, bmcs=None):
    self.bmcs_status = bmcs_status
    self.bmcs = [bmc()] if bmcs is None else bmcs
    self.patches = []

  def get(self, url):
    if url == f"{URL}systems/?hostname=node01":
      return Response(200, [{"id": 7, "hostname": "node01"}])
    if url == f"{URL}systembmcs/?system=7&detail":
      return Response(self.bmcs_status, self.bmcs)
    raise AssertionError(f"unexpected GET {url}")

  def patch(self, url, data=None):
    self.patches.append((url, json.loads(data)))
    return Response(200, {})


def bmc(password="s3cret pw's", gateway="10.0.0.1"):
  return {"id": 3, "ipaddress": "10.0.0.50", "vlan": 0, "username": "admin", "password": password,
          "network": {"netmask": 24, "vlan": 0, "gateway": gateway}}


class Run:
  """ Records subprocess.run calls; reads an exec batch file while it exists. """

  def __init__(self, fail=()):
    self.calls = []
    self.batches = []
    self.fail = fail

  def __call__(self, args, **kwargs):
    self.calls.append(list(args))
    if args[1:2] == ["exec"]:
      path = args[2]
      self.batches.append((Path(path).read_text(), stat.S_IMODE(os.stat(path).st_mode), path))
    failed = any(f in " ".join(args) for f in self.fail)
    return types.SimpleNamespace(returncode=1 if failed else 0,
                                 stdout="", stderr="Invalid data field in request" if failed else "")

  def ipmitool(self):
    return [c[1:] for c in self.calls if c[0] == "/usr/bin/ipmitool"]


@pytest.fixture
def script(monkeypatch):
  monkeypatch.setitem(sys.modules, "mprov_jobserver", types.ModuleType("mprov_jobserver"))
  base = types.ModuleType("mprov_jobserver.script")
  base.MProvScript = type("MProvScript", (), {})
  monkeypatch.setitem(sys.modules, "mprov_jobserver.script", base)
  fake_sh = types.ModuleType("sh")
  fake_sh.ipmitool = lambda args, **kw: iter(["IP Address : 10.0.0.50", "MAC Address             : aa:bb:cc:dd:ee:ff"])
  monkeypatch.setitem(sys.modules, "sh", fake_sh)
  for name in ("requests", "yaml"):
    monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
  spec = importlib.util.spec_from_file_location("setupBMC", SCRIPT)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  monkeypatch.setattr(mod.socket, "gethostname", lambda: "node01")

  s = object.__new__(mod.MScript)
  s.mprovURL = URL
  s.session = Session()
  s.run_ = Run()
  monkeypatch.setattr(mod.subprocess, "run", s.run_)
  return s


def test_bmc_is_set_up_and_its_mac_registered(script):
  script.run()
  assert script.run_.ipmitool() == [
    ["lan", "set", "1", "ipsrc", "static"],
    ["lan", "set", "1", "ipaddr", "10.0.0.50"],
    ["lan", "set", "1", "netmask", "255.255.255.0"],  # once, no trailing space (#27)
    ["lan", "set", "1", "defgw", "ipaddr", "10.0.0.1"],
    ["lan", "set", "1", "vlan", "id", "off"],
    ["user", "set", "name", "2", "admin"],
    ["exec", script.run_.batches[0][2]],
    ["user", "enable", "2"],
  ]
  assert script.session.patches == [(f"{URL}systembmcs/3/", {
    "id": 3, "mac": "aa:bb:cc:dd:ee:ff", "ipv6ll": "fe80::A8bb:ccff:fedd:eeff"})]  # mac2LL() upper-cases the first octet


def test_password_is_never_on_a_command_line(script):
  script.run()
  assert not any("s3cret" in arg for call in script.run_.calls for arg in call)
  text, mode, path = script.run_.batches[0]
  assert text == 'user set password 2 "s3cret pw\'s"\n'
  assert mode == 0o600
  assert not os.path.exists(path)  # removed once ipmitool is done


def test_no_gateway_no_defgw(script):
  script.session.bmcs = [bmc(gateway=None)]
  script.run()
  assert not any("defgw" in c for c in script.run_.ipmitool())


@pytest.mark.parametrize("password, shown", [
  ('qu"ote', '"'), ("ha#sh", "#"), ("til~de", "~"), ("tab\there", "\t"), ("café", "é"),
])
def test_password_exec_cant_carry_is_refused(script, capsys, password, shown):
  script.session.bmcs = [bmc(password=password)]
  script.run()
  err = capsys.readouterr().err
  assert "can't carry" in err and repr(shown) in err
  assert password not in err
  assert script.run_.batches == []
  # the user isn't enabled with a password it doesn't have.
  assert ["user", "enable", "2"] not in script.run_.ipmitool()


def test_empty_password_leaves_the_bmc_password_alone(script, capsys):
  script.session.bmcs = [bmc(password="")]
  script.run()
  assert script.run_.batches == []
  assert "leaving the BMC's password" in capsys.readouterr().err
  assert script.run_.ipmitool()[-1] == ["user", "enable", "2"]


def test_failed_bmc_request_exits_instead_of_crashing(script, capsys):
  # it fell through to the MAC registration and died with NameError (#36).
  script.session.bmcs_status = 500
  with pytest.raises(SystemExit) as exit:
    script.run()
  assert exit.value.code == 1
  assert "Unable to get bmc info, 500" in capsys.readouterr().err
  assert script.run_.calls == [] and script.session.patches == []


def test_system_without_a_bmc_is_skipped(script, capsys):
  script.session.bmcs = []
  script.run()
  assert "no BMC in the mPCC" in capsys.readouterr().out
  assert script.run_.calls == [] and script.session.patches == []


def test_ipmitool_failure_shows_its_error(script, capsys):
  # stdout/stderr weren't captured, so this printed 'None None'.
  script.run_.fail = ("vlan",)
  script.run()
  err = capsys.readouterr().err
  assert "ipmitool lan set 1 vlan id off failed (1): Invalid data field in request" in err
  assert ["user", "set", "name", "2", "admin"] not in script.run_.ipmitool()
