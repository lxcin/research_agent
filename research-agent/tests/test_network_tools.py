# tests/test_network_tools.py — network plugin: SSRF policy, fetch, search.
# Fully offline: no test performs a real network request.
import socket
import sys
import types

import httpx
import pytest

from research_agent.tools.builtin import network
from research_agent.tools.builtin.network import (
    _handle_web_fetch,
    _handle_web_search,
    _strip_html,
    extract_text,
    validate_url,
)


def _cfg(**over) -> dict:
    cfg = {
        "timeout": 5,
        "max_bytes": 1000,
        "max_chars": 500,
        "max_redirects": 3,
        "allow_private": False,
        "allow_domains": [],
        "deny_domains": [],
        "search_provider": "none",
        "search_api_key_env": "TEST_SEARCH_KEY",
        "search_max_results": 5,
        "user_agent": "test-agent",
    }
    cfg.update(over)
    return cfg


@pytest.fixture
def cfg(monkeypatch):
    c = _cfg()
    monkeypatch.setattr(network, "_web_cfg", lambda: c)
    return c


# ── SSRF / URL policy ──

def test_validate_scheme_blocked(cfg):
    assert validate_url("ftp://example.com/") is not None
    assert validate_url("file:///etc/passwd") is not None
    assert validate_url("") is not None


def test_validate_blocks_loopback(cfg):
    assert validate_url("http://127.0.0.1:8080/") is not None


def test_validate_blocks_cloud_metadata(cfg):
    assert validate_url("http://169.254.169.254/latest/meta-data/") is not None


def test_validate_blocks_private_ranges(cfg):
    assert validate_url("http://10.1.2.3/") is not None
    assert validate_url("http://192.168.1.1/") is not None
    assert validate_url("http://172.16.5.5/") is not None


def test_validate_blocks_ipv6_loopback(cfg):
    assert validate_url("http://[::1]/") is not None


def test_validate_allows_public_ip(cfg):
    assert validate_url("http://93.184.216.34/") is None


def test_is_blocked_ipv6_special_use_allowed():
    # 2001::/23 (Teredo etc.) is special-use, NOT internal → must not be blocked
    assert network._is_blocked_ip("2001::1") is False
    assert network._is_blocked_ip("2001:4860:4860::8888") is False


def test_is_blocked_ipv6_internal_ranges():
    assert network._is_blocked_ip("fc00::1") is True       # ULA
    assert network._is_blocked_ip("fe80::1") is True       # link-local
    assert network._is_blocked_ip("ff02::1") is True       # multicast
    assert network._is_blocked_ip("2001:db8::1") is True   # documentation
    assert network._is_blocked_ip("::") is True            # unspecified


def test_is_blocked_unwraps_ipv4_mapped():
    assert network._is_blocked_ip("::ffff:127.0.0.1") is True
    assert network._is_blocked_ip("::ffff:10.0.0.1") is True
    assert network._is_blocked_ip("::ffff:8.8.8.8") is False


def test_is_blocked_unwraps_6to4_and_nat64():
    assert network._is_blocked_ip("2002:0a00:0001::") is True       # 6to4 → 10.0.0.1
    assert network._is_blocked_ip("64:ff9b::a00:1") is True         # NAT64 → 10.0.0.1
    assert network._is_blocked_ip("64:ff9b::808:808") is False      # NAT64 → 8.8.8.8


def test_validate_allows_host_with_special_use_aaaa(cfg, monkeypatch):
    # real-world case: wikipedia returns public IPv4 + bogus 2001::1
    monkeypatch.setattr(network, "_resolve_ips",
                        lambda h: ["199.16.158.12", "2001::1"])
    assert validate_url("https://en.wikipedia.org/") is None


def test_validate_blocks_host_if_any_ip_private(cfg, monkeypatch):
    monkeypatch.setattr(network, "_resolve_ips",
                        lambda h: ["93.184.216.34", "10.0.0.1"])
    assert validate_url("https://mixed.example/") is not None


def test_validate_localhost_disabled_by_default(cfg):
    assert validate_url("http://127.0.0.1:8080/") is not None


def test_validate_localhost_allowed_when_enabled(cfg):
    cfg["allow_localhost"] = True
    assert validate_url("http://127.0.0.1:8080/") is None
    assert validate_url("http://localhost:3000/") is None


