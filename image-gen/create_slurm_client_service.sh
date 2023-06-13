#!/bin/bash

# slurm needs munge
dnf -y install munge

# munge key should be in /opt/mprov/etc/munge
rm -rf /etc/munge/munge.key
ln -s /opt/mprov/etc/munge/munge.key /etc/munge/munge.key

# enable munge
systemctl enable munge

# setup the slurm config
rm -rf /etc/slurm/slurm.conf
mkdir -p /etc/slurm/ # just in case.
ln -s /opt/mprov/etc/slurm/slurm.conf /etc/slurm/slurm.conf

# create the slurm service file.
cat << EOF > /usr/lib/systemd/system/slurmd.service
[Unit]
Description=Slurm node daemon
After=munge.service network.target remote-fs.target

[Service]
Type=simple
EnvironmentFile=-/opt/mprov/etc/sysconfig/slurmd
ExecStart=/opt/mprov/sbin/slurmd -D $SLURMD_OPTIONS
ExecReload=/bin/kill -HUP $MAINPID
KillMode=process
LimitNOFILE=131072
LimitMEMLOCK=infinity
LimitSTACK=infinity
Delegate=yes
TasksMax=infinity

[Install]
WantedBy=multi-user.target
EOF

systemctl enable slurmd
