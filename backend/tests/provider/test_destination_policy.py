"""Destination policy and DNS-bound transport regressions; no paid provider calls."""

import socket

import pytest

from x_insight.reasoning import provider_destination as policy


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://EXAMPLE.com", "https://example.com/v1/chat/completions"),
        ("https://example.com/v1/", "https://example.com/v1/chat/completions"),
        ("https://example.com/custom", "https://example.com/custom"),
        ("https://[2606:4700::1111]:443/v1", "https://[2606:4700::1111]/v1/chat/completions"),
    ],
)
def test_normalizes_endpoint(raw, expected, monkeypatch):
    monkeypatch.delenv(policy.ALLOWED_HOSTS_ENV, raising=False)
    assert policy.destination(raw).url == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com:99999",
        "https://example.com/#secret",
        "https://example.com?api_key=secret",
        "https://127.0.0.1",
        "https://[::ffff:127.0.0.1]",
        "https://169.254.169.254",
        "https://0.0.0.0",
        "https://example.com/a b",
    ],
)
def test_unsafe_url_denied(url, monkeypatch):
    monkeypatch.delenv(policy.ALLOWED_HOSTS_ENV, raising=False)
    with pytest.raises(policy.DestinationDenied):
        policy.destination(url)


def test_narrow_local_and_proxy_approval(monkeypatch):
    monkeypatch.delenv(policy.ALLOWED_HOSTS_ENV, raising=False)
    assert policy.destination("http://127.0.0.1:8000", local=True)
    for ip in ["169.254.169.254", "0.0.0.0", "100.100.100.200"]:
        with pytest.raises(policy.DestinationDenied):
            policy.destination(f"http://{ip}", local=True)
    assert policy.address_allowed("198.18.0.1", local=False, trusted_proxy=True)
    assert not policy.address_allowed("198.18.0.1", local=True, trusted_proxy=False)
    assert not policy.address_allowed("10.0.0.1", local=False, trusted_proxy=True)


def test_mixed_dns_and_rebinding_denied(monkeypatch):
    monkeypatch.delenv(policy.ALLOWED_HOSTS_ENV, raising=False)
    endpoint = policy.destination("https://example.com")
    answers = ["93.184.216.34", "127.0.0.1"]
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in answers],
    )
    with pytest.raises(policy.DestinationDenied):
        endpoint.resolve()
    answers[:] = ["93.184.216.34"]
    assert endpoint.resolve()[0][4][0] == answers[0]
    answers[:] = ["169.254.169.254"]
    with pytest.raises(policy.DestinationDenied):
        endpoint.resolve()


def test_legacy_nonempty_restricts_hosts(monkeypatch):
    monkeypatch.setenv(policy.ALLOWED_HOSTS_ENV, "other.example")
    with pytest.raises(policy.DestinationDenied):
        policy.destination("https://example.com")
    monkeypatch.setenv(policy.ALLOWED_HOSTS_ENV, "")
    assert policy.destination("https://example.com")


@pytest.mark.parametrize("address, proxy", [("93.184.216.34", False), ("198.18.0.128", True)])
def test_socket_uses_checked_numeric_address_and_original_tls_host(monkeypatch, address, proxy):
    import io
    import ssl

    monkeypatch.delenv(policy.ALLOWED_HOSTS_ENV, raising=False)
    calls = []

    class Socket:
        def __init__(self):
            self.body = io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")

        def recv_into(self, buffer):
            return self.body.readinto(buffer)

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            calls.append(("connect", address))

        def sendall(self, data):
            calls.append(("send", data))

        def makefile(self, *args, **kwargs):
            return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")

        def close(self):
            pass

    class TLS:
        def wrap_socket(self, sock, *, server_hostname):
            calls.append(("tls", server_hostname))
            return sock

    def resolve(*args, **kwargs):
        calls.append(("dns", args[0]))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", lambda *a: Socket())
    monkeypatch.setattr(ssl, "create_default_context", lambda: TLS())
    monkeypatch.setenv("HTTPS_PROXY", "http://169.254.169.254:80")
    response = policy.checked_post(
        policy.destination("https://example.com/v1", trusted_proxy=proxy),
        {},
        {"Authorization": "Bearer sentinel"},
        1,
    )
    assert response.status_code == 200
    assert calls.count(("dns", "example.com")) == 1
    assert ("connect", (address, 443)) in calls
    assert ("tls", "example.com") in calls
    sent = b"".join(v for k, v in calls if k == "send")
    assert b"Host: example.com\r\n" in sent
    assert b"Authorization: Bearer sentinel\r\n" in sent


