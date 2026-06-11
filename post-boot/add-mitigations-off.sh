#!/bin/bash
#
# add-mitigations-off.sh
# Add "mitigations=off" to the kernel boot command line for Rocky Linux 9.6
# Uses grubby to safely modify kernel boot arguments.
#

set -euo pipefail

KERNEL_PARAM="mitigations=off"

# Check for root
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: This script must be run as root (use sudo)."
  exit 1
fi

# Check that grubby is available
if ! command -v grubby &>/dev/null; then
  echo "ERROR: 'grubby' not found. Install it with: dnf install grubby"
  exit 1
fi

# Check if the parameter is already present on any kernel
if grubby --info all | grep -q "args=.*${KERNEL_PARAM}"; then
  exit 0
fi

# Add the argument to ALL existing kernel entries
if ! grubby --update-kernel=ALL --args="$KERNEL_PARAM"; then
  echo "ERROR: Failed to add '$KERNEL_PARAM' to kernel entries."
  exit 1
fi

# Set it as the default for future kernel installations
if ! grubby --update-kernel=DEFAULT --args="$KERNEL_PARAM"; then
  echo "ERROR: Failed to set '$KERNEL_PARAM' as default for future kernels."
  exit 1
fi
