"""The post-boot Python scripts pick their interpreter at run time (#86).

Line 2 of each script is a sh/python polyglot: sh execs the job server's
bundled python (/usr/libexec/mprov/bin/python3, in images built by
mprov_jobserver 0.7.0+) if it's there, or else /usr/bin/python3 (images built
by 0.6.x).  The cases run each script the way the script runner does (as an
executable, through the shell) in a container built from tests/Dockerfile,
with both interpreters replaced by stubs that log what they were given.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"
SCRIPTS = ["setupBMC.py", "register_IPv6_ll.py", "localTempDisk.py",
           "report_booted.py"]
BUNDLE = "/usr/libexec/mprov/bin/python3"
SYSTEM = "/usr/bin/python3"

# stub_python <path> <label>: a fake interpreter that prints its label and
# arguments, one per line.
STUBS = r"""
  stub_python() {
    mkdir -p "$(dirname "$1")"
    printf '#!/bin/bash\necho "%s"\nprintf "%%s\\n" "$@"\n' "$2" > "$1.stub"
    chmod 755 "$1.stub"
    mv -f "$1.stub" "$1"
  }
"""


@pytest.fixture(scope="session", autouse=True)
def image():
  subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(REPO / "tests")],
                 check=True, stdout=subprocess.DEVNULL)


def run(script, setup):
  cmd = f"""
    {STUBS}
    cp /script /tmp/mprov-script.py && chmod 755 /tmp/mprov-script.py
    {setup}
    /tmp/mprov-script.py one "two words"; echo "exit=$?"
  """
  r = subprocess.run(
    ["docker", "run", "--rm", "--network", "none",
     "-v", f"{REPO / 'post-boot' / script}:/script:ro",
     IMAGE, "bash", "-c", cmd],
    capture_output=True, text=True, timeout=120,
  )
  assert r.returncode == 0, r.stderr
  return r.stdout.splitlines(), r.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_uses_the_bundled_python_when_it_is_there(script):
  out, _ = run(script, f"stub_python {BUNDLE} bundle; stub_python {SYSTEM} system")
  assert out == ["bundle", "/tmp/mprov-script.py", "one", "two words", "exit=0"]


@pytest.mark.parametrize("script", SCRIPTS)
def test_follows_the_bundles_python3_symlink(script):
  # the bundle's python3 is a relative symlink to python3.11 (#89).
  out, _ = run(script, f"stub_python {BUNDLE}.11 bundle; "
                       f"ln -s python3.11 {BUNDLE}; stub_python {SYSTEM} system")
  assert out == ["bundle", "/tmp/mprov-script.py", "one", "two words", "exit=0"]


@pytest.mark.parametrize("script", SCRIPTS)
def test_falls_back_to_system_python3_without_the_bundle(script):
  out, _ = run(script, f"stub_python {SYSTEM} system")
  assert out == ["system", "/tmp/mprov-script.py", "one", "two words", "exit=0"]


@pytest.mark.parametrize("script", SCRIPTS)
def test_skips_a_bundled_python_that_is_not_executable(script):
  out, _ = run(script, f"stub_python {BUNDLE} bundle; chmod 644 {BUNDLE}; "
                       f"stub_python {SYSTEM} system")
  assert out[0] == "system"


@pytest.mark.parametrize("script", SCRIPTS)
def test_fails_without_any_python(script):
  out, err = run(script, f"rm -f {SYSTEM}")
  assert out[-1] != "exit=0"
  assert SYSTEM in err


@pytest.mark.parametrize("script", SCRIPTS)
def test_python_skips_line_two(script):
  # the real system python3 runs the script's polyglot header and stops.
  head = "".join((REPO / "post-boot" / script).read_text()
                 .splitlines(keepends=True)[:7])
  r = subprocess.run(["python3", "-I", "-c", head + "print('reached')"],
                     capture_output=True, text=True, timeout=30)
  assert r.returncode == 0, r.stderr
  assert r.stdout == "reached\n"
