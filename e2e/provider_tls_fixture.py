"""Controlled external HTTPS provider; replaces only OS DNS/routing boundaries."""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import ssl
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def capability_response(payload):
    tool_result = any(message.get("role") == "tool" for message in payload["messages"])
    message = {"role": "assistant", "content": '{"ready":true}'} if tool_result else {
        "role": "assistant", "tool_calls": [{"id": "fixture-ping", "type": "function",
            "function": {"name": "ping", "arguments": "{}"}}]}
    return {"choices": [{"message": message}]}


@contextmanager
def public_provider_https(
    hosts=("provider-one.example", "provider-two.example"),
    response_for=capability_response,
):
    """Yield secret-free request metadata from real validated HTTPS exchanges.

    Application URL policy, DNS answer validation, numeric connection, TLS SNI,
    certificate verification, HTTP and response parsing all execute unchanged.
    Exact fixture DNS names resolve to one public address; only that numeric
    address on port 443 is routed to the local external TLS server. Other DNS
    names and socket destinations retain the operating system's behavior.
    """
    requests = []
    now = dt.datetime.now(dt.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Provider test CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - dt.timedelta(minutes=1))
          .not_valid_after(now + dt.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(ca_key, hashes.SHA256()))
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    certificate = (x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])]))
        .issuer_name(ca_name).public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host) for host in hosts]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256()))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            host = self.headers.get("Host")
            sni = getattr(self.connection, "fixture_sni", None)
            if host not in hosts or sni != host:
                self.send_error(400, "Unexpected provider identity")
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"host": host, "sni": sni, "path": self.path})
            response = response_for(payload)
            status, response_body = response if isinstance(response, tuple) else (200, response)
            body = json.dumps(response_body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    original_resolve = socket.getaddrinfo
    original_socket = socket.socket
    original_ca = os.environ.get("SSL_CERT_FILE")
    fixture_address = "93.184.216.34"
    with tempfile.TemporaryDirectory(prefix="x-insight-provider-tls-") as directory:
        directory = Path(directory)
        ca_file, cert_file, key_file = [directory / name for name in ("ca.pem", "cert.pem", "key.pem")]
        ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(server_key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        key_file.chmod(0o600)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_file, key_file)
        def record_sni(sock, server_name, _context):
            sock.fixture_sni = server_name
        context.set_servername_callback(record_sni)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def resolve(host, port, *args, **kwargs):
            if host in hosts:
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (fixture_address, int(port))) ]
            return original_resolve(host, port, *args, **kwargs)

        class RoutedSocket(original_socket):
            def connect(self, address):
                if address == (fixture_address, 443):
                    address = server.server_address
                return super().connect(address)

        socket.getaddrinfo = resolve
        socket.socket = RoutedSocket
        os.environ["SSL_CERT_FILE"] = str(ca_file)
        try:
            yield requests
        finally:
            socket.getaddrinfo = original_resolve
            socket.socket = original_socket
            if original_ca is None:
                os.environ.pop("SSL_CERT_FILE", None)
            else:
                os.environ["SSL_CERT_FILE"] = original_ca
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
