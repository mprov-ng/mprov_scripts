#!/bin/bash

# slurm needs ... a bunch of stuff...
dnf -y --enablerepo=powertools install \
  munge \
  pam \
  json-c \
  libyaml \
  numactl \
  libjwt \
  http-parser \
  hwloc \
  hwloc-plugins \
  pmix \
  ucx \
  lz4 \
  freeipmi \
  rrdtool \
  dbus \
  gtk2 \
  man2html \
  readline \
  libcurl \
  lua \
  cuda-*-11-7 \
  kmod-iser \
  kmod-isert \
  kmod-kernel-mft-mlnx \
  kmod-knem \
  kmod-mlnx-ofa_kernel \
  kmod-srp \
  hcoll \
  ibutils2 \
  infiniband-diags \
  infiniband-diags-compat \
  libibumad \
  libibverbs \
  libibverbs-utils \
  librdmacm \
  librdmacm-utils \
  mft \
  mlnx-ethtool \
  mlnx-iproute2 \
  mlnx-ofa_kernel \
  mlnx-ofa_kernel-devel \
  mlnx-ofed-basic \
  mstflint \
  ofed-scripts \
  python3-pyverbs \
  rdma-core \
  rdma-core-devel \
  sharp \
  ucx-cma \
  ucx-devel \
  ucx-ib \
  ucx-knem

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
