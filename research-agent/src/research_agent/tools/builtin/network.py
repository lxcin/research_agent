"""Network plugin: web_fetch / web_search.

Outbound HTTP is opt-in (`research-agent plugin enable web`; the plugin default is
disabled) and governed by an SSRF-aware URL policy:

  - only http/https schemes;
  - hostname allow/deny lists (`web.allow_domains` / `web.deny_domains`);
  - every resolved IP must be public — private / loopback / link-local / reserved /
    multicast addresses are rejected (blocks 127.0.0.0/8, 10/8, 172.16/12,
    192.168/16, 169.254.169.254 cloud metadata, ::1, ...);
  - redirects are followed manually, each hop re-validated (no redirect-based SSRF);
  - the response body is capped at `web.max_bytes` and the text handed to the model
    at `web.max_chars`.

Both tools are read-only (`side_effect=False`): fetched content is NOT written to
the workspace and never enters the keep/undo proposal flow. It does enter the LLM
context for the current turn — see docs/NETWORK_PLUGIN.md for exactly what is
extracted and the privacy/limitation notes.

Fetching uses the **host machine's network egress** (httpx; honors HTTP(S)_PROXY
through trust_env), so it inherits the host's DNS/region constraints — DNS
poisoning, regional blocking or timeouts are environment issues, not tool bugs.
The agent is told in the tool description not to blind-retry a failing URL.
"""
import html as _html
import ipaddress
import os
import re
import socket
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx

from research_agent.tools.schema import ToolSchema, ToolResult

_ALLOWED_SCHEMES = ("http", "https")
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_TEXT_TYPES = ("application/json", "application/xml", "text/xml")
_PDF_SNIFF = b"%PDF-"
_MAX_RESULTS_CAP = 10


def _web_cfg() -> dict:
    from research_agent.config import get_web_config
    return get_web_config()


# ── URL policy / SSRF guard ──

# Explicitly blocked IPv6 ranges. IPv6 deliberately does NOT use `is_private`
# wholesale: Python lumps all of IANA's 2001::/23 (incl. Teredo) into it, which
# false-positives on special-use addresses that are not internal. Only genuinely
# unsafe ranges are blocked; IPv4 still uses `is_private` (accurate there).
_BLOCKED_V6_NETS = (
    ipaddress.ip_network("fc00::/7"),       # unique local (ULA) — internal
    ipaddress.ip_network("fe80::/10"),      # link-local
    ipaddress.ip_network("ff00::/8"),       # multicast
    ipaddress.ip_network("100::/64"),       # discard-only
    ipaddress.ip_network("2001:db8::/32"),  # documentation
    ipaddress.ip_network("2001:2::/48"),    # benchmarking
)
_NAT64_NET = ipaddress.ip_network("64:ff9b::/96")


def _is_blocked_ip(ip_str: str) -> bool:
    """True if an IP must not be reached (internal / special-use / unparseable)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → treat as unsafe
    if isinstance(ip, ipaddress.IPv6Address):
        # Unwrap transition mechanisms that embed an IPv4 address, so a private
        # target can't smuggle itself past the guard (e.g. ::ffff:127.0.0.1).
        if ip.ipv4_mapped is not None:
            return _is_blocked_ip(str(ip.ipv4_mapped))
        if ip.sixtofour is not None:
            return _is_blocked_ip(str(ip.sixtofour))
        if ip in _NAT64_NET:
            return _is_blocked_ip(str(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)))
        return (ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_unspecified or ip.is_reserved
                or any(ip in net for net in _BLOCKED_V6_NETS))
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def _resolve_ips(host: str) -> list[str]:
    """Resolve a hostname to all its IPs (empty on failure). Best-effort: a
    time-of-check/time-of-use DNS-rebinding gap remains, see docs."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return []
    return list({info[4][0] for info in infos})


def _all_loopback(ips: list[str]) -> bool:
    try:
        return bool(ips) and all(ipaddress.ip_address(ip).is_loopback for ip in ips)
    except ValueError:
        return False


