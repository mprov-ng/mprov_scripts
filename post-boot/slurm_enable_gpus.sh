#!/bin/bash

# Sets the number of gpus available via the /etc/sysconfig/slurm file
# If no count specified in the config, default to 1

gpus=`jq -r '.config_params.gpus // empty' /tmp/mprov/entity.json`

if [ "$gpus" == "" ]
then
  gpus=1
fi

if [ -f /etc/sysconfig/slurmd ]
then
  . /etc/sysconfig/slurmd
fi

grep -qsF 'Gres=gpu' /etc/sysconfig/slurmd
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