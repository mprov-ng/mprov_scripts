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


# #26: one record per line, CR and surrounding whitespace stripped, bad rows refused.

def nic(mash, name):
  # the first node's NIC of that name
  line = [l for l in creates(mash) if l.startswith(f"create systems.models.NetworkInterface name={name} ")][0]
  return dict(f.split("=", 1) for f in line.split()[2:])


def test_crlf_and_spaces_around_fields_are_stripped():
  code, err, mash, _, _ = run("n01 , aa:bb:cc:dd:ee:01 , 172.20.1.11\r\n\r\nn02,aa:bb:cc:dd:ee:02,172.20.1.12\r\n")
  assert code == 0, err
  assert "\r" not in mash
  hosts = [l.split()[2] for l in creates(mash) if l.startswith("create systems.models.System ")]
  assert hosts == ["hostname=n01", "hostname=n02"]
  assert nic(mash, "ens0")["ipaddress"] == "172.20.1.11"
  assert nic(mash, "ens0")["mac"] == "'aa:bb:cc:dd:ee:01'"


def test_last_line_without_a_newline_is_imported():
  code, err, mash, _, _ = run("n01,aa:bb:cc:dd:ee:01,172.20.1.11")
  assert code == 0, err
  assert len(creates(mash)) == 4


def test_header_and_comment_lines_are_skipped():
  code, err, mash, _, _ = run("nodename,mac,ipaddress\n# rack 1\nn01,aa:bb:cc:dd:ee:01,172.20.1.11\n")
  assert code == 0, err
  assert len(creates(mash)) == 4


def test_dns_fallback_for_an_empty_ip():
  code, err, mash, _, _ = run("dns01,aa:bb:cc:dd:ee:05,\n")
  assert code == 0, err
  assert nic(mash, "ens0")["ipaddress"] == "172.20.1.50"


@pytest.mark.parametrize("row, message", [
  ("n 01,aa:bb:cc:dd:ee:01,172.20.1.11", "bad node name 'n 01'"),
  ("n01,aa:bb:cc:dd:ee:01,172.20.1.11,extra", "expected nodename,mac[,ipaddress]"),
  ("n01", "expected nodename,mac[,ipaddress]"),
  ("n01,aa:bb:cc:dd:ee,172.20.1.11", "bad MAC address 'aa:bb:cc:dd:ee'"),
  ("nxhost,aa:bb:cc:dd:ee:01,", "no IPv4 address for nxhost"),
])
def test_a_bad_row_stops_the_whole_import(row, message):
  code, err, mash, _, after = run(GOOD + row + "\n")
  assert code == 1
  assert f"line 3: {message}" in err
  assert "nothing was imported" in err
  assert mash is None and after == ""


# #39: BMC and IB addresses swap the management prefix, or the row is refused.

def test_bmc_and_ib_addresses_swap_the_management_prefix():
  code, err, mash, _, _ = run(GOOD)
  assert code == 0, err
  bmc = [l for l in creates(mash) if l.startswith("create systems.models.SystemBMC ")][0]
  assert " ipaddress=172.29.1.11 " in bmc
  assert nic(mash, "ib0")["ipaddress"] == "172.21.1.11"


@pytest.mark.parametrize("ip", [
  "10.9.9.9",      # another network: the BMC and IB got the node's own address
  "172.200.1.5",   # the old sed's '.' matched '0': BMC 172.29..1.5
])
def test_an_address_off_the_management_network_stops_the_import(ip):
  code, err, mash, _, _ = run(GOOD + f"n03,aa:bb:cc:dd:ee:03,{ip}\n")
  assert code == 1
  assert f"line 3: {ip} for n03 isn't on the management network 172.20.x.x" in err
  assert mash is None


# #47: -i was parsed but never used; the NIC name was always ens0.

def test_interface_name_option_names_the_nic():
  code, err, mash, _, _ = run(GOOD, "-i eno1")
  assert code == 0, err
  assert nic(mash, "eno1")["ipaddress"] == "172.20.1.11"
  assert not any("name=ens0" in l for l in creates(mash))


def test_nic_is_still_ens0_by_default():
  code, err, mash, _, _ = run(GOOD)
  assert code == 0, err
  assert nic(mash, "ens0")["ipaddress"] == "172.20.1.11"
