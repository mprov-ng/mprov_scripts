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

# a count, or a GPU type and count as Slurm writes them (e.g. a100:4).
if ! [[ "$gpus" =~ ^([A-Za-z0-9_.-]+:)?[0-9]+$ ]]
then
  echo "Error: config param gpus must be a count or <type>:<count>, got '$gpus'" >&2
  exit 1
fi

# Rebuild the file next to the real one, never through a fixed /tmp path a
# local user could plant, and rename it into place only once it is complete.
tmp=$(mktemp "$CONF.XXXXXX")
trap 'rm -f "$tmp"' EXIT

src=/dev/null
if [ -f "$CONF" ]
then
  src=$CONF
  chmod --reference="$CONF" "$tmp"
  chown --reference="$CONF" "$tmp"
else
  chmod 644 "$tmp"
fi

# slurmd.service passes $SLURMD_OPTIONS to slurmd.  Put "--conf Gres=gpu:N"
# (or gpu:<type>:N)
# in the last SLURMD_OPTIONS line (the one that counts), replacing an old
# count and keeping its other options, or add the line.  The SLURM= line
# older versions of this script wrote is dropped.  slurmd takes one --conf,
# so another --conf already there is left for the admin to merge by hand.
gres="--conf Gres=gpu:$gpus"
if ! awk -v gres="$gres" -v sq="'" '
  /^[[:space:]]*SLURM=.*Gres=gpu/ { next }
  { lines[++n] = $0; if ($0 ~ /^[[:space:]]*SLURMD_OPTIONS=/) last = n }
  END {
    for (i = 1; i <= n; i++) {
      l = lines[i]
      if (i == last) {
        v = l
        sub(/^[[:space:]]*SLURMD_OPTIONS=/, "", v)
        q = substr(v, 1, 1)
        if ((q == "\"" || q == sq) && length(v) > 1 && substr(v, length(v), 1) == q)
          v = substr(v, 2, length(v) - 2)
        gsub(/(^|[[:space:]]+)--conf[[:space:]]+"?Gres=gpu:([A-Za-z0-9_.-]+:)?[0-9]+"?/, "", v)
        if (v ~ /--conf/) {
          print "Error: SLURMD_OPTIONS already has a --conf: " v > "/dev/stderr"
          exit 1
        }
        sub(/^[[:space:]]+/, "", v)
        sub(/[[:space:]]+$/, "", v)
        l = "SLURMD_OPTIONS=\"" (v == "" ? "" : v " ") gres "\""
      }
      print l
    }
    if (!last)
      print "SLURMD_OPTIONS=\"" gres "\""
  }' "$src" > "$tmp"
then
  echo "Error: unable to update $CONF, left unchanged" >&2
  exit 1
fi
mv -fT "$tmp" "$CONF"
if command -v restorecon > /dev/null
then
  restorecon "$CONF" || echo "Warn: restorecon $CONF failed" >&2
fi

systemctl daemon-reload
systemctl restart slurmd
