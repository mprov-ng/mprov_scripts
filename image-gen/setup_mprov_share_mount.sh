#!/bin/bash

mprovURL=`cat entity.json | jq .mprovURL -r`
mpccHost=`echo "$mprovURL" | awk -F/ '{print $3}'`

dnf -y install nfs-utils

mkdir -p /opt/mprov
# Add bind mount to fstab
grep -qF '/export/mprov' /etc/fstab || echo "${mpccHost}:/export/mprov    /opt/mprov    nfs defaults    0 0" >> /etc/fstab

cat << EOF > /etc/profile.d/99-mprov.sh
export PATH=/opt/mprov/bin:$PATH

if [ "$USER" == "root" ]
then
  export PATH=/opt/sbin/:$PATH
fi
EOF
