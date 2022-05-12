#!/bin/bash
#
# extra_packages.sh - install a list of extra packages from config-params
# Author: Jason Williams <jasonw@.jhu.edu>


pkgs=`cat /tmp/mprov/entity.json | jq -r .config_params | jq -r '.extra_packages|join(" " )'


if [ "$pkgs" == "" ]
then
  echo "No extra packages."
  exit 0
fi
echo $pkgs
dnf -y install $pkgs