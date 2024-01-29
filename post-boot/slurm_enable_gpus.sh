#!/bin/bash

# Sets the number of gpus available via the /etc/sysconfig/slurm file
# If no count specified in the config, default to 1

gpus=`cat /tmp/mprov/entity.json | jq -r .config_params | jq -r .gpus`

if [ "$gpus" == "" ]
then
  gpus=1
fi

if [ -f /etc/sysconfig/slurmd ]
then
  . /etc/sysconfig/slurmd
fi

grep -qF 'Gres=gpu' /etc/sysconfig/slurmd
if [ "$?" != "0" ]
then
  # entry not found, add it.
  echo "SLURM=\" --conf Gres=gpu:$gpus\"" >> /etc/sysconfig/slurmd
else
  # entry found, update it.
  cat /etc/sysconfig/slurmd | grep -v "Gres=gpu" > /tmp/slurmd
  echo "SLURM=\" --conf Gres=gpu:$gpus\"" >> /tmp/slurmd
  /bin/mv -f /tmp/slurmd /etc/sysconfig/slurmd
fi
systemctl daemon-reload
systemctl restart slurmd