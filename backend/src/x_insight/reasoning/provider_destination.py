"""Provider destination selection and authoritative DNS-bound HTTP connection.

An approval belongs to one immutable settings revision, never a network range.
Environment proxies are intentionally unused: DNS and the actual socket must agree.
"""

from __future__ import annotations

import http.client
import io
import ipaddress
import json
import os
import queue
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import NoReturn
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

ALLOWED_HOSTS_ENV = "X_INSIGHT_PROVIDER_ALLOWED_HOSTS"
_FAKE = ipaddress.ip_network("198.18.0.0/15")
_METADATA = ipaddress.ip_address("100.100.100.200")
_RESOLVER_SLOTS = threading.BoundedSemaphore(4)


class DestinationDenied(ValueError):
    """Stable, credential-free policy rejection."""


class ResolutionFailed(OSError):
    """DNS failed without revealing the supplied URL."""


def address_allowed(value: str, *, local: bool, trusted_proxy: bool) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and (
        ip.sixtofour is not None
        or ip.teredo is not None
        or ip in ipaddress.ip_network("64:ff9b::/96")
        or ip in ipaddress.ip_network("64:ff9b:1::/48")
    ):
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if (
        ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or ip == _METADATA
        or str(ip) in ("fd00:ec2::254", "168.63.129.16")
    ):
        return False
    if ip.version == 4 and ip in _FAKE:
        return trusted_proxy
    if ip.is_global and not ip.is_reserved:
        return True
    # Only ordinary private/loopback addresses can receive local approval.
    return local and (
        ip.is_loopback
        or (
            ip in ipaddress.ip_network("10.0.0.0/8")
            if ip.version == 4
            else ip in ipaddress.ip_network("fc00::/7")
        )
        or (
            ip.version == 4
            and (
                ip in ipaddress.ip_network("172.16.0.0/12")
                or ip in ipaddress.ip_network("192.168.0.0/16")
            )
        )
    )


def local_address(value: str) -> bool:
    return address_allowed(value, local=True, trusted_proxy=False) and not address_allowed(
        value, local=False, trusted_proxy=False
    )


@dataclass(frozen=True)
class Destination:
    url: str
    host: str
    port: int
    local: bool = False
    trusted_proxy: bool = False

    def resolve(self, deadline: float | None = None) -> list[tuple]:
        def resolve_answers():
            return socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)

        try:
            if deadline is None:
                answers = resolve_answers()
            else:
                if not _RESOLVER_SLOTS.acquire(timeout=_remaining(deadline)):
                    raise TimeoutError("Provider request timed out.")
                results: queue.Queue = queue.Queue(maxsize=1)

                def run_resolver():
                    try:
                        results.put((resolve_answers(), None))
                    except Exception as exc:
                        results.put((None, exc))
                    finally:
                        _RESOLVER_SLOTS.release()

                try:
                    threading.Thread(target=run_resolver, daemon=True).start()
                except RuntimeError:
                    _RESOLVER_SLOTS.release()
                    raise ResolutionFailed("Provider hostname could not be resolved.") from None
                try:
                    answers, error = results.get(timeout=_remaining(deadline))
                except queue.Empty:
                    raise TimeoutError("Provider request timed out.") from None
                if error is not None:
                    raise error
        except TimeoutError:
            raise
        except OSError:
            raise ResolutionFailed("Provider hostname could not be resolved.") from None
        if not answers:
            raise ResolutionFailed("Provider hostname could not be resolved.")
        if any(
            not address_allowed(str(a[4][0]), local=self.local, trusted_proxy=self.trusted_proxy)
            for a in answers
        ):
            raise DestinationDenied("Provider resolves to a blocked address.")
        if self.url.startswith("http:") and any(not local_address(str(a[4][0])) for a in answers):
            raise DestinationDenied("HTTP is supported only for approved local addresses.")
        return answers


