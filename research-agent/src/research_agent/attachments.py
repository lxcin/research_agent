"""Attachment staging + parsing (chat-scoped).

User-provided files/images are staged OUTSIDE the workspace, under
`data_dir/attachments/<chat_id>/<id>/`, parsed deterministically by type, and
normalized into **content blocks** (text | image_url) that are injected into the
turn's user message.

Policy:
  - chat-scoped lifetime (cleaned with the chat, or `attachments cleanup`);
  - raw attachments are NOT written to the workspace and NOT stored in Tier B memory;
  - deterministic parsing; optional deps missing → explicit error (no silent fallback);
  - type/size allowlist; no execution; no content logging.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import time
import uuid

from research_agent.config import get_data_dir

_TEXT_EXT = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".tex",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cc", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".bat", ".sql",
    ".html", ".htm", ".css", ".scss", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".csv", ".tsv", ".xml",
}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_PDF_EXT = {".pdf"}
_DOCX_EXT = {".docx"}
_XLSX_EXT = {".xlsx", ".xlsm"}
_PPTX_EXT = {".pptx"}
_SUPPORTED = _TEXT_EXT | _IMAGE_EXT | _PDF_EXT | _DOCX_EXT | _XLSX_EXT | _PPTX_EXT


def _cfg() -> dict:
    from research_agent.config import get_attachments_config
    return get_attachments_config()


def _safe(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", (name or "").strip()).strip("-") or "default"


def _ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def _kind(ext: str) -> str:
    if ext in _IMAGE_EXT:
        return "image"
    if ext in _PDF_EXT:
        return "pdf"
    if ext in _DOCX_EXT:
        return "docx"
    if ext in _XLSX_EXT:
        return "xlsx"
    if ext in _PPTX_EXT:
        return "pptx"
    return "text"


def attachments_root(chat_id: str = "") -> str:
    return os.path.join(str(get_data_dir()), "attachments", _safe(chat_id or "default"))


def _save_meta(d: str, meta: dict) -> None:
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)


def stage(filename: str, data: bytes, chat_id: str = "", config: dict | None = None) -> dict:
    """Stage a file into the chat-scoped attachments dir. Returns the meta dict."""
    cfg = config or _cfg()
    ext = _ext(filename)
    if ext not in _SUPPORTED:
        raise ValueError(f"unsupported file type: {ext or filename}")
    max_bytes = int(cfg.get("max_mb", 20)) * 1024 * 1024
    if len(data) > max_bytes:
        raise ValueError(f"file too large ({len(data)} > {max_bytes} bytes)")

    sha = hashlib.sha256(data).hexdigest()
    root = attachments_root(chat_id)
    for m in list_attachments(chat_id):
        if m.get("sha256") == sha:
            return m                                   # dedup within the chat

    aid = "att_" + uuid.uuid4().hex[:10]
    d = os.path.join(root, aid)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "original" + ext)
    with open(path, "wb") as fh:
        fh.write(data)
    meta = {"id": aid, "chat_id": _safe(chat_id or "default"),
            "filename": filename, "ext": ext, "kind": _kind(ext),
            "size": len(data), "sha256": sha, "created": int(time.time()),
            "dir": d, "original": path, "parsed": False, "error": ""}
    _save_meta(d, meta)
    return meta


# ── parsing → content blocks ──

def _decode(data: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _image_block(data: bytes, ext: str, cfg: dict) -> dict:
    mime = {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
            ".bmp": "image/bmp"}.get(ext, "image/jpeg")
    raw = data
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        side = int(cfg.get("image_max_side", 1280))
        im.thumbnail((side, side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=int(cfg.get("image_quality", 80)))
        raw, mime = buf.getvalue(), "image/jpeg"
    except Exception:
        pass  # Pillow missing / undecodable → send original bytes
    b64 = base64.b64encode(raw).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def _parse_pdf(data: bytes, cfg: dict) -> str:
    from research_agent.tools.builtin.network import _extract_pdf
    text, err = _extract_pdf(data, {"pdf_max_pages": int(cfg.get("pdf_max_pages", 50))})
    if err:
        raise ValueError(err)
    return text


def _parse_office(data: bytes, ext: str, cfg: dict) -> str:
    if ext in _DOCX_EXT:
        try:
            import docx
        except Exception:
            raise ValueError("解析 .docx 需要可选依赖：pip install -e \".[attach]\"（python-docx）")
        return "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
    if ext in _XLSX_EXT:
        try:
            import openpyxl
        except Exception:
            raise ValueError("解析 .xlsx 需要可选依赖：pip install -e \".[attach]\"（openpyxl）")
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows = []
        for ws in wb.worksheets:
            rows.append(f"# sheet: {ws.title}")
            for row in ws.iter_rows(values_only=True):
                rows.append(", ".join("" if c is None else str(c) for c in row))
        return "\n".join(rows)
    if ext in _PPTX_EXT:
        try:
            from pptx import Presentation
        except Exception:
            raise ValueError("解析 .pptx 需要可选依赖：pip install -e \".[attach]\"（python-pptx）")
        prs = Presentation(io.BytesIO(data))
        out = []
        for i, slide in enumerate(prs.slides, 1):
            out.append(f"# slide {i}")
            for sh in slide.shapes:
                if getattr(sh, "has_text_frame", False):
                    out.append(sh.text)
        return "\n".join(out)
    raise ValueError(f"unsupported office type: {ext}")


def parse(staged: dict, config: dict | None = None) -> list[dict]:
    """Parse a staged attachment into content blocks (text | image_url)."""
    cfg = config or _cfg()
    with open(staged["original"], "rb") as fh:
        data = fh.read()
    kind, ext = staged["kind"], staged["ext"]
    max_chars = int(cfg.get("max_chars", 8000))

    if kind == "image":
        return [_image_block(data, ext, cfg)]
    if kind == "pdf":
        text = _parse_pdf(data, cfg)
    elif kind in ("docx", "xlsx", "pptx"):
        text = _parse_office(data, ext, cfg)
    else:
        text = _decode(data)

    text = text[:max_chars]
    return [{"type": "text", "text": text}]


def parse_file(path: str, chat_id: str = "", config: dict | None = None) -> list[dict]:
    with open(path, "rb") as fh:
        data = fh.read()
    staged = stage(os.path.basename(path), data, chat_id=chat_id, config=config)
    return parse(staged, config=config)


# ── listing / cleanup ──

def list_attachments(chat_id: str = "") -> list[dict]:
    import glob
    root = attachments_root(chat_id)
    out = []
    for mp in sorted(glob.glob(os.path.join(root, "*", "meta.json"))):
        try:
            with open(mp, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except Exception:
            continue
    return out


def cleanup(chat_id: str | None = None) -> int:
    """Remove staged attachments (one chat, or all). Returns removed dirs count."""
    import shutil
    import glob
    if chat_id is None:
        base = os.path.join(str(get_data_dir()), "attachments")
        dirs = glob.glob(os.path.join(base, "*"))
    else:
        base = attachments_root(chat_id)
        dirs = glob.glob(os.path.join(base, "*"))
    n = 0
    for d in dirs:
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n
