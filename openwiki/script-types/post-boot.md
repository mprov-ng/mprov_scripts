---
type: Reference
title: Post-Boot Scripts
description: The post-boot scripts that configure freshly provisioned mProv nodes — BMC/IPMI setup, local disk formatting, IPv6 link-local registration, Slurm GPU gres and NVIDIA fabric manager/CUDA enablement, kernel mitigations, firewalld, chrony time sync, and the GPFS restore one-off.
tags: [mprov, post-boot, bmc, ipmi, slurm, gpu, cuda, disk-layout, hpc]
---

# Post-Boot Scripts

Scripts in `post-boot/` run on the physical node after it boots its provisioned image. They are dispatched by the `script-runner` jobserver plugin baked in by [install_mprov_script-runner.sh](image-generation.md#mprov-jobserver-installs), and receive `/tmp/mprov/entity.json` context per the [entity.json contract](../architecture/overview.md#the-entityjson-contract). Python scripts subclass `MProvScript` to get an authenticated mPCC REST session.

## Node identity and hardware configuration

- **`setupBMC.py`** (`MProvScript`) — self-service BMC configuration. The node looks itself up in the mPCC (`GET systems/?hostname=...`), fetches its `SystemBMC` record (`GET systembmcs/?system=<id>&detail`), loads `ipmi_devintf`, and drives `ipmitool lan set` to apply the static IP/netmask/gateway/VLAN from mPCC (includes CIDR→netmask and MAC→IPv6 link-local helpers). Notable behaviors: each `ipmitool` call runs through a helper that prints its stderr and aborts the script on the first failure, so a half-configured BMC doesn't go unnoticed; the netmask is set once without the trailing space it used to carry, and the default gateway is skipped when the mPCC network has none; the user password is set via `ipmitool exec` reading a root-only `mktemp` batch file — never on the command line, where any local user could read it — which means passwords containing `"`, `#`, `~`, or non-printable characters are refused with an actionable error. A missing `SystemBMC` record is a hard error (the MAC/IPv6-LL registration needs something to PATCH against); a record without a password warns and leaves the BMC password alone. `tests/test_setup_bmc.py` covers these paths.
- **`register_IPv6_ll.py`** (`MProvScript`, requires `mprov-jobserver>=0.0.34`) — enumerates the node's NICs, matches them by MAC against mPCC `networkinterfaces`, and registers each interface's IPv6 link-local address back with the mPCC via a PATCH that sends **only** the `ipv6ll` field, so nothing else on the interface record is overwritten with previously-read values. This is how the controller finds nodes on the provisioning fabric before DNS exists.
- **`localTempDisk.py`** — standalone stateless disk formatter (`main()`: `buildDisks()` → `buildRAIDs()` → `writeFstab()`). Config comes from `entity.json` next to the script when the script runner provides it, falling back to `/etc/mprov/script-runner.yaml` then `/etc/mprov/jobserver.yaml` for manual runs. It fetches the node's disklayout from the mPCC, then builds GPT partition tables (`parted`), filesystems, and mounts for local scratch/temp disks. Behaviors to know before changing it:
  - **In-use disk guard**: before touching anything it checks `/proc/mounts`, `/proc/swaps`, and `/sys/class/block/*/holders` (via kernel names resolved with `realpath`, so `/dev/disk/by-id/...` links count) and refuses to repartition any layout disk that is mounted, active swap, or held by md/LVM — printing every problem, then exiting. On a stateful node this protects the root disk.
  - **Partition naming**: each new partition's device is computed the kernel's way — the disk's kernel name plus the number, with a `p` separator when the name ends in a digit (`nvme0n1p1`, `md0p1`) — then `udevadm settle` plus a polling wait guarantees the device exists before `mkfs`/`mkswap` use it. RAID members are looked up by these real devices, and a missing member partition is a fatal error.
  - **Filesystems**: `mkfs` is passed `-t <type>` first (mkfs stops parsing options at the first unknown one, so a late `-t` was silently ignored); `linux-swap` partitions become `linux-swap(v1)` and are activated with `swapon UUID=...` instead of `mount` (their fstab mount point is `none`).
  - **fstab is written once, at the end**: entries accumulate in memory and `writeFstab()` swaps in a whole new `/etc/fstab` via `os.replace` (old file kept as `/etc/fstab.mprov-bak`), so a failed run never leaves a broken fstab. Its entries live inside a `# BEGIN/END mprov localTempDisk.py` marker block that a rerun replaces — needed because swap lines have no mount point to match on.
  - Watch the mPCC disk-layout serializer: `pdisk['mount']` is `None` for every disk whose `dtype` is not `mdrd`, so mount the partition's own `part['mount']` (a past regression crashed `mount(8)` on the string `'None'` for non-RAID disks).
  - Test coverage is split across `tests/test_local_temp_disk.py`, `tests/test_local_temp_disk_devices.py`, `tests/test_local_temp_disk_in_use.py`, and `tests/test_local_temp_disk_config.py`.

## Slurm and GPU configuration

- **`slurm_enable_gpus.sh`** — reads `config_params.gpus` (default `1` when unset/null) and writes `SLURM=" --conf Gres=gpu:$gpus"` into `/etc/sysconfig/slurmd` (replace-if-present, idempotent), then restarts `slurmd`. The variable is consumed via the slurmd unit's `EnvironmentFile` hook by whatever Slurm client setup the cluster uses.
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

- Python post-boot scripts depend on the `mprov_jobserver`/`MProvScript` package API and the mPCC REST endpoints (`systems/`, `systembmcs/`, `networkinterfaces/`) — endpoint or package changes ripple here (`localTempDisk.py` additionally depends on `parted`, `sh`, `udevadm`, and either a nearby `entity.json` or a working `/etc/mprov/script-runner.yaml`/`jobserver.yaml`).
- Idempotency matters: post-boot scripts may re-run on every boot for some entities; follow the existing grep-before-write patterns (`slurm_enable_gpus.sh`, `add-mitigations-off.sh`).
- BMC changes touch physical out-of-band networking — validate `ipmitool` sequencing against a real node before rolling out; a wrong static IP bricks console access to the node.
