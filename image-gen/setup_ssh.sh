#!/bin/bash
#
# Deprecated: set the OS distro's Root SSH Keys instead, which the jobserver
# writes when it builds the image.  This script only replaces root's
# authorized_keys if the rootsshkey config param is set.

# generate the SSH keys.
/usr/bin/ssh-keygen -A

# copy the SSH keys out of the configparams.
rootsshkey=`jq -r '.config_params.rootsshkey // empty' /tmp/mprov/entity.json`


if [ "$rootsshkey" == "" ]
then
  # leave the keys the distro set alone.
  echo "No rootsshkey in the config params, leaving root's authorized_keys unchanged."
  exit 0
fi

# this should be the only key in root's authorized keys.
mkdir -p /root/.ssh/
chmod 700 /root/.ssh/
echo "$rootsshkey" > /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
exit 0
