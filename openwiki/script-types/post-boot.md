---
type: Reference
title: Post-Boot Scripts
description: The post-boot scripts that configure freshly provisioned mProv nodes — BMC/IPMI setup, local disk formatting, IPv6 link-local registration, Slurm GPU gres and NVIDIA fabric manager/CUDA enablement, kernel mitigations, firewalld, chrony time sync, and the GPFS restore one-off.
tags: [mprov, post-boot, bmc, ipmi, slurm, gpu, cuda, disk-layout, hpc]
---

# Post-Boot Scripts

Scripts in `post-boot/` run on the physical node after it boots its provisioned image. They are dispatched by the `script-runner` jobserver plugin baked in by [install_mprov_script-runner.sh](image-generation.md#mprov-jobserver-installs), and receive `/tmp/mprov/entity.json` context per the [entity.json contract](../architecture/overview.md#the-entity-json-contract). Python scripts subclass `MProvScript` to get an authenticated mPCC REST session.

## Node identity and hardware configuration

- **`setupBMC.py`** (`MProvScript`) — self-service BMC configuration. The node looks itself up in the mPCC (`GET systems/?hostname=...`), fetches its `SystemBMC` record (`GET systembmcs/?system=<id>&detail`), loads `ipmi_devintf`, and drives `ipmitool lan set` to apply the static IP/netmask/gateway from mPCC (includes CIDR→netmask and MAC→IPv6 link-local helpers). Git history shows iteration on filtering the mPCC response to the exact requested hostname.
- **`register_IPv6_ll.py`** (`MProvScript`, requires `mprov-jobserver>=0.0.34`) — enumerates the node's NICs, matches them by MAC against mPCC `networkinterfaces`, and registers each interface's IPv6 link-local address back with the mPCC. This is how the controller finds nodes on the provisioning fabric before DNS exists.
- **`localTempDisk.py`** — standalone stateless disk formatter (predates the `MProvScript` base; loads `/etc/mprov/jobserver.yaml` itself). Fetches the node's disklayout from the mPCC, then builds partition tables (`parted`), filesystems, and `/etc/fstab` entries for local scratch/temp disks.

## Slurm and GPU configuration

- **`slurm_enable_gpus.sh`** — reads `config_params.gpus` (default `1`) and writes `SLURM=" --conf Gres=gpu:$gpus"` into `/etc/sysconfig/slurmd` (replace-if-present, idempotent), then restarts `slurmd`. The variable is consumed by the `EnvironmentFile` line of the slurmd unit created in [create_slurm_client_service.sh](image-generation.md#slurm-and-munge-client-services).
- **`ansible-playbooks/nvidia-fm-install.yaml`** — example Ansible playbook run through [run_ansible.sh](image-generation.md#ansible-runner): installs `@nvidia-driver:latest-dkms/fm` and `cuda-toolkit`, enables/starts `nvidia-fabricmanager` (errors ignored, so it is safe on non-NVSwitch nodes).
- **`add-cuda-to-path.sh`** — writes `/etc/profile.d/zz-cuda.sh` exporting `CUDA_HOME`, `PATH`, and `LD_LIBRARY_PATH` for `/usr/local/cuda`.

## Performance and security posture

- **`add-mitigations-off.sh`** — uses `grubby` to add `mitigations=off` to all kernel entries *and* the default for future kernels (Rocky 9.6 target). Strict `set -euo pipefail`, root check, idempotent. Documented tradeoff: CPU vulnerability mitigations disabled for HPC performance — an explicit cluster-policy choice, not an oversight.
- **`disable-fw-on-compute-nodes.sh`** — `systemctl disable --now firewalld` on compute nodes (network trust handled by fabric segmentation).

## Time, storage, and services

- **`install_chrony_and_timesync.sh`** — installs chrony and forces a one-shot sync (`chronyd -q`). Needed before Kerberos/munge-adjacent auth and Slurm scheduling behave.
- **`enable_and_run_jobserver.sh`** — enables/starts the `mprov-jobserver` service installed by [install_mprov_jobserver.sh](image-generation.md#mprov-jobserver-installs) and opens firewall port 8080 (the image/webserver distribution port).
- **`gpfs_mmsdrrestore.sh`** — environment-specific one-off: restores GPFS MMDR from a hardcoded manager node (`mgmt03.ib.cluster`) and starts the cluster. Treat as a template, not a general script.

## Deprecated — do not use

- **`nads.sh`** — marked DEPRECATED, exits 0 immediately. Superseded by the `nads` job module installed via [install_mprov_script-runner.sh](image-generation.md#mprov-jobserver-installs).
- **`NetworkManager-1.40-COPR.sh`** — exits 0; the COPR workaround for NetworkManager 1.40 is no longer needed.

## Changing this area

- Python post-boot scripts depend on the `mprov_jobserver`/`MProvScript` package API and the mPCC REST endpoints (`systems/`, `systembmcs/`, `networkinterfaces/`) — endpoint or package changes ripple here (`localTempDisk.py` additionally depends on `parted`, `sh`, and a working `jobserver.yaml`).
- Idempotency matters: post-boot scripts may re-run on every boot for some entities; follow the existing grep-before-write patterns (`slurm_enable_gpus.sh`, `add-mitigations-off.sh`).
- BMC changes touch physical out-of-band networking — validate `ipmitool` sequencing against a real node before rolling out; a wrong static IP bricks console access to the node.
