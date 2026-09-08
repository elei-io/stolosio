#!/usr/bin/python3
"""Install the same connection boundary for Chromium and Squid before either starts."""

import ipaddress
import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

# Deliberately conservative: special-use ranges are not page destinations.
BLOCKED_V4 = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.88.99.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/3",
)
BLOCKED_V6 = ("2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20")


def main() -> None:
    user = pwd.getpwnam(sys.argv[1])
    extra = [
        str(ipaddress.ip_network(value))
        for value in os.environ.get("EGRESS_EXTRA_BLOCKED_CIDRS", "").split()
    ]
    v4 = [*BLOCKED_V4, *(value for value in extra if ":" not in value)]
    v6 = [*BLOCKED_V6, *(value for value in extra if ":" in value)]
    resolvers = [
        str(ipaddress.ip_address(line.split()[1]))
        for line in Path("/etc/resolv.conf").read_text().splitlines()
        if line.split() and line.split()[0] == "nameserver"
    ]
    if not resolvers:
        raise RuntimeError("A DNS resolver is required")
    interfaces = json.loads(subprocess.check_output(["ip", "-j", "address", "show"]))
    for interface in interfaces:
        for address in interface.get("addr_info", []):
            local = str(ipaddress.ip_address(address["local"]))
            (v6 if ":" in local else v4).append(local)
    # Mark the narrow resolver exception before Docker's DNS DNAT changes port 53.
    # All destination filtering runs after DNAT, against the actual destination.
    rules = [
        "table inet stolosio_egress {",
        "chain dns { type filter hook output priority -150; policy accept;",
    ]
    for resolver in resolvers:
        family = "ip6" if ":" in resolver else "ip"
        rules.append(
            f"meta skuid {user.pw_uid} {family} daddr {resolver} "
            "meta l4proto { tcp, udp } th dport 53 meta mark set 0x53544f4c"
        )
    rules.extend(
        [
            "}",
            "chain output { type filter hook output priority 0; policy accept;",
            f"meta skuid {user.pw_uid} jump page_fetch",
            "}",
            "chain page_fetch {",
            "ct direction reply ct state established accept",
            "meta mark 0x53544f4c accept",
        ]
    )
    rules.extend(
        [
            f"ip daddr {{ {', '.join(v4)} }} reject",
            "ip6 daddr != 2000::/3 reject",
            f"ip6 daddr {{ {', '.join(v6)} }} reject",
            "tcp dport { 80, 443 } accept",
            "reject",
            "}",
            "}",
        ]
    )
    # A container restart keeps its network namespace; replace our own table only.
    exists = subprocess.run(
        ["nft", "list", "table", "inet", "stolosio_egress"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    policy = "delete table inet stolosio_egress\n" if exists.returncode == 0 else ""
    subprocess.run(["nft", "-f", "-"], input=policy + "\n".join(rules), text=True, check=True)
    if sys.argv[1] == "proxy":
        # ACLs give a useful denial response; the firewall closes DNS/routing races.
        Path("/etc/squid/blocked-destinations.txt").write_text(
            "\n".join(
                [
                    *v4,
                    "::/3",
                    "4000::/2",
                    "8000::/1",
                    *v6,
                ]
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
