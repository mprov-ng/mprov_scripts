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
# Add the NFS mount to fstab
grep -qF '/export/data' /etc/fstab || echo "${shareHost}:/export/data    /data    nfs defaults    0 0" >> /etc/fstab
