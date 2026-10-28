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
mPCC replies.  Those entities have no ``config``, as from an mPCC before
config inheritance (mprov-ng/mprov_control_center#269), so the lists are
collected from the layers.

With #269 the mPCC serves the merged lists as slugs in ``config`` (mcc#370,
#376), in inheritance order (distro, system groups, image, system) and once
each; the script then runs those, looking each one up in entity.json.  The
last cases cover that.
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


# wget that also "downloads": creates the file in the -P directory, so the
# playbooks it fetched get run.
WGET_DOWNLOADS = """cat > /usr/local/bin/wget <<'STUB'
#!/bin/bash
echo "$@" >> /tmp/wget.log
touch "$2/$(basename "${@: -1}")"
STUB
chmod 755 /usr/local/bin/wget"""


def run(entity, downloads=False):
  '''Run run_ansible.sh in the test container with the ansible commands
  stubbed to log their arguments; with downloads, wget creates the files it
  fetches.  Returns (exit code, stderr, log dict).'''
  cmd = f"""
    mkdir -p /tmp/mprov && echo {shlex.quote(json.dumps(entity))} > /tmp/mprov/entity.json
    {STUBS}
    {WGET_DOWNLOADS if downloads else ""}
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


# The merged lists from the mPCC (#269).

def role(slug, mode="post-boot"):
  return {"slug": slug, "roleurl": f"https://example.test/roles/{slug}.tar.gz", "scriptType": {"slug": mode}}


def playbook(slug, mode="post-boot"):
  return {"slug": slug, "filename": f"https://example.test/playbooks/{slug}", "scriptType": {"slug": mode}}


def collection(slug, mode="post-boot"):
  return {"slug": slug, "collectionurl": f"https://example.test/collections/{slug}.tar.gz", "scriptType": {"slug": mode}}


# A system's entity.json (post-boot: no slug) the way script_runner writes it:
# the ?self reply, with the image and its groups nested under systemimage,
# osdistro copied up from it, and the merged lists in config.
SYSTEM = {
  "mprovURL": "http://mpcc.example/",
  "apikey": "k",
  "hostname": "node01",
  "config_params": {},
  "ansibleroles": [role("r-system"), role("r-shared")],
  "ansibleplaybooks": [playbook("a-system")],
  "ansiblecollections": [],
  "systemgroups": [{"ansibleroles": [role("r-sysgroup"), role("r-shared")],
                    "ansibleplaybooks": [], "ansiblecollections": [collection("c-group")]}],
  "osdistro": {"ansibleroles": [role("r-distro"), role("r-distro-ig", "image-gen")],
               "ansibleplaybooks": [playbook("z-distro")], "ansiblecollections": []},
  "systemimage": {
    "ansibleroles": [role("r-image")], "ansibleplaybooks": [], "ansiblecollections": [],
    "systemgroups": [{"ansibleroles": [role("r-imagegroup")], "ansibleplaybooks": [],
                      "ansiblecollections": []}],
  },
  "config": {
    "ansibleroles": ["r-distro", "r-distro-ig", "r-shared", "r-sysgroup", "r-imagegroup", "r-image", "r-system"],
    "ansibleplaybooks": ["z-distro", "a-system"],
    "ansiblecollections": ["c-group"],
  },
}


def installed_roles(logs):
  return [line.split()[-2].rsplit("/", 1)[-1][:-len(".tar.gz")]
          for line in logs["ansible-galaxy"].splitlines() if line.startswith("role install")]


def test_config_lists_run_once_in_order_with_the_image_layers():
  code, err, logs = run(SYSTEM, downloads=True)
  assert err == ""
  assert code == 0
  # distro, system groups, image group, image, system; r-shared once; the
  # image-gen role left out of a post-boot run
  assert installed_roles(logs) == ["r-distro", "r-shared", "r-sysgroup", "r-imagegroup", "r-image", "r-system"]
  assert "collection install -p /tmp/mprov/ansible/collections/ https://example.test/collections/c-group.tar.gz -f\n" \
    in logs["ansible-galaxy"]
  # the playbooks run in the listed order, not by file name
  runs = [line.split()[-1] for line in logs["ansible-playbook"].splitlines() if "playbooks/" in line]
  assert runs == ["playbooks/z-distro", "playbooks/a-system"]


def test_image_gen_run_takes_only_image_gen_entries_from_config():
  image = json.loads(json.dumps(SYSTEM))
  image["slug"] = "compute"  # a slug means run mode image-gen
  code, err, logs = run(image)
  assert err == ""
  assert installed_roles(logs) == ["r-distro-ig"]
  assert logs["wget"] == ""


def test_a_config_entry_missing_from_the_entity_is_skipped_with_a_warning():
  system = json.loads(json.dumps(SYSTEM))
  system["config"]["ansibleroles"].insert(1, "ghost")
  code, err, logs = run(system)
  assert code == 0
  assert "WARNING: ansibleroles ghost is in the mPCC's config but not in entity.json, skipped." in err
  assert installed_roles(logs) == ["r-distro", "r-shared", "r-sysgroup", "r-imagegroup", "r-image", "r-system"]
