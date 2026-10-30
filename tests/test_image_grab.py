"""tools/image-grab.sh must only sync into a directory inside the image store
(#25), and must log rsync's errors and report its failure (#48).

Each case runs the script in a container built from tests/Dockerfile with
imageDir /export/images and an rsync stub that records its arguments (one per
line) in /tmp/rsync.args, prints a line to stdout and one to stderr, and
exits with $RSYNC_EXIT.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"


@pytest.fixture(scope="session", autouse=True)
def image():
  subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(REPO / "tests")],
                 check=True, stdout=subprocess.DEVNULL)


SETUP = r"""
  mkdir -p /etc/mprov/plugins /export/images/compute
  echo "imageDir: '/export/images'" > /etc/mprov/plugins/image-update.yaml
  cat > /usr/local/bin/rsync <<'EOF'
#!/bin/bash
printf '%s\n' "$@" > /tmp/rsync.args
echo "rsync-stdout-line"
echo "rsync-stderr-line" >&2
exit ${RSYNC_EXIT:-0}
EOF
  chmod 755 /usr/local/bin/rsync
"""


def run(args, pre="", env="", check=""):
  '''Run the script with args after SETUP and pre.  Returns (exit code,
  script output, check output).'''
  cmd = f"""
    {SETUP}
    {pre}
    cd /work
    {env} bash /script {args} >/tmp/run.log 2>&1; echo "exit=$?"
    cat /tmp/run.log
    echo '--- CHECK ---'
    {check}
    true
  """
  r = subprocess.run(
    ["docker", "run", "--rm", "--network", "none", "--workdir", "/",
     "--tmpfs", "/work",
     "-v", f"{REPO / 'tools' / 'image-grab.sh'}:/script:ro",
     IMAGE, "bash", "-c", cmd],
    capture_output=True, text=True, timeout=180,
  )
  assert r.returncode == 0, r.stderr
  body, out = r.stdout.split("--- CHECK ---\n", 1)
  return int(body.strip().splitlines()[0].split("=")[1]), body, out


def rsync_args(out):
  return out.strip().splitlines()


def test_grabs_into_the_named_image():
  code, log, out = run("-n node01 -i compute", check="cat /tmp/rsync.args")
  assert code == 0, log
  args = rsync_args(out)
  assert args[-2:] == ["root@node01:/", "/export/images/compute/"]
  assert "--delete" not in args
  assert "successfully grabbed" in log


def test_creates_a_missing_image_directory():
  code, log, out = run("-n node01 -i new-image_1.0",
                       check="test -d /export/images/new-image_1.0 && echo made")
  assert code == 0, log
  assert out.strip() == "made"


def test_delete_option_passes_delete_to_rsync():
  # -d was missing from getopts, so it used to be rejected as illegal.
  code, log, out = run("-n node01 -i compute -d", check="cat /tmp/rsync.args")
  assert code == 0, log
  assert "--delete" in rsync_args(out)


@pytest.mark.parametrize("name", [
  ".", "..", "../../etc", "a/b", "/etc", "-x", ".hidden", "with space", "a*",
])
def test_refuses_image_names_that_are_not_a_plain_directory_name(name):
  code, log, out = run(
    f"-n node01 -i '{name}' -d",
    check="test -e /tmp/rsync.args && echo rsync-ran; "
          "ls -A /export /export/images | tr '\\n' ' '")
  assert code == 1, log
  assert "invalid image name" in log
  assert "rsync-ran" not in out
  # Nothing created next to or inside the store.
  assert out.split() == ["/export:", "images", "/export/images:", "compute"]


@pytest.mark.parametrize("node", ["../x", "a/b", "-oProxyCommand=x", "n 1"])
def test_refuses_bad_node_names(node):
  code, log, out = run(f"-n '{node}' -i compute",
                       check="test -e /tmp/rsync.args && echo rsync-ran")
  assert code == 1, log
  assert "invalid node name" in log
  assert "rsync-ran" not in out


def test_refuses_an_image_directory_that_links_outside_the_store():
  code, log, out = run(
    "-n node01 -i escape -d",
    pre="mkdir -p /srv/other && ln -s /srv/other /export/images/escape",
    check="test -e /tmp/rsync.args && echo rsync-ran")
  assert code == 1, log
  assert "outside /export/images" in log
  assert "rsync-ran" not in out


def test_image_store_itself_may_be_a_symlink():
  code, log, out = run(
    "-n node01 -i compute",
    pre="mkdir -p /data/images && rm -rf /export/images && "
        "ln -s /data/images /export/images",
    check="cat /tmp/rsync.args")
  assert code == 0, log
  assert rsync_args(out)[-1] == "/data/images/compute/"


def test_log_gets_stdout_and_stderr():
  code, log, out = run("-n node01 -i compute",
                       check="cat /tmp/node01-to-compute.log")
  assert code == 0, log
  assert "rsync-stdout-line" in out
  assert "rsync-stderr-line" in out
  assert "rsync-stderr-line" not in log


def test_rsync_failure_is_reported():
  code, log, out = run("-n node01 -i compute", env="RSYNC_EXIT=23",
                       check="cat /tmp/node01-to-compute.log")
  assert code == 1, log
  assert "Error: rsync failed (exit 23), see /tmp/node01-to-compute.log" in log
  assert "successfully" not in log
  assert "rsync-stderr-line" in out


def test_vanished_files_are_only_a_warning():
  # rsync exits 24 when files disappear on a running node mid-copy.
  code, log, out = run("-n node01 -i compute", env="RSYNC_EXIT=24",
                       check="cat /tmp/node01-to-compute.log")
  assert code == 0, log
  assert "Warn: some files vanished on node01 during the copy (rsync exit 24)" in log
  assert "successfully grabbed" in log
  assert "rsync-stderr-line" in out


def test_exclude_patterns_are_not_globbed():
  # A matching file in the working directory must not replace the pattern.
  code, log, out = run("-n node01 -i compute",
                       pre="touch /work/x.initramfs /work/y.vmlinuz",
                       check="cat /tmp/rsync.args")
  assert code == 0, log
  args = rsync_args(out)
  assert "--exclude=*.initramfs" in args
  assert "--exclude=*.vmlinuz" in args


def test_unknown_option_prints_usage():
  code, log, _ = run("-n node01 -i compute -z")
  assert code == 1
  assert "Usage: image-grab.sh" in log
