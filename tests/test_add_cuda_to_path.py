"""post-boot/add-cuda-to-path.sh writes /etc/profile.d/zz-cuda.sh, which must
not delete anything at login and must not put an empty element (the current
directory) on LD_LIBRARY_PATH or PATH (#76).

Each case runs the script in a container built from tests/Dockerfile, then
sources the profile script it wrote in a fresh shell, as root and as an
ordinary user.
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


def run(login):
  '''Run the script, put a 99-cuda.sh in place, then run login (a shell
  snippet) after sourcing zz-cuda.sh.  Returns its output lines.'''
  cmd = f"""
    bash /script || echo script-failed
    echo 'export FROM_99=1' > /etc/profile.d/99-cuda.sh
    {login}
  """
  r = subprocess.run(
    ["docker", "run", "--rm", "--network", "none",
     "-v", f"{REPO / 'post-boot' / 'add-cuda-to-path.sh'}:/script:ro",
     IMAGE, "bash", "-c", cmd],
    capture_output=True, text=True, timeout=120,
  )
  assert r.returncode == 0, r.stderr
  assert "script-failed" not in r.stdout
  return r.stdout.splitlines()


SHOW = ('echo "LD=$LD_LIBRARY_PATH"; echo "PATH=$PATH"; echo "HOME_CUDA=$CUDA_HOME"; '
        'test -e /etc/profile.d/99-cuda.sh && echo kept-99 || echo removed-99')


@pytest.mark.parametrize("prefix", ["unset LD_LIBRARY_PATH;", "export LD_LIBRARY_PATH=;"])
def test_no_empty_library_path_element(prefix):
  out = run(f"env -i PATH=/usr/bin:/bin sh -c '{prefix} . /etc/profile.d/zz-cuda.sh; {SHOW}'")
  ld = out[0].removeprefix("LD=")
  assert ld == "/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64"
  assert "" not in ld.split(":")
  assert out[1] == "PATH=/usr/bin:/bin:/usr/local/cuda/bin"
  assert out[2] == "HOME_CUDA=/usr/local/cuda"


def test_existing_library_path_is_kept():
  out = run(f"env -i PATH=/usr/bin LD_LIBRARY_PATH=/opt/lib sh -c '. /etc/profile.d/zz-cuda.sh; {SHOW}'")
  assert out[0] == "LD=/opt/lib:/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64"


@pytest.mark.parametrize("user", ["root", "nobody"])
def test_login_deletes_nothing(user):
  # the old profile script ran rm -f /etc/profile.d/99-cuda.sh at each login.
  out = run(f"su -s /bin/sh {user} -c '. /etc/profile.d/zz-cuda.sh; {SHOW}' 2>&1")
  assert out[-1] == "kept-99"
  assert not any("rm" in line or "denied" in line for line in out)


def test_profile_script_is_world_readable():
  out = run("stat -c '%a' /etc/profile.d/zz-cuda.sh")
  assert out == ["644"]
