"""The image-gen scripts that read /tmp/mprov/entity.json stop before writing
anything when it, or a value they need from it, is missing (#31).

They used to cd /tmp/mprov unchecked and read entity.json by a relative path,
so a missing file gave empty values, and they wrote jobserver configs with an
empty mprovURL/apikey or an fstab line with no NFS server.

Each case runs a script in a container built from tests/Dockerfile with no
network, then checks that nothing under /etc/mprov, /etc/fstab, the systemd
unit directory or the dnf stub's log changed.
"""
import json
import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"

SCRIPTS = [
  "image-gen/install_mprov_jobserver.sh",
  "image-gen/install_mprov_script-runner.sh",
  "image-gen/setup_data_share_mount.sh",
  "image-gen/run_ansible.sh",
]
NEEDS_KEY = SCRIPTS[:2]

STATE = ("{ ls -A /etc/mprov 2>&1; md5sum /etc/fstab 2>&1; "
         "ls /usr/lib/systemd/system | grep mprov; cat /tmp/dnf.log 2>&1; } | md5sum")


@pytest.fixture(scope="session", autouse=True)
def image():
  subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(REPO / "tests")],
                 check=True, stdout=subprocess.DEVNULL)


def run(script, entity):
  '''Run script with /tmp/mprov/entity.json holding entity (None: no file;
  a str: written as is).  Returns (exit code, stderr, whether anything the
  scripts write changed).'''
  if entity is None:
    write = "mkdir -p /tmp/mprov"
  else:
    text = entity if isinstance(entity, str) else json.dumps(entity)
    write = f"mkdir -p /tmp/mprov && printf %s {shlex.quote(text)} > /tmp/mprov/entity.json"
  cmd = f"""
    {write}
    before=$({STATE})
    cd /
    bash /script >/dev/null 2>/tmp/stderr; echo "exit=$?"
    echo "---"; cat /tmp/stderr; echo "---"
    [ "$before" = "$({STATE})" ] && echo unchanged || echo changed
  """
  r = subprocess.run(
    ["docker", "run", "--rm", "--network", "none", "-v", f"{REPO / script}:/script:ro",
     IMAGE, "bash", "-c", cmd],
    capture_output=True, text=True, timeout=180,
  )
  assert r.returncode == 0, r.stderr
  status, stderr, out = r.stdout.split("---\n", 2)
  return int(status.strip().split("=")[1]), stderr, out.strip() == "changed"


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("entity", [None, "", "not json {", "null"],
                         ids=["missing", "empty", "garbage", "json-null"])
def test_unreadable_entity_json_stops_the_script(script, entity):
  code, err, changed = run(script, entity)
  assert code == 1
  assert "Error: unable to read /tmp/mprov/entity.json" in err
  assert not changed


@pytest.mark.parametrize("script", NEEDS_KEY)
@pytest.mark.parametrize("entity", [
  {"apikey": "key"},
  {"mprovURL": "http://mpcc.example/"},
  {"mprovURL": None, "apikey": "key"},
  {"mprovURL": "http://mpcc.example/", "apikey": ""},
], ids=["no-url", "no-key", "null-url", "empty-key"])
def test_missing_url_or_key_stops_the_install(script, entity):
  code, err, changed = run(script, entity)
  assert code == 1
  assert "Error: mprovURL or apikey missing from /tmp/mprov/entity.json" in err
  assert not changed


def test_data_share_without_any_host_stops():
  code, err, changed = run("image-gen/setup_data_share_mount.sh",
                           {"mprovURL": None, "config_params": {}})
  assert code == 1
  assert "no mprovDataShareHost config param and no mPCC host" in err
  assert not changed


def test_data_share_host_alone_is_enough():
  # mprovURL is only the fallback for the NFS server.
  code, err, changed = run("image-gen/setup_data_share_mount.sh",
                           {"config_params": {"mprovDataShareHost": "nfs01"}})
  assert code == 0, err
  assert changed
