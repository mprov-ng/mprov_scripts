#!/bin/bash
#
# setup_data_share_mount.sh - NFS mount /export/data from the mPCC on /data
#
# Config params:
#   mprovDataShareHost - host name or IP to mount /export/data from, e.g. the
#                        mPCC's name on an IB or other cluster network.  Can be
#                        set on the OS distro, a system group or the image.
#                        Defaults to the mPCC host in mprovURL.
cd /tmp/mprov/
mprovURL=`cat entity.json | jq .mprovURL -r`
mpccHost=`echo "$mprovURL" | awk -F/ '{print $3}'`
# drop any :port, which isn't part of an NFS server name.
mpccHost=`echo "$mpccHost" | sed -E 's/:[0-9]+$//'`

shareHost=`jq -r '.config_params.mprovDataShareHost // empty' entity.json`
shareHost=${shareHost:-$mpccHost}

dnf -y install nfs-utils

mkdir -p /data
# Add the NFS mount to fstab, unless an entry already mounts something on
# /data.  Match the mount point field: a comment or another export that only
# mentions /export/data doesn't count (#21).
if awk '$1 !~ /^#/ && $2 == "/data" { found = 1 } END { exit !found }' /etc/fstab 2>/dev/null; then
  echo "/etc/fstab already mounts /data, leaving it alone."
else
  # don't glue our entry onto a last line with no newline (#21).
  if [ -s /etc/fstab ] && [ -n "$(tail -c1 /etc/fstab)" ]; then
    echo >> /etc/fstab
  fi
  echo "${shareHost}:/export/data    /data    nfs defaults    0 0" >> /etc/fstab
fi
