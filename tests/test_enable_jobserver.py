"""post-boot/enable_and_run_jobserver.sh must fail when the jobserver can't be
enabled and started, and skip the firewall when firewalld is off (#32).

It ran systemctl and firewall-cmd unchecked, so the exit status was that of
the last firewall-cmd: a missing unit still exited 0 with no jobserver
running, and a node without firewalld failed.

Each case runs the script in a container built from tests/Dockerfile with
systemctl and firewall-cmd stubbed.  The stubs log their arguments to
/tmp/<name>.log; systemctl exits 1 for the commands named in /tmp/fail.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"

STUBS = r"""
  cat > /usr/local/bin/systemctl <<'EOF'
#!/bin/bash
echo "$@" >> /tmp/systemctl.log
grep -qxF "$*" /tmp/fail 2>/dev/null && exit 1
exit 0
EOF
  printf '#!/bin/bash\necho "$@" >> /tmp/firewall-cmd.log\n' > /usr/local/bin/firewall-cmd
  chmod 755 /usr/local/bin/systemctl /usr/local/bin/firewall-cmd
  touch /tmp/fail
"""


@pytest.fixture(scope="session", autouse=True)
def image():
  subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(REPO / "tests")],
                 check=True, stdout=subprocess.DEVNULL)


def run(fail=(), firewall_fails=False):
  '''Run the script with the systemctl commands in fail failing, and
  firewall-cmd failing if firewall_fails.  Returns (exit code, output,
  {stub: logged lines}).'''
  fails = "".join(f"echo '{f}' >> /tmp/fail\n" for f in fail)
  if firewall_fails:
    fails += "echo 'exit 1' >> /usr/local/bin/firewall-cmd\n"
  cmd = f"""
    {STUBS}
    {fails}
    bash /script >/tmp/run.log 2>&1; echo "exit=$?"
    cat /tmp/run.log
    for l in systemctl firewall-cmd; do echo "== $l"; cat /tmp/$l.log 2>/dev/null; done
    true
  """
  r = subprocess.run(
    ["docker", "run", "--rm", "--network", "none",
     "-v", f"{REPO / 'post-boot' / 'enable_and_run_jobserver.sh'}:/script:ro",
     IMAGE, "bash", "-c", cmd],
    capture_output=True, text=True, timeout=180,
  )
  assert r.returncode == 0, r.stderr
  body, _, logs = r.stdout.partition("== systemctl\n")
  systemctl, _, firewall = logs.partition("== firewall-cmd\n")
  return (int(body.splitlines()[0].split("=")[1]), body,
          {"systemctl": systemctl.splitlines(), "firewall-cmd": firewall.splitlines()})


def test_starts_the_jobserver_and_opens_the_port():
  code, log, calls = run()
  assert code == 0, log
  assert calls["systemctl"] == [
    "enable --now mprov-jobserver",
    "is-active --quiet firewalld",
  ]
  assert calls["firewall-cmd"] == ["--zone=public --add-port=8080/tcp --permanent", "--reload"]


def test_missing_unit_fails():
  code, log, calls = run(fail=["enable --now mprov-jobserver"])
  assert code == 1
  assert "unable to enable and start mprov-jobserver" in log
  assert calls["firewall-cmd"] == []


def test_no_firewalld_is_not_a_failure():
  code, log, calls = run(fail=["is-active --quiet firewalld"])
  assert code == 0, log
  assert "firewalld isn't running, not opening port 8080." in log
  assert calls["firewall-cmd"] == []


def test_failed_firewall_command_fails():
  code, log, calls = run(firewall_fails=True)
  assert code == 1, log
  assert calls["firewall-cmd"] == ["--zone=public --add-port=8080/tcp --permanent"]
