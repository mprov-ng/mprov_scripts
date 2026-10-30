"""tools/backup-mprov.sh reports success only when every step worked, and
keeps the previous backup until the new one is complete (#38).

It had no error checks: a failed cd, venv activation, dump or media copy
still ended with "Backup saved" and exit 0, and it deleted the previous
backup before dumping anything.  It also built the tarball inside the
directory it was archiving.

Each case runs the script in a container built from tests/Dockerfile against
a stubbed mPCC venv whose manage.py dumpdata fails for the app named in
/tmp/fail_app.  A previous backup with a marker file is put in place first.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"
DEST = "/var/backups/mprov"

STUB_VENV = r"""
  mkdir -p /var/www/mprov_control_center/bin /var/www/mprov_control_center/media
  printf ':; deactivate() { :; }\n' > /var/www/mprov_control_center/bin/activate
  echo media > /var/www/mprov_control_center/media/file.txt
  cat > /var/www/mprov_control_center/manage.py <<'PYEOF'
import sys
app = sys.argv[2]
try:
  if open("/tmp/fail_app").read().strip() == app:
    sys.exit(f"CommandError: dump of {app} failed")
except FileNotFoundError:
  pass
with open(sys.argv[sys.argv.index("-o") + 1], "w") as f:
  f.write(f"{app}: data\n")
PYEOF
"""

PREVIOUS = rf"""
  mkdir -p {DEST} && echo old > {DEST}/OLD_BACKUP && chmod -R go-rwx {DEST}
"""

STATE = rf"""
  echo "old=$(cat {DEST}/OLD_BACKUP 2>/dev/null || echo gone)"
  echo "systems=$(cat {DEST}/mprov_systems.yaml 2>/dev/null || echo none)"
  echo "leftovers=$(ls -A /var/backups | grep -v '^mprov$' | wc -l)"
  echo "dir_mode=$(stat -c '%a' {DEST} 2>/dev/null || echo missing)"
  echo "tar=$(tar -tzf {DEST}/mprov_backup.tar.gz 2>/dev/null | sort | tr '\n' ' ')"
"""


@pytest.fixture(scope="session", autouse=True)
def image():
  subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(REPO / "tests")],
                 check=True, stdout=subprocess.DEVNULL)


def run(pre=""):
  '''Run the backup after the stub venv, a previous backup and pre.  Returns
  (exit code, script output, state dict).'''
  cmd = f"""
    {STUB_VENV}
    {PREVIOUS}
    {pre}
    bash /script >/tmp/run.log 2>&1; echo "exit=$?"
    cat /tmp/run.log
    echo '--- CHECK ---'
    {STATE}
  """
  r = subprocess.run(
    ["docker", "run", "--rm", "--network", "none",
     "-v", f"{REPO / 'tools' / 'backup-mprov.sh'}:/script:ro",
     IMAGE, "bash", "-c", cmd],
    capture_output=True, text=True, timeout=180,
  )
  assert r.returncode == 0, r.stderr
  body, out = r.stdout.split("--- CHECK ---\n", 1)
  state = dict(l.split("=", 1) for l in out.strip().splitlines())
  return int(body.strip().splitlines()[0].split("=")[1]), body, state


def test_success_replaces_the_previous_backup():
  code, log, state = run()
  assert code == 0, log
  assert "Backup saved to /var/backups/mprov/mprov_backup.tar.gz" in log
  assert state["old"] == "gone"
  assert state["systems"] == "systems: data"
  assert state["leftovers"] == "0"
  assert state["dir_mode"] == "700"
  # same layout as before: one top-level directory named like the
  # destination, and the tarball isn't inside itself.
  assert state["tar"].split() == [
    "mprov/", "mprov/media/", "mprov/media/file.txt", "mprov/mprov_disklayouts.yaml",
    "mprov/mprov_networks.yaml", "mprov/mprov_osmanagement.yaml",
    "mprov/mprov_scripts.yaml", "mprov/mprov_systems.yaml"]


@pytest.mark.parametrize("pre", [
  "echo systems > /tmp/fail_app",
  "echo osmanagement > /tmp/fail_app",
  "rm -rf /var/www/mprov_control_center",
  "rm /var/www/mprov_control_center/bin/activate",
  "rm -rf /var/www/mprov_control_center/media",
], ids=["dump-fails", "last-dump-fails", "no-mpcc", "no-venv", "no-media"])
def test_failure_keeps_the_previous_backup(pre):
  code, log, state = run(pre)
  assert code != 0, log
  assert "Backup saved" not in log
  assert "ERROR: backup failed, /var/backups/mprov was left as it was" in log
  assert state["old"] == "old"
  assert state["systems"] == "none"
  assert state["leftovers"] == "0"


def test_first_backup_failure_leaves_nothing():
  code, log, state = run("rm -rf /var/backups/mprov; echo networks > /tmp/fail_app")
  assert code != 0
  assert state["dir_mode"] == "missing"
  assert state["leftovers"] == "0"
