"""localTempDisk.py finds its mPCC URL and API key on a normal node (#56).

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
  def load(entity=None, configs=()):
    '''
    Load the script from tmp_path/run, with entity.json there if entity is
    given, and configfiles pointed at tmp_path/etc: configs names the ones
    that exist.  Returns a formatter with load_config() run.
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
        "mprovURL": f"http://{name}/", "apikey": f"key-from-{name}", "jobmodules": ["x"]}}]))

    spec = importlib.util.spec_from_file_location("localTempDisk", run / "localTempDisk.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = mod.mProvStatelessDiskFormatter
    monkeypatch.setattr(cls, "configfiles", [str(etc / "script-runner.yaml"), str(etc / "jobserver.yaml")])
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
