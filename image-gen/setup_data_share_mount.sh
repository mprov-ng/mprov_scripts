#!/bin/bash
cd /tmp/mprov/
mprovURL=`cat entity.json | jq .mprovURL -r`
mpccHost=`echo "$mprovURL" | awk -F/ '{print $3}'`

dnf -y install nfs-utils

mkdir -p /data
# Add bind mount to fstab
grep -qF '/export/data' /etc/fstab || echo "${mpccHost}:/export/data    /data    nfs defaults    0 0" >> /etc/fstab
