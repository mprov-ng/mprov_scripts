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

# run the collections.
mkdir -p collections/


# jq magic!
for collection in `cat /tmp/mprov/entity.json | jq ".systemgroups[].ansiblecollections,.ansiblecollections[],.osdistro.ansiblecollections" | jq -s 'flatten(1)' | jq "select(.[].scriptType.slug|startswith(\\"$runMode\\"))" | jq -r '.[].collectionurl'`
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
for role in `cat /tmp/mprov/entity.json | jq ".systemgroups[].ansibleroles,.ansibleroles[],.osdistro.ansibleroles" | jq -s 'flatten(1)' | jq "select(.[].scriptType.slug|startswith(\\"$runMode\\"))" | jq -r '.[].roleurl'`
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
    ANSIBLE_ROLES_PATH="/tmp/mprov/ansible/roles" ansible localhost -c local -e "@variables.json" --module-name include_role --args name="$role"

  fi
done

cd /tmp/mprov/ansible
# download the playbooks.
mkdir -p playbooks/

# jq magic!
for playbook in `cat /tmp/mprov/entity.json | jq ".systemgroups[].ansibleplaybooks,.ansibleplaybooks[],.osdistro.ansibleplaybooks" | jq -s 'flatten(1)' | jq "select(.[].scriptType.slug|startswith(\\"$runMode\\"))" | jq -r '.[].filename'`
do
  if [ "$playbook" != "null" ]
  then
    wget -P playbooks/ -N $playbook
  fi
done

# run the playbooks.
for i in `ls playbooks/`
do  
  ansible-playbook -i inventory -c local -e "@variables.json" playbooks/$i 
done




# clear any ansible stuff
rm -rf /tmp/mprov/ansible/