---
type: Documentation Index
title: "Script Types"
description: "Files and subdirectories in Script Types."
---

# Files

- [Image Generation Scripts](image-generation.md) - The image-gen scripts baked into mProv OS images — mProv jobserver and script-runner/NADS installation, the /data NFS share mount (mprovDataShareHost), Ansible collection/role/playbook execution, root password and SSH setup (deprecated), extra packages, and yq install. Unset config params fall back to documented defaults, covered by the tests/ pytest suite.
- [Post-Boot Scripts](post-boot.md) - The post-boot scripts that configure freshly provisioned mProv nodes — BMC/IPMI setup, local disk formatting, IPv6 link-local registration, Slurm GPU gres and NVIDIA fabric manager/CUDA enablement, kernel mitigations, firewalld, chrony time sync, and the GPFS restore one-off.
