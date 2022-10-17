#!/bin/bash

# This script will install the mprov_jobserver to run script-runner.
# It will make some files in /etc/mprov/ for the jobserver and
# the script-runner plugins.  script-runner should handle
# all the rest of the setup for the postboot scripts after
# it runs in the system.
cd /tmp/mprov

# get some initial variables.
mprovURL=`cat entity.json | jq .mprovURL -r`
apikey=`cat entity.json | jq .apikey -r`
entityName=`cat entity.json | jq .slug -r`
echo "Entity Name: $entityName"
if [ "$entityName" == "nads" ]
then
    mod="nads"
    descr="The mProv Node Auto Detection System (NADS)"
else
    mod="script-runner"
    descr="The mProv Jobserver for postboot scripts"
fi

mkdir -p /etc/mprov/plugins

# the jobserver config
cat << EOF > /etc/mprov/${mod}.yaml
- global:
    mprovURL: "$mprovURL"
    apikey: '$apikey'
    heartbeatInterval: 10
    runonce: True # uncomment to run the jobserver once and exit.
    jobmodules:
      - ${mod}
- !include plugins/*.yaml
EOF
chmod 600 /etc/mprov/${mod}.yaml

# the script-runner config
cat << EOF > /etc/mprov/plugins/script-runner.yaml
- script-runner:
    scriptTmpDir: '/tmp/mprov'
EOF


# NADS config
cat << EOF > /etc/mprov/plugins/nads.yaml
- nads:
    reboot: True
EOF

# the systemd service file
cat << EOF > /usr/lib/systemd/system/mprov-${mod}.service
[Unit]
Description=$descr 
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
# runonce, in system mode, in post boot mode, use the /etc/mprov/${mod}.yaml
ExecStart=/usr/local/bin/mprov_jobserver -d -r -s -b -c /etc/mprov/${mod}.yaml

[Install]
WantedBy=multi-user.target
EOF

# enable the systemd service
systemctl enable mprov-${mod}
