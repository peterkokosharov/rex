# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""webfetch tool tests: local http.server on 127.0.0.1, no internet."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from runtimelib.tools import webfetch as wf
from runtimelib.tools.webfetch import WebFetchError, WebFetchTool, html_to_text

HTML = (b"<html><head><title>T</title><style>p{color:red}</style><script>var x=1;</script></head>"
        b"<body><h1>Head</h1><p>Hello <a href='/next'>world</a></p><script>evil()</script></body></html>")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, ctype, body, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path
        if p == "/html":
            self._send(200, "text/html; charset=utf-8", HTML)
        elif p == "/json":
            self._send(200, "application/json", json.dumps({"a": 1}).encode())
        elif p == "/text":
            self._send(200, "text/plain", "café".encode("utf-8"))
        elif p == "/big":
            self._send(200, "text/plain", b"x" * (wf.MAX_BYTES + 5000))
        elif p == "/bin":
            self._send(200, "application/octet-stream", b"\x00\x01")
        elif p == "/loop":
            self._send(302, "text/plain", b"", {"Location": "/loop"})
        elif p == "/redir":
            self._send(302, "text/plain", b"", {"Location": "/text"})
        elif p == "/to-private":
            self._send(302, "text/plain", b"", {"Location": "http://127.0.0.1:%d/text" % self.server.server_address[1]})
        elif p == "/hostile":
            self._send(302, "text/plain", b"", {"Location": "file:///etc/passwd"})
        else:
            self._send(404, "text/plain", b"nope")


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def tool():
    return WebFetchTool(allow_private=True)  # test-only override: the fixture server is loopback


def test_html_to_text(server, tool):
    r = tool.execute(url=server + "/html")
    assert r["status"] == 200 and r["content_type"] == "text/html" and r["truncated"] is False
    assert "# Head" in r["text"]
    assert f"world ({server}/next)" in r["text"]
    assert "evil" not in r["text"] and "color:red" not in r["text"] and "var x" not in r["text"]


def test_html_to_text_function():
    assert html_to_text("<h2>A</h2><p>x<br>y</p>") == "## A\nx\ny"


def test_json_and_text_as_is(server, tool):
    assert json.loads(tool.execute(url=server + "/json")["text"]) == {"a": 1}
    r = tool.execute(url=server + "/text")
    assert r["text"] == "café" and r["content_type"] == "text/plain"


def test_size_cap_and_max_chars(server, tool):
    r = tool.execute(url=server + "/big", max_chars=10 ** 9)
    assert r["truncated"] is True and len(r["text"]) == wf.MAX_CHARS_LIMIT
    r = tool.execute(url=server + "/text", max_chars=2)
    assert r["text"] == "ca" and r["truncated"] is True


def test_redirect_followed_and_loop_refused(server, tool):
    r = tool.execute(url=server + "/redir")
    assert r["url"].endswith("/text") and r["text"] == "café"
    with pytest.raises(WebFetchError, match="too many redirects"):
        tool.execute(url=server + "/loop")


def test_redirect_to_bad_scheme_refused(server, tool):
    with pytest.raises(WebFetchError, match="only http"):
        tool.execute(url=server + "/hostile")


def test_non_http_scheme(tool):
    for u in ("file:///etc/passwd", "ftp://example.com/x", "gopher://x", "javascript:alert(1)", "http://"):
        with pytest.raises(WebFetchError):
            tool.execute(url=u)


def test_private_refused_by_default(server):
    with pytest.raises(WebFetchError, match="private or local"):
        WebFetchTool().execute(url=server + "/text")
    for u in ("http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/", "http://10.0.0.1/",
              "http://192.168.1.1/", "http://0.0.0.0/", "http://[::ffff:127.0.0.1]/"):
        with pytest.raises(WebFetchError, match="private or local"):
            WebFetchTool().execute(url=u)


def test_dns_rebinding_pinned(monkeypatch, server):
    """A name resolving to a public and a private address is refused; the checked IP is the one used."""
    port = int(server.rsplit(":", 1)[1])
    calls = []

    def fake_gai(host, p, *a, **k):
        calls.append(host)
        return [(2, 1, 6, "", ("8.8.8.8", p)), (2, 1, 6, "", ("127.0.0.1", p))]

    monkeypatch.setattr(wf.socket, "getaddrinfo", fake_gai)
    with pytest.raises(WebFetchError, match="private or local"):
        WebFetchTool().execute(url=f"http://rebind.test:{port}/text")
    # Allowed mode: connection goes to the pinned first IP (8.8.8.8 would not answer); use loopback first.
    monkeypatch.setattr(wf.socket, "getaddrinfo",
                        lambda h, p, *a, **k: [(2, 1, 6, "", ("127.0.0.1", p))])
    r = WebFetchTool(allow_private=True).execute(url=f"http://pinned.test:{port}/text")
    assert r["text"] == "café"


def test_public_to_private_redirect_refused(monkeypatch, server):
    """The first hop looks public; the redirect target is loopback and must be refused."""
    port = int(server.rsplit(":", 1)[1])
    real = wf.resolve_public

    def fake(host, p, allow_private=False):
        if host == "public.test":
            return "127.0.0.1"  # stands in for a public address that happens to reach our test server
        return real(host, p, allow_private)

    monkeypatch.setattr(wf, "resolve_public", fake)
    with pytest.raises(WebFetchError, match="private or local"):
        WebFetchTool().execute(url=f"http://public.test:{port}/to-private")


def test_one_getaddrinfo_per_hop(monkeypatch, server):
    port = int(server.rsplit(":", 1)[1])
    calls = []

    def fake_gai(host, p, *a, **k):
        calls.append(host)
        return [(2, 1, 6, "", ("127.0.0.1", p))]

    monkeypatch.setattr(wf.socket, "getaddrinfo", fake_gai)
    r = WebFetchTool(allow_private=True).execute(url=f"http://hop.test:{port}/redir")  # 302 -> /text
    assert r["text"] == "café"
    # exactly one name lookup per hop (create_connection also "resolves" the pinned IP literal)
    assert [c for c in calls if c == "hop.test"] == ["hop.test", "hop.test"]
    assert set(calls) <= {"hop.test", "127.0.0.1"}


def test_nat64_embedded_private_blocked():
    import ipaddress
    assert wf._ip_blocked(ipaddress.ip_address("64:ff9b::7f00:1"))
    assert wf._ip_blocked(ipaddress.ip_address("64:ff9b::a00:1"))


def test_binary_refused(server, tool):
    with pytest.raises(WebFetchError, match="content type"):
        tool.execute(url=server + "/bin")


def test_404_returns_status(server, tool):
    r = tool.execute(url=server + "/missing")
    assert r["status"] == 404 and r["text"] == "nope"


def test_schema_and_extra_kwargs(server, tool):
    assert tool.schema.name == "webfetch"
    r = tool.execute(url=server + "/text", _role="x", _agent_id="y")
    assert r["status"] == 200
    with pytest.raises(WebFetchError):
        tool.execute()
