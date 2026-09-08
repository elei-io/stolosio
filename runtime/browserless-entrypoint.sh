#!/bin/sh
set -eu
python3 /opt/stolosio/egress.py blessuser
mkdir -p /tmp/browserless-data-dirs
chmod 755 /tmp/browserless-data-dirs
# The controller needs UID/GID changes for launching Chromium and ownership of its
# disposable profiles. Neither controller nor Chromium retains NET_ADMIN/NET_RAW.
exec setpriv --bounding-set=-all,+setuid,+setgid,+chown,+dac_override,+kill,+setpcap \
    --inh-caps=-all --ambient-caps=-all --no-new-privs /usr/src/app/scripts/start.sh
