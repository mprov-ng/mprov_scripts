"""run_ansible.sh's jq run-mode filters (#28).

The script selects the collections, roles and playbooks whose scriptType slug
starts with the run mode and feeds them to the ansible loops.  The entity's
optional keys (osdistro, per-group ansibleroles, ...) project to null through
the selection pipeline, and a jq program that cannot swallow those nulls
makes each loop iterate over nothing, so no ansible command ever runs.  These
cases run the script with the ansible commands stubbed and require each loop
to fire exactly for the matching entries, with no jq errors on stderr.
"""
import json
import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"

ENTITY = {
  "mprovURL": "http://mpcc.example/",
  "apikey": "k",
  "slug": "test-image",  # a slug means run mode image-gen
  "config_params": {},
  "ansiblecollections": [],
  "systemgroups": [{"ansiblecollections": [
    {"scriptType": {"slug": "post-boot"},
     "collectionurl": "https://example.test/collections/other.tar.gz"},
    {"scriptType": {"slug": "image-gen"},
     "collectionurl": "https://example.test/collections/match.tar.gz"}]}],
  "ansibleroles": [
    {"scriptType": {"slug": "image-gen"},
     "roleurl": "https://example.test/roles/match.tar.gz"}],
  "ansibleplaybooks": [
    {"scriptType": {"slug": "image-gen"},
     "filename": "https://example.test/playbooks/match.yaml"}],
}

STUBS = ("for cmd in dnf ansible-galaxy ansible-playbook wget; do "
         "printf '#!/bin/bash\\necho \"$@\" >> /tmp/%s.log\\n' $cmd "
         "> /usr/local/bin/$cmd && chmod 755 /usr/local/bin/$cmd; done")


@pytest.fixture(scope="session", autouse=True)
def image():
  subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(REPO / "tests")],
                 check=True, stdout=subprocess.DEVNULL)


def run(entity):
  '''Run run_ansible.sh in the test container with the ansible commands
  stubbed to log their arguments.  Returns (exit code, stderr, log dict).'''
  cmd = f"""
    mkdir -p /tmp/mprov && echo {shlex.quote(json.dumps(entity))} > /tmp/mprov/entity.json
    {STUBS}
    bash /script >/tmp/stdout 2>/tmp/stderr; echo "exit=$?"
    echo "---"; cat /tmp/stderr; echo "---"
    for l in /tmp/ansible-galaxy.log /tmp/ansible-playbook.log /tmp/wget.log; do
      echo "== $l"; cat $l 2>/dev/null
    done
    exit 0
  """
  r = subprocess.run(
    ["docker", "run", "--rm",
     "-v", f"{REPO / 'image-gen/run_ansible.sh'}:/script:ro", IMAGE,
     "bash", "-c", cmd],
    capture_output=True, text=True, timeout=300,
  )
  assert r.returncode == 0, r.stderr
  status, stderr, out = r.stdout.split("---\n", 2)
  logs = {}
  for chunk in out.split("== /tmp/")[1:]:
    name, _, body = chunk.partition(".log\n")
    logs[name] = body
  return int(status.strip().split("=")[1]), stderr, logs


def test_matching_entries_reach_the_ansible_loops():
  code, err, logs = run(ENTITY)
  assert err == ""  # no jq errors
  assert code == 0
  # only the image-gen collection installs, then the role installs; the
  # post-boot collection in the same array is not touched.
  assert logs["ansible-galaxy"] == (
    "collection install -p /tmp/mprov/ansible/collections/ "
    "https://example.test/collections/match.tar.gz -f\n"
    "role install -p /tmp/mprov/ansible/roles/ "
    "https://example.test/roles/match.tar.gz -f\n")
  # the role runs as a playbook, and the matching playbook is fetched.
  assert logs["ansible-playbook"] == (
    "-i localhost, -c local -e @variables.json /tmp/role.yaml\n")
  assert logs["wget"] == (
    "-P playbooks/ -N https://example.test/playbooks/match.yaml\n")


def test_other_run_mode_entries_are_skipped():
  entity = json.loads(json.dumps(ENTITY))
  for group in entity["systemgroups"]:
    for coll in group["ansiblecollections"]:
      coll["scriptType"]["slug"] = "post-boot"
  for entry in entity["ansibleroles"] + entity["ansibleplaybooks"]:
    entry["scriptType"]["slug"] = "post-boot"
  code, err, logs = run(entity)
  assert err == ""
  assert code == 0
  assert logs["ansible-galaxy"] == ""
  assert logs["ansible-playbook"] == ""
  assert logs["wget"] == ""
