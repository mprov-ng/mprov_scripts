"""install_mprov_script-runner.sh and install_mprov_jobserver.sh with a job
server that builds by certificate (#98, mprov_control_center#381).

Then entity.json has apikey "", an https mprovURL and tls {cert, key, ca}:
cert and key are a build-only credential that must never reach the image,
ca is the mPCC's CA certificate, which goes in as /etc/mprov/mpcc-ca.crt with
caBundle pointing at it.  Without tls, the scripts write the API key as
before.

Each case runs the script in a container built from tests/Dockerfile with no
network.  install_mprov_jobserver.sh gets the stand-in mprov_jobserver
package from test_install_jobserver.py.
"""
import json
import shlex
import subprocess
from pathlib import Path

import pytest

from test_install_jobserver import INSTALL_STANDIN

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"

RUNNER = ("image-gen/install_mprov_script-runner.sh", "/etc/mprov/script-runner.yaml")
JOBSERVER = ("image-gen/install_mprov_jobserver.sh", "/etc/mprov/jobserver.yaml")

CA = "-----BEGIN CERTIFICATE-----\nmPCC-CA\n-----END CERTIFICATE-----\n"
SECRET = "BUILD-CREDENTIAL-SECRET"
TLS = {"cert": "/tmp/mprov/tls/jobserver.pem", "key": "/tmp/mprov/tls/jobserver.pem",
       "ca": "/tmp/mprov/tls/ca.crt"}
URL = "https://mpcc.example/"


@pytest.fixture(scope="session", autouse=True)
def image():
  subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(REPO / "tests")],
                 check=True, stdout=subprocess.DEVNULL)


def run(script, entity, check):
  '''Run script with entity.json holding entity and the build credential in
  /tmp/mprov/tls; check runs after it.  Returns (exit code, stderr, check's
  output).'''
  path, _ = script
  setup = INSTALL_STANDIN if script == JOBSERVER else ""
  cmd = f"""
    mkdir -p /tmp/mprov/tls
    echo {shlex.quote(json.dumps(entity))} > /tmp/mprov/entity.json
    printf '%s\\n' {shlex.quote(SECRET)} > /tmp/mprov/tls/jobserver.pem
    printf %s {shlex.quote(CA)} > /tmp/mprov/tls/ca.crt
    {setup}
    bash /script >/tmp/stdout 2>/tmp/stderr; echo "exit=$?"
    echo "---"; cat /tmp/stderr; echo "---"
    {check}
    true
  """
  r = subprocess.run(
    ["docker", "run", "--rm", "--network", "none", "-v", f"{REPO / path}:/script:ro",
     IMAGE, "bash", "-c", cmd],
    capture_output=True, text=True, timeout=180,
  )
  assert r.returncode == 0, r.stderr
  status, stderr, out = r.stdout.split("---\n", 2)
  return int(status.strip().split("=")[1]), stderr, out


def config(script):
  return f"cat {script[1]}; echo '#'; cat /etc/mprov/mpcc-ca.crt 2>&1; stat -c '%a' /etc/mprov/mpcc-ca.crt 2>&1"


# the build credential must not be anywhere the image keeps
LEAK_CHECK = f"; echo '#'; grep -rlF {SECRET} /etc /usr/lib/systemd /usr/local 2>/dev/null; true"


@pytest.mark.parametrize("script", [RUNNER, JOBSERVER], ids=["script-runner", "jobserver"])
def test_by_certificate_writes_no_key_and_the_ca(script):
  entity = {"mprovURL": URL, "apikey": "", "slug": "compute", "tls": TLS}
  code, err, out = run(script, entity, config(script) + LEAK_CHECK)
  assert code == 0, err
  conf, ca, leaks = out.split("#\n")
  assert f'mprovURL: "{URL}"' in conf
  assert "apikey" not in conf
  assert "caBundle: '/etc/mprov/mpcc-ca.crt'" in conf
  assert ca == CA + "644\n"
  assert leaks.strip() == ""


@pytest.mark.parametrize("script", [RUNNER, JOBSERVER], ids=["script-runner", "jobserver"])
def test_by_certificate_without_a_ca_writes_neither(script):
  tls = {k: v for k, v in TLS.items() if k != "ca"}
  entity = {"mprovURL": URL, "apikey": "", "slug": "compute", "tls": tls}
  code, err, out = run(script, entity, config(script) + LEAK_CHECK)
  assert code == 0, err
  conf, ca, leaks = out.split("#\n")
  assert "apikey" not in conf and "caBundle" not in conf
  assert "No such file" in ca
  assert leaks.strip() == ""


@pytest.mark.parametrize("script", [RUNNER, JOBSERVER], ids=["script-runner", "jobserver"])
def test_a_missing_ca_file_fails(script):
  tls = dict(TLS, ca="/tmp/mprov/tls/missing.crt")
  entity = {"mprovURL": URL, "apikey": "", "slug": "compute", "tls": tls}
  code, err, out = run(script, entity, f"ls {script[1]} 2>&1")
  assert code == 1
  assert "unable to copy the mPCC's CA certificate" in err
  # it stops before writing the config
  assert "No such file" in out


@pytest.mark.parametrize("script", [RUNNER, JOBSERVER], ids=["script-runner", "jobserver"])
def test_with_the_shared_key_nothing_changes(script):
  entity = {"mprovURL": "http://mpcc.example/", "apikey": "the-key", "slug": "compute"}
  code, err, out = run(script, entity, config(script))
  assert code == 0, err
  conf, ca = out.split("#\n")
  assert "    apikey: 'the-key'\n" in conf
  assert "caBundle" not in conf
  assert "No such file" in ca


@pytest.mark.parametrize("script", [RUNNER, JOBSERVER], ids=["script-runner", "jobserver"])
def test_no_key_and_no_certificate_still_fails(script):
  entity = {"mprovURL": URL, "apikey": "", "slug": "compute"}
  code, err, _ = run(script, entity, "")
  assert code == 1
  assert "mprovURL or apikey missing" in err


def test_nads_with_no_key_is_unchanged():
  # the public nads image (#84) keeps its empty key line
  entity = {"mprovURL": "http://mpcc.example/", "apikey": "", "slug": "nads"}
  code, err, out = run(RUNNER, entity, "cat /etc/mprov/nads.yaml")
  assert code == 0, err
  assert "    apikey: ''\n" in out