def test_allow_localhost_does_not_open_other_private_ranges(cfg):
    cfg["allow_localhost"] = True
    assert validate_url("http://10.0.0.5/") is not None
    assert validate_url("http://192.168.1.1/") is not None


def test_validate_deny_list(cfg, monkeypatch):
    monkeypatch.setattr(network, "_resolve_ips", lambda h: ["93.184.216.34"])
    cfg["deny_domains"] = ["evil.com"]
    assert validate_url("https://sub.evil.com/") is not None
    assert validate_url("https://good.com/") is None


def test_validate_allow_list(cfg, monkeypatch):
    monkeypatch.setattr(network, "_resolve_ips", lambda h: ["93.184.216.34"])
    cfg["allow_domains"] = ["good.com"]

    assert validate_url("https://good.com/a") is None
    assert validate_url("https://api.good.com/a") is None
    assert validate_url("https://other.com/a") is not None


def test_validate_dns_failure(cfg, monkeypatch):
    monkeypatch.setattr(network, "_resolve_ips", lambda h: [])
    assert validate_url("https://nope.invalid/") is not None


# ── HTML → text ──

def test_strip_html_removes_scripts_and_styles():
    raw = ("<html><head><style>.x{color:red}</style><script>alert(1)</script></head>"
           "<body><h1>标题</h1><p>正文 &amp; 更多</p></body></html>")
    text = _strip_html(raw)
    assert "标题" in text and "正文 & 更多" in text
    assert "alert(1)" not in text
    assert "color:red" not in text


def test_extract_text_fallback(monkeypatch):
    monkeypatch.setitem(sys.modules, "trafilatura", None)
    assert "正文" in extract_text("<div><p>正文</p></div>")


def test_extract_text_uses_trafilatura_when_available(monkeypatch):
    import types
    fake = types.ModuleType("trafilatura")
    fake.extract = lambda raw, **kw: "CLEAN:" + raw
    monkeypatch.setitem(sys.modules, "trafilatura", fake)
    assert extract_text("<p>x</p>").startswith("CLEAN:")


def test_is_blocked_ip_handles_garbage():
    assert network._is_blocked_ip("not-an-ip") is True
    assert network._is_blocked_ip("8.8.8.8") is False


# ── fake HTTP plumbing ──

class _FakeResponse:
    def __init__(self, status_code=200, headers=None, body=b"",
                 url="https://example.com/", redirect=False, encoding="utf-8"):
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {})
        self._body = body
        self.url = httpx.URL(url)
        self.is_redirect = redirect
        self.encoding = encoding

    def iter_bytes(self):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, method, url):
        self.calls.append((method, str(url)))
        return self._responses.pop(0)


def _patch_client(monkeypatch, responses) -> _FakeClient:
    client = _FakeClient(responses)
    monkeypatch.setattr(network.httpx, "Client", lambda **kw: client)
    return client


# ── web_fetch ──

def test_web_fetch_extracts_html(cfg, monkeypatch):
    monkeypatch.setitem(sys.modules, "trafilatura", None)
    body = b"<html><body><h1>Hello</h1><script>x()</script><p>World</p></body></html>"
    _patch_client(monkeypatch, [_FakeResponse(200, {"content-type": "text/html; charset=utf-8"}, body)])
    res = _handle_web_fetch({"url": "http://93.184.216.34/"}, None, None, lambda *a: None)
    assert res.success
    assert res.data["source"] == "html"
    assert "Hello" in res.data["content"] and "World" in res.data["content"]
    assert "x()" not in res.data["content"]


def test_web_fetch_returns_json_text(cfg, monkeypatch):
    body = b'{"ok": true, "n": 3}'
    _patch_client(monkeypatch, [_FakeResponse(200, {"content-type": "application/json"}, body)])
    res = _handle_web_fetch({"url": "http://93.184.216.34/api"}, None, None, lambda *a: None)
    assert res.success
    assert res.data["source"] == "text"
    assert '{"ok": true' in res.data["content"]


def test_web_fetch_rejects_binary(cfg, monkeypatch):
    _patch_client(monkeypatch, [_FakeResponse(200, {"content-type": "image/png"}, b"\x89PNG...")])
    res = _handle_web_fetch({"url": "http://93.184.216.34/f.png"}, None, None, lambda *a: None)
    assert not res.success
    assert "content-type" in res.data["error"].lower()