def destination(raw: str, *, local: bool = False, trusted_proxy: bool = False) -> Destination:
    def deny(message: str) -> NoReturn:
        raise DestinationDenied(message)

    if not isinstance(raw, str) or any(c.isspace() or ord(c) < 32 for c in raw):
        deny("Provider URL must not contain whitespace or control characters.")
    try:
        parts = urlsplit(raw)
        host = parts.hostname
        port = parts.port
        if parts.scheme not in ("https", "http") or not host or parts.username is not None:
            deny("Use an HTTPS provider URL without user information.")
        if "#" in raw or "\\" in raw or "%" in host:
            deny("Provider URL contains an unsupported fragment or host.")
        if parts.scheme == "http" and not local:
            deny("HTTP requires explicit approval for this local endpoint.")
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
        try:
            ip = ipaddress.ip_address(host)
            host = str(ip)
        except ValueError:
            if len(host) > 253 or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.split(".")
            ):
                deny("Provider URL has an invalid hostname.")
        else:
            if parts.scheme == "http" and not local_address(host):
                deny("HTTP is supported only for approved local addresses.")
            if not address_allowed(host, local=local, trusted_proxy=trusted_proxy):
                deny("Provider URL targets a blocked address.")
        if port is not None and not 1 <= port <= 65535:
            deny("Provider URL has an invalid port.")
        if any(
            any(
                token in key.casefold().replace("-", "_")
                for token in (
                    "key",
                    "token",
                    "secret",
                    "password",
                    "passwd",
                    "credential",
                    "authorization",
                    "auth",
                    "signature",
                )
            )
            or key.casefold() == "sig"
            for key, _ in parse_qsl(parts.query, keep_blank_values=True)
        ):
            deny("Keep provider credentials in the API key field, not the URL.")
        allowed = set()
        for entry in os.environ.get(ALLOWED_HOSTS_ENV, "").split(","):
            entry = entry.strip().rstrip(".")
            if not entry:
                continue
            try:
                normalized_host = str(ipaddress.ip_address(entry.strip("[]")))
            except ValueError:
                normalized_host = entry.encode("idna").decode("ascii").lower()
            allowed.add(normalized_host)
        if allowed and host not in allowed:
            deny("Provider host is outside the optional operator restriction.")
        path = parts.path
        if path in ("", "/"):
            path = "/v1/chat/completions"
        elif path.rstrip("/") == "/v1":
            path = "/v1/chat/completions"
        authority = f"[{host}]" if ":" in host else host
        default = 443 if parts.scheme == "https" else 80
        if port is not None and port != default:
            authority += f":{port}"
        return Destination(
            urlunsplit(
                (
                    parts.scheme,
                    authority,
                    quote(path, safe="/%:@!$&'()*+,;=-._~"),
                    quote(parts.query, safe="%=&/?@:!$'()*+,;-._~"),
                    "",
                )
            ),
            host,
            port or default,
            local,
            trusted_proxy,
        )
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, DestinationDenied):
            raise
        raise DestinationDenied("Provider URL is malformed or has an invalid port.") from None


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Provider request timed out.")
    return remaining


class _DeadlineReader(io.RawIOBase):
    def __init__(self, sock, deadline: float):
        self.sock = sock
        self.raw = sock.makefile("rb", buffering=0)
        self.deadline = deadline

    def close(self):
        self.raw.close()
        super().close()

    def readable(self):
        return True

    def readinto(self, buffer):
        self.sock.settimeout(_remaining(self.deadline))
        size = self.raw.readinto(buffer)
        _remaining(self.deadline)
        return size


class _DeadlineSocket:
    def __init__(self, sock, deadline: float):
        self.sock = sock
        self.deadline = deadline

    def sendall(self, data):
        self.sock.settimeout(_remaining(self.deadline))
        self.sock.sendall(data)
        _remaining(self.deadline)

    def makefile(self, mode):
        # Every raw read, including slow-drip headers/body, shares the same deadline.
        return io.BufferedReader(_DeadlineReader(self.sock, self.deadline))

    def close(self):
        self.sock.close()


def checked_post(
    endpoint: Destination,
    payload: dict,
    headers: dict[str, str],
    timeout: float,
    max_bytes: int = 1_048_576,
):
    """Resolve once, validate every answer, connect numerically, preserve TLS identity."""
    import httpx

    deadline = time.monotonic() + timeout
    answers = endpoint.resolve(deadline)
    conn = http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=timeout)
    try:
        last_error: OSError | None = None
        for family, kind, proto, _, sockaddr in answers:
            remaining = _remaining(deadline)
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(remaining)
                sock.connect(sockaddr)
                if endpoint.url.startswith("https:"):
                    sock.settimeout(_remaining(deadline))
                    sock = ssl.create_default_context().wrap_socket(
                        sock, server_hostname=endpoint.host
                    )
                _remaining(deadline)
                conn.sock = _DeadlineSocket(sock, deadline)
                break
            except TimeoutError:
                sock.close()
                raise
            except OSError as exc:
                sock.close()
                _remaining(deadline)
                last_error = exc
        else:
            raise OSError("Provider connection failed.") from last_error
        parts = urlsplit(endpoint.url)
        target = parts.path + ("?" + parts.query if parts.query else "")
        # The original normalized hostname, never the selected numeric address.
        request_headers = {k: v for k, v in headers.items() if k.lower() != "host"}
        request_headers["Host"] = parts.netloc
        request_headers["Content-Type"] = "application/json"
        conn.request("POST", target, body=json.dumps(payload).encode(), headers=request_headers)
        response = conn.getresponse()
        body = response.read(max_bytes + 1)
        _remaining(deadline)
        return httpx.Response(response.status, content=body, headers=dict(response.getheaders()))
    finally:
        conn.close()
