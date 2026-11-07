#!/bin/bash

# This script will install the mprov_jobserver as a secondary jobserver.
# It runs the following modules: image-sync, image-delete, mprov-webserver,
# and repo-update.  This should allow for distributed jobservers to run 
# via the mPCC's redirection stuff.

# read entity.json by its full path, and stop before writing anything if it,
# or a value we need from it, is missing (#31).
ENTITY=/tmp/mprov/entity.json
if ! [ -s "$ENTITY" ] || ! jq -e 'type == "object"' "$ENTITY" > /dev/null 2>&1
then
  echo "Error: unable to read $ENTITY" >&2
  exit 1
fi

# get some initial variables.
mprovURL=`jq -r '.mprovURL // empty' "$ENTITY"`
apikey=`jq -r '.apikey // empty' "$ENTITY"`
if [ -z "$mprovURL" ] || [ -z "$apikey" ]
then
  echo "Error: mprovURL or apikey missing from $ENTITY" >&2
  exit 1
fi

# make sure the jobserver is installed (#87).
#
# A job server from 0.7.0 on puts itself into every image it builds, as a
# bundle with its own Python at /usr/libexec/mprov, before this script runs
# (mprov_jobserver#309).  Use that one: the image's system pip can't install
# 0.7.0 (it needs Python 3.11, mprov_jobserver#311), so it would quietly pick
# an old 0.6.x instead, and replace the bundle's /usr/local/bin/mprov_jobserver
# link with it.
#
# Images built by a 0.6.x job server have no bundle, so for them this keeps
# installing with system pip.  That install is deliberately not pinned: fixes
# reach images only through new releases.  Either way the version is logged,
# so a build shows which one it got (#24).
bundlePython=/usr/libexec/mprov/bin/python3
if [ -x "$bundlePython" ]
then
  echo "Using the job server bundled in the image ($bundlePython)."
  jsVersion=$("$bundlePython" -c 'from importlib.metadata import version; print(version("mprov_jobserver"))' 2>/dev/null)
  pkgLocation=$("$bundlePython" -c 'import os, mprov_jobserver; print(os.path.dirname(os.path.dirname(mprov_jobserver.__file__)))' 2>/dev/null)
else
  pip3 install mprov_jobserver
  jsVersion=$(pip3 show mprov_jobserver 2>/dev/null | awk '/^Version:/{print $2}')
  pkgLocation=$(pip3 show mprov_jobserver 2>/dev/null | awk '/^Location:/{print $2}')
fi
echo "mprov_jobserver version: $jsVersion"

mkdir -p /etc/mprov/plugins

# the jobserver config
cat << EOF > /etc/mprov/jobserver.yaml
- global:
    mprovURL: "$mprovURL"
    apikey: '$apikey'
    heartbeatInterval: 10
    jobmodules:
      - image-sync
      - mprov-webserver
      - image-delete
      #- repo-update
- !include plugins/*.yaml
EOF
chmod 600 /etc/mprov/jobserver.yaml

# copy the default plugin configs from wherever the package is installed:
# inside the bundle, or wherever system pip put it (python3.8 on Rocky 8,
# python3.9 on Rocky 9).  Without them the webserver has no rootDir and
# serves / (#42).
pluginDir="$pkgLocation/mprov_jobserver/plugins"
if [ -z "$pkgLocation" ] || [ ! -d "$pluginDir" ]
then
  echo "Error: unable to find the mprov_jobserver plugin configs (looked in '$pluginDir')." >&2
  exit 1
fi
if ! cp "$pluginDir"/*.yaml /etc/mprov/plugins/
then
  echo "Error: unable to copy the plugin configs from $pluginDir." >&2
  exit 1
fi

cat << EOF > /etc/mprov/plugins/image-sync.yaml
- image-sync:
    imageDir: '/export/images'
    imageList: null
EOF
chmod 600 /etc/mprov/plugins/image-sync.yaml

# the systemd service file
cat << EOF > /usr/lib/systemd/system/mprov-jobserver.service
[Unit]
Description=The mProv Jobserver
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# runonce, in system mode, in post boot mode, use the /etc/mprov/jobserver.yaml
ExecStart=/usr/local/bin/mprov_jobserver -c /etc/mprov/jobserver.yaml

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