def _is_local_host(url: str, cfg: dict) -> bool:
    """True if the URL resolves exclusively to loopback (a local dev service)."""
    host = urlparse(url).hostname
    if not host:
        return False
    return _all_loopback(_resolve_ips(host))


def _host_allowed(host: str, cfg: dict) -> bool:
    host = (host or "").lower().rstrip(".")
    deny = [str(d).lower().lstrip(".") for d in cfg.get("deny_domains", []) if d]
    allow = [str(d).lower().lstrip(".") for d in cfg.get("allow_domains", []) if d]

    def _matches(domains: list[str]) -> bool:
        return any(host == d or host.endswith("." + d) for d in domains)

    if deny and _matches(deny):
        return False
    if allow and not _matches(allow):
        return False
    return True


def validate_url(url: str, cfg: dict | None = None) -> str | None:
    """Return an error string if the URL is unsafe/unreachable, else None."""
    cfg = cfg or _web_cfg()
    if not url or not isinstance(url, str):
        return "Missing url"
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return f"Blocked scheme '{parsed.scheme}': only http/https allowed"
    host = parsed.hostname
    if not host:
        return "Blocked: URL has no host"
    if not _host_allowed(host, cfg):
        return f"Blocked host '{host}' by allow/deny policy"
    if not cfg.get("allow_private", False):
        ips = _resolve_ips(host)
        if not ips:
            return f"Cannot resolve host '{host}'"
        # Loopback-only exception for local dev services (opencode-style local
        # access). Scoped strictly to loopback — other private ranges stay blocked.
        if cfg.get("allow_localhost", False) and _all_loopback(ips):
            return None
        for ip in ips:
            if _is_blocked_ip(ip):
                return f"Blocked host '{host}' resolves to non-public IP {ip} (SSRF guard)"
    return None


# ── HTML → readable text ──

_SCRIPT_RE = re.compile(
    r"<(script|style|noscript|template|svg|head)\b[^>]*>.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\n{3,}")


def _strip_html(raw: str) -> str:
    """Minimal, dependency-free HTML-to-text fallback."""
    text = _SCRIPT_RE.sub(" ", raw)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|h[1-6]|tr|article|section)>", "\n", text, flags=re.I)
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = _WS_RE.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()


def extract_text(raw: str) -> str:
    """Readable text from an HTML document.

    Uses `trafilatura` when installed (optional extra `research-agent[web]`),
    otherwise the built-in regex stripper. Never raises.
    """
    try:
        import trafilatura  # type: ignore
        out = trafilatura.extract(raw, include_comments=False, include_tables=True)
        if out and out.strip():
            return out.strip()
    except Exception:
        pass
    return _strip_html(raw)


# ── PDF → text ──

def _extract_pdf(body: bytes, cfg: dict) -> tuple[str, str | None]:
    """Extract text from a PDF with pypdf (optional extra `research-agent[web]`).

    Returns (text, error). Scanned/image-only PDFs have no text layer → error.
    """
    try:
        import io
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return "", ("PDF received but parser unavailable; install with "
                    '`pip install -e ".[web]"` (pypdf)')
    try:
        reader = PdfReader(io.BytesIO(body))
    except Exception as e:
        return "", f"PDF parse failed: {e}"
    max_pages = max(1, int(cfg.get("pdf_max_pages", 50)))
    parts: list[str] = []
    for i, page in enumerate(reader.pages):
        if i >= max_pages:
            break
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        if text.strip():
            parts.append(text.strip())
    if not parts:
        return "", "PDF has no extractable text (scanned/image-only PDF?)"
    return "\n\n".join(parts), None


# ── HTTP fetch (manual redirects, byte cap) ──

def _read_capped(resp, max_bytes: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    truncated = False
    for chunk in resp.iter_bytes():
        if not chunk:
            continue
        if total + len(chunk) > max_bytes:
            chunks.append(chunk[: max_bytes - total])
            truncated = True
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), truncated


