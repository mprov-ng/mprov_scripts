"""mpcc_scripts.yaml, the default script catalogue the mPCC's
install_scripts/load_default_scripts.sh copies and loads, must only name
files that exist, with each entry's pk equal to its slug and unique, and a
scriptType matching where the file lives.

The loader reads the filenames with grep/awk, so this parses the manifest
the same plain way instead of needing PyYAML.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def entries():
  out, cur = [], None
  for line in (REPO / "mpcc_scripts.yaml").read_text().splitlines():
    key, _, value = line.strip().lstrip("- ").partition(":")
    if line.startswith("- model:"):
      cur = {"model": value.strip()}
      out.append(cur)
    elif cur is not None and value.strip():
      cur[key] = value.strip()
  return out


def test_every_entry_names_an_existing_file():
  for e in entries():
    assert (REPO / e["filename"]).is_file(), e


def test_pks_are_slugs_and_unique():
  pks = [e["pk"] for e in entries()]
  assert len(pks) == len(set(pks))
  for e in entries():
    assert e["model"] == "scripts.script"
    assert e["pk"] == e["slug"], e


def test_script_type_matches_the_directory():
  # run_ansible.sh is the one image-gen script also listed for post-boot.
  for e in entries():
    if e["filename"] == "image-gen/run_ansible.sh":
      continue
    assert e["filename"].split("/")[0] == e["scriptType"], e


def test_catalogue_has_the_generic_scripts():
  files = {e["filename"] for e in entries()}
  assert "image-gen/setup_data_share_mount.sh" in files
  assert "post-boot/install_chrony_and_timesync.sh" in files
