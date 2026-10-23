"""tools/backup-mprov.sh must keep the secret-bearing dump root-only (#20).

The script dumps the full mPCC database -- every SystemBMC password -- plus
media/ into a staging directory and tars the result.  It must stage and store
everything under a root-only directory (dir 700, files 600) at a persistent
destination, overridable with MPROV_BACKUP_DIR, never in volatile
world-readable /tmp, and it must fail if the tarball ends up readable beyond
root.

Each case runs the script in a container built from tests/Dockerfile against
a stubbed mPCC venv: manage.py dumpdata writes a fake secret file at the -o
path with default perms, like the real one.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"

STUB_VENV = r"""
  mkdir -p /var/www/mprov_control_center/bin /var/www/mprov_control_center/media
  printf ':; deactivate() { :; }\n' > /var/www/mprov_control_center/bin/activate
  printf 'pw: s3cret\n' > /var/www/mprov_control_center/media/secret_media.yaml
  cat > /var/www/mprov_control_center/manage.py <<'PYEOF'
import sys
with open(sys.argv[sys.argv.index("-o") + 1], "w") as f:
    f.write("pw: s3cret\n")
PYEOF
"""


def checks_for(backup_dir):
  return rf"""
    d='{backup_dir}'
    t="$d/mprov_backup.tar.gz"
    echo "dir_mode=$(stat -c '%a' "$d" 2>/dev/null || echo missing)"
    echo "tar_mode=$(stat -c '%a' "$t" 2>/dev/null || echo missing)"
    echo "leaky=$(find "$d" -perm /077 2>/dev/null | wc -l)"
    echo "tar_has_dump=$(tar -tzf "$t" 2>/dev/null | grep -c '/mprov_systems.yaml$' || true)"
    echo "tmp_dir_leak=$([ -e /tmp/mprov_backup ] && echo yes || echo no)"
    echo "tmp_tar_leak=$([ -e /tmp/mprov_backup.tar.gz ] && echo yes || echo no)"
  """


def run(script_env="", check=""):
  '''Run the backup script (script_env is a shell prefix such as an env
  assignment).  Returns (exit code, script output, check output).'''
  cmd = f"""
    {STUB_VENV}
    {script_env} bash /script >/tmp/run.log 2>&1; echo "exit=$?"
    cat /tmp/run.log
    echo '--- CHECK ---'
    {check}
  """
  r = subprocess.run(
    ["docker", "run", "--rm",
     "-v", f"{REPO / 'tools' / 'backup-mprov.sh'}:/script:ro",
     IMAGE, "bash", "-c", cmd],
    capture_output=True, text=True, timeout=180,
  )
  assert r.returncode == 0, r.stderr
  body, out = r.stdout.split("--- CHECK ---\n", 1)
  return int(body.strip().splitlines()[0].split("=")[1]), body, out


def parse(out):
  return dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)


def test_default_destination_is_root_only_and_persistent():
  # No /tmp leakage; tarball in a 700 dir with mode 600; dump really inside it.
  code, log, out = run(check=checks_for("/var/backups/mprov"))
  m = parse(out)
  assert code == 0, log
  assert m["tmp_dir_leak"] == "no"
  assert m["tmp_tar_leak"] == "no"
  assert m["dir_mode"] == "700"
  assert m["tar_mode"] == "600"
  assert m["leaky"] == "0"
  assert m["tar_has_dump"] == "1"


def test_operator_configured_destination():
  # MPROV_BACKUP_DIR redirects everything, still root-only.
  code, log, out = run(
    script_env="MPROV_BACKUP_DIR=/srv/mpcc_backup",
    check=checks_for("/srv/mpcc_backup"))
  m = parse(out)
  assert code == 0, log
  assert m["dir_mode"] == "700"
  assert m["tar_mode"] == "600"
  assert m["leaky"] == "0"
  assert m["tar_has_dump"] == "1"
