#!/bin/bash

# The dumpdata output holds every SystemBMC password, so keep everything this
# backup creates root-only (#20).  umask 077 gives dirs 700 / files 600.
umask 077

# Persistent destination (a reboot no longer destroys the only copy);
# operators can point the backup elsewhere with MPROV_BACKUP_DIR.
BACKUP_DIR="${MPROV_BACKUP_DIR:-/var/backups/mprov}"
TARBALL="$BACKUP_DIR/mprov_backup.tar.gz"

# activate the mpcc venv
cd /var/www/mprov_control_center/

. ./bin/activate

rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

echo  "disklayouts: "
python3 manage.py dumpdata disklayouts --indent=2 --format yaml -o "$BACKUP_DIR/mprov_disklayouts.yaml"
echo  "networks: "
python3 manage.py dumpdata networks --indent=2 --format yaml -o "$BACKUP_DIR/mprov_networks.yaml"
echo  "scripts: "
python3 manage.py dumpdata scripts --indent=2 --format yaml -o "$BACKUP_DIR/mprov_scripts.yaml"
echo  "systems: "
python3 manage.py dumpdata systems --indent=2 --format yaml -o "$BACKUP_DIR/mprov_systems.yaml"
echo  "osmanagement: "
python3 manage.py dumpdata osmanagement --indent=2 --format yaml -o "$BACKUP_DIR/mprov_osmanagement.yaml"

echo -n "Copying Media..."
cp -arpf media/ "$BACKUP_DIR/"
# cp -p keeps the source modes; strip group/other bits from everything staged.
chmod -R go-rwx "$BACKUP_DIR"
echo "Done."

deactivate

echo -n "Building tar.gz..."
tar -zcf "$TARBALL" -C "$(dirname "$BACKUP_DIR")" "$(basename "$BACKUP_DIR")"
echo "Done"
echo

# The tarball is secret material; fail loudly rather than leave it readable.
tar_mode=$(stat -c '%a' "$TARBALL")
if [ "$tar_mode" != "600" ]; then
  echo "ERROR: $TARBALL has mode $tar_mode, expected 600" >&2
  exit 1
fi

echo "Backup saved to $TARBALL"
