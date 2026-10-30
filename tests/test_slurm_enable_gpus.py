"""post-boot/slurm_enable_gpus.sh must rewrite /etc/sysconfig/slurmd without
going through a fixed /tmp path, atomically, and without sourcing it (#23).

Each case runs the script in a container built from tests/Dockerfile (its
systemctl stub logs to /tmp/systemctl.log) with config_params from
/tmp/mprov/entity.json.
"""
import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"
CONF = "/etc/sysconfig/slurmd"


@pytest.fixture(scope="session", autouse=True)
def image():
  subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(REPO / "tests")],
                 check=True, stdout=subprocess.DEVNULL)


def run(params, pre="", check=""):
  '''Run the script with config_params params after pre.  Returns (exit code,
  script output, check output).'''
  entity = json.dumps({"config_params": params})
  cmd = f"""
    mkdir -p /tmp/mprov /etc/sysconfig
    cat > /tmp/mprov/entity.json <<'EOF'
{entity}
EOF
    {pre}
    bash /script >/tmp/run.log 2>&1; echo "exit=$?"
    cat /tmp/run.log
    echo '--- CHECK ---'
    {check}
    true
  """
  r = subprocess.run(
    ["docker", "run", "--rm", "--network", "none",
     "-v", f"{REPO / 'post-boot' / 'slurm_enable_gpus.sh'}:/script:ro",
     IMAGE, "bash", "-c", cmd],
    capture_output=True, text=True, timeout=180,
  )
  assert r.returncode == 0, r.stderr
  body, out = r.stdout.split("--- CHECK ---\n", 1)
  return int(body.strip().splitlines()[0].split("=")[1]), body, out


def test_creates_the_file_when_missing():
  code, log, out = run({"gpus": "4"},
                       check=f"cat {CONF}; stat -c 'mode=%a' {CONF}")
  assert code == 0, log
  assert out.splitlines() == ['SLURM=" --conf Gres=gpu:4"', "mode=644"]


def test_defaults_to_one_gpu():
  code, log, out = run({}, check=f"cat {CONF}")
  assert code == 0, log
  assert out.strip() == 'SLURM=" --conf Gres=gpu:1"'


def test_replaces_the_old_entry_and_keeps_the_rest():
  code, log, out = run(
    {"gpus": 2},
    pre=f"""printf 'SLURMD_OPTIONS="-M c1"\\nSLURM=" --conf Gres=gpu:8"\\nOTHER=1\\n' > {CONF}
            chmod 640 {CONF}; chown 0:12 {CONF}""",
    check=f"cat {CONF}; stat -c 'mode=%a own=%u:%g' {CONF}")
  assert code == 0, log
  assert out.splitlines() == [
    'SLURMD_OPTIONS="-M c1"', "OTHER=1", 'SLURM=" --conf Gres=gpu:2"',
    "mode=640 own=0:12"]


def test_appends_to_a_file_without_an_entry():
  code, log, out = run({"gpus": "3"}, pre=f"echo 'OTHER=1' > {CONF}",
                       check=f"cat {CONF}")
  assert code == 0, log
  assert out.splitlines() == ["OTHER=1", 'SLURM=" --conf Gres=gpu:3"']


@pytest.mark.parametrize("planted", [
  "ln -s /etc/evil /tmp/slurmd",
  "echo 'SLURMD_OPTIONS=\"--evil\"' > /tmp/slurmd",
])
def test_ignores_a_planted_tmp_slurmd(planted):
  # The old script wrote to /tmp/slurmd and moved it over the real file.
  code, log, out = run(
    {"gpus": "2"},
    pre=f"echo 'SLURM=\" --conf Gres=gpu:1\"' > {CONF}; {planted}",
    check=f"cat {CONF}; test -L {CONF} && echo is-link; "
          "test -e /etc/evil && echo evil-written; ls -A /tmp")
  assert code == 0, log
  lines = out.splitlines()
  assert lines[0] == 'SLURM=" --conf Gres=gpu:2"'
  assert "is-link" not in lines
  assert "evil-written" not in lines
  assert "--evil" not in out
  assert "slurmd" in lines  # the planted file is left alone


def test_does_not_source_the_file():
  code, log, out = run({"gpus": "2"},
                       pre=f"echo 'touch /tmp/sourced' > {CONF}",
                       check="test -e /tmp/sourced && echo sourced")
  assert code == 0, log
  assert "sourced" not in out


def test_leaves_no_temp_files_behind():
  code, log, out = run({"gpus": "2"}, pre=f"echo 'OTHER=1' > {CONF}",
                       check="ls -A /etc/sysconfig | grep ^slurmd")
  assert code == 0, log
  assert out.split() == ["slurmd"]


@pytest.mark.parametrize("gpus", ["two", "2\"; touch /tmp/x; \"", "-1", "1.5"])
def test_refuses_a_non_numeric_gpu_count(gpus):
  code, log, out = run({"gpus": gpus}, pre=f"echo 'OTHER=1' > {CONF}",
                       check=f"cat {CONF}; ls -A /etc/sysconfig | grep ^slurmd; "
                             "cat /tmp/systemctl.log 2>/dev/null")
  assert code == 1, log
  assert "must be a number" in log
  assert out.split() == ["OTHER=1", "slurmd"]


def test_restarts_slurmd():
  code, log, out = run({"gpus": "2"}, check="cat /tmp/systemctl.log")
  assert code == 0, log
  assert out.splitlines() == ["daemon-reload", "restart slurmd"]


def test_a_failed_rename_stops_the_script():
  # A directory in the way: mv -T refuses to move the file into it, and the
  # script must stop before restarting slurmd and leave no temp file.
  code, log, out = run(
    {"gpus": "2"},
    pre=f"mkdir {CONF}",
    check="ls -A /etc/sysconfig | grep ^slurmd; cat /tmp/systemctl.log 2>/dev/null")
  assert code != 0, log
  assert out.split() == ["slurmd"]
