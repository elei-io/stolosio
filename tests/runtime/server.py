"""Disposable public/private endpoints and DNS for the container boundary tests."""

import json
import os
import socket
import socketserver
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PUBLIC = "93.184.216.10"
PRIVATE = "172.30.96.10"
counts = {}
rebound = False


class Server(ThreadingHTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        local = connection.getsockname()[0]
        counts[local] = counts.get(local, 0) + 1
        if os.environ.get("CANARY"):
            Path("/tmp/canary-count").write_text(str(sum(counts.values())))
        return connection, address


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        global rebound
        if self.path == "/stats":
            body = json.dumps(counts)
        elif self.path == "/rebind":
            rebound = True
            body = "ok"
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://{PRIVATE}/private")
            self.end_headers()
            return
        elif self.path == "/worker.js":
            body = f"fetch('http://{PRIVATE}/worker').catch(() => postMessage('blocked'));"
        else:
            body = (
                "<html><head><title>Public fixture</title></head><body>Public fixture</body></html>"
            )
        self.send_response(200)
        self.send_header(
            "Content-Type", "text/javascript" if self.path == "/worker.js" else "text/html"
        )
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode())


class DNS(socketserver.BaseRequestHandler):
    def handle(self):
        packet, sock = self.request
        pos = 12
        labels = []
        while packet[pos]:
            size = packet[pos]
            labels.append(packet[pos + 1 : pos + 1 + size].decode())
            pos += size + 1
        pos += 1
        qtype, _ = struct.unpack("!HH", packet[pos : pos + 4])
        hostname = ".".join(labels)
        private = hostname.startswith("private") or (hostname.startswith("rebind") and rebound)
        address = PRIVATE if private else PUBLIC
        answer = b""
        if qtype == 1:
            answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 0, 4) + socket.inet_aton(address)
        header = packet[:2] + struct.pack("!HHHHH", 0x8180, 1, bool(answer), 0, 0)
        sock.sendto(header + packet[12 : pos + 4] + answer, self.client_address)


if os.environ.get("CANARY"):
    Path("/tmp/canary-count").write_text("0")

    class IPv6Server(Server):
        address_family = socket.AF_INET6

    ipv6 = IPv6Server(("::1", 80), Handler)
    threading.Thread(target=ipv6.serve_forever, daemon=True).start()
    Server(("127.0.0.1", 80), Handler).serve_forever()
else:
    dns = socketserver.ThreadingUDPServer(("0.0.0.0", 53), DNS)
    threading.Thread(target=dns.serve_forever, daemon=True).start()
    Server(("0.0.0.0", 80), Handler).serve_forever()