def test_web_fetch_blocks_ssrf_before_request(cfg, monkeypatch):
    client = _patch_client(monkeypatch, [])
    res = _handle_web_fetch({"url": "http://169.254.169.254/latest/meta-data/"},
                            None, None, lambda *a: None)
    assert not res.success
    assert "SSRF" in res.data["error"] or "non-public" in res.data["error"]
    assert client.calls == []


def test_web_fetch_caps_bytes(cfg, monkeypatch):
    cfg["max_bytes"] = 10
    monkeypatch.setitem(sys.modules, "trafilatura", None)
    body = b"<p>" + b"a" * 200 + b"</p>"
    _patch_client(monkeypatch, [_FakeResponse(200, {"content-type": "text/html"}, body)])
    res = _handle_web_fetch({"url": "http://93.184.216.34/"}, None, None, lambda *a: None)
    assert res.success
    assert res.data["truncated"] is True
    assert res.data["bytes"] <= 10


def test_web_fetch_redirect_to_private_is_blocked(cfg, monkeypatch):
    first = _FakeResponse(302, {"location": "http://127.0.0.1/secret"}, b"", redirect=True)
    client = _patch_client(monkeypatch, [first])
    res = _handle_web_fetch({"url": "http://93.184.216.34/"}, None, None, lambda *a: None)
    assert not res.success
    assert len(client.calls) == 1  # next hop rejected before any request


def test_web_fetch_http_error_status(cfg, monkeypatch):
    _patch_client(monkeypatch, [_FakeResponse(404, {"content-type": "text/html"}, b"nope")])
    res = _handle_web_fetch({"url": "http://93.184.216.34/missing"}, None, None, lambda *a: None)
    assert not res.success
    assert "404" in res.data["error"]


def test_web_fetch_follows_public_redirect(cfg, monkeypatch):
    monkeypatch.setitem(sys.modules, "trafilatura", None)
    first = _FakeResponse(301, {"location": "http://93.184.216.34/final"}, b"",
                          redirect=True, url="http://93.184.216.34/start")
    second = _FakeResponse(200, {"content-type": "text/html"}, b"<p>Final</p>",
                           url="http://93.184.216.34/final")
    client = _patch_client(monkeypatch, [first, second])
    res = _handle_web_fetch({"url": "http://93.184.216.34/start"}, None, None, lambda *a: None)
    assert res.success
    assert "Final" in res.data["content"]
    assert len(client.calls) == 2


def test_web_fetch_too_many_redirects(cfg, monkeypatch):
    cfg["max_redirects"] = 1
    r1 = _FakeResponse(302, {"location": "http://93.184.216.34/b"}, redirect=True)
    r2 = _FakeResponse(302, {"location": "http://93.184.216.34/c"}, redirect=True)
    _patch_client(monkeypatch, [r1, r2])
    res = _handle_web_fetch({"url": "http://93.184.216.34/a"}, None, None, lambda *a: None)
    assert not res.success
    assert "redirect" in res.data["error"].lower()


def test_web_fetch_handles_transport_error(cfg, monkeypatch):
    class _BoomClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def stream(self, method, url):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(network.httpx, "Client", lambda **kw: _BoomClient())
    res = _handle_web_fetch({"url": "http://93.184.216.34/"}, None, None, lambda *a: None)
    assert not res.success
    assert "HTTP error" in res.data["error"]


def test_web_fetch_decodes_charset(cfg, monkeypatch):
    body = "中文正文".encode("gbk")
    resp = _FakeResponse(200, {"content-type": "text/plain; charset=gbk"}, body, encoding="gbk")
    _patch_client(monkeypatch, [resp])
    res = _handle_web_fetch({"url": "http://93.184.216.34/gbk"}, None, None, lambda *a: None)
    assert res.success
    assert "中文正文" in res.data["content"]


# ── web_search ──

def test_web_search_needs_provider(cfg):
    res = _handle_web_search({"query": "x"}, None, None, lambda *a: None)
    assert not res.success
    assert "provider" in res.data["error"].lower()


def test_web_search_duckduckgo_parses(cfg, monkeypatch):
    cfg["search_provider"] = "duckduckgo"
    page = (b'<a rel="nofollow" class="result__a" '
            b'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fpaper">'
            b'<b>Paper</b> Title</a>')

    def _fake_fetch(url, c, method="GET"):
        return {"url": url, "status": 200, "content_type": "text/html",
                "bytes": len(page), "truncated": False, "encoding": "utf-8",
                "body": page}, None

    monkeypatch.setattr(network, "_fetch", _fake_fetch)
    res = _handle_web_search({"query": "papers"}, None, None, lambda *a: None)
    assert res.success
    assert res.data["count"] == 1
    item = res.data["results"][0]
    assert item["url"] == "https://example.org/paper"
    assert "Paper" in item["title"]


