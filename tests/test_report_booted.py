"""report_booted.py tells the mPCC the node has booted (#91,
mprov_control_center#433) and, in a POST of its own, its serial number (#94,
mprov_control_center#60).  It never fails the boot: every outcome exits 0.

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


OK = '{"provision": false, "bootable_nic": "ens18"}'


class Session:
  """Answers each POST with `reply`, or with `replies[key]` for a body
  carrying that key (booted or serial)."""
  def __init__(self, reply=None, error=None, replies=None):
    self.reply = reply or Response(200, OK)
    self.error = error
    self.replies = replies or {}
    self.posts = []

  def post(self, url, data=None):
    body = json.loads(data)
    self.posts.append((url, body))
    if self.error:
      raise self.error
    for key, reply in self.replies.items():
      if key in body:
        return reply
    return self.reply


def load(monkeypatch, session=None, init_exit=None, importable=True, serial_file="/nonexistent/product_serial"):
  """Load the script with a stub MProvScript whose __init__ sets up `session`,
  or sys.exit()s with `init_exit` like the real one does without a session.
  The serial number is read from `serial_file` (by default, none)."""
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
  mod.SERIAL_FILE = str(serial_file)
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


def serial_file(tmp_path, text):
  path = tmp_path / "product_serial"
  path.write_text(text)
  return path


def test_reports_the_serial_after_booted_in_its_own_post(monkeypatch, tmp_path, capsys):
  session = Session()
  mod = load(monkeypatch, session, serial_file=serial_file(tmp_path, "  ABC123\n"))
  assert mod.main() == 0
  # two POSTs, booted first, never one body with both keys (#94)
  assert session.posts == [(f"{URL}systems/?self", {"booted": True}),
                           (f"{URL}systems/?self", {"serial": "ABC123"})]
  out = capsys.readouterr()
  assert "reported booted to the mPCC (200)." in out.out
  assert "reported serial number to the mPCC (200)." in out.out
  assert out.err == ""


def test_a_403_on_the_serial_is_tolerated_and_booted_still_reported(monkeypatch, tmp_path, capsys):
  # an mPCC with #433 but without #60
  session = Session(replies={"serial": Response(403, "")})
  mod = load(monkeypatch, session, serial_file=serial_file(tmp_path, "ABC123"))
  assert mod.main() == 0
  out = capsys.readouterr()
  assert "reported booted to the mPCC (200)." in out.out
  assert "serial not recorded: the mPCC is too old for serial reporting (403)." in out.out
  assert out.err == ""


def test_the_serial_is_still_tried_when_booted_is_refused(monkeypatch, tmp_path, capsys):
  # a v0.6.x mPCC refuses both; neither is fatal
  session = Session(Response(403, ""))
  mod = load(monkeypatch, session, serial_file=serial_file(tmp_path, "ABC123"))
  assert mod.main() == 0
  assert [body for _, body in session.posts] == [{"booted": True}, {"serial": "ABC123"}]
  out = capsys.readouterr()
  assert "booted not recorded: the mPCC answered 403" in out.err
  assert "too old for serial reporting" in out.out


@pytest.mark.parametrize("status", [400, 404, 500])
def test_other_serial_refusals_are_logged_not_fatal(monkeypatch, tmp_path, capsys, status):
  session = Session(replies={"serial": Response(status, "nope")})
  mod = load(monkeypatch, session, serial_file=serial_file(tmp_path, "ABC123"))
  assert mod.main() == 0
  out = capsys.readouterr()
  assert "reported booted to the mPCC (200)." in out.out
  assert f"serial not recorded: the mPCC answered {status}" in out.err
  assert "nope" in out.err


def test_a_serial_connection_error_is_logged_not_fatal(monkeypatch, tmp_path, capsys):
  class Flaky(Session):
    def post(self, url, data=None):
      if "serial" in json.loads(data):
        raise ConnectionError("refused")
      return super().post(url, data)
  mod = load(monkeypatch, Flaky(), serial_file=serial_file(tmp_path, "ABC123"))
  assert mod.main() == 0
  assert "serial not recorded: unable to reach the mPCC" in capsys.readouterr().err


@pytest.mark.parametrize("text", [None, "", "  \n"])
def test_no_serial_skips_the_serial_post(monkeypatch, tmp_path, capsys, text):
  path = serial_file(tmp_path, text) if text is not None else tmp_path / "missing"
  session = Session()
  mod = load(monkeypatch, session, serial_file=path)
  assert mod.main() == 0
  assert session.posts == [(f"{URL}systems/?self", {"booted": True})]
  out = capsys.readouterr()
  assert "no serial number to report" in out.out
  assert out.err == ""


def test_an_unreadable_serial_file_skips_the_serial_post(monkeypatch, tmp_path, capsys):
  # a directory stands in for a file that can't be read (as non-root)
  session = Session()
  mod = load(monkeypatch, session, serial_file=tmp_path)
  assert mod.main() == 0
  assert len(session.posts) == 1
  assert "no serial number to report: unable to read" in capsys.readouterr().out


def test_a_long_serial_is_cut_to_the_mpcc_limit(monkeypatch, tmp_path):
  session = Session()
  mod = load(monkeypatch, session, serial_file=serial_file(tmp_path, "X" * 300))
  assert mod.main() == 0
  assert session.posts[1][1] == {"serial": "X" * 255}


def test_exits_0_when_run_as_a_script(monkeypatch):
  # run for real, without mprov_jobserver: still exit 0.
  import subprocess
  r = subprocess.run([sys.executable, "-I", str(SCRIPT)],
                     capture_output=True, text=True, timeout=30)
  assert r.returncode == 0, r.stderr
  assert "booted not recorded" in r.stderr
