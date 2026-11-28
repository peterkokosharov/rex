# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License

"""Web fetch tool: GET one http(s) URL and return it as text.

Runs in the rex host process (not in the guard). Safety rules:

- http/https only, GET only, 20 s overall deadline, 2 MB download cap.
- At most 5 redirects; every hop is checked again (scheme and address), and a
  hop to another host is checked against the permission rules (``redirect_check``).
- A host is refused when ANY of its resolved addresses is private, loopback,
  link-local, multicast, reserved or unspecified (unless ``allow_private``).
- DNS rebinding: the name is resolved once, checked, and the connection is made
  to that checked IP address (TLS still verifies the original host name), so a
  second DNS answer cannot redirect the connection.

Environment proxies are deliberately not used: a proxy would do its own name
resolution and bypass the address check.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import time
from html.parser import HTMLParser
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlsplit

from .base import Tool, ToolSchema
from kikolib.http import ca_context

TIMEOUT = 20.0
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
DEFAULT_MAX_CHARS = 20000
MAX_CHARS_LIMIT = 200_000
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
USER_AGENT = "rex-webfetch/1.0"


class WebFetchError(ValueError):
    """A fetch that was refused or failed; the message is safe to show the model."""


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:  # NAT64 well-known prefix: judge the embedded IPv4 address
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return (not ip.is_global or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified or ip.is_private)


def resolve_public(host: str, port: int, allow_private: bool = False) -> str:
    """Resolve ``host`` and return one IP to connect to; refuse if any address is non-public."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebFetchError(f"cannot resolve host {host!r}: {exc}") from None
    ips: list[str] = []
    for info in infos:
        ip_text = info[4][0].split("%", 1)[0]
        if ip_text not in ips:
            ips.append(ip_text)
    if not ips:
        raise WebFetchError(f"no address for host {host!r}")
    if not allow_private:
        for ip_text in ips:
            if _ip_blocked(ipaddress.ip_address(ip_text)):
                raise WebFetchError(f"refused: {host!r} resolves to a private or local address")
    return ips[0]


