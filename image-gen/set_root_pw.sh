#!/bin/bash
#
# set_root_pw.sh - sets the root password to the value from config_params
# Author: Jason Williams <jasonw@.jhu.edu>


rootpw=`cat /tmp/mprov/entity.json | jq -r .config_params | jq -r .rootpw`


if [ "$rootpw" == "" ]
then
  echo "Error: No rootpw specified, exiting." >&2
  exit 1
fi

# make a new shadow file
TMPFILE=`mktemp`
cat /etc/shadow | grep -v "^root:" > $TMPFILE
echo "root:${rootpw}:18700:0:99999:7:::" | cat - $TMPFILE  > /etc/shadow

# clean up the tmp file
rm -f $TMPFILE

