#!/bin/bash

# This script will import a CSV of nodes into mProv.  
# Currently, the following fields are required:
#
#   nodename,mac,ipaddress
# 

mashhelp() {
  echo "Usage: $0 <file> [-i iface_name] [-m image_name]" >&2
  exit 1
}

mashBin=`which mash`
if [ ! -x "$mashBin" ]
then
  echo "Error: mash Binary not found/not executable."
  exit 1
fi

if [ "$1" == "" ]
then
  mashhelp
fi

if [ ! -e "$1" ]
then
  echo "Error: File $1 not accessible."
  exit 1
fi

filename=$1
shift

interfaceName=eno1
imageName=compute

while [[ $# -gt 0 ]]
do
  case $1 in 
    -i)
      shift
      interfaceName=$1
      shift
      ;;
    -m)
      shift
      imageName=$1
      shift
      ;;
    *)
      echo "Error: Unknown arg $1"
      exit 1
      ;;
  esac
done

echo -n "" > /tmp/mash_node_import

for entry in `cat $filename`
do
  IFS=',' read -ra DATA <<< "$entry"
  m_hostname=${DATA[0]}
  m_macaddr=${DATA[1]}
  m_ipaddr=${DATA[2]}
  if [ "${DATA[2]}" == "" ]
  then
    #echo "Resolving host ${m_hostname}"
    m_ipaddr=`host ${DATA[0]} | awk '{print $4}'`
    
  fi
  cat << EOF >> /tmp/mash_node_import

print Creating System ${m_hostname}
create systems.models.System hostname=${m_hostname} created_by=1 tmpfs_root_size=0 stateful=0 systemimage=${imageName} stateful=1 disks=['compute-state-lite']
let nodeId = {{ MPROV_RESULT['id'] }}

# add the nic
create systems.models.NetworkInterface name=ens0 hostname=${m_hostname} ipaddress=${m_ipaddr} bootable=1 system={{nodeId}} mac='${m_macaddr}'


EOF

done

# Engage...
echo "connect" | cat - /tmp/mash_node_import | mash
