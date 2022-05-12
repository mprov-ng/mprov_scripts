#!/bin/bash
#
# set_root_pw.sh - sets the root password to the same password that is on the image-update jobserver.
# Author: Jason Williams <jasonw@.jhu.edu>

# get the root pw entry from /etc/shadow.
ROOT_PW=`cat /etc/shadow | grep "^root:"`

# Did we get a path to write the new pw to?
if [ "$1" == "" ]
then
  echo "Error: You must tell me where the target shadow file is." >&2
  exit 1
fi

# make a new shadow file
TMPFILE=`mktemp`
cat $1 | grep -v "^root:" > $TMPFILE
echo $ROOT_PW | cat - $TMPFILE  >> $1

# clean up the tmp file
rm -f $TMPFILE

