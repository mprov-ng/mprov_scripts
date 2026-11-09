#!/bin/bash

# Install the timer that keeps a node's own certificate current (#96,
# mprov_control_center#506).
#
# From 0.7.0 a node enrolls with the mPCC at boot and gets its own
# certificate in /etc/mprov/tls (mprov_control_center#381).  This puts the
# mPCC's helper for it, mprov_enroll.py, at /usr/libexec/mprov/ and a systemd
# timer that runs "mprov_enroll.py renew" 5 minutes after boot and then about
# daily.  renew does nothing (and exits 0) on a node without a certificate.
# Otherwise it asks the mPCC each time: the mPCC renews the certificate in
# its renewal window, and if it revoked the certificate the node re-enrolls
# through the window an admin opened for it ("Let these systems re-enroll").
# The helper comes from the mPCC, so an older mPCC's copy only renews a
# certificate in its last 14 days and doesn't notice a revocation.
#
# The timer never holds up the boot: nothing waits for the service, and a
# failed renewal is only logged (journalctl -u mprov-cert-renew).
#
# An mPCC before 0.7.0 has no helper to download.  The script then installs
# nothing and exits 0, so it can be in an image's script list before the
# mPCC is upgraded.

ENTITY=/tmp/mprov/entity.json
LIBEXEC=/usr/libexec/mprov
HELPER=$LIBEXEC/mprov_enroll.py
UNITDIR=/usr/lib/systemd/system

if ! [ -s "$ENTITY" ] || ! jq -e 'type == "object"' "$ENTITY" > /dev/null 2>&1
then
  echo "Error: unable to read $ENTITY" >&2
  exit 1
fi
mprovURL=$(jq -r '.mprovURL // empty' "$ENTITY")
if [ -z "$mprovURL" ]
then
  echo "Error: mprovURL missing from $ENTITY" >&2
  exit 1
fi
# a job server building with its own certificate hands over the mPCC's CA
# (mprov_control_center#381); otherwise use the image's own trust store.
ca=$(jq -r '.tls.ca // empty' "$ENTITY")
cacert=()
if [ -n "$ca" ]
then
  cacert=(--cacert "$ca")
fi

if ! command -v curl > /dev/null
then
  echo "Error: curl is needed to download $HELPER." >&2
  exit 1
fi

mkdir -p "$LIBEXEC" || exit 1
url="${mprovURL%/}/static/mprov_enroll.py"
tmp=$(mktemp "$LIBEXEC/.mprov_enroll.XXXXXX") || exit 1
code=$(curl -sS -L --retry 2 "${cacert[@]}" -o "$tmp" -w '%{http_code}' "$url")
status=$?
if [ "$code" = 404 ]
then
  rm -f "$tmp"
  echo "The mPCC has no $url (it's older than 0.7.0), so nodes have no certificate to renew; not installing the renewal timer."
  exit 0
fi
if [ $status -ne 0 ] || [ "$code" != 200 ] || ! grep -q '^def renew' "$tmp"
then
  rm -f "$tmp"
  echo "Error: unable to download the certificate helper from $url (HTTP ${code:-none}, curl exit $status)." >&2
  exit 1
fi
chmod 755 "$tmp" && mv -f "$tmp" "$HELPER" || exit 1

if ! command -v openssl > /dev/null
then
  echo "Warning: no openssl in the image; nodes can't enroll or renew a certificate without it." >&2
fi

# the job server's bundled python in images built by 0.7.0 and later,
# otherwise the system python3 (as in the fallback shebangs, #86/#89).
cat << 'EOF' > "$UNITDIR/mprov-cert-renew.service"
[Unit]
Description=Renew this node's mProv certificate when it's due
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'p=/usr/libexec/mprov/bin/python3; [ -x "$$p" ] || p=/usr/bin/python3; exec "$$p" /usr/libexec/mprov/mprov_enroll.py renew'
TimeoutStartSec=5min
EOF

cat << 'EOF' > "$UNITDIR/mprov-cert-renew.timer"
[Unit]
Description=Renew this node's mProv certificate when it's due, at boot and daily

[Timer]
OnBootSec=5min
OnUnitActiveSec=1d
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF

if ! systemctl enable mprov-cert-renew.timer
then
  echo "Error: unable to enable mprov-cert-renew.timer." >&2
  exit 1
fi
echo "Installed $HELPER and enabled mprov-cert-renew.timer."
