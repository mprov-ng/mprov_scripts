---
type: Reference
title: Image Generation Scripts
description: The image-gen scripts baked into mProv OS images — mProv jobserver and script-runner/NADS installation, NFS share mounts, Slurm/Munge client services, Ansible collection/role/playbook execution, root password and SSH setup, extra packages, and yq install.
tags: [mprov, image-gen, jobserver, slurm, munge, ansible, nfs]
---

# Image Generation Scripts

Scripts in `image-gen/` run inside the mPCC image builder while an OS image (Rocky/RHEL-family, `dnf`-based) is generated. Their output is baked into the image: services, mounts, users, and config symlinks. They read context from `/tmp/mprov/entity.json` per the [entity.json contract](../architecture/overview.md#the-entity-json-contract).

## mProv jobserver installs

Two scripts install the pip package `mprov_jobserver` in different roles, each writing configs under `/etc/mprov/` (chmod 600 because they embed the `apikey`) and a systemd unit:

- **`install_mprov_jobserver.sh`** — a *secondary/edge jobserver* for image and repo distribution. Job modules: `image-sync` (image dir `/export/images`), `mprov-webserver`, `image-delete`. This is what enables the mPCC's "redirection" to distributed jobservers. `post-boot/enable_and_run_jobserver.sh` later enables the service and opens firewall port 8080.
- **`install_mprov_script-runner.sh`** — installs the `script-runner` module that executes image-gen and post-boot scripts, or the `nads` module when `entity.json`'s `slug` is `nads` (the node auto-detection image). The service is `Type=oneshot` with `mprov_jobserver -d -r -s -b -c /etc/mprov/<mod>.yaml` and is enabled (not started) so it runs at boot — this is the bootstrap that lets post-boot scripts run at all.

## NFS share mounts

- **`setup_mprov_share_mount.sh`** — parses the mPCC host out of `mprovURL`, installs `nfs-utils`, and adds a fstab entry mounting `${mpccHost}:/export/mprov` at `/opt/mprov`; also drops `/etc/profile.d/99-mprov.sh` to put `/opt/mprov/bin` and (for root) `/opt/mprov/sbin` on PATH.
- **`setup_data_share_mount.sh`** — same pattern for `${mpccHost}:/export/data` at `/data`.

Both are idempotent (grep-guarded fstab appends). The `/opt/mprov` mount is the shared-config plane consumed by the Slurm client setup below.

## Slurm and Munge client services

`create_slurm_client_service.sh` turns an image into a Slurm compute client:

1. Creates fixed-UID service users: `munge` (uid 449) and `slurm` (uid 450) — UIDs must match the rest of the cluster.
2. Installs a large `dnf --skip-broken --nobest` package set: Slurm runtime deps (munge, pmix, hwloc, numactl…) plus the Mellanox/InfiniBand stack (OFED, rdma-core, ucx, mft, sharp).
3. Replaces local config with cluster config via symlinks: `/etc/munge → /opt/mprov/etc/munge/` (shared munge key) and `/etc/slurm/slurm.conf → /opt/mprov/etc/slurm/slurm.conf`.
4. Writes `slurmd.service` which starts `/opt/mprov/sbin/slurmd -D -Z --conf "Feature=compute"` (stateless `-Z` dynamic node registration) with `EnvironmentFile=-/etc/sysconfig/slurmd`, and `munge.service` ordered after `remote-fs.target` (it needs the NFS-mounted key).

The `--conf` hook in `EnvironmentFile` is how [post-boot/slurm_enable_gpus.sh](post-boot.md#slurm-and-gpu-configuration) injects `Gres=gpu:N` later.

## Ansible runner

`run_ansible.sh` (registered for **both** script types in `mpcc_scripts.yaml`) lets users extend provisioning with Ansible instead of shell:

- Installs `ansible` from EPEL, detects run mode from `entity.json` (`slug` present → image-gen, else post-boot).
- Extracts `config_params` from `entity.json` into `variables.json` as the extra-vars file.
- Collects Ansible **collections**, **roles**, and **playbooks** from three mPCC levels via `jq` — systemgroups, the entity itself, and the OS distro — filtered to entries whose `scriptType.slug` starts with the run mode.
- Installs collections/roles with `ansible-galaxy`, runs each role against localhost through a generated `/tmp/role.yaml` wrapper, then `wget`s and runs each playbook locally. Cleans up `/tmp/mprov/ansible/` afterward.

Note the roles are executed in download order with implicit gathering; playbook URLs are fetched with `wget -N`, so mPCC-hosted playbook URLs must be reachable from the node/image build.

## Identity and package scripts

- **`set_root_pw.sh`** — rewrites the `root:` line in `/etc/shadow` from `config_params.rootpw` (expects a pre-hashed value; fails hard if absent).
- **`setup_ssh.sh`** — `ssh-keygen -A`, then **replaces** `/root/.ssh/authorized_keys` with `config_params.rootsshkey` (single-key by design).
- **`extra_packages.sh`** — installs the space-joined `config_params.extra_packages` list.
- **`install_yq.sh`** — installs Go, then `github.com/mikefarah/yq/v4` into `/usr/local/bin` (for scripts that parse YAML at runtime).
- **`ansible-playbooks/`-style payloads**: `post-boot/ansible-playbooks/nvidia-fm-install.yaml` is an example playbook consumed by the runner (see [Post-Boot Scripts](post-boot.md#gpu-and-hpc-enablement)).

## Key source files

`image-gen/install_mprov_jobserver.sh`, `image-gen/install_mprov_script-runner.sh`, `image-gen/setup_mprov_share_mount.sh`, `image-gen/setup_data_share_mount.sh`, `image-gen/create_slurm_client_service.sh`, `image-gen/run_ansible.sh`, `image-gen/set_root_pw.sh`, `image-gen/setup_ssh.sh`, `image-gen/extra_packages.sh`, `image-gen/install_yq.sh`, `mpcc_scripts.yaml`.

## Changing this area

- Fixed UIDs (449/450), the `/opt/mprov` symlink targets, and the slurmd `-Z` dynamic-node mode must stay consistent with the cluster controller; changing one side without the other breaks Slurm auth/registration.
- Jobserver configs embed the mPCC `apikey` — never log or copy `/etc/mprov/*.yaml` contents into docs or issues.
