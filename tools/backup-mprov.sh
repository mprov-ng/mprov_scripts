#!/bin/bash

# Any failed step stops the backup; the previous one is only replaced once
# the new one is complete (#38).
set -eo pipefail

# The dumpdata output holds every SystemBMC password, so keep everything this
# backup creates root-only (#20).  umask 077 gives dirs 700 / files 600.
umask 077

# Persistent destination (a reboot no longer destroys the only copy);
# operators can point the backup elsewhere with MPROV_BACKUP_DIR.
BACKUP_DIR="${MPROV_BACKUP_DIR:-/var/backups/mprov}"
BACKUP_DIR="${BACKUP_DIR%/}"
TARBALL="$BACKUP_DIR/mprov_backup.tar.gz"
PARENT="$(dirname "$BACKUP_DIR")"
NAME="$(basename "$BACKUP_DIR")"

# Build the new backup in a staging directory next to the destination, then
# swap it in.  STAGE/$NAME becomes the new $BACKUP_DIR.
mkdir -p "$PARENT"
STAGE="$(mktemp -d "$PARENT/.$NAME.XXXXXX")"
NEW="$STAGE/$NAME"
cleanup() {
  status=$?
  rm -rf "$STAGE"
  if [ "$status" -ne 0 ]; then
    echo >&2
    echo "ERROR: backup failed, $BACKUP_DIR was left as it was" >&2
  fi
}
trap cleanup EXIT
mkdir "$NEW"

# activate the mpcc venv
cd /var/www/mprov_control_center/

. ./bin/activate

for app in disklayouts networks scripts systems osmanagement; do
  echo  "$app: "
  python3 manage.py dumpdata "$app" --indent=2 --format yaml -o "$NEW/mprov_$app.yaml"
done

echo -n "Copying Media..."
cp -arpf media/ "$NEW/"
# cp -p keeps the source modes; strip group/other bits from everything staged.
chmod -R go-rwx "$NEW"
echo "Done."

deactivate

# Written outside the directory it archives, then moved in.
echo -n "Building tar.gz..."
tar -zcf "$STAGE/mprov_backup.tar.gz" -C "$STAGE" "$NAME"
mv "$STAGE/mprov_backup.tar.gz" "$NEW/"
echo "Done"
echo

# The tarball is secret material; fail loudly rather than leave it readable.
tar_mode=$(stat -c '%a' "$NEW/mprov_backup.tar.gz")
if [ "$tar_mode" != "600" ]; then
  echo "ERROR: $TARBALL would have mode $tar_mode, expected 600" >&2
  exit 1
fi

# Swap the new backup in, keeping the old one until that has worked.
if [ -e "$BACKUP_DIR" ]; then
  mv "$BACKUP_DIR" "$STAGE/previous"
fi
if ! mv "$NEW" "$BACKUP_DIR"; then
  [ -e "$STAGE/previous" ] && mv "$STAGE/previous" "$BACKUP_DIR"
  exit 1
fi
chmod 700 "$BACKUP_DIR"

echo "Backup saved to $TARBALL"
