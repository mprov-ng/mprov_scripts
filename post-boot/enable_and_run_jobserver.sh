#!/bin/bash
# Enable and start the secondary jobserver that
# image-gen/install_mprov_jobserver.sh installed in the image, and open its
# webserver port (8080) if firewalld is running.
set -e

if ! systemctl enable --now mprov-jobserver
then
  echo "Error: unable to enable and start mprov-jobserver (did the image run image-gen/install_mprov_jobserver.sh?)" >&2
  exit 1
fi

# some sites turn firewalld off (post-boot/disable-fw-on-compute-nodes.sh).
if systemctl is-active --quiet firewalld
then
  firewall-cmd --zone=public --add-port=8080/tcp --permanent
  firewall-cmd --reload
else
  echo "firewalld isn't running, not opening port 8080."
fi