def _fetch(url: str, cfg: dict, method: str = "GET") -> tuple[dict, str | None]:
    """GET url with manual, re-validated redirects. Returns (info, error).

    info keys: url, status, content_type, bytes, truncated, encoding, body.
    """
    headers = {"User-Agent": cfg.get("user_agent", "PaperPilot/1.0"), "Accept": "*/*"}
    max_redirects = cfg.get("max_redirects", 5)
    current = url
    redirects = 0
    try:
        with httpx.Client(timeout=cfg.get("timeout", 15), follow_redirects=False,
                          headers=headers, default_encoding="utf-8") as client:
            while True:
                err = validate_url(current, cfg)
                if err:
                    return {}, err
                with client.stream(method, current) as resp:
                    if resp.is_redirect and "location" in resp.headers:
                        if redirects >= max_redirects:
                            return {}, f"Too many redirects (>{max_redirects})"
                        current = urljoin(str(resp.url), resp.headers["location"])
                        redirects += 1
                        continue
                    early_type = (resp.headers.get("content-type", "") or "")
                    early_type = early_type.split(";")[0].strip().lower()
                    cap = (cfg.get("max_pdf_bytes", 20_000_000)
                           if early_type == "application/pdf"
                           else cfg.get("max_bytes", 2_000_000))
                    body, truncated = _read_capped(resp, cap)
                    return {
                        "url": str(resp.url),
                        "status": resp.status_code,
                        "content_type": resp.headers.get("content-type", ""),
                        "bytes": len(body),
                        "truncated": truncated,
                        "encoding": resp.encoding or "utf-8",
                        "body": body,
                    }, None
    except httpx.HTTPError as e:
        return {}, f"HTTP error: {e}"
    except Exception as e:
        return {}, f"Request failed: {e}"


