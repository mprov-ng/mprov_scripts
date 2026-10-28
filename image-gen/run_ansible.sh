#!/bin/bash

# Script to run ansible roles and playbooks referenced in the mPCC



# make sure ansible is installed
# it comes form epel
dnf -y install epel-release

dnf -y install ansible

cd /tmp/mprov

mprovURL=`cat entity.json | jq .mprovURL -r`
apikey=`cat entity.json | jq .apikey -r`

# detect our run mode.
imgSlug=`cat entity.json | jq -r .slug`
if [ $imgSlug != "null" ]
then
  runMode="image-gen"
else
  runMode="post-boot"
fi

# clear any existing ansible stuff
rm -rf /tmp/mprov/ansible/

# create a variables.json file for ansible
mkdir -p /tmp/mprov/ansible/
cd /tmp/mprov/ansible/

if [ ! -e /tmp/mprov/entity.json ]
then
  echo "ERROR: Missing entity.json!" >&2
  exit 1
fi
# grab just the config_params... maybe more?  we will see...
cat /tmp/mprov/entity.json | jq .config_params  > variables.json

# create an inventory of just localhost
echo "localhost" > inventory

# ansible_list <list> <key>: the <key> (URL or file) of each entry of one
# ansible list (ansiblecollections, ansibleroles or ansibleplaybooks) to run
# in this run mode, in order.  The mPCC merges the lists from the distro,
# system groups, image and system and serves them as slugs in `config`
# (mprov_control_center#269); each slug is looked up in entity.json for its
# URL and type.  An mPCC from before #269 doesn't serve them, so they're
# collected from the entity's layers instead.
ansible_list() {
  jq -r --arg m "$runMode" --arg list "$1" --arg key "$2" '
    (INDEX(.. | objects | select(has($list)) | .[$list] | arrays | .[] | objects; .slug)) as $by
    | (if (.config[$list] | type) == "array"
       then [.config[$list][] | $by[.] // {missing: .}]
       else [.systemgroups[]?[$list], .[$list], .osdistro[$list]] | map(. // []) | flatten(1) end)
    | map(select(.missing or (.scriptType.slug // "" | startswith($m))))
    | .[] | if .missing then "missing \(.missing)" else .[$key] end' /tmp/mprov/entity.json |
  while read -r entry
  do
    case "$entry" in
      "missing "*) echo "WARNING: $1 ${entry#missing } is in the mPCC's config but not in entity.json, skipped." >&2 ;;
      *) echo "$entry" ;;
    esac
  done
}

# run the collections.
mkdir -p collections/


# jq magic!
for collection in `ansible_list ansiblecollections collectionurl`
do
  if [ "$collection" != "null" ]
  then
      
    ansible-galaxy collection install -p /tmp/mprov/ansible/collections/ $collection -f

  fi
done


cd /tmp/mprov/ansible
# Now on to the roles, if applicable.
# download the roles.
mkdir -p roles/


# jq magic!
for role in `ansible_list ansibleroles roleurl`
do
  if [ "$role" != "null" ]
  then
    # download the role

      
    ansible-galaxy role install -p /tmp/mprov/ansible/roles/ $role -f
    # run the role, hope you set your variables.....
    if [[ $role == git* ]]
    then
      role=`basename -s .git $role`
    fi
    
    cat <<- EOF > /tmp/role.yaml
    - hosts: localhost
      become: true
      gather_facts: true
      tasks:
        - name: Import role $role
          ansible.builtin.include_role:
            name: $role
EOF
    ANSIBLE_ROLES_PATH="/tmp/mprov/ansible/roles" ANSIBLE_GATHER=implicit ansible-playbook -i localhost, -c local -e "@variables.json" /tmp/role.yaml

  fi
done

cd /tmp/mprov/ansible
# download the playbooks.
mkdir -p playbooks/

# jq magic!
playbooks=()
for playbook in `ansible_list ansibleplaybooks filename`
do
  if [ "$playbook" != "null" ]
  then
    wget -P playbooks/ -N $playbook
    # only the ones that downloaded
    if [ -e "playbooks/$(basename $playbook)" ]
    then
      playbooks+=("$(basename $playbook)")
    fi
  fi
done

# run the playbooks, in the order they're listed.
for i in "${playbooks[@]}"
do  
  ansible-playbook -i inventory -c local -e "@variables.json" playbooks/$i 
done




# clear any ansible stuff
rm -rf /tmp/mprov/ansible/