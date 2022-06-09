#!/bin/bash

# This script will install the mprov_jobserver to run postboot.
# It will make some files in /etc/mprov/ for the jobserver and
# the script-runner plugins.  script-runner should handle
# all the rest of the setup for the postboot scripts after
# it runs in the system.
cd /tmp/mprov

# get some initial variables.
mprovURL=`cat entity.json | jq .mprovURL -r`
apikey=`cat entity.json | jq .apikey -r`

mkdir -p /etc/mprov/plugins

# the jobserver config
cat << EOF > /etc/mprov/jobserver.yaml
- global:
    mprovURL: "$mprovURL"
    apikey: '$apikey'
    heartbeatInterval: 10
    runonce: True # uncomment to run the jobserver once and exit.
    jobmodules:
      - script-runner
- !include plugins/*.yaml
EOF

# the script-runner config
cat << EOF > /etc/mprov/plugins/script-runner.yaml
- script-runner:
    scriptTmpDir: '/tmp/mprov'
EOF

# the systemd service file
cat << EOF > /usr/lib/systemd/system/mprov_jobserver_postboot.service
[Unit]
Description=The mProv Jobserver for postboot scripts
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
# runonce, in system mode, in post boot mode, use the /etc/mprov/jobserver.yaml
ExecStart=/usr/local/bin/mprov_jobserver -d -r -s -b -c /etc/mprov/jobserver.yaml

[Install]
WantedBy=multi-user.target
EOF

# enable the systemd service
systemctl enable mprov_jobserver_postboot