@pytest.mark.parametrize("ip", ["2002:7f00:1::", "64:ff9b::7f00:1", "2001::1"])
def test_transitional_ipv6_denied(ip):
    assert not policy.address_allowed(ip, local=True, trusted_proxy=True)


@pytest.mark.parametrize("address", ["169.254.169.254", "198.18.0.128", "::ffff:127.0.0.1"])
def test_denied_dns_never_creates_socket_or_sends_bearer(monkeypatch, address):
    monkeypatch.delenv(policy.ALLOWED_HOSTS_ENV, raising=False)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
    )
    monkeypatch.setattr(socket, "socket", lambda *a: pytest.fail("Denied address connected"))
    with pytest.raises(policy.DestinationDenied):
        policy.checked_post(
            policy.destination("https://example.com"), {}, {"Authorization": "Bearer sentinel"}, 1
        )


@pytest.mark.parametrize(
    "query",
    [
        "auth=secret",
        "sIG=secret",
        "Signature=secret",
        "passwd=secret",
        "%61uth=secret",
        "API%5fKEY=secret",
    ],
)
def test_credential_query_aliases_denied(query, monkeypatch):
    monkeypatch.delenv(policy.ALLOWED_HOSTS_ENV, raising=False)
    with pytest.raises(policy.DestinationDenied):
        policy.destination("https://example.com/custom?" + query)


def test_benign_version_query_preserved(monkeypatch):
    monkeypatch.delenv(policy.ALLOWED_HOSTS_ENV, raising=False)
    assert policy.destination("https://example.com/custom?api-version=2026-01").url.endswith(
        "/custom?api-version=2026-01"
    )


def test_dns_resolution_obeys_total_request_deadline(monkeypatch):
    import time

    monkeypatch.delenv(policy.ALLOWED_HOSTS_ENV, raising=False)

    def slow_dns(*args, **kwargs):
        time.sleep(0.15)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", slow_dns)
    monkeypatch.setattr(socket, "socket", lambda *args: pytest.fail("DNS timed out before connect"))
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        policy.checked_post(policy.destination("https://example.com"), {}, {}, 0.02)
    assert time.monotonic() - started < 0.1


def test_slow_drip_headers_cannot_reset_total_request_deadline(monkeypatch):
    import time

    monkeypatch.delenv(policy.ALLOWED_HOSTS_ENV, raising=False)

    class DripSocket:
        def makefile(self, *args, **kwargs):
            import io

            class Raw(io.RawIOBase):
                def readinto(raw_self, buffer):
                    return self.recv_into(buffer)

            return Raw()

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            pass

        def sendall(self, data):
            pass

        def recv_into(self, buffer):
            time.sleep(0.015)
            buffer[0] = ord("H")
            return 1

        def close(self):
            pass

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
    )
    monkeypatch.setattr(socket, "socket", lambda *args: DripSocket())
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        policy.checked_post(policy.destination("http://127.0.0.1", local=True), {}, {}, 0.04)
    assert time.monotonic() - started < 0.1


def test_address_attempts_share_one_deadline_and_close_sockets(monkeypatch):
    import time

    monkeypatch.delenv(policy.ALLOWED_HOSTS_ENV, raising=False)
    clock = [0.0]
    timeouts, closed = [], []
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
            for ip in ("93.184.216.34", "93.184.216.35")
        ],
    )

    class FailedSocket:
        def settimeout(self, timeout):
            timeouts.append(timeout)

        def connect(self, address):
            clock[0] += 0.03
            raise OSError("fixture connect failed")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(socket, "socket", lambda *args: FailedSocket())
    with pytest.raises(TimeoutError):
        policy.checked_post(policy.destination("https://example.com"), {}, {}, 0.05)
    assert timeouts == pytest.approx([0.05, 0.02])
    assert len(closed) == 2
