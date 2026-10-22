#!/bin/bash
#
# extra_packages.sh - install a list of extra packages from config-params
# Author: Jason Williams <jasonw@.jhu.edu>
#
# extra_packages is a list of package names, or a space-separated string.


pkgs=`jq -r '.config_params.extra_packages // [] | if type == "array" then map(tostring) | join(" ") else tostring end' /tmp/mprov/entity.json`


if [ "$pkgs" == "" ]
then
  echo "No extra packages."
  exit 0
fi
echo $pkgs
dnf -y install $pkgs