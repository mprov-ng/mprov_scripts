#!/bin/bash

# make sure golang is installed.
rpm -qa | grep golang > /dev/null 2>&1
if [ "$?" != "0" ]
then  
  dnf -y install golang
fi

# grab yq via go.
go install github.com/mikefarah/yq/v4@latest