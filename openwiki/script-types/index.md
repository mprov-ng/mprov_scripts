---
type: Documentation Index
title: "Script Types"
description: "Files and subdirectories in Script Types."
---

# Files

- [Image Generation Scripts](image-generation.md) - The image-gen scripts baked into mProv OS images — mProv jobserver and script-runner/NADS installation, the /data NFS share mount (mprovDataShareHost), Ansible collection/role/playbook execution, extra packages, and pinned yq install. Scripts guard their entity.json read up front, unset config params fall back to documented defaults, and the tests/ pytest suite enforces both.
- [Post-Boot Scripts](post-boot.md) - The post-boot scripts that configure freshly provisioned mProv nodes — BMC/IPMI setup, local disk formatting, IPv6 link-local registration, Slurm GPU gres (SLURMD_OPTIONS --conf) and NVIDIA CUDA PATH setup, kernel mitigations, firewalld, and chrony time sync.
