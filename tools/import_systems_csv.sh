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

interfaceName=ens0
imageName=compute
mgmtNet=172.20.
bmcNet=172.29.
bmcNetId=2
nodeNetId=1
bmcUser="admin"
bmcPass=changeme
ibNet=172.21.
ibNetId=4

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

# the mash input carries the BMC credentials: keep it private (mktemp makes
# it 0600) and remove it however we leave (#40).
mashInput=$(mktemp /tmp/mash_node_import.XXXXXX) || exit 1
trap 'rm -f "$mashInput"' EXIT

trim() {
  local s=$1
  s=${s#"${s%%[![:space:]]*}"}
  printf %s "${s%"${s##*[![:space:]]}"}"
}

# one record per line; a bad row stops the import before anything is sent
# to the mPCC (#26).
errors=0
lineno=0
while IFS= read -r line || [ -n "$line" ]
do
  lineno=$((lineno+1))
  # spreadsheets write CRLF line endings.
  line=${line//$'\r'/}
  # skip blank lines and comments.
  [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
  IFS=',' read -ra DATA <<< "$line"
  for i in "${!DATA[@]}"
  do
    DATA[$i]=$(trim "${DATA[$i]}")
  done
  # a header row: the format at the top of this script.
  if [ $lineno -eq 1 ] && [ "${DATA[1],,}" == "mac" ]
  then
    continue
  fi
  if [ ${#DATA[@]} -lt 2 ] || [ ${#DATA[@]} -gt 3 ]
  then
    echo "Error: line $lineno: expected nodename,mac[,ipaddress]: $line" >&2
    errors=$((errors+1))
    continue
  fi
  m_hostname=${DATA[0]}
  m_macaddr=${DATA[1]}
  m_ipaddr=${DATA[2]}
  if [ "$m_ipaddr" == "" ]
  then
    m_ipaddr=`host "$m_hostname" | awk '/ has address /{print $4; exit}'`
  fi
  if ! [[ "$m_hostname" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]
  then
    echo "Error: line $lineno: bad node name '$m_hostname'" >&2
    errors=$((errors+1))
    continue
  fi
  if ! [[ "$m_macaddr" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]
  then
    echo "Error: line $lineno: bad MAC address '$m_macaddr' for $m_hostname" >&2
    errors=$((errors+1))
    continue
  fi
  if ! [[ "$m_ipaddr" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
  then
    echo "Error: line $lineno: no IPv4 address for $m_hostname (got '$m_ipaddr')" >&2
    errors=$((errors+1))
    continue
  fi
  # the BMC and IB addresses swap the management prefix for theirs; an
  # address outside the management network has no BMC/IB address (#39).
  if [[ "$m_ipaddr" != "$mgmtNet"* ]]
  then
    echo "Error: line $lineno: $m_ipaddr for $m_hostname isn't on the management network ${mgmtNet}x.x, so its BMC and IB addresses can't be worked out" >&2
    errors=$((errors+1))
    continue
  fi
  bmcIp=${bmcNet}${m_ipaddr#"$mgmtNet"}
  ibIp=${ibNet}${m_ipaddr#"$mgmtNet"}

  cat << EOF >> "$mashInput"

print Creating System ${m_hostname}
create systems.models.System hostname=${m_hostname} created_by=1 tmpfs_root_size=0 systemimage=${imageName} stateful=1 disks=['compute-state-lite']
let nodeId = {{ MPROV_RESULT['id'] }}

# add the nic
create systems.models.NetworkInterface name=${interfaceName} hostname=${m_hostname} ipaddress=${m_ipaddr} bootable=1 system={{nodeId}} mac='${m_macaddr}' network=${nodeNetId}

# add the bmc
create systems.models.SystemBMC system={{nodeId}} ipaddress=${bmcIp} username=${bmcUser} password=${bmcPass} network=${bmcNetId}

# add the ib interface
create systems.models.NetworkInterface name=ib0 hostname=${m_hostname} ipaddress=${ibIp} system={{nodeId}} network=${ibNetId}

EOF

done < "$filename"

if [ $errors -gt 0 ]
then
  echo "Error: $errors bad row(s) in $filename; nothing was imported." >&2
  exit 1
fi

# Engage...
echo "connect" | cat - "$mashInput" | mash
