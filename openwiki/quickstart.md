---
type: Reference
title: mprov_scripts Quickstart
description: Entry point for the mprov_scripts repository — a curated library of image-generation, post-boot, and admin scripts for the mProv Control Center (mPCC) bare-metal provisioning system used to build HPC clusters.
tags: [mprov, macc, provisioning, hpc, quickstart]
---

# mprov_scripts Quickstart

`mprov_scripts` is a community script library for **mProv** / **mProv Control Center (mPCC)**, a bare-metal provisioning system for HPC-style clusters. The repo does not contain the mPCC web app, the jobserver daemon, or the image builder — it contains the *scripts and fixtures you upload to (or run against) an mPCC* so that generated OS images and freshly booted nodes are configured correctly.

Scripts are organized by **where they run** in the provisioning lifecycle. See [Architecture Overview](architecture/overview.md) for how the pieces fit together.

## Directory map

| Directory | Runs where / when | Purpose |
|---|---|---|
| [`image-gen/`](script-types/image-generation.md) | During OS image generation inside the mPCC image builder | Install the mProv jobserver + script-runner, mount the `/data` NFS share, install extra packages/yq, run Ansible |
| [`post-boot/`](script-types/post-boot.md) | On the node after first boot, dispatched by the script-runner jobserver | BMC setup, local disk formatting, IPv6 link-local registration, Slurm GPU gres, CUDA, kernel mitigations, time sync |
| [`tools/`](operations/admin-tools.md) | Admin command line against a running mPCC | Bulk-create systems/switch ports (`mash`), CSV import, DB backup, image capture |
| `mpcc_scripts.yaml` | Uploaded/imported into mPCC | Django fixture (`scripts.script`) registering selected scripts into the mPCC database |

## How a script gets used

1. Copy a script from `image-gen/` or `post-boot/` into your environment and upload it to the mPCC (or import `mpcc_scripts.yaml` with `manage.py loaddata` as a starting catalog).
2. In the mPCC, attach the script to an image, system group, or distro with the matching script type (`image-gen` or `post-boot`).
3. The mProv jobserver's `script-runner` plugin executes it, providing context in `/tmp/mprov/entity.json` (`mprovURL`, `apikey`, `config_params`). Most scripts here are just `jq` away from that file — see [Architecture Overview](architecture/overview.md#the-entityjson-contract).

## Where to go next

- [Architecture Overview](architecture/overview.md) — the mPCC/jobserver ecosystem, script types, the `entity.json` contract, and the `/data` NFS share layout.
- [Image Generation Scripts](script-types/image-generation.md) — what goes into a mProv compute image and why.
- [Post-Boot Scripts](script-types/post-boot.md) — first-boot configuration, BMC/IPMI, disks, and the Slurm + GPU stack.
- [Admin Tools & Operations](operations/admin-tools.md) — `mash` bulk scripting, backups, image capture.

## Change guidance

- Scripts run as root with no interactive input; anything they need must come from `entity.json` / `config_params` or the mPCC REST API. An unset or `null` config param must take the script's documented default, never the string `null` — `tests/` enforces this (`python -m pytest tests`, requires Docker).
- Adding a script that mPCC should dispatch means also registering it (direct upload or a new entry in `mpcc_scripts.yaml` with the right `scriptType`); `tests/test_manifest.py` checks the manifest against the repo (every `filename` exists, `pk` equals `slug`, `scriptType` matches the directory).
- Site-specific one-offs get removed from this repo, not deprecated in place — `gpfs_mmsdrrestore.sh`, `nads.sh`, `NetworkManager-1.40-COPR.sh`, `set_root_pw.sh` and `setup_ssh.sh` were all deleted; check git history before reviving any of them.
- Shell changes are gated by `.github/workflows/lint.yml` (`bash -n` plus `shellcheck -S error` over every tracked `*.sh`; `.mash` files are excluded), alongside the pytest suite in `tests/`.

## Backlog

- LICENSE — AGPL-style license text present at repo root; not expanded in the wiki.
