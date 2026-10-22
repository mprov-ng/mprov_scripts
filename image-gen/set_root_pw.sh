#!/bin/bash
#
# set_root_pw.sh - sets the root password to the value from config_params
# Author: Jason Williams <jasonw@.jhu.edu>
#
# Deprecated: set the OS distro's Root Password instead, which the jobserver
# applies when it builds the image.  This script only changes the password if
# the rootpw config param is set.  rootpw is the hash field of /etc/shadow,
# e.g. '$6$<salt>$<hash>'.


rootpw=`jq -r '.config_params.rootpw // empty' /tmp/mprov/entity.json`


if [ "$rootpw" == "" ]
then
  # leave the password the distro set alone.
  echo "No rootpw in the config params, leaving the root password unchanged."
  exit 0
fi

# make a new shadow file
TMPFILE=`mktemp`
cat /etc/shadow | grep -v "^root:" > $TMPFILE
echo "root:${rootpw}:18700:0:99999:7:::" | cat - $TMPFILE  > /etc/shadow

# clean up the tmp file
rm -f $TMPFILE

