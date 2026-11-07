"""install_mprov_jobserver.sh copies the jobserver's default plugin configs (#42),
from the bundled job server when the image has one (#87).

The copy used a hardcoded python3.8 site-packages path, so on Rocky 9 (python
3.9) nothing was copied, the script still succeeded, and the secondary
jobserver's webserver started with an empty rootDir and served /.

Each case runs the script in a container built from tests/Dockerfile with no
network.  A small stand-in mprov_jobserver package carrying plugin YAMLs is
pip-installed from a local directory first, so pip3 puts it wherever it puts
packages on that python, and the script's own 'pip3 install' finds it already
installed.  Without it, that install fails the way a broken one would.
"""
import json
import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"
SCRIPT = "image-gen/install_mprov_jobserver.sh"

WEBSERVER_YAML = "- mprov-webserver:\n    rootDir: '/export/'\n"

INSTALL_STANDIN = f"""
  mkdir -p /tmp/pkg/mprov_jobserver/plugins
  touch /tmp/pkg/mprov_jobserver/__init__.py /tmp/pkg/mprov_jobserver/plugins/__init__.py
  printf %s {shlex.quote(WEBSERVER_YAML)} > /tmp/pkg/mprov_jobserver/plugins/mprov-webserver.yaml
  echo "- image-sync: {{}}" > /tmp/pkg/mprov_jobserver/plugins/image-sync.yaml
  cat > /tmp/pkg/setup.py <<'EOF'
from setuptools import setup
setup(name='mprov_jobserver', version='0.0.0', packages=['mprov_jobserver', 'mprov_jobserver.plugins'],
      package_data={{'mprov_jobserver.plugins': ['*.yaml']}})
EOF
  pip3 install -q --no-index --no-build-isolation /tmp/pkg >/dev/null 2>&1 || echo "stand-in install failed" >&2
"""


@pytest.fixture(scope="session", autouse=True)
def image():
  subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(REPO / "tests")],
                 check=True, stdout=subprocess.DEVNULL)


def run(setup, check):
  '''
  Run the script after setup; check runs after it in the same container
  (the script's stdout is in /tmp/stdout).  Returns (script's exit code,
  script's stderr, check's stdout).
  '''
  entity = json.dumps({"mprovURL": "http://mpcc.example/", "apikey": "key"})
  cmd = f"""
    mkdir -p /tmp/mprov && echo {shlex.quote(entity)} > /tmp/mprov/entity.json
    {setup}
    bash /script >/tmp/stdout 2>/tmp/stderr; echo "exit=$?"
    echo "---"; cat /tmp/stderr; echo "---"
    {check}
  """
  r = subprocess.run(
    ["docker", "run", "--rm", "--network", "none", "-v", f"{REPO / SCRIPT}:/script:ro",
     IMAGE, "bash", "-c", cmd],
    capture_output=True, text=True, timeout=180,
  )
  assert r.returncode == 0, r.stderr
  status, stderr, out = r.stdout.split("---\n", 2)
  return int(status.strip().split("=")[1]), stderr, out


def test_copies_the_plugin_configs_from_the_installed_package():
  check = """
    echo "location=$(pip3 show mprov_jobserver | awk '/^Location:/{print $2}')"
    ls /etc/mprov/plugins
    echo "#"; cat /etc/mprov/plugins/mprov-webserver.yaml
    echo "#"; cat /etc/mprov/plugins/image-sync.yaml
  """
  code, err, out = run(INSTALL_STANDIN, check)
  assert code == 0, err
  location, rest = out.split("\n", 1)
  # the case the old python3.8 path missed.
  assert "python3.8" not in location
  files, webserver, image_sync = rest.split("#\n")
  assert files.split() == ["image-sync.yaml", "mprov-webserver.yaml"]
  assert webserver == WEBSERVER_YAML
  # the script's own image-sync.yaml replaces the package's.
  assert "imageDir: '/export/images'" in image_sync


def test_fails_when_the_jobserver_is_not_installed():
  code, err, out = run("", "ls /etc/mprov/plugins; ls /usr/lib/systemd/system/mprov-jobserver.service 2>&1; true")
  assert code == 1
  assert "unable to find the mprov_jobserver plugin configs" in err
  # it stops before writing the rest of the config and the service.
  assert out.splitlines() == [
    "ls: cannot access '/usr/lib/systemd/system/mprov-jobserver.service': No such file or directory"]


def test_fails_when_the_package_has_no_plugin_configs():
  setup = INSTALL_STANDIN + "rm $(pip3 show mprov_jobserver | awk '/^Location:/{print $2}')/mprov_jobserver/plugins/*.yaml"
  code, err, out = run(setup, "ls /etc/mprov/plugins")
  assert code == 1
  assert "unable to copy the plugin configs" in err
  assert out == ""


