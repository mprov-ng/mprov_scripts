#!/bin/bash

# Sets the number of gpus available via the /etc/sysconfig/slurm file
# If no count specified in the config, default to 1
set -e

CONF=/etc/sysconfig/slurmd

gpus=`jq -r '.config_params.gpus // empty' /tmp/mprov/entity.json`

if [ "$gpus" == "" ]
then
  gpus=1
fi

if ! [[ "$gpus" =~ ^[0-9]+$ ]]
then
  echo "Error: config param gpus must be a number, got '$gpus'" >&2
  exit 1
fi

# Rebuild the file next to the real one, never through a fixed /tmp path a
# local user could plant, and rename it into place only once it is complete.
tmp=$(mktemp "$CONF.XXXXXX")
trap 'rm -f "$tmp"' EXIT

if [ -f "$CONF" ]
then
  # drop any old entry, keep everything else.
  grep -vF 'Gres=gpu' "$CONF" > "$tmp" || [ $? -eq 1 ]
  chmod --reference="$CONF" "$tmp"
  chown --reference="$CONF" "$tmp"
else
  chmod 644 "$tmp"
fi
echo "SLURM=\" --conf Gres=gpu:$gpus\"" >> "$tmp"
mv -fT "$tmp" "$CONF"
if command -v restorecon > /dev/null
then
  restorecon "$CONF" || echo "Warn: restorecon $CONF failed" >&2
fi

systemctl daemon-reload
systemctl restart slurmd
