---
type: Reference
title: Image Generation Scripts
description: The image-gen scripts baked into mProv OS images — mProv jobserver and script-runner/NADS installation, the /data NFS share mount (mprovDataShareHost), Ansible collection/role/playbook execution, root password and SSH setup (deprecated), extra packages, and yq install. Unset config params fall back to documented defaults, covered by the tests/ pytest suite.
tags: [mprov, image-gen, jobserver, slurm, munge, ansible, nfs]
---

# Image Generation Scripts

Scripts in `image-gen/` run inside the mPCC image builder while an OS image (Rocky/RHEL-family, `dnf`-based) is generated. Their output is baked into the image: services, mounts, users, and config symlinks. They read context from `/tmp/mprov/entity.json` per the [entity.json contract](../architecture/overview.md#the-entityjson-contract).

## mProv jobserver installs

Two scripts install the pip package `mprov_jobserver` in different roles, each writing configs under `/etc/mprov/` (chmod 600 because they embed the `apikey`) and a systemd unit:

- **`install_mprov_jobserver.sh`** — a *secondary/edge jobserver* for image and repo distribution. Job modules: `image-sync` (image dir `/export/images`), `mprov-webserver`, `image-delete`. This is what enables the mPCC's "redirection" to distributed jobservers. `post-boot/enable_and_run_jobserver.sh` later enables the service and opens firewall port 8080.
- **`install_mprov_script-runner.sh`** — installs the `script-runner` module that executes image-gen and post-boot scripts, or the `nads` module when `entity.json`'s `slug` is `nads` (the node auto-detection image). The service is `Type=oneshot` with `mprov_jobserver -d -r -s -b -c /etc/mprov/<mod>.yaml` and is enabled (not started) so it runs at boot — this is the bootstrap that lets post-boot scripts run at all.

## NFS data share mount

- **`setup_data_share_mount.sh`** — installs `nfs-utils` and adds a grep-guarded (idempotent) fstab entry mounting `${shareHost}:/export/data` at `/data`. The NFS server is chosen by `config_params.mprovDataShareHost` — settable on the OS distro, a system group, or the image, e.g. the mPCC's name on an IB or other cluster network — and defaults to the mPCC host parsed out of `mprovURL` (any `:port` is stripped, since it isn't part of an NFS server name).

`setup_mprov_share_mount.sh` (mounting `${mpccHost}:/export/mprov` at `/opt/mprov`) and `create_slurm_client_service.sh` were **removed** because they depended on the mPCC's `/export/mprov` share; Slurm client setup is no longer part of this repo's image-gen catalog.

## Ansible runner

`run_ansible.sh` (registered for **both** script types in `mpcc_scripts.yaml`) lets users extend provisioning with Ansible instead of shell:

- Installs `ansible` from EPEL, detects run mode from `entity.json` (`slug` present → image-gen, else post-boot).
- Extracts `config_params` from `entity.json` into `variables.json` as the extra-vars file.
- Selects the Ansible **collections**, **roles**, and **playbooks** to run via a single `ansible_list` `jq` helper. When the mPCC serves merged lists — newer mPCCs (mprov-ng/mprov_control_center#269) put the inherited slugs (distro → system groups → image → system, deduped, in inheritance order) into `config.ansiblecollections` / `config.ansibleroles` / `config.ansibleplaybooks` — the script walks those slugs and looks each one up in `entity.json` for its URL and type; a slug present in `config` but absent from `entity.json` is warned about on stderr and skipped. Against an older mPCC (no `config` lists) it falls back to collecting the lists from the entity's layers — systemgroups, the entity itself, and the OS distro. Both paths then filter to entries whose `scriptType.slug` starts with the run mode, and tolerate the list keys being missing at any level.
- Installs collections/roles with `ansible-galaxy`, runs each role against localhost through a generated `/tmp/role.yaml` wrapper, then `wget`s and runs each playbook locally **in the order the list provides** (only playbooks that actually downloaded). Cleans up `/tmp/mprov/ansible/` afterward.

Note the roles are executed in list order with implicit gathering; playbook URLs are fetched with `wget -N`, so mPCC-hosted playbook URLs must be reachable from the node/image build. `tests/test_run_ansible.py` exercises both selection paths (merged `config` slugs and layer fallback) with the ansible commands stubbed.

## Identity and package scripts

- **`set_root_pw.sh`** (deprecated — prefer the OS distro's Root Password, which the jobserver applies at image build) — rewrites the `root:` line in `/etc/shadow` only when `config_params.rootpw` (a pre-hashed `/etc/shadow` value) is set; otherwise it exits 0 leaving the distro password alone.
- **`setup_ssh.sh`** (deprecated — prefer the OS distro's Root SSH Keys) — `ssh-keygen -A`, then **replaces** `/root/.ssh/authorized_keys` with `config_params.rootsshkey` (single-key by design) only when set; otherwise the distro keys are left unchanged.
- **`extra_packages.sh`** — installs `config_params.extra_packages`, accepted either as a JSON array or a space-separated string; a missing/null/empty value installs nothing.
- **`install_yq.sh`** — installs Go, then `github.com/mikefarah/yq/v4` into `/usr/local/bin` (for scripts that parse YAML at runtime).
- **`ansible-playbooks/`-style payloads**: `post-boot/ansible-playbooks/nvidia-fm-install.yaml` is an example playbook consumed by the runner (see [Post-Boot Scripts](post-boot.md#slurm-and-gpu-configuration)).

## Key source files

`image-gen/install_mprov_jobserver.sh`, `image-gen/install_mprov_script-runner.sh`, `image-gen/setup_data_share_mount.sh`, `image-gen/run_ansible.sh`, `image-gen/set_root_pw.sh`, `image-gen/setup_ssh.sh`, `image-gen/extra_packages.sh`, `image-gen/install_yq.sh`, `mpcc_scripts.yaml`.

## Changing this area

- Config-param handling is covered by the pytest suite in `tests/` (see [Architecture Overview](../architecture/overview.md#the-entityjson-contract)): an unset or `null` param must take the script's documented default, never the string `null`. Run `python -m pytest tests` (needs Docker) when touching scripts that read `config_params`.
- Jobserver configs embed the mPCC `apikey` — never log or copy `/etc/mprov/*.yaml` contents into docs or issues.
