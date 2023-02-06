#!/bin/bash

#DEPRICATED!

exit 0

# This script is meant to be used in the '__nads__' system image 
# after it boots, to attempt to autodetect a system.  This script
# will create a jobserver.yaml, start the jobserver and run NADS.
cd /tmp/mprov
# extract some information from the entity.json
# mprovURL
# apikey
# provIntf
mprovURL=`cat entity.json | jq .mprovURL -r`
apikey=`cat entity.json | jq .apikey -r`
provIntf='eno1'

echo -n "Running in "
pwd

echo -n "Creating configs ..."

# create a jobserver config
cat << EOF > jobserver.yaml
- global:
    mprovURL: "$mprovURL"
    apikey: '$apikey'
    heartbeatInterval: 10
    runonce: True # uncomment to run the jobserver once and exit.
    jobmodules:
      - nads
- !include plugins/*.yaml
EOF
chmod 600 jobserver.yaml

mkdir -p plugins 

cat << EOF > plugins/nads.yaml
- nads:
    provIntf: '$provIntf'
    maxLLDPWait: 60
    reboot: True
EOF

# now we try to run NADS, and don't register as a jobserver.
mprov_jobserver -r -d -c ./jobserver.yaml 