def _arm_timeout(conn: http.client.HTTPConnection, deadline: float) -> None:
    """Limit the next socket wait to the time left of the overall deadline."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise WebFetchError("timeout")
    if conn.sock is not None:
        conn.sock.settimeout(left)


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, ip: str, timeout: float, context: ssl.SSLContext) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip

    def connect(self) -> None:
        raw = socket.create_connection((self._ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


_SKIP = {"script", "style", "noscript", "template", "svg"}
_BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "nav", "aside", "ul", "ol",
          "li", "tr", "table", "br", "hr", "pre", "blockquote", "form", "dl", "dt", "dd", "title"}


class _TextExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.parts: list[str] = []
        self._skip = 0
        self._links: list[str | None] = []

    def _nl(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._nl()
            self.parts.append("#" * int(tag[1]) + " ")
        elif tag in _BLOCK:
            self._nl()
        if tag == "a":
            href = dict(attrs).get("href")
            self._links.append(urljoin(self.base, href) if href and not href.lower().startswith(
                ("javascript:", "#", "mailto:")) else None)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("br", "hr"):
            self._nl()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "a" and self._links:
            url = self._links.pop()
            if url:
                self.parts.append(f" ({url})")
        elif tag in _BLOCK or tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._nl()

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in raw.split("\n")]
        out: list[str] = []
        for ln in lines:
            if ln or (out and out[-1]):
                out.append(ln)
        return "\n".join(out).strip()


def html_to_text(html: str, base_url: str = "") -> str:
    """Plain text of an HTML page: scripts/styles dropped, headings as '# ', links as 'text (url)'."""
    parser = _TextExtractor(base_url)
    parser.feed(html)
    parser.close()
    return parser.text()


def _charset(content_type: str, body: bytes) -> str:
    m = re.search(r"charset=([\w.-]+)", content_type, re.I)
    if not m:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w.-]+)", body[:2048], re.I)
        name = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    else:
        name = m.group(1)
    try:
        "".encode(name)
    except LookupError:
        return "utf-8"
    return name


class WebFetchTool(Tool):
    """Fetch a web page or text resource."""

    def __init__(self, allow_private: bool = False) -> None:
        # allow_private is set by the host (config webfetch.allow_private, tests); never by the model.
        self.allow_private = allow_private
        # Set by the agent loop to the permission gate: (from_url, to_url) -> refusal
        # reason or None. Without it every redirect is followed (scheme/address checks only).
        self.redirect_check: Optional[Callable[[str, str], Optional[str]]] = None
        schema = ToolSchema(
            name="webfetch",
            description=("Fetch one http(s) URL with GET and return its content as text (HTML is "
                         "converted to plain text). Private and local addresses are refused.\n"
                         "Use it to read documentation pages found with websearch.\n"
                         'Example: {"url": "https://maven.apache.org/surefire/", "max_chars": 20000}'),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http:// or https:// URL"},
                    "max_chars": {"type": "integer", "description": "Maximum characters returned",
                                  "default": DEFAULT_MAX_CHARS},
                },
                "required": ["url"],
            },
        )
        super().__init__("webfetch", "Fetch a URL as text", schema)

    def _get(self, url: str, deadline: float) -> tuple[int, http.client.HTTPMessage, bytes, bool]:
        u = urlsplit(url)
        scheme = u.scheme.lower()
        if scheme not in ("http", "https") or not u.hostname:
            raise WebFetchError("only http:// and https:// URLs with a host are allowed")
        host = u.hostname
        try:
            port = u.port or (443 if scheme == "https" else 80)
        except ValueError:
            raise WebFetchError("invalid port in URL") from None
        ip = resolve_public(host, port, self.allow_private)
        left = deadline - time.monotonic()
        if left <= 0:
            raise WebFetchError("timeout")
        if scheme == "https":
            conn: http.client.HTTPConnection = _PinnedHTTPS(host, port, ip, left, ca_context())
        else:
            conn = _PinnedHTTP(host, port, ip, left)
        try:
            target = (u.path or "/") + (f"?{u.query}" if u.query else "")
            conn.request("GET", target, headers={
                "Host": u.netloc.rpartition("@")[2], "User-Agent": USER_AGENT,
                "Accept-Encoding": "identity", "Accept": "text/html,text/*,application/json;q=0.9,*/*;q=0.5"})
            _arm_timeout(conn, deadline)
            resp = conn.getresponse()
            chunks: list[bytes] = []
            total = 0
            truncated = False
            if not (300 <= resp.status < 400):
                while True:
                    _arm_timeout(conn, deadline)
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BYTES:
                        chunks.append(chunk[: len(chunk) - (total - MAX_BYTES)])
                        truncated = True
                        break
                    chunks.append(chunk)
            return resp.status, resp.msg, b"".join(chunks), truncated
        except (OSError, http.client.HTTPException) as exc:
            raise WebFetchError(f"request to {host} failed: {type(exc).__name__}") from None
        finally:
            conn.close()

    def execute(self, **kwargs: Any) -> Any:
        url = kwargs.get("url")
        if not isinstance(url, str) or not url.strip():
            raise WebFetchError("url is required")
        try:
            max_chars = int(kwargs.get("max_chars") or DEFAULT_MAX_CHARS)
        except (TypeError, ValueError):
            max_chars = DEFAULT_MAX_CHARS
        max_chars = min(max(1, max_chars), MAX_CHARS_LIMIT)
        url = url.strip()
        deadline = time.monotonic() + TIMEOUT
        for _ in range(MAX_REDIRECTS + 1):
            status, headers, body, cut = self._get(url, deadline)
            if 300 <= status < 400 and headers.get("Location"):
                target = urljoin(url, headers["Location"])
                if self.redirect_check is not None:
                    refusal = self.redirect_check(url, target)
                    if refusal:
                        raise WebFetchError(refusal)
                url = target
                continue
            break
        else:
            raise WebFetchError(f"too many redirects (more than {MAX_REDIRECTS})")
        if 300 <= status < 400:
            raise WebFetchError(f"redirect status {status} without a Location header")
        ctype = headers.get("Content-Type", "") or ""
        mime = ctype.split(";", 1)[0].strip().lower()
        textual = (mime.startswith("text/") or mime in ("", "application/json", "application/xml",
                   "application/xhtml+xml", "application/javascript") or mime.endswith(("+json", "+xml")))
        if not textual:
            raise WebFetchError(f"unsupported content type {mime!r}")
        text = body.decode(_charset(ctype, body), "replace")
        if mime in ("text/html", "application/xhtml+xml"):
            text = html_to_text(text, url)
        truncated = cut
        if len(text) > max_chars:
            text = text[:max_chars]
            truncated = True
        return {"url": url, "status": status, "content_type": mime, "text": text, "truncated": truncated}


def create_webfetch_tool(allow_private: bool = False) -> WebFetchTool:
    return WebFetchTool(allow_private=allow_private)
