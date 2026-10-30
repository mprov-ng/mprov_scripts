#!/bin/bash
# Install yq (https://github.com/mikefarah/yq) into /usr/local/bin.
#
# yq is pinned so image builds are reproducible (#24); go install checks
# the module against the Go checksum database.  EPEL has a yq package for
# Rocky 9 but none for Rocky 8, so it's built with go on both.  To move
# to a newer release, change YQ_VERSION.
set -e

YQ_VERSION=v4.53.3

# make sure golang is installed.
if ! rpm -q golang > /dev/null 2>&1
then
  dnf -y install golang
fi

GOBIN=/usr/local/bin go install "github.com/mikefarah/yq/v4@$YQ_VERSION"
/usr/local/bin/yq --version
