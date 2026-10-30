"""image-gen/install_yq.sh installs a pinned yq release, not @latest (#24).

The case runs the script in a container built from tests/Dockerfile (dnf is
a stub that logs its arguments) with a go stub that logs its arguments and
drops a fake yq into $GOBIN.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"

GO_STUB = r"""
  cat > /usr/local/bin/go <<'EOF'
#!/bin/bash
echo "GOBIN=$GOBIN $*" >> /tmp/go.log
printf '#!/bin/bash\necho "yq version %s"\n' "${2##*@}" > "$GOBIN/yq"
chmod 755 "$GOBIN/yq"
EOF
  chmod 755 /usr/local/bin/go
"""


@pytest.fixture(scope="session", autouse=True)
def image():
  subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(REPO / "tests")],
                 check=True, stdout=subprocess.DEVNULL)


def test_installs_the_pinned_version():
  cmd = f"""
    {GO_STUB}
    bash /script > /tmp/out 2>&1; echo "exit=$?"
    cat /tmp/out; echo "---"; cat /tmp/go.log; echo "---"; cat /tmp/dnf.log
  """
  r = subprocess.run(
    ["docker", "run", "--rm", "--network", "none",
     "-v", f"{REPO / 'image-gen' / 'install_yq.sh'}:/script:ro",
     IMAGE, "bash", "-c", cmd],
    capture_output=True, text=True, timeout=120,
  )
  assert r.returncode == 0, r.stderr
  out, go, dnf = r.stdout.split("---\n")
  assert out.splitlines()[0] == "exit=0", out
  assert go.strip() == "GOBIN=/usr/local/bin install github.com/mikefarah/yq/v4@v4.53.3"
  assert "yq version v4.53.3" in out
  # golang isn't in the test image, so it's installed first.
  assert dnf.strip() == "-y install golang"
