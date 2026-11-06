"""report_booted.py tells the mPCC the node has booted (#91,
mprov_control_center#433), and never fails the boot: every outcome exits 0.

The tests run the real main() with mprov_jobserver's MProvScript and the
mPCC session stubbed.
"""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "post-boot" / "report_booted.py"
URL = "http://mpcc.example/"


class Response:
  def __init__(self, status_code, text=""):
    self.status_code = status_code
    self.text = text


class Session:
  def __init__(self, reply=None, error=None):
    self.reply = reply or Response(200, '{"provision": false, "bootable_nic": "ens18"}')
    self.error = error
    self.posts = []

  def post(self, url, data=None):
    self.posts.append((url, json.loads(data)))
    if self.error:
      raise self.error
    return self.reply


def load(monkeypatch, session=None, init_exit=None, importable=True):
  """Load the script with a stub MProvScript whose __init__ sets up `session`,
  or sys.exit()s with `init_exit` like the real one does without a session."""
  monkeypatch.setitem(sys.modules, "mprov_jobserver", types.ModuleType("mprov_jobserver"))
  if importable:
    base = types.ModuleType("mprov_jobserver.script")

    class MProvScript:
      def __init__(self, **kwargs):
        if init_exit is not None:
          sys.exit(init_exit)
        self.mprovURL = URL
        self.session = session

    base.MProvScript = MProvScript
    monkeypatch.setitem(sys.modules, "mprov_jobserver.script", base)
  else:
    monkeypatch.setitem(sys.modules, "mprov_jobserver.script", None)
  spec = importlib.util.spec_from_file_location("report_booted", SCRIPT)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def test_reports_booted(monkeypatch, capsys):
  session = Session()
  mod = load(monkeypatch, session)
  assert mod.main() == 0
  assert session.posts == [(f"{URL}systems/?self", {"booted": True})]
  out = capsys.readouterr()
  assert "reported booted to the mPCC (200)." in out.out
  assert "not recorded" not in out.err


def test_sends_the_scripts_version(monkeypatch):
  mod = load(monkeypatch, Session())
  assert mod.MScript.script_version == mod.MPROV_SCRIPTS_VERSION


@pytest.mark.parametrize("status, hint", [
  (403, "before 0.7.0"),
  (404, "doesn't know this node"),
  (500, ""),
])
def test_a_refusal_is_logged_not_fatal(monkeypatch, capsys, status, hint):
  mod = load(monkeypatch, Session(Response(status, "nope")))
  assert mod.main() == 0
  err = capsys.readouterr().err
  assert f"booted not recorded: the mPCC answered {status}" in err
  assert hint in err
  assert "nope" in err


def test_a_connection_error_is_logged_not_fatal(monkeypatch, capsys):
  mod = load(monkeypatch, Session(error=ConnectionError("refused")))
  assert mod.main() == 0
  assert "booted not recorded: unable to reach the mPCC" in capsys.readouterr().err


def test_no_session_is_logged_not_fatal(monkeypatch, capsys):
  # MProvScript.__init__ sys.exit(1)s when it can't start a session.
  mod = load(monkeypatch, init_exit=1)
  assert mod.main() == 0
  assert "booted not recorded: SystemExit" in capsys.readouterr().err


def test_no_mprov_jobserver_is_logged_not_fatal(monkeypatch, capsys):
  mod = load(monkeypatch, importable=False)
  assert mod.main() == 0
  assert "booted not recorded: unable to load mprov_jobserver" in capsys.readouterr().err


def test_exits_0_when_run_as_a_script(monkeypatch):
  # run for real, without mprov_jobserver: still exit 0.
  import subprocess
  r = subprocess.run([sys.executable, "-I", str(SCRIPT)],
                     capture_output=True, text=True, timeout=30)
  assert r.returncode == 0, r.stderr
  assert "booted not recorded" in r.stderr
