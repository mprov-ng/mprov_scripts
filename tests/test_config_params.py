"""Scripts that read optional config params from entity.json (#16).

Each case runs a script in a fresh container built from tests/Dockerfile, with
/tmp/mprov/entity.json written from the case's config params, then checks the
files it changed.  An unset or null param must take the script's documented
default, never the string 'null'.
"""
import json
import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"

MISSING = object()


@pytest.fixture(scope="session", autouse=True)
def image():
  subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(REPO / "tests")],
                 check=True, stdout=subprocess.DEVNULL)


def run(script, params, setup="", check=""):
  '''
  Run script with config_params set to params (a key set to MISSING is left
  out).  setup runs before the script and check after it, in the same
  container.  Returns (script's exit code, script's stderr, check's stdout).
  '''
  params = {k: v for k, v in params.items() if v is not MISSING}
  entity = json.dumps({"mprovURL": "http://mpcc.example/", "config_params": params})
  cmd = f"""
    mkdir -p /tmp/mprov && echo {shlex.quote(entity)} > /tmp/mprov/entity.json
    {setup}
    bash /script >/dev/null 2>/tmp/stderr; echo "exit=$?"
    echo "---"; cat /tmp/stderr; echo "---"
    {check}
  """
  r = subprocess.run(
    ["docker", "run", "--rm", "-v", f"{REPO / script}:/script:ro", IMAGE, "bash", "-c", cmd],
    capture_output=True, text=True, timeout=120,
  )
  assert r.returncode == 0, r.stderr
  status, stderr, out = r.stdout.split("---\n", 2)
  return int(status.strip().split("=")[1]), stderr, out


# set_root_pw.sh

SHADOW = "root:$6$distro$hash:19000:0:99999:7:::\nbin:*:19000:0:99999:7:::\n"
SET_SHADOW = f"printf %s {shlex.quote(SHADOW)} > /etc/shadow"


def test_rootpw_set():
  code, err, out = run("image-gen/set_root_pw.sh", {"rootpw": "$6$new$hash"},
                       SET_SHADOW, "cat /etc/shadow")
  assert code == 0 and err == ""
  lines = out.splitlines()
  assert lines[0] == "root:$6$new$hash:18700:0:99999:7:::"
  assert "bin:*:19000:0:99999:7:::" in lines
  assert len(lines) == 2


@pytest.mark.parametrize("value", [MISSING, None, ""])
def test_rootpw_unset_leaves_shadow_alone(value):
  code, err, out = run("image-gen/set_root_pw.sh", {"rootpw": value},
                       SET_SHADOW, "cat /etc/shadow")
  assert code == 0 and err == ""
  assert out == SHADOW


# setup_ssh.sh

KEYS = "ssh-ed25519 AAAAdistro root@distro\n"
SET_KEYS = f"mkdir -p /root/.ssh && printf %s {shlex.quote(KEYS)} > /root/.ssh/authorized_keys"
SHOW_SSH = "cat /root/.ssh/authorized_keys; ls /etc/ssh/ssh_host_ed25519_key"


def test_rootsshkey_set():
  key = "ssh-ed25519 AAAAconfig root@config"
  code, err, out = run("image-gen/setup_ssh.sh", {"rootsshkey": key}, SET_KEYS, SHOW_SSH)
  assert code == 0 and err == ""
  assert out == f"{key}\n/etc/ssh/ssh_host_ed25519_key\n"


@pytest.mark.parametrize("value", [MISSING, None, ""])
def test_rootsshkey_unset_keeps_the_distro_keys(value):
  code, err, out = run("image-gen/setup_ssh.sh", {"rootsshkey": value}, SET_KEYS, SHOW_SSH)
  assert code == 0 and err == ""
  # the host keys are still generated.
  assert out == f"{KEYS}/etc/ssh/ssh_host_ed25519_key\n"


# slurm_enable_gpus.sh

SHOW_SLURM = "cat /etc/sysconfig/slurmd; echo '#'; cat /tmp/systemctl.log"


@pytest.mark.parametrize("value, gres", [
  (4, "gpu:4"),
  ("2", "gpu:2"),
  (MISSING, "gpu:1"),
  (None, "gpu:1"),
  ("", "gpu:1"),
])
def test_gpus(value, gres):
  code, err, out = run("post-boot/slurm_enable_gpus.sh", {"gpus": value},
                       "mkdir -p /etc/sysconfig", SHOW_SLURM)
  assert code == 0 and err == ""
  assert out == f'SLURM=" --conf Gres={gres}"\n#\ndaemon-reload\nrestart slurmd\n'


def test_gpus_replaces_an_existing_entry():
  setup = """mkdir -p /etc/sysconfig && printf '%s\\n' 'OTHER=1' 'SLURM=" --conf Gres=gpu:null"' > /etc/sysconfig/slurmd"""
  code, err, out = run("post-boot/slurm_enable_gpus.sh", {"gpus": 8}, setup, SHOW_SLURM)
  assert code == 0 and err == ""
  assert out.split("#\n")[0] == 'OTHER=1\nSLURM=" --conf Gres=gpu:8"\n'


# extra_packages.sh

SHOW_DNF = "cat /tmp/dnf.log 2>/dev/null || echo 'dnf not run'"


@pytest.mark.parametrize("value, dnf", [
  (["vim-enhanced", "wget"], "-y install vim-enhanced wget\n"),
  ("vim-enhanced wget", "-y install vim-enhanced wget\n"),
  (MISSING, "dnf not run\n"),
  (None, "dnf not run\n"),
  ([], "dnf not run\n"),
  ("", "dnf not run\n"),
])
def test_extra_packages(value, dnf):
  code, err, out = run("image-gen/extra_packages.sh", {"extra_packages": value}, check=SHOW_DNF)
  assert code == 0
  assert err == ""  # no jq errors
  assert out == dnf
