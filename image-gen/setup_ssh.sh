#!/bin/bash

#TODO: Need a way to copy the SSH keys out of the configparams.
rootsshkey=`cat /tmp/mprov/entity.json | jq -r .config_params | jq -r .rootsshkey`


if [ "$rootsshkey" == "" ]
then
  echo "Error: No rootsshkey specified, exiting." >&2
  exit 1
fi

# this should be the only key in root's authorized keys.
mkdir -p /root/.ssh/
chmod 700 /root/.ssh/
echo "$rootsshkey" > /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
exit 0
