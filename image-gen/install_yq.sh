#!/bin/bash

# make sure golang is installed.
GORPM=`rpm -qa | grep golang`
if [ "GORPM" == "" ]
then  
  dnf -y install golang
fi

# grab yq via go.
go install github.com/mikefarah/yq/v4@latest