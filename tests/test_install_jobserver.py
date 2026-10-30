"""install_mprov_jobserver.sh copies the jobserver's default plugin configs (#42).

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
