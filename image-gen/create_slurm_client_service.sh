#!/bin/bash

# add a munge user
useradd -u 449 munge

# add a slurm user
useradd -u 450 slurm

# slurm needs ... a bunch of stuff...
dnf -y  install --skip-broken --nobest \
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
rm -rf /etc/munge
ln -s /opt/mprov/etc/munge/ /etc/munge

# setup the slurm config
rm -rf /etc/slurm/slurm.conf
mkdir -p /etc/slurm/ # just in case.
ln -s /opt/mprov/etc/slurm/slurm.conf /etc/slurm/slurm.conf

# create the slurm service file.
cat << EOF > /usr/lib/systemd/system/slurmd.service
[Unit]
Description=Slurm node daemon
After=munge.service network.target remote-fs.target
Requires=munge.service

[Service]
Type=simple
EnvironmentFile=-/etc/sysconfig/slurmd
ExecStart=/opt/mprov/sbin/slurmd -D -Z --conf "Feature=compute" \$SLURM
ExecReload=/bin/kill -HUP \$MAINPID
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
systemctl start slurmd

cat << EOF > /etc/systemd/system/munge.service
[Unit]
Description=MUNGE authentication service
Documentation=man:munged(8)
After=remote-fs.target
Requires=remote-fs.target
After=time-sync.target

[Service]
Type=forking
ExecStart=/usr/sbin/munged
PIDFile=/var/run/munge/munged.pid
User=munge
Group=munge
Restart=on-abort

[Install]
WantedBy=multi-user.target
EOF
systemctl enable munge
systemctl start munge
