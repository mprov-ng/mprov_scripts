#!/bin/bash

# activate the mpcc venv
cd /var/www/mprov_control_center/

. ./bin/activate

rm -rf /tmp/mprov_backup/
mkdir -p /tmp/mprov_backup/

echo  "disklayouts: "
python3 manage.py dumpdata disklayouts --indent=2 --format yaml -o /tmp/mprov_backup/mprov_disklayouts.yaml
echo  "networks: "
python3 manage.py dumpdata networks --indent=2 --format yaml -o /tmp/mprov_backup/mprov_networks.yaml
echo  "scripts: "
python3 manage.py dumpdata scripts --indent=2 --format yaml -o /tmp/mprov_backup/mprov_scripts.yaml
echo  "systems: "
python3 manage.py dumpdata systems --indent=2 --format yaml -o /tmp/mprov_backup/mprov_systems.yaml
echo  "osmanagement: "
python3 manage.py dumpdata osmanagement --indent=2 --format yaml -o /tmp/mprov_backup/mprov_osmanagement.yaml

echo -n "Copying Media..."
cp -arpf media/ /tmp/mprov_backup/
echo "Done."

deactivate

cd /tmp	
echo -n "Building tar.gz..."
tar -zcf mprov_backup.tar.gz mprov_backup/
echo "Done"
echo

echo "Backup saved to /tmp/mprov_backup.tar.gz"
