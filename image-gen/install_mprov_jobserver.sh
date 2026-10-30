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

# make sure the jobserver is installed
pip3 install mprov_jobserver

mkdir -p /etc/mprov/plugins

# the jobserver config
cat << EOF > /etc/mprov/jobserver.yaml
- global:
    mprovURL: "$mprovURL"
    apikey: '$apikey'
    heartbeatInterval: 10
    runonce: True # uncomment to run the jobserver once and exit.
    jobmodules:
      - image-sync
      - mprov-webserver
      - image-delete
      #- repo-update
- !include plugins/*.yaml
EOF
chmod 600 /etc/mprov/jobserver.yaml

# copy the default plugin configs from wherever pip3 installed the package
# (python3.8 on Rocky 8, python3.9 on Rocky 9).  Without them the webserver
# has no rootDir and serves / (#42).
pkgLocation=`pip3 show mprov_jobserver 2>/dev/null | awk '/^Location:/{print $2}'`
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
