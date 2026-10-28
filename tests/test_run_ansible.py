"""run_ansible.sh's jq run-mode filters (#28).

The script selects the collections, roles and playbooks whose scriptType slug
starts with the run mode and feeds them to the ansible loops.  The mPCC serves
these lists as nested objects on the entity, each system group and the distro
(systems/models.py: System, SystemGroup, SystemImage; osmanagement/models.py:
OSDistro); post-boot also on the system image and its groups.  The collection
stage must tolerate missing keys at every level.  These cases run the script
with the ansible commands stubbed and require each loop to fire exactly for the
matching entries, with no jq errors on stderr.  One case has lists only under
osdistro; the other has them at every level — the realistic shape per live
mPCC replies.  Config inheritance (mprov-ng/mprov_control_center#269) has
landed as merged ``config`` (mcc#370, #376; jobserver#213); dedupe and
ordering gaps remain as a follow-up (part 5 of #269) and are out of scope
here.
"""
import json
import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGE = "mprov-scripts-test"

# Minimal shape: ansible lists only under osdistro (other levels lack the keys).
ENTITY = {
  "mprovURL": "http://mpcc.example/",
  "apikey": "k",
  "slug": "test-image",  # a slug means run mode image-gen
  "config_params": {},
  "systemgroups": [{"name": "g"}],
  "osdistro": {
    "ansiblecollections": [
      {"scriptType": {"slug": "post-boot"},
       "collectionurl": "https://example.test/collections/other.tar.gz"},
      {"scriptType": {"slug": "image-gen"},
       "collectionurl": "https://example.test/collections/match.tar.gz"}],
    "ansibleroles": [
      {"scriptType": {"slug": "image-gen"},
       "roleurl": "https://example.test/roles/match.tar.gz"}],
    "ansibleplaybooks": [
      {"scriptType": {"slug": "image-gen"},
       "filename": "https://example.test/playbooks/match.yaml"}],
  },
}

# Realistic shape per live mPCC replies: ansible lists at every level (entity,
# groups, distro).  Config inheritance (mprov-ng/mprov_control_center#269) has
# landed as merged ``config`` (mcc#370, #376; jobserver#213); dedupe and
# ordering are a follow-up (part 5 of #269).
ENTITY_ALL_LEVELS = {
  "mprovURL": "http://mpcc.example/",
  "apikey": "k",
  "slug": "test-image",
  "config_params": {},
  "ansiblecollections": [
    {"scriptType": {"slug": "image-gen"},
     "collectionurl": "https://example.test/collections/coll-entity.tar.gz"}],
  "ansibleroles": [
    {"scriptType": {"slug": "image-gen"},
     "roleurl": "https://example.test/roles/role-entity.tar.gz"}],
  "ansibleplaybooks": [
    {"scriptType": {"slug": "image-gen"},
     "filename": "https://example.test/playbooks/pb-entity.yaml"}],
  "systemgroups": [{
    "ansiblecollections": [
      {"scriptType": {"slug": "post-boot"},
       "collectionurl": "https://example.test/collections/coll-group-skip.tar.gz"},
      {"scriptType": {"slug": "image-gen"},
       "collectionurl": "https://example.test/collections/coll-group.tar.gz"}],
    "ansibleroles": [
      {"scriptType": {"slug": "image-gen"},
       "roleurl": "https://example.test/roles/role-group.tar.gz"}],
    "ansibleplaybooks": [
      {"scriptType": {"slug": "image-gen"},
       "filename": "https://example.test/playbooks/pb-group.yaml"}]}],
  "osdistro": {
    "ansiblecollections": [
      {"scriptType": {"slug": "image-gen"},
       "collectionurl": "https://example.test/collections/coll-distro.tar.gz"}],
    "ansibleroles": [
      {"scriptType": {"slug": "image-gen"},
       "roleurl": "https://example.test/roles/role-distro.tar.gz"}],
    "ansibleplaybooks": [
      {"scriptType": {"slug": "image-gen"},
       "filename": "https://example.test/playbooks/pb-distro.yaml"}],
  },
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


def test_distro_only_entity_reaches_the_ansible_loops():
  # Minimal shape: osdistro has the lists, the entity and its groups lack the
  # ansible keys entirely.  The stage must still emit the distro's entries.
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


def test_lists_at_every_level_all_reach_the_ansible_loops():
  # Realistic shape: entries at the group, entity and distro levels all get
  # collected (groups first, then entity, then distro).
  code, err, logs = run(ENTITY_ALL_LEVELS)
  assert err == ""
  assert code == 0
  assert logs["ansible-galaxy"] == (
    "collection install -p /tmp/mprov/ansible/collections/ "
    "https://example.test/collections/coll-group.tar.gz -f\n"
    "collection install -p /tmp/mprov/ansible/collections/ "
    "https://example.test/collections/coll-entity.tar.gz -f\n"
    "collection install -p /tmp/mprov/ansible/collections/ "
    "https://example.test/collections/coll-distro.tar.gz -f\n"
    "role install -p /tmp/mprov/ansible/roles/ "
    "https://example.test/roles/role-group.tar.gz -f\n"
    "role install -p /tmp/mprov/ansible/roles/ "
    "https://example.test/roles/role-entity.tar.gz -f\n"
    "role install -p /tmp/mprov/ansible/roles/ "
    "https://example.test/roles/role-distro.tar.gz -f\n")
  assert logs["ansible-playbook"] == (
    "-i localhost, -c local -e @variables.json /tmp/role.yaml\n") * 3
  assert logs["wget"] == (
    "-P playbooks/ -N https://example.test/playbooks/pb-group.yaml\n"
    "-P playbooks/ -N https://example.test/playbooks/pb-entity.yaml\n"
    "-P playbooks/ -N https://example.test/playbooks/pb-distro.yaml\n")


def test_other_run_mode_entries_are_skipped():
  entity = json.loads(json.dumps(ENTITY))
  for lists in entity["osdistro"].values():
    for entry in lists:
      entry["scriptType"]["slug"] = "post-boot"
  code, err, logs = run(entity)
  assert err == ""
  assert code == 0
  assert logs["ansible-galaxy"] == ""
  assert logs["ansible-playbook"] == ""
  assert logs["wget"] == ""
