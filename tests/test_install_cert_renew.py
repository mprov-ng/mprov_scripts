"""image-gen/install_mprov_cert_renew.sh installs mprov_enroll.py and the
timer that runs "mprov_enroll.py renew" (#96, mprov_control_center#506).

Each case runs the script in a container built from tests/Dockerfile with no
network.  A python http.server on 127.0.0.1 stands in for the mPCC, serving
static/mprov_enroll.py when the case puts one there.  systemctl is the
Dockerfile's logging stub.
"""
import json
import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"
SCRIPT = "image-gen/install_mprov_cert_renew.sh"

HELPER = "#!/usr/bin/env python3\ndef renew():\n  return True\n"
URL = "http://127.0.0.1:8080/"


@pytest.fixture(scope="session", autouse=True)
def image():
  subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(REPO / "tests")],
                 check=True, stdout=subprocess.DEVNULL)


def run(helper=HELPER, entity=None, server=True, setup="", check=""):
  '''Run the script against a stand-in mPCC serving helper (None: no
  file there, so a 404).  Returns (exit code, output, check's output).'''
  entity = {"mprovURL": URL, "apikey": "key"} if entity is None else entity
  serve = ""
  if helper is not None:
    serve = f"printf %s {shlex.quote(helper)} > /srv/static/mprov_enroll.py"
  start = ""
  if server:
    start = """
      (cd /srv && python3 -m http.server 8080 --bind 127.0.0.1 >/dev/null 2>&1 &)
      for i in $(seq 50); do curl -s -o /dev/null http://127.0.0.1:8080/ && break; sleep 0.1; done
    """
  cmd = f"""
    mkdir -p /tmp/mprov /srv/static
    echo {shlex.quote(json.dumps(entity))} > /tmp/mprov/entity.json
    {serve}
    {start}
    {setup}
    bash /script >/tmp/run.log 2>&1; echo "exit=$?"
    cat /tmp/run.log; echo "---"
    {check}
    true
  """
  r = subprocess.run(
    ["docker", "run", "--rm", "--network", "none", "-v", f"{REPO / SCRIPT}:/script:ro",
     IMAGE, "bash", "-c", cmd],
    capture_output=True, text=True, timeout=180,
  )
  assert r.returncode == 0, r.stderr
  head, out = r.stdout.split("---\n", 1)
  status, log = head.split("\n", 1)
  return int(status.split("=")[1]), log, out


def test_installs_the_helper_and_the_timer():
  check = """
    stat -c '%a' /usr/libexec/mprov/mprov_enroll.py; cat /usr/libexec/mprov/mprov_enroll.py
    echo "#"; cat /usr/lib/systemd/system/mprov-cert-renew.service
    echo "#"; cat /usr/lib/systemd/system/mprov-cert-renew.timer
    echo "#"; cat /tmp/systemctl.log
    echo "#"; ls -A /usr/libexec/mprov
  """
  code, log, out = run(check=check)
  assert code == 0, log
  assert "enabled mprov-cert-renew.timer" in log
  helper, service, timer, systemctl, files = out.split("#\n")
  assert helper == "755\n" + HELPER
  assert "Type=oneshot" in service
  assert "After=network-online.target" in service
  assert "/usr/libexec/mprov/mprov_enroll.py renew" in service
  # systemd would expand a bare $p itself (to nothing)
  assert '"$$p"' in service and '"$p"' not in service
  # nothing waits for the service, so it can't hold up the boot
  assert "[Install]" not in service
  for line in ("OnBootSec=5min", "OnUnitActiveSec=1d", "RandomizedDelaySec=1h",
               "Persistent=true", "WantedBy=timers.target"):
    assert line in timer.splitlines()
  assert systemctl.splitlines() == ["enable mprov-cert-renew.timer"]
  # the temporary download is gone
  assert files.split() == ["mprov_enroll.py"]


def test_an_mpcc_before_0_7_installs_nothing():
  code, log, out = run(helper=None, check="ls /usr/lib/systemd/system/mprov-cert-renew.* /usr/libexec/mprov 2>&1; cat /tmp/systemctl.log 2>&1")
  assert code == 0, log
  assert "older than 0.7.0" in log
  assert "mprov-cert-renew" in out and "No such file" in out
  assert "enable" not in out


def test_fails_when_the_mpcc_cant_be_reached():
  code, log, out = run(server=False, check="ls /usr/libexec/mprov/mprov_enroll.py 2>&1; cat /tmp/systemctl.log 2>&1")
  assert code == 1
  assert "unable to download the certificate helper" in log
  assert out.count("No such file") == 2


def test_fails_on_something_that_isnt_the_helper():
  # e.g. an HTML page a proxy put in its place
  code, log, out = run(helper="<html>sign in</html>", check="ls -A /usr/libexec/mprov")
  assert code == 1
  assert "unable to download the certificate helper" in log
  assert out == ""


def test_uses_the_mpcc_ca_when_the_job_server_hands_one_over():
  # a job server building with its own certificate passes the mPCC's CA in
  # tls.ca (mprov_control_center#381); curl must verify with it.
  entity = {"mprovURL": URL, "apikey": "", "tls": {"cert": "/tmp/mprov/tls/jobserver.pem",
                                                   "key": "/tmp/mprov/tls/jobserver.pem",
                                                   "ca": "/tmp/mprov/tls/ca.crt"}}
  setup = r"""
    mkdir -p /tmp/stub
    printf '#!/bin/sh\necho "$@" >> /tmp/curl.log\nexec /usr/bin/curl "$@"\n' > /tmp/stub/curl
    chmod 755 /tmp/stub/curl; export PATH=/tmp/stub:$PATH
    # plain http here, so curl accepts any file as the CA
    mkdir -p /tmp/mprov/tls; cp /etc/pki/tls/certs/ca-bundle.crt /tmp/mprov/tls/ca.crt
  """
  code, log, out = run(entity=entity, setup=setup, check="grep mprov_enroll /tmp/curl.log")
  assert code == 0, log
  assert "--cacert /tmp/mprov/tls/ca.crt" in out
  assert "jobserver.pem" not in out


def test_fails_without_entity_json():
  code, log, _ = run(entity={}, check="")
  assert code == 1
  assert "unable to read /tmp/mprov/entity.json" in log or "mprovURL missing" in log


@pytest.mark.parametrize("bundle, expect", [(True, "bundle"), (False, "system")])
def test_the_service_prefers_the_bundled_python(bundle, expect):
  # after the install, run the service's ExecStart with stub interpreters
  # that say which one they are
  stubs = """
    rm -f /usr/bin/python3
    printf '#!/bin/sh\\necho "system $*"\\n' > /usr/bin/python3; chmod 755 /usr/bin/python3
  """
  if bundle:
    stubs += """
    mkdir -p /usr/libexec/mprov/bin
    printf '#!/bin/sh\\necho "bundle $*"\\n' > /usr/libexec/mprov/bin/python3
    chmod 755 /usr/libexec/mprov/bin/python3
    """
  check = stubs + r"""
    # systemd turns "$$" into "$" before running the command line
    cmd=$(sed -n 's/^ExecStart=//p' /usr/lib/systemd/system/mprov-cert-renew.service | sed 's/[$][$]/$/g')
    eval "$cmd"
  """
  code, log, out = run(check=check)
  assert code == 0, log
  assert out.strip() == f"{expect} /usr/libexec/mprov/mprov_enroll.py renew"
