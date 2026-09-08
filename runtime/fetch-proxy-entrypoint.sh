#!/bin/sh
set -eu
python3 /opt/stolosio/egress.py proxy
exec setpriv --reuid=proxy --regid=proxy --clear-groups --bounding-set=-all \
    --inh-caps=-all --ambient-caps=-all --no-new-privs \
    /usr/sbin/squid -N -d 1 -f /etc/squid/squid.conf