def _decode(body: bytes, encoding: str) -> str:
    for enc in (encoding, "utf-8", "latin-1"):
        try:
            return body.decode(enc, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


# ── Tool: web_fetch ──

def _fetch_direct(url: str, cfg: dict) -> tuple[dict | None, str | None]:
    """Direct fetch + content-type handling. Returns (payload, error)."""
    info, err = _fetch(url, cfg)
    if err:
        return None, err
    if info["status"] >= 400:
        return None, f"HTTP {info['status']} for {info['url']}"
    ctype = (info["content_type"] or "").split(";")[0].strip().lower()
    raw = info["body"]
    if ctype in _HTML_TYPES or (not ctype and b"<html" in raw[:2000].lower()):
        content, source = extract_text(_decode(raw, info["encoding"])), "html"
    elif ctype == "application/pdf" or raw[:5] == _PDF_SNIFF:
        content, perr = _extract_pdf(raw, cfg)
        if perr:
            return None, perr
        source = "pdf"
    elif ctype.startswith("text/") or ctype in _TEXT_TYPES or ctype.endswith("+json"):
        content, source = _decode(raw, info["encoding"]), "text"
    else:
        return None, (f"Unsupported content-type '{ctype or 'unknown'}': only "
                      "HTML/text/JSON/PDF are returned")
    return {"url": info["url"], "status": info["status"],
            "content_type": ctype or source, "source": source, "content": content,
            "bytes": info["bytes"], "truncated": bool(info["truncated"])}, None


def _fetch_via_jina(url: str, cfg: dict) -> tuple[str, str | None]:
    """Read a URL through Jina Reader (r.jina.ai) → clean markdown/text.

    The *target* URL is validated first so we never ask a third party to reach an
    internal address. Note: Jina fetches server-side, so our IP-level SSRF check
    is best-effort for the target (it protects the request we *send* to Jina).
    """
    err = validate_url(url, cfg)
    if err:
        return "", err
    base = cfg.get("jina_reader_base", "https://r.jina.ai/")
    if not base.endswith("/"):
        base += "/"
    info, ferr = _fetch(base + url, cfg)
    if ferr:
        return "", ferr
    if info["status"] >= 400:
        return "", f"Jina Reader HTTP {info['status']}"
    return _decode(info["body"], info["encoding"]), None


def _fetch_backend_list(url: str, cfg: dict) -> list[str]:
    # Local dev services can only be reached by us — external readers can't see
    # the caller's loopback. Force direct (when localhost access is allowed).
    if cfg.get("allow_localhost", False) and _is_local_host(url, cfg):
        return ["direct"]
    backends = [str(b).strip().lower()
                for b in (cfg.get("fetch_backends") or []) if str(b).strip()]
    return backends or ["direct"]


def _finalize_fetch(payload: dict, backend: str, cfg: dict, emit) -> ToolResult:
    max_chars = cfg.get("max_chars", 8000)
    content = payload["content"]
    truncated = bool(payload.get("truncated")) or len(content) > max_chars
    content = content[:max_chars]
    emit("tool", {"tool": "web_fetch", "status": "done", "url": payload["url"],
                  "chars": len(content), "source": payload["source"], "backend": backend})
    return ToolResult.ok(
        url=payload["url"], status=payload["status"], content_type=payload["content_type"],
        source=payload["source"], content=content, chars=len(content),
        bytes=payload["bytes"], truncated=truncated, backend=backend)


def _handle_web_fetch(params: dict, llm, state, emit) -> ToolResult:
    url = (params.get("url") or "").strip()
    cfg = _web_cfg()
    backends = _fetch_backend_list(url, cfg)
    emit("tool", {"tool": "web_fetch", "status": "start", "url": url[:120],
                  "backends": backends})

    min_chars = cfg.get("fetch_min_chars", 200)
    errors: list[str] = []
    for backend in backends:
        if backend == "direct":
            payload, err = _fetch_direct(url, cfg)
            if err:
                errors.append(f"direct: {err}")
                emit("tool", {"tool": "web_fetch", "status": "fallback",
                              "backend": "direct", "error": err[:120]})
                continue
            if (payload["source"] == "html" and len(payload["content"]) < min_chars
                    and len(backends) > 1):
                errors.append(f"direct: content too short ({len(payload['content'])} chars)")
                emit("tool", {"tool": "web_fetch", "status": "fallback",
                              "backend": "direct", "error": "content too short"})
                continue
            return _finalize_fetch(payload, "direct", cfg, emit)
        elif backend == "jina":
            text, err = _fetch_via_jina(url, cfg)
            if err:
                errors.append(f"jina: {err}")
                emit("tool", {"tool": "web_fetch", "status": "fallback",
                              "backend": "jina", "error": err[:120]})
                continue
            if not text.strip():
                errors.append("jina: empty body")
                continue
            payload = {"url": url, "status": 200, "content_type": "text/markdown",
                       "source": "jina", "content": text,
                       "bytes": len(text.encode("utf-8")), "truncated": False}
            return _finalize_fetch(payload, "jina", cfg, emit)
        else:
            errors.append(f"Unknown fetch backend: {backend}")

    emit("tool", {"tool": "web_fetch", "status": "error",
                  "error": "; ".join(errors)[:150]})
    return ToolResult.fail("Fetch failed on all backends: " + "; ".join(errors))


# ── Tool: web_search (provider abstraction) ──

def _search_tavily(query: str, cfg: dict) -> tuple[list[dict], str | None]:
    key = os.environ.get(cfg.get("search_api_key_env", "TAVILY_API_KEY"), "")
    if not key:
        return [], f"Missing API key: set {cfg.get('search_api_key_env', 'TAVILY_API_KEY')}"
    resp = httpx.post("https://api.tavily.com/search", timeout=cfg.get("timeout", 15),
                      json={"api_key": key, "query": query,
                            "max_results": cfg.get("search_max_results", 5)})
    resp.raise_for_status()
    data = resp.json() or {}
    return [{"title": r.get("title", ""), "url": r.get("url", ""),
             "snippet": r.get("content", "")} for r in data.get("results", [])], None


def _search_serper(query: str, cfg: dict) -> tuple[list[dict], str | None]:
    key = os.environ.get(cfg.get("search_api_key_env", "TAVILY_API_KEY"), "")
    if not key:
        return [], f"Missing API key: set {cfg.get('search_api_key_env', 'TAVILY_API_KEY')}"
    resp = httpx.post("https://google.serper.dev/search", timeout=cfg.get("timeout", 15),
                      headers={"X-API-KEY": key, "Content-Type": "application/json"},
                      json={"q": query, "num": cfg.get("search_max_results", 5)})
    resp.raise_for_status()
    data = resp.json() or {}
    return [{"title": r.get("title", ""), "url": r.get("link", ""),
             "snippet": r.get("snippet", "")} for r in data.get("organic", [])], None


def _search_exa(query: str, cfg: dict) -> tuple[list[dict], str | None]:
    """Exa semantic web search (no-key-free tier requires a key)."""
    key = os.environ.get(cfg.get("exa_api_key_env", "EXA_API_KEY"), "")
    if not key:
        return [], f"Missing API key: set {cfg.get('exa_api_key_env', 'EXA_API_KEY')}"
    resp = httpx.post("https://api.exa.ai/search", timeout=cfg.get("timeout", 15),
                      headers={"x-api-key": key, "Content-Type": "application/json"},
                      json={"query": query, "numResults": cfg.get("search_max_results", 5),
                            "contents": {"text": True}})
    resp.raise_for_status()
    data = resp.json() or {}
    out = []
    for r in data.get("results", []):
        snippet = r.get("text") or ""
        if not snippet:
            hl = r.get("highlights") or []
            snippet = hl[0] if hl else ""
        out.append({"title": r.get("title", ""), "url": r.get("url", ""),
                    "snippet": snippet[:500]})
    return out, None


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo wraps result links as //duckduckgo.com/l/?uddg=<encoded>."""
    href = _html.unescape(href)
    if "uddg=" in href:
        parsed = urlparse(href if href.startswith("http") else "https:" + href)
        vals = parse_qs(parsed.query).get("uddg")
        if vals:
            return unquote(vals[0])
    return href


def _search_duckduckgo(query: str, cfg: dict) -> tuple[list[dict], str | None]:
    info, err = _fetch("https://html.duckduckgo.com/html/?q=" + quote_plus(query), cfg)
    if err:
        return [], err
    if info["status"] >= 400:
        return [], f"HTTP {info['status']} from DuckDuckGo"
    page = _decode(info["body"], info["encoding"])
    results: list[dict] = []
    for m in re.finditer(r'<a\b[^>]*class="[^"]*result__a[^"]*"[^>]*>(.*?)</a>',
                         page, re.S | re.I):
        tag = m.group(0)
        hm = re.search(r'href="([^"]+)"', tag)
        if not hm:
            continue
        results.append({"title": _strip_html(m.group(1)),
                        "url": _unwrap_ddg(hm.group(1)), "snippet": ""})
        if len(results) >= cfg.get("search_max_results", 5):
            break
    return results, None


# Backend registry (borrowed from Agent-Reach's "首选 + 备选" idea: each search
# provider is a candidate backend; the first one that answers wins).
_PROVIDERS = {
    "exa": _search_exa,
    "tavily": _search_tavily,
    "serper": _search_serper,
    "duckduckgo": _search_duckduckgo,
}


def _ordered_providers(cfg: dict) -> list[str]:
    """Ordered backend list: `web.search_providers` if set, else legacy
    `web.search_provider` (single)."""
    providers = [str(p).strip().lower()
                 for p in (cfg.get("search_providers") or []) if str(p).strip()]
    if not providers:
        single = str(cfg.get("search_provider", "none") or "none").lower()
        if single not in ("none", "", "off"):
            providers = [single]
    return providers


def web_doctor(cfg: dict | None = None) -> list[dict]:
    """Per-backend readiness, like `agent-reach doctor` (offline: config/key check).

    Returns a list of {provider, ready, detail} in routing order.
    """
    cfg = cfg or _web_cfg()
    rows: list[dict] = []
    for p in _ordered_providers(cfg):
        if p == "duckduckgo":
            rows.append({"provider": p, "ready": True, "detail": "无需 key"})
        elif p == "exa":
            env = cfg.get("exa_api_key_env", "EXA_API_KEY")
            has = bool(os.environ.get(env))
            rows.append({"provider": p, "ready": has, "detail": "" if has else f"设置 {env}"})
        elif p in ("tavily", "serper"):
            env = cfg.get("search_api_key_env", "TAVILY_API_KEY")
            has = bool(os.environ.get(env))
            rows.append({"provider": p, "ready": has, "detail": "" if has else f"设置 {env}"})
        else:
            rows.append({"provider": p, "ready": False, "detail": "未知 provider"})
    return rows


def _handle_web_search(params: dict, llm, state, emit) -> ToolResult:
    query = (params.get("query") or "").strip()
    if not query:
        return ToolResult.fail("Missing query")
    cfg = _web_cfg()
    providers = _ordered_providers(cfg)
    if not providers:
        return ToolResult.fail(
            "Search provider not configured. Set web.search_providers "
            "([exa, tavily, duckduckgo]) or legacy web.search_provider.")

    try:
        limit = int(params.get("max_results", cfg.get("search_max_results", 5)))
    except (TypeError, ValueError):
        limit = cfg.get("search_max_results", 5)
    cfg = {**cfg, "search_max_results": max(1, min(limit, _MAX_RESULTS_CAP))}

    emit("tool", {"tool": "web_search", "status": "start",
                  "providers": providers, "query": query[:120]})
    errors: list[str] = []
    for provider in providers:
        fn = _PROVIDERS.get(provider)
        if fn is None:
            errors.append(f"Unknown search provider: {provider}")
            continue
        try:
            results, err = fn(query, cfg)
        except Exception as e:
            errors.append(f"{provider}: {e}")
            continue
        if err:
            errors.append(f"{provider}: {err}")
            emit("tool", {"tool": "web_search", "status": "fallback",
                          "provider": provider, "error": err[:120]})
            continue
        emit("tool", {"tool": "web_search", "status": "done",
                      "provider": provider, "count": len(results)})
        return ToolResult.ok(provider=provider, backend=provider, query=query,
                             count=len(results), results=results, tried=providers)

    emit("tool", {"tool": "web_search", "status": "error",
                  "error": "; ".join(errors)[:120]})
    return ToolResult.fail("Search failed on all backends: " + "; ".join(errors))


# ── Tool definitions ──

web_fetch_tool = ToolSchema(
    name="web_fetch",
    description=("抓取指定 HTTP/HTTPS URL 并返回正文文本（HTML 提取可读正文、PDF 解析文本层、"
                 "JSON/文本原样返回）。只读，不写工作区。抓取经本机网络出口，可能因 DNS 污染、"
                 "区域封锁、超时或站点反爬而失败；同一 URL 失败后不要反复重试，应改用其他 URL/"
                 "其他来源，或直接告知用户抓取失败及原因。"),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "完整 URL，必须以 http:// 或 https:// 开头"}
        },
        "required": ["url"],
    },
    handler=_handle_web_fetch,
    category="builtin",
)

web_search_tool = ToolSchema(
    name="web_search",
    description=("用配置的搜索服务商检索网页，返回标题/链接/摘要列表。用于查找资料或发现可 "
                 "web_fetch 的 URL。依赖本机网络与外部服务商，可能失败；同一查询不要反复重试，"
                 "换关键词或换用已知 URL 直接 web_fetch。"),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "返回条数（1-10，默认取配置）"},
        },
        "required": ["query"],
    },
    handler=_handle_web_search,
    category="builtin",
)