def test_unwrap_ddg_leaves_plain_url():
    assert network._unwrap_ddg("https://plain.org/x") == "https://plain.org/x"


def test_web_search_unknown_provider(cfg):
    cfg["search_provider"] = "bing"
    res = _handle_web_search({"query": "x"}, None, None, lambda *a: None)
    assert not res.success
    assert "Unknown search provider" in res.data["error"]


def test_web_search_serper_parses(cfg, monkeypatch):
    cfg["search_provider"] = "serper"
    monkeypatch.setenv("TEST_SEARCH_KEY", "k")

    class _Post:
        def raise_for_status(self):
            return None

        def json(self):
            return {"organic": [{"title": "T", "link": "https://s.org", "snippet": "sn"}]}

    monkeypatch.setattr(network.httpx, "post", lambda *a, **k: _Post())
    res = _handle_web_search({"query": "x"}, None, None, lambda *a: None)
    assert res.success
    assert res.data["results"][0]["url"] == "https://s.org"


def test_web_search_tavily_missing_key(cfg, monkeypatch):
    cfg["search_provider"] = "tavily"
    monkeypatch.delenv("TEST_SEARCH_KEY", raising=False)
    res = _handle_web_search({"query": "x"}, None, None, lambda *a: None)
    assert not res.success
    assert "API key" in res.data["error"]


def test_web_search_tavily_parses(cfg, monkeypatch):
    cfg["search_provider"] = "tavily"
    monkeypatch.setenv("TEST_SEARCH_KEY", "k")

    class _Post:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"title": "T", "url": "https://x.org", "content": "s"}]}

    monkeypatch.setattr(network.httpx, "post", lambda *a, **k: _Post())
    res = _handle_web_search({"query": "x"}, None, None, lambda *a: None)
    assert res.success
    assert res.data["results"][0]["url"] == "https://x.org"


# ── plugin lifecycle / gating ──

# ── PDF / document extraction ──

def _fake_pypdf(pages):
    fake = types.ModuleType("pypdf")

    class _Page:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class _Reader:
        def __init__(self, stream):
            self.pages = [_Page(t) for t in pages]

    fake.PdfReader = _Reader
    return fake


