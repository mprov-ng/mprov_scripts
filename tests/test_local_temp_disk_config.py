"""localTempDisk.py finds its mPCC URL and API key on a normal node (#56),
or its certificate (#98).

It read only /etc/mprov/jobserver.yaml and exited 1 without it, but nodes
only have the script runner's /etc/mprov/script-runner.yaml; jobserver.yaml
exists only on a secondary jobserver.  Now it reads the entity.json the
script runner writes next to the scripts it downloads, and for runs by hand
falls back to script-runner.yaml, then jobserver.yaml.

Each case copies the script into a temp dir (the runner's scriptTmpDir) and
loads it from there with its environment deps stubbed.  yaml.load is stubbed
with json.load, so the config files are written as JSON (valid YAML).
"""
import importlib.util
import json
import shutil
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "post-boot" / "localTempDisk.py"


@pytest.fixture
def load(monkeypatch, tmp_path):
  def load(entity=None, configs=(), extra=None, node_cert=False, expired=False):
    '''
    Load the script from tmp_path/run, with entity.json there if entity is
    given, and configfiles pointed at tmp_path/etc: configs names the ones
    that exist, with extra added to their global sections.  node_cert puts
    a node certificate in nodeTlsDir (tmp_path/tls); expired makes it
    expired.  Returns a formatter with load_config() run.
    '''
    for name in ("parted", "sh", "requests"):
      monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["requests"].Session = lambda: types.SimpleNamespace(headers={})
    sys.modules["parted"].PARTITION_NORMAL = 1
    yaml = types.ModuleType("yaml")
    yaml.FullLoader = object
    yaml.add_constructor = lambda tag, fn: None
    yaml.load = lambda f, Loader: json.load(f)
    monkeypatch.setitem(sys.modules, "yaml", yaml)

    run = tmp_path / "run"
    run.mkdir()
    shutil.copy(SCRIPT, run / "localTempDisk.py")
    if entity is not None:
      (run / "entity.json").write_text(json.dumps(entity))
    etc = tmp_path / "etc"
    etc.mkdir()
    for name in configs:
      (etc / name).write_text(json.dumps([{"global": {
        "mprovURL": f"http://{name}/", "apikey": f"key-from-{name}", "jobmodules": ["x"],
        **(extra or {})}}]))
    tls = tmp_path / "tls"
    tls.mkdir()
    if node_cert:
      for name in ("node.crt", "node.key", "ca.crt"):
        (tls / name).write_text(name)

    spec = importlib.util.spec_from_file_location("localTempDisk", run / "localTempDisk.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = mod.mProvStatelessDiskFormatter
    monkeypatch.setattr(cls, "configfiles", [str(etc / "script-runner.yaml"), str(etc / "jobserver.yaml")])
    monkeypatch.setattr(cls, "nodeTlsDir", str(tls))
    monkeypatch.setattr(cls, "_certExpired", lambda self, cert: expired)
    f = object.__new__(cls)
    f.load_config()
    return f
  return load


def test_reads_the_script_runners_entity_json(load):
  f = load({"mprovURL": "http://mpcc.example/", "apikey": "entity-key", "hostname": "n1"},
           configs=["script-runner.yaml", "jobserver.yaml"])
  assert (f.mprovURL, f.apikey) == ("http://mpcc.example/", "entity-key")


def test_by_hand_on_a_node_uses_script_runner_yaml(load):
  f = load(configs=["script-runner.yaml"])
  assert (f.mprovURL, f.apikey) == ("http://script-runner.yaml/", "key-from-script-runner.yaml")


def test_by_hand_on_a_secondary_jobserver_uses_jobserver_yaml(load):
  f = load(configs=["jobserver.yaml"])
  assert (f.mprovURL, f.apikey) == ("http://jobserver.yaml/", "key-from-jobserver.yaml")


def test_no_config_at_all_exits_1(load, capsys):
  with pytest.raises(SystemExit) as exit:
    load()
  assert exit.value.code == 1
  out = capsys.readouterr().out
  assert "Unable to find a working config file" in out and "script-runner.yaml" in out


# by certificate (#98, mprov_control_center#381)
NODE_TLS = {"cert": "/etc/mprov/tls/node.crt", "key": "/etc/mprov/tls/node.key",
            "ca": "/etc/mprov/tls/ca.crt"}


def test_takes_the_certificate_the_runner_hands_over(load):
  f = load({"mprovURL": "https://mpcc.example/", "apikey": "", "tls": NODE_TLS},
           configs=["script-runner.yaml"])
  assert f.mprovURL == "https://mpcc.example/"
  assert f.tls == (NODE_TLS["cert"], NODE_TLS["key"], NODE_TLS["ca"])


def test_without_a_certificate_verifies_with_the_images_ca(load):
  # a node that booted without one, in an image built by certificate
  f = load({"mprovURL": "https://mpcc.example/", "apikey": ""},
           configs=["script-runner.yaml"], extra={"caBundle": "/etc/mprov/mpcc-ca.crt"})
  assert f.tls is None
  assert f.caBundle == "/etc/mprov/mpcc-ca.crt"


def test_by_hand_uses_the_nodes_own_certificate(load, tmp_path):
  f = load(configs=["script-runner.yaml"], node_cert=True)
  tls = tmp_path / "tls"
  assert f.tls == (str(tls / "node.crt"), str(tls / "node.key"), str(tls / "ca.crt"))
  # over https, to the same host
  assert f.mprovURL == "https://script-runner.yaml/"


def test_by_hand_cabundle_wins_over_the_nodes_ca(load, tmp_path):
  f = load(configs=["script-runner.yaml"], node_cert=True, extra={"caBundle": "/etc/mprov/mpcc-ca.crt"})
  assert f.tls[2] == "/etc/mprov/mpcc-ca.crt"


def test_by_hand_an_expired_certificate_falls_back_to_the_key(load, capsys):
  f = load(configs=["script-runner.yaml"], node_cert=True, expired=True)
  assert f.tls is None
  assert (f.mprovURL, f.apikey) == ("http://script-runner.yaml/", "key-from-script-runner.yaml")
  assert "has expired" in capsys.readouterr().err


def test_by_hand_without_a_certificate_uses_the_key(load):
  f = load(configs=["script-runner.yaml"])
  assert f.tls is None


@pytest.mark.parametrize("url, https", [
  ("http://mpcc.example/", "https://mpcc.example/"),
  ("http://mpcc.example:8080/", "https://mpcc.example/"),
  ("http://[fd00::1]:8080/mpcc/", "https://[fd00::1]/mpcc/"),
  ("http://mpcc.example", "https://mpcc.example/"),
])
def test_https_url(load, url, https):
  f = load(configs=["script-runner.yaml"])
  https_url = type(f).__init__.__globals__["https_url"]
  assert https_url(url) == https


class Session:
  def __init__(self):
    self.headers = {"Authorization": "stale"}

  def get(self, url, **kwargs):
    self.url = url
    raise ConnectionError("not in a test")


def session_after_start(f):
  f.session = Session()
  f.heartbeatInterval = 0
  assert f.startSession() is False
  return f.session


def test_start_session_by_certificate(load):
  f = load({"mprovURL": "https://mpcc.example/", "apikey": "", "tls": NODE_TLS},
           configs=["script-runner.yaml"])
  s = session_after_start(f)
  assert s.cert == (NODE_TLS["cert"], NODE_TLS["key"])
  assert s.verify == NODE_TLS["ca"]
  assert "Authorization" not in s.headers
  assert s.url == "https://mpcc.example/"


def test_start_session_with_the_key(load):
  f = load({"mprovURL": "http://mpcc.example/", "apikey": "entity-key"}, configs=["script-runner.yaml"])
  s = session_after_start(f)
  assert s.headers["Authorization"] == "Api-Key entity-key"
  assert not hasattr(s, "cert") and not hasattr(s, "verify")


def test_start_session_with_no_key_and_no_certificate(load):
  f = load({"mprovURL": "https://mpcc.example/", "apikey": ""},
           configs=["script-runner.yaml"], extra={"caBundle": "/etc/mprov/mpcc-ca.crt"})
  s = session_after_start(f)
  assert s.verify == "/etc/mprov/mpcc-ca.crt"
  # no empty "Api-Key " header
  assert "Authorization" not in s.headers