def test_logs_the_installed_jobserver_version():
  # not pinned, so the build log shows which release the image got (#24).
  code, err, out = run(INSTALL_STANDIN, "grep '^mprov_jobserver version' /tmp/stdout")
  assert code == 0, err
  assert out.strip() == "mprov_jobserver version: 0.0.0"


# A job server from 0.7.0 on puts itself into the image as a bundle with its
# own Python at /usr/libexec/mprov before this script runs (mprov_jobserver
# #309).  The script must use that one, not system pip, which can't install
# 0.7.0 and would pull an old 0.6.x instead (#87).  The stand-in bundle is a
# venv at that path, so the package lands where the real bundle has it
# (/usr/libexec/mprov/lib/python3.*/site-packages), and a stub pip3 earlier in
# PATH records any call.
BUNDLE_WEBSERVER_YAML = "- mprov-webserver:\n    rootDir: '/bundle/'\n"

INSTALL_BUNDLE = f"""
  python3 -m venv /usr/libexec/mprov >/dev/null 2>&1 || echo "bundle venv failed" >&2
  mkdir -p /tmp/bpkg/mprov_jobserver/plugins
  touch /tmp/bpkg/mprov_jobserver/__init__.py /tmp/bpkg/mprov_jobserver/plugins/__init__.py
  printf %s {shlex.quote(BUNDLE_WEBSERVER_YAML)} > /tmp/bpkg/mprov_jobserver/plugins/mprov-webserver.yaml
  echo "- image-sync: {{}}" > /tmp/bpkg/mprov_jobserver/plugins/image-sync.yaml
  cat > /tmp/bpkg/setup.py <<'EOF'
from setuptools import setup
setup(name='mprov_jobserver', version='0.7.0', packages=['mprov_jobserver', 'mprov_jobserver.plugins'],
      package_data={{'mprov_jobserver.plugins': ['*.yaml']}})
EOF
  /usr/libexec/mprov/bin/python3 -m pip install -q --no-index --no-build-isolation /tmp/bpkg >/dev/null 2>&1 \\
    || echo "bundle stand-in install failed" >&2
"""

STUB_PIP = """
  mkdir -p /tmp/stub
  printf '#!/bin/sh\\necho "pip3 $*" >> /tmp/pip3-calls\\nexit 1\\n' > /tmp/stub/pip3
  chmod 755 /tmp/stub/pip3
  export PATH=/tmp/stub:$PATH
"""


def test_uses_the_bundled_jobserver_without_system_pip():
  check = """
    ls /usr/libexec/mprov/lib/python3*/site-packages/mprov_jobserver/plugins/*.yaml >/dev/null && echo "layout=ok"
    echo "pip3 calls: $(cat /tmp/pip3-calls 2>/dev/null)"
    grep -e '^mprov_jobserver version' -e '^Using the job server bundled' /tmp/stdout
    ls /etc/mprov/plugins
    echo "#"; cat /etc/mprov/plugins/mprov-webserver.yaml
  """
  code, err, out = run(INSTALL_BUNDLE + STUB_PIP, check)
  assert code == 0, err
  head, webserver = out.split("#\n")
  lines = head.splitlines()
  assert lines[0] == "layout=ok"
  assert lines[1] == "pip3 calls: "
  assert lines[2] == "Using the job server bundled in the image (/usr/libexec/mprov/bin/python3)."
  assert lines[3] == "mprov_jobserver version: 0.7.0"
  assert lines[4:] == ["image-sync.yaml", "mprov-webserver.yaml"]
  assert webserver == BUNDLE_WEBSERVER_YAML


def test_prefers_the_bundle_over_a_system_pip_install():
  # an image rebuilt from a 0.6-era base may still carry the old one
  code, err, out = run(INSTALL_STANDIN + INSTALL_BUNDLE,
                       "grep '^mprov_jobserver version' /tmp/stdout; cat /etc/mprov/plugins/mprov-webserver.yaml")
  assert code == 0, err
  version, webserver = out.split("\n", 1)
  assert version == "mprov_jobserver version: 0.7.0"
  assert webserver == BUNDLE_WEBSERVER_YAML


def test_fails_when_the_bundle_has_no_jobserver():
  setup = "python3 -m venv --without-pip /usr/libexec/mprov" + STUB_PIP
  code, err, out = run(setup, "echo \"pip3 calls: $(cat /tmp/pip3-calls 2>/dev/null)\"; ls /etc/mprov/plugins")
  assert code == 1
  assert "unable to find the mprov_jobserver plugin configs" in err
  assert out == "pip3 calls: \n"