def test_web_fetch_parses_pdf(cfg, monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", _fake_pypdf(["Page one", "Page two"]))
    body = b"%PDF-1.7 fake"
    resp = _FakeResponse(200, {"content-type": "application/pdf"}, body)
    _patch_client(monkeypatch, [resp])
    res = _handle_web_fetch({"url": "http://93.184.216.34/p.pdf"}, None, None, lambda *a: None)
    assert res.success
    assert res.data["source"] == "pdf"
    assert "Page one" in res.data["content"] and "Page two" in res.data["content"]


def test_web_fetch_sniffs_pdf_without_content_type(cfg, monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", _fake_pypdf(["sniffed"]))
    resp = _FakeResponse(200, {"content-type": "application/octet-stream"}, b"%PDF-1.4 x")
    _patch_client(monkeypatch, [resp])
    res = _handle_web_fetch({"url": "http://93.184.216.34/x"}, None, None, lambda *a: None)
    assert res.success
    assert res.data["source"] == "pdf"
    assert "sniffed" in res.data["content"]


def test_web_fetch_pdf_without_parser(cfg, monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)
    resp = _FakeResponse(200, {"content-type": "application/pdf"}, b"%PDF-1.7 x")
    _patch_client(monkeypatch, [resp])
    res = _handle_web_fetch({"url": "http://93.184.216.34/p.pdf"}, None, None, lambda *a: None)
    assert not res.success
    assert "parser unavailable" in res.data["error"]


def test_web_fetch_pdf_without_text(cfg, monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", _fake_pypdf(["", "   "]))
    resp = _FakeResponse(200, {"content-type": "application/pdf"}, b"%PDF-1.7 x")
    _patch_client(monkeypatch, [resp])
    res = _handle_web_fetch({"url": "http://93.184.216.34/scan.pdf"}, None, None, lambda *a: None)
    assert not res.success
    assert "no extractable text" in res.data["error"]


def test_extract_pdf_respects_max_pages(cfg, monkeypatch):
    cfg["pdf_max_pages"] = 1
    monkeypatch.setitem(sys.modules, "pypdf", _fake_pypdf(["one", "two"]))
    text, err = network._extract_pdf(b"%PDF-1.7", cfg)
    assert err is None
    assert "one" in text and "two" not in text


def test_extract_pdf_skips_broken_pages(cfg, monkeypatch):
    fake = types.ModuleType("pypdf")

    class _BadPage:
        def extract_text(self):
            raise RuntimeError("broken page")

    class _GoodPage:
        def extract_text(self):
            return "good"

    class _Reader:
        def __init__(self, stream):
            self.pages = [_BadPage(), _GoodPage()]

    fake.PdfReader = _Reader
    monkeypatch.setitem(sys.modules, "pypdf", fake)
    text, err = network._extract_pdf(b"%PDF-1.7", cfg)
    assert err is None
    assert "good" in text


def test_web_fetch_pdf_corrupt(cfg, monkeypatch):
    fake = types.ModuleType("pypdf")

    class _Reader:
        def __init__(self, stream):
            raise ValueError("bad header")

    fake.PdfReader = _Reader
    monkeypatch.setitem(sys.modules, "pypdf", fake)
    resp = _FakeResponse(200, {"content-type": "application/pdf"}, b"%PDF-1.7 x")
    _patch_client(monkeypatch, [resp])
    res = _handle_web_fetch({"url": "http://93.184.216.34/bad.pdf"}, None, None, lambda *a: None)
    assert not res.success
    assert "PDF parse failed" in res.data["error"]


# ── fetch backends / Jina Reader fallback ──

def test_web_fetch_jina_fallback_when_direct_too_short(cfg, monkeypatch):
    cfg["fetch_backends"] = ["direct", "jina"]
    monkeypatch.setattr(network, "_resolve_ips", lambda h: ["93.184.216.34"])
    monkeypatch.setitem(sys.modules, "trafilatura", None)
    short = _FakeResponse(200, {"content-type": "text/html"}, b"<p>hi</p>",
                          url="http://93.184.216.34/p")
    md = _FakeResponse(200, {"content-type": "text/plain"}, b"# Title\n\nBody",
                       url="https://r.jina.ai/http://93.184.216.34/p")
    client = _patch_client(monkeypatch, [short, md])
    res = _handle_web_fetch({"url": "http://93.184.216.34/p"}, None, None, lambda *a: None)
    assert res.success
    assert res.data["backend"] == "jina" and res.data["source"] == "jina"
    assert "Title" in res.data["content"]
    assert len(client.calls) == 2


def test_web_fetch_jina_only_backend(cfg, monkeypatch):
    cfg["fetch_backends"] = ["jina"]
    monkeypatch.setattr(network, "_resolve_ips", lambda h: ["93.184.216.34"])
    md = _FakeResponse(200, {"content-type": "text/plain"}, b"# Hello",
                       url="https://r.jina.ai/http://93.184.216.34/x")
    _patch_client(monkeypatch, [md])
    res = _handle_web_fetch({"url": "http://93.184.216.34/x"}, None, None, lambda *a: None)
    assert res.success
    assert res.data["backend"] == "jina" and "Hello" in res.data["content"]


def test_web_fetch_jina_blocks_private_target(cfg, monkeypatch):
    cfg["fetch_backends"] = ["jina"]
    client = _patch_client(monkeypatch, [])
    res = _handle_web_fetch({"url": "http://169.254.169.254/"}, None, None, lambda *a: None)
    assert not res.success
    assert "non-public" in res.data["error"] or "SSRF" in res.data["error"]
    assert client.calls == []


def test_web_fetch_all_backends_fail(cfg, monkeypatch):
    cfg["fetch_backends"] = ["direct", "jina"]
    monkeypatch.setattr(network, "_resolve_ips", lambda h: ["93.184.216.34"])
    r1 = _FakeResponse(500, {"content-type": "text/html"}, b"err",
                       url="http://93.184.216.34/x")
    r2 = _FakeResponse(502, {"content-type": "text/plain"}, b"bad",
                       url="https://r.jina.ai/http://93.184.216.34/x")
    _patch_client(monkeypatch, [r1, r2])
    res = _handle_web_fetch({"url": "http://93.184.216.34/x"}, None, None, lambda *a: None)
    assert not res.success
    assert "all backends" in res.data["error"]


# ── local dev services: direct only (opencode-style local access) ──

def test_fetch_backends_localhost_forces_direct(cfg, monkeypatch):
    cfg["allow_localhost"] = True
    cfg["fetch_backends"] = ["jina", "direct"]
    monkeypatch.setattr(network, "_resolve_ips",
                        lambda h: ["127.0.0.1"] if h in ("127.0.0.1", "localhost")
                        else ["93.184.216.34"])
    assert network._fetch_backend_list("http://127.0.0.1:3000/", cfg) == ["direct"]
    assert network._fetch_backend_list("http://example.com/", cfg) == ["jina", "direct"]


def test_web_fetch_localhost_uses_direct(cfg, monkeypatch):
    cfg["allow_localhost"] = True
    cfg["fetch_backends"] = ["jina", "direct"]
    monkeypatch.setattr(network, "_resolve_ips", lambda h: ["127.0.0.1"])
    monkeypatch.setitem(sys.modules, "trafilatura", None)
    resp = _FakeResponse(200, {"content-type": "text/html"},
                         b"<p>local dev page</p>", url="http://127.0.0.1:3000/")
    client = _patch_client(monkeypatch, [resp])
    res = _handle_web_fetch({"url": "http://127.0.0.1:3000/"}, None, None, lambda *a: None)
    assert res.success
    assert res.data["backend"] == "direct"
    assert len(client.calls) == 1  # never went to the external reader


# ── defensive / error branches ──

def test_web_cfg_reads_real_config():
    # exercises network._web_cfg() (not the monkeypatched fixture)
    c = network._web_cfg()
    assert c["allow_private"] is False
    assert c["search_provider"] == "none"
    assert c["max_pdf_bytes"] >= c["max_bytes"]


def test_validate_url_missing_host(cfg):
    assert validate_url("http:///no-host") is not None


def test_resolve_ips_handles_dns_failure(monkeypatch):
    def _boom(*a, **k):
        raise socket.gaierror("no dns")

    monkeypatch.setattr(network.socket, "getaddrinfo", _boom)
    assert network._resolve_ips("whatever.invalid") == []


def test_read_capped_skips_empty_chunks():
    class _Resp:
        def iter_bytes(self):
            yield b""
            yield b"abc"

    body, truncated = network._read_capped(_Resp(), 10)
    assert body == b"abc"
    assert truncated is False


def test_fetch_wraps_unexpected_error(cfg, monkeypatch):
    class _Boom:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def stream(self, method, url):
            raise ValueError("weird")

    monkeypatch.setattr(network.httpx, "Client", lambda **kw: _Boom())
    info, err = network._fetch("http://93.184.216.34/", cfg)
    assert err and "Request failed" in err


def test_decode_falls_back_on_garbage():
    out = network._decode(b"\xff\xfe\x00\x01", "utf-8")
    assert isinstance(out, str) and out


def test_web_search_missing_query(cfg):
    res = _handle_web_search({}, None, None, lambda *a: None)
    assert not res.success
    assert "Missing query" in res.data["error"]


def test_search_serper_missing_key(cfg, monkeypatch):
    monkeypatch.delenv("TEST_SEARCH_KEY", raising=False)
    res, err = network._search_serper("x", cfg)
    assert res == [] and "API key" in err


def test_search_duckduckgo_fetch_error(cfg, monkeypatch):
    monkeypatch.setattr(network, "_fetch", lambda url, c, method="GET": ({}, "boom"))
    res, err = network._search_duckduckgo("x", cfg)
    assert res == [] and err == "boom"


def test_search_duckduckgo_http_error(cfg, monkeypatch):
    monkeypatch.setattr(network, "_fetch",
                        lambda url, c, method="GET": ({"status": 503, "body": b"", "encoding": "utf-8"}, None))
    res, err = network._search_duckduckgo("x", cfg)
    assert res == [] and "503" in err


def test_search_duckduckgo_skips_anchor_without_href(cfg, monkeypatch):
    page = (b'<a class="result__a">no href</a>'
            b'<a class="result__a" href="https://ok.org">OK</a>')

    def _fake_fetch(url, c, method="GET"):
        return {"status": 200, "body": page, "encoding": "utf-8"}, None

    monkeypatch.setattr(network, "_fetch", _fake_fetch)
    res, err = network._search_duckduckgo("x", cfg)
    assert err is None
    assert len(res) == 1 and res[0]["url"] == "https://ok.org"


def test_search_duckduckgo_respects_limit(cfg, monkeypatch):
    cfg["search_max_results"] = 1
    page = (b'<a class="result__a" href="https://a.org">A</a>'
            b'<a class="result__a" href="https://b.org">B</a>')

    def _fake_fetch(url, c, method="GET"):
        return {"status": 200, "body": page, "encoding": "utf-8"}, None

    monkeypatch.setattr(network, "_fetch", _fake_fetch)
    res, err = network._search_duckduckgo("x", cfg)
    assert len(res) == 1


def test_search_exa_parses(cfg, monkeypatch):
    monkeypatch.setenv("TEST_EXA_KEY", "k")
    cfg["exa_api_key_env"] = "TEST_EXA_KEY"

    class _Post:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"title": "E", "url": "https://e.org", "text": "snip"}]}

    monkeypatch.setattr(network.httpx, "post", lambda *a, **k: _Post())
    res, err = network._search_exa("x", cfg)
    assert err is None
    assert res[0]["url"] == "https://e.org" and res[0]["snippet"] == "snip"


