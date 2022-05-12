#!/bin/bash
#
# extra_packages.sh - install a list of extra packages from config-params
# Author: Jason Williams <jasonw@.jhu.edu>


pkgs=`cat /tmp/mprov/entity.json | jq -r .config_params | yq --unwrapScalar  .extra_packages  | sed -e 's/\s*\-\s*//'


if [ "$pkgs" == "" ]
then
  echo "No extra packages."
  exit 0
fi

dnf -y install $pkgs