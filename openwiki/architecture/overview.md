---
type: Reference
title: mProv Script Execution Architecture
description: How mprov_scripts fits into the mProv Control Center ecosystem — script types (image-gen vs post-boot), the mprov_jobserver and its script-runner/NADS/image-sync plugins, the /tmp/mprov/entity.json contract, Python MProvScript classes, and the shared NFS layout under /opt/mprov.
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
| `mprovURL` | nearly every script, NFS mounts | Base URL of the mPCC API; the host part is also the NFS server for `/export/*` |
| `apikey` | jobserver configs, Python scripts | API credential for the mPCC |
| `config_params` | extra_packages, set_root_pw, setup_ssh, slurm_enable_gpus | Free-form per-entity JSON: `extra_packages`, `rootpw`, `rootsshkey`, `gpus`, Ansible variables |
| `slug` | run_ansible.sh, install_mprov_script-runner.sh | Image slug when running in image-gen mode; `nads` selects the NADS module |

Scripts treat missing config params as errors (`set_root_pw.sh`, `setup_ssh.sh`) or defaults (`slurm_enable_gpus.sh` defaults `gpus=1`). `config_params` doubles as the Ansible variable file: `run_ansible.sh` extracts it into `variables.json` and passes `-e @variables.json`.

## Python post-boot scripts (MProvScript)

Three post-boot scripts are Python classes subclassing `MProvScript` from the `mprov_jobserver.script` package (`setupBMC.py`, `register_IPv6_ll.py`; `localTempDisk.py` predates this and reimplements config/session loading from `/etc/mprov/jobserver.yaml`). The base class provides `self.session` (an authenticated `requests.Session`) and `self.mprovURL`, so scripts query the mPCC REST API directly, e.g. `GET {mprovURL}systems/?hostname=...` in `setupBMC.py`. `register_IPv6_ll.py` notes it requires `mprov-jobserver>=0.0.34`.

## Shared NFS layout

Two image-gen scripts mount mPCC-host NFS exports into the image, establishing the shared-config plane consumed at boot:

- `${mpccHost}:/export/mprov` → `/opt/mprov` ([setup_mprov_share_mount.sh](../../image-gen/setup_mprov_share_mount.sh)), plus a `profile.d` PATH addition for `/opt/mprov/{bin,sbin}`.
- `${mpccHost}:/export/data` → `/data` ([setup_data_share_mount.sh](../../image-gen/setup_data_share_mount.sh)).

`/opt/mprov` then carries the authoritative cluster config: the munge key at `/opt/mprov/etc/munge`, `slurm.conf` at `/opt/mprov/etc/slurm/slurm.conf`, and the Slurm binaries at `/opt/mprov/sbin/slurmd` — all symlinked in by [create_slurm_client_service.sh](../../script-types/image-generation.md#slurm-and-munge-client-services). This is why `munge.service` is ordered `After=remote-fs.target` and `slurmd.service` requires it.

## Registration: mpcc_scripts.yaml

`mpcc_scripts.yaml` is a Django fixture of `scripts.script` objects (slug, name, filename, scriptType) that seeds an mPCC database with this repo's catalog. It references repo-relative `filename` paths, so it assumes the script bodies are uploaded/available separately. Adding a new dispatchable script means an entry here (or direct upload via the mPCC UI).

## Operational docs

The repo also carries a scheduled [OpenWiki update workflow](../operations/admin-tools.md#repository-automation) and AGENTS.md/CLAUDE.md banner blocks; these are documentation tooling, not part of the provisioning flow.