def test_search_routing_falls_over_to_next(cfg, monkeypatch):
    cfg["search_providers"] = ["tavily", "duckduckgo"]
    monkeypatch.delenv("TEST_SEARCH_KEY", raising=False)  # tavily has no key
    page = b'<a class="result__a" href="https://ok.org">OK</a>'
    monkeypatch.setattr(network, "_fetch", lambda url, c, method="GET":
                        ({"status": 200, "body": page, "encoding": "utf-8"}, None))
    res = _handle_web_search({"query": "x"}, None, None, lambda *a: None)
    assert res.success
    assert res.data["backend"] == "duckduckgo"


def test_search_routing_all_fail_reports_backends(cfg, monkeypatch):
    cfg["search_providers"] = ["tavily"]
    monkeypatch.delenv("TEST_SEARCH_KEY", raising=False)
    res = _handle_web_search({"query": "x"}, None, None, lambda *a: None)
    assert not res.success
    assert "all backends" in res.data["error"] and "tavily" in res.data["error"]


def test_web_doctor_reports_readiness(cfg, monkeypatch):
    cfg["search_providers"] = ["exa", "duckduckgo"]
    cfg["exa_api_key_env"] = "TEST_EXA_KEY"
    monkeypatch.delenv("TEST_EXA_KEY", raising=False)
    rows = network.web_doctor(cfg)
    assert [r["provider"] for r in rows] == ["exa", "duckduckgo"]
    assert rows[0]["ready"] is False and rows[1]["ready"] is True


