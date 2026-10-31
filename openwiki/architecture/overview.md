---
type: Reference
title: mProv Script Execution Architecture
description: How mprov_scripts fits into the mProv Control Center ecosystem — script types (image-gen vs post-boot), the mprov_jobserver and its script-runner/NADS/image-sync plugins, the /tmp/mprov/entity.json contract with null-safe config_params defaults (tested by the tests/ pytest suite), Python MProvScript classes, and the /data NFS share mount.
tags: [mprov, macc, jobserver, architecture, provisioning, entity-json]
---

# Architecture Overview

`mprov_scripts` is the script catalog for an **mProv** deployment. Understanding four concepts explains almost every script in the repo.

## The mPCC ecosystem

- **mProv Control Center (mPCC)** — a Django web app (`/var/www/mprov_control_center/`, see [tools/backup-mprov.sh](../operations/admin-tools.md)) that holds the cluster inventory: systems, network interfaces, BMCs, switches/ports, disk layouts, OS distros/images, and scripts. Scripts in this repo are metadata + payload uploaded into the mPCC `scripts.script` model.
- **mProv jobserver** (`mprov_jobserver`, a pip package) — an agent that polls the mPCC for jobs. The scripts in `image-gen/` install it and wire up systemd services. Job modules seen in configs: `script-runner`, `nads`, `image-sync`, `image-delete`, `mprov-webserver`, `repo-update` ([image-gen/install_mprov_jobserver.sh](../../image-gen/install_mprov_jobserver.sh), [image-gen/install_mprov_script-runner.sh](../../image-gen/install_mprov_script-runner.sh)).
- **NADS** (Node Auto Detection System) — runs inside a special `nads` image on an unknown booting node to auto-detect it (waits for LLDP, then registers). Installation is handled by the same script-runner install script, which detects the `nads` slug in `entity.json` and swaps the `nads` job module in for `script-runner`.
- **mash** — a Django-shell-style CLI (`#!/usr/local/bin/mash`) for scripting against mPCC models; used by everything in [tools/](../operations/admin-tools.md).

## Script types: image-gen vs post-boot

Every mPCC script has a `scriptType` of `image-gen` or `post-boot` (see the slugs in `mpcc_scripts.yaml`). The repo directory layout mirrors this:

- **image-gen** scripts run while the mPCC image builder generates an OS image — baked into the image itself ([Image Generation Scripts](../script-types/image-generation.md)).
- **post-boot** scripts run on the real node after it boots the image, dispatched by the `script-runner` jobserver plugin ([Post-Boot Scripts](../script-types/post-boot.md)).

One file can serve both types: `image-gen/run_ansible.sh` is registered twice in `mpcc_scripts.yaml` (slugs `run_ansible_post_sh` / `run_ansible_image_sh`). It detects the mode at runtime — if `entity.json` has a `slug` (an image), it is `image-gen`; otherwise `post-boot` — and filters the Ansible collections/roles/playbooks to run by `scriptType.slug` prefix.

## The entity.json contract

When the jobserver runs a script, it stages the invocation in `/tmp/mprov/` with `entity.json` describing the target entity. Fields relied upon across the repo:

| Field | Used by | Meaning |
|---|---|---|
| `mprovURL` | nearly every script, NFS mounts | Base URL of the mPCC API; the host part is also the default NFS server for `/export/data` |
| `apikey` | jobserver configs, Python scripts | API credential for the mPCC |
| `config_params` | extra_packages, slurm_enable_gpus, setup_data_share_mount | Free-form per-entity JSON: `extra_packages`, `gpus` (count or `type:count`, e.g. `a100:4`), `mprovDataShareHost`, Ansible variables |
| `config` | run_ansible.sh | Merged Ansible collection/role/playbook slugs served by mPCCs with config inheritance (mprov-ng/mprov_control_center#269); `run_ansible.sh` falls back to the per-entity ansible lists when absent |
| `slug` | run_ansible.sh, install_mprov_script-runner.sh | Image slug when running in image-gen mode; `nads` selects the NADS module |

Scripts treat a missing or `null` config param as unset — via `jq` guards like `// empty` and `// []` — and fall back to their documented default (e.g. `slurm_enable_gpus.sh` defaults `gpus=1`), never the literal string `null`. The pytest suite in `tests/` (`tests/test_config_params.py`, run in a Rocky 9 container from `tests/Dockerfile` with `dnf`/`systemctl` stubs, on push/PR via `.github/workflows/tests.yml`) enforces this for the param-reading scripts; `.github/workflows/lint.yml` separately gates every tracked `*.sh` on `bash -n` and `shellcheck -S error`. `config_params` doubles as the Ansible variable file: `run_ansible.sh` extracts it into `variables.json` and passes `-e @variables.json`.

## Python post-boot scripts (MProvScript)

Three post-boot scripts are Python classes subclassing `MProvScript` from the `mprov_jobserver.script` package (`setupBMC.py`, `register_IPv6_ll.py`; `localTempDisk.py` predates this and reimplements config/session loading — it reads `entity.json` next to the script when the script runner provides it, falling back to `/etc/mprov/script-runner.yaml` then `/etc/mprov/jobserver.yaml` for manual runs). The base class provides `self.session` (an authenticated `requests.Session`) and `self.mprovURL`, so scripts query the mPCC REST API directly, e.g. `GET {mprovURL}systems/?hostname=...` in `setupBMC.py`.

## Shared NFS layout

One image-gen script mounts an mPCC-host NFS export into the image:

- `${shareHost}:/export/data` → `/data` ([setup_data_share_mount.sh](../../image-gen/setup_data_share_mount.sh)), where `shareHost` comes from `config_params.mprovDataShareHost` (settable on the OS distro, a system group, or the image — e.g. a cluster-network name for the mPCC) and defaults to the host parsed from `mprovURL`.

The former `/export/mprov` → `/opt/mprov` mount and the Slurm/Munge client service it fed (`setup_mprov_share_mount.sh`, `create_slurm_client_service.sh`) were removed from this repo along with the mPCC's `/export/mprov` share dependency; clusters provide Slurm client setup themselves.

## Registration: mpcc_scripts.yaml

`mpcc_scripts.yaml` is a Django fixture of `scripts.script` objects (slug, name, filename, scriptType) that seeds an mPCC database with this repo's catalog. It references repo-relative `filename` paths, so it assumes the script bodies are uploaded/available separately. Adding a new dispatchable script means an entry here (or direct upload via the mPCC UI).

## Operational docs

The repo root also carries AGENTS.md/CLAUDE.md banner blocks pointing at these `openwiki/` docs, refreshed by the scheduled `.github/workflows/openwiki-update.yml` workflow (daily `openwiki code --update`, PR against `openwiki/update`); these are documentation tooling, not part of the provisioning flow.

