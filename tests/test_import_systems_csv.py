"""tools/import_systems_csv.sh: the CSV node import (#40, #26, #39).

Each case runs the script in a container built from tests/Dockerfile, with
stub 'mash' and 'host' commands ahead on PATH: mash saves the input it was
piped and the listing of the script's temp file while it runs; host knows
one name.  Returns the script's exit code and stderr, the mash input (None
if mash never ran), and what the temp dir held during and after the run.
"""
import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"
SCRIPT = "tools/import_systems_csv.sh"

STUBS = r"""
mkdir -p /stubs
cat > /stubs/mash <<'EOF'
#!/bin/bash
# the files the import has open in /tmp while mash runs, with their modes
find /tmp -maxdepth 1 -type f ! -name 'mash-*' ! -name 'stderr' ! -name 'nodes.csv' -printf '%m %p\n' > /tmp/mash-during
cat > /tmp/mash-input
exit ${MASH_RC:-0}
EOF
cat > /stubs/host <<'EOF'
#!/bin/bash
if [ "$1" == "dns01" ]; then echo "dns01 has address 172.20.1.50"; exit 0; fi
echo "Host $1 not found: 3(NXDOMAIN)"; exit 1
EOF
chmod 755 /stubs/*
"""


@pytest.fixture(scope="session", autouse=True)
def image():
  subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(REPO / "tests")],
                 check=True, stdout=subprocess.DEVNULL)


def run(csv, args="", mash_rc=0):
  cmd = f"""
    {STUBS}
    export PATH=/stubs:$PATH MASH_RC={mash_rc}
    printf %s {shlex.quote(csv)} > /tmp/nodes.csv
    cd /tmp && bash /script /tmp/nodes.csv {args} >/dev/null 2>/tmp/stderr; echo "exit=$?"
    echo "---"; cat /tmp/stderr
    echo "---"; cat /tmp/mash-input 2>/dev/null || echo "MASH NOT RUN"
    echo "---"; cat /tmp/mash-during 2>/dev/null
    echo "---"; find /tmp -maxdepth 1 -type f ! -name 'mash-*' ! -name 'stderr' ! -name 'nodes.csv' -printf '%p\n'
  """
  r = subprocess.run(
    ["docker", "run", "--rm", "--network", "none", "-v", f"{REPO / SCRIPT}:/script:ro",
     IMAGE, "bash", "-c", cmd],
    capture_output=True, text=True, timeout=120,
  )
  assert r.returncode == 0, r.stderr
  status, stderr, mash, during, after = r.stdout.split("---\n")
  return (int(status.strip().split("=")[1]), stderr,
          None if mash == "MASH NOT RUN\n" else mash, during, after)


def creates(mash):
  return [l for l in mash.splitlines() if l.startswith("create ")]


GOOD = "n01,aa:bb:cc:dd:ee:01,172.20.1.11\nn02,aa:bb:cc:dd:ee:02,172.20.1.12\n"


# #40: the mash input (BMC credentials included) is private and removed.

def test_mash_input_is_private_and_removed():
  code, err, mash, during, after = run(GOOD)
  assert code == 0, err
  assert "password=changeme" in mash
  # one temp file while mash runs, readable by its owner only, gone after.
  files = during.splitlines()
  assert len(files) == 1 and files[0].split()[0] == "600", during
  assert after == ""


def test_mash_input_is_removed_when_mash_fails():
  code, err, mash, during, after = run(GOOD, mash_rc=3)
  assert code == 3
  assert mash is not None and after == ""
