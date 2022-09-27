#!/bin/bash

# This script will install the mprov_jobserver as a secondary jobserver.
# It runs the following modules: image-sync, image-delete, mprov-webserver,
# and repo-update.  This should allow for distributed jobservers to run 
# via the mPCC's redirection stuff.

cd /tmp/mprov

# get some initial variables.
mprovURL=`cat entity.json | jq .mprovURL -r`
apikey=`cat entity.json | jq .apikey -r`

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
      - repo-update
- !include plugins/*.yaml
EOF

# the systemd service file
cat << EOF > /usr/lib/systemd/system/mprov-jobserver.service
[Unit]
Description=The mProv Jobserver
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# runonce, in system mode, in post boot mode, use the /etc/mprov/jobserver.yaml
ExecStart=/usr/local/bin/mprov_jobserver -c /etc/mprov/script-runner.yaml

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable mprov-jobserver
