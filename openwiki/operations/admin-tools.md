---
type: Playbook
title: Admin Tools and Operations
description: Command-line admin tools for operating an mProv Control Center — mash bulk scripts for systems/switch ports/BMC passwords, CSV node import, mPCC database and media backup, node-to-image capture with image-grab.sh, and the repository's OpenWiki automation.
tags: [mprov, macc, mash, backup, csv-import, image-grab, operations]
---

# Admin Tools and Operations

`tools/` holds scripts an admin runs **at the command line against a running mPCC** — per `tools/README.md`, these are *not* uploaded to the mPCC. They complement the dispatchable [image-gen](../script-types/image-generation.md) and [post-boot](../script-types/post-boot.md) scripts.

## mash bulk scripts

`mash` is an mPCC-shipped interactive Django-model shell (`#!/usr/local/bin/mash`) with `connect`, `get`, `create`, `update`, `let`, `foreach`, and template expressions. It returns query results in `MPROV_RESULT`.

- **`add_switch_ports.mash`** — generates `networks.models.SwitchPort` records for a port range (edit the `let` header vars: switchId, networkId, port range). Run before `add_systems.mash` so ports exist to bind NICs to.
- **`add_systems.mash`** — bulk-creates a rack of nodes: for each node number it creates a `systems.models.System`, looks up the matching `SwitchPort` by name, and creates a bootable `NetworkInterface` (computed management IP) plus a `SystemBMC` (computed BMC IP, username/password). All topology values are the `let` block at the top.
- **`set_bmc_pw`** — iterates systems matching a hostname prefix (`get ... hostname=sr` uses partial match) and updates each `SystemBMC.password`.

**`import_systems_csv.sh`** wraps mash for spreadsheet-driven inventory: a CSV with `nodename,mac,ipaddress` plus flags (`-i` interface name, `-m` image; defaults include management/BMC/IB subnets `172.20.`/`172.29.`/`172.21.` and BMC creds `admin/changeme`). It requires the `mash` binary on PATH. Review the default subnet/network-ID assumptions before importing into a non-reference network layout, and change the default BMC password afterward (see `set_bmc_pw`).

## Backup and image operations

- **`backup-mprov.sh`** — full mPCC backup: activates the venv at `/var/www/mprov_control_center/`, runs `manage.py dumpdata` to YAML for the `disklayouts`, `networks`, `scripts`, `systems`, and `osmanagement` apps into `/tmp/mprov_backup/`, copies `media/`, and tars everything to `/tmp/mprov_backup.tar.gz`. Note `media/` and the systems dump contain BMC credentials and API keys — treat the tarball as secret material.
- **`image-grab.sh`** — captures a live node back into an mProv image: reads `imageDir` from `/etc/mprov/plugins/image-update.yaml` on the jobserver host, then `rsync -avzx` from `root@<node>:/` into `$IMAGE_DIR/<image-name>/`, excluding virtual/pseudo paths (`proc/`, `dev/`, `sys/`, `run/`, `tmp/`, `mnt/`, kernels/initramfs). `-d` passes `--delete` for a strict mirror. Intended for "bless a hand-tuned node into a golden image" workflows; pairs with the `image-sync` jobserver module from [install_mprov_jobserver.sh](../script-types/image-generation.md#mprov-jobserver-installs).

## Catalog management

`mpcc_scripts.yaml` at the repo root is the Django fixture that registers the repo's dispatchable scripts into the mPCC `scripts.script` model (see [Architecture Overview](../architecture/overview.md#registration-mpcc_scriptsyaml)). Keeping it in sync with new/renamed scripts in `image-gen/` and `post-boot/` is a manual step.

## Repository automation

`.github/workflows/openwiki-update.yml` runs OpenWiki (`openwiki code --update --print`) nightly to refresh the `openwiki/` docs and open a PR; the AGENTS.md/CLAUDE.md OpenWiki banner blocks are maintained by that run. Generated pages there should not be hand-edited except when explicitly asked.

## Changing this area

- mash scripts hardcode model paths (`systems.models.*`, `networks.models.*`) and reference IDs (network IDs, `created_by=1`, image/model slugs like `compute`, `dell-inc-poweredge-c6320`); verify them against your mPCC DB before running.
- Nothing in `tools/` is idempotency-guarded beyond fstab-style checks elsewhere — rerunning `add_systems.mash` or the CSV import can create duplicates. Test header vars against a scratch mPCC first.
