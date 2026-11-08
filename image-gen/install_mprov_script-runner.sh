#!/bin/bash

# This script will install the mprov_jobserver to run script-runner or NADS.
# It will make some files in /etc/mprov/ for the jobserver and
# the script-runner/nads plugins.  The script-runner handles image-gen and post-boot
# scripts run in images and after a system boots.  This script also includes 
# installing the mprov-nads service into the nads image.  This allows for node
# auto detection.
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
# A job server building with its own certificate (mprov_control_center#381)
# hands over no API key, the mPCC's https URL and a "tls" object: a 2-hour
# build credential (cert, key) that it deletes before packing, and the
# mPCC's CA certificate (ca).  Only the CA goes into the image, so nodes
# without a certificate of their own (N.A.D.S., failed enrollment) can still
# verify the mPCC; a node with one finds it in /etc/mprov/tls by itself.
# Never copy tls.cert or tls.key into the image.
tlsMode=$(jq -r 'if (.tls | type) == "object" then "yes" else empty end' "$ENTITY")
tlsCA=$(jq -r '.tls.ca // empty' "$ENTITY")
entityName=`jq -r .slug "$ENTITY"`
# the job server deliberately sends an empty apikey for the public "nads"
# image (#84), and for every image when it builds with a certificate
# (below): only require one otherwise.
if [ -z "$mprovURL" ] || { [ -z "$apikey" ] && [ "$entityName" != "nads" ] && [ -z "$tlsMode" ]; }
then
  echo "Error: mprovURL or apikey missing from $ENTITY" >&2
  exit 1
fi
echo "Entity Name: $entityName"
if [ "$entityName" == "nads" ]
then
    mod="nads"
    descr="The mProv Node Auto Detection System (NADS)"
else
    mod="script-runner"
    descr="The mProv Jobserver for postboot scripts"
fi

# how the image's job server authenticates: by certificate (no key; the CA
# to verify the mPCC with), or with the shared API key as before.
mkdir -p /etc/mprov/plugins
if [ -n "$tlsMode" ]
then
  authConfig=""
  if [ -n "$tlsCA" ]
  then
    if ! install -m 644 "$tlsCA" /etc/mprov/mpcc-ca.crt
    then
      echo "Error: unable to copy the mPCC's CA certificate from $tlsCA." >&2
      exit 1
    fi
    authConfig="    caBundle: '/etc/mprov/mpcc-ca.crt'"
  fi
else
  authConfig="    apikey: '$apikey'"
fi

# the jobserver config
cat << EOF > /etc/mprov/${mod}.yaml
- global:
    mprovURL: "$mprovURL"
$authConfig
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