def test_web_search_non_numeric_max_results(cfg, monkeypatch):
    cfg["search_provider"] = "tavily"
    monkeypatch.setenv("TEST_SEARCH_KEY", "k")

    class _Post:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    monkeypatch.setattr(network.httpx, "post", lambda *a, **k: _Post())
    res = _handle_web_search({"query": "x", "max_results": "abc"}, None, None, lambda *a: None)
    assert res.success


def test_web_search_provider_error_wrapped(cfg, monkeypatch):
    cfg["search_provider"] = "tavily"
    monkeypatch.setattr(network, "_search_tavily",
                        lambda q, c: (_ for _ in ()).throw(RuntimeError("kaboom")))
    res = _handle_web_search({"query": "x"}, None, None, lambda *a: None)
    assert not res.success
    assert "Search failed" in res.data["error"]


def test_web_plugin_default_disabled_then_enable():
    from research_agent.tools import get_registry
    from research_agent.tools.builtin import register_builtins
    reg = get_registry()
    # Non-destructive: re-register builtins. With a fresh config (no
    # plugins.web entry) the plugin must reconcile to its default-disabled state.
    register_builtins()

    assert "web" in reg.plugins
    assert reg.get_plugin("web").enabled is False
    assert "web_fetch" not in reg

    assert reg.enable_plugin("web") is True
    assert "web_fetch" in reg and "web_search" in reg
    assert reg.tools["web_fetch"].plugin_id == "web"
    assert reg.tools["web_fetch"].side_effect is False
    assert reg.tools["web_search"].requires_approval is False
