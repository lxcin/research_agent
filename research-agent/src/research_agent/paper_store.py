"""Paper file layout for grep-mode (Tier A working memory).

Two zones inside a workspace:
  - formal:   {ws}/papers/{paper_id}.md      the only grep-able source of truth
  - staging:  {ws}/.research-agent/tmp/papers/{paper_id}.md  sandbox, no persistence

Papers are never auto-persisted. read_paper(paper_id) lands in staging;
read_paper(paper_id, persist=True) promotes staging → formal.
"""
import os
from pathlib import Path

PAPERS_SUBDIR = "papers"
TMP_SUBDIR = os.path.join(".research-agent", "tmp", "papers")


def _safe_name(paper_id: str) -> str:
    return paper_id.replace("/", "_").replace("\\", "_")


def formal_papers_dir(workspace_dir: str) -> str:
    d = os.path.join(workspace_dir, PAPERS_SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


def staging_papers_dir(workspace_dir: str) -> str:
    d = os.path.join(workspace_dir, TMP_SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


def formal_path(workspace_dir: str, paper_id: str) -> str:
    return os.path.join(formal_papers_dir(workspace_dir), f"{_safe_name(paper_id)}.md")


def staging_path(workspace_dir: str, paper_id: str) -> str:
    return os.path.join(staging_papers_dir(workspace_dir), f"{_safe_name(paper_id)}.md")


def read_md(path: str) -> dict | None:
    """Parse a paper .md with YAML-ish front matter into {meta, text, full_path}."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None
    meta, body = _parse_front_matter(content)
    return {"meta": meta, "text": body, "full_path": path}


def write_md(path: str, meta: dict, text: str) -> str:
    """Write front-matter paper .md. Meta is JSON-encoded per key for safe round-trip."""
    lines = ["---"]
    for k in ("id", "title", "authors", "year", "abstract", "doi", "source"):
        if meta.get(k):
            lines.append(f"{k}: {_fmt_meta(meta[k])}")
    lines.append("---")
    lines.append("")
    lines.append(text.strip())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def promote_to_formal(workspace_dir: str, paper_id: str) -> str | None:
    """Move a staged paper into the formal papers dir. No-op if not staged."""
    src = staging_path(workspace_dir, paper_id)
    if not os.path.isfile(src):
        return None
    dst = formal_path(workspace_dir, paper_id)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src, dst)
    return dst


def discard_staging(workspace_dir: str, paper_id: str) -> bool:
    src = staging_path(workspace_dir, paper_id)
    if os.path.isfile(src):
        try:
            os.unlink(src)
            return True
        except Exception:
            return False
    return False


def _fmt_meta(value) -> str:
    if isinstance(value, (list, dict)):
        import json
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _parse_front_matter(content: str) -> tuple[dict, str]:
    """Parse YAML-ish front matter (no yaml dependency needed at read time)."""
    import json
    meta: dict = {}
    if content.startswith("---"):
        end = content.find("\n---", 4)
        if end != -1:
            header = content[4:end]
            for line in header.strip().splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    k = k.strip()
                    v = v.strip()
                    if k == "authors":
                        try:
                            v = json.loads(v)
                        except Exception:
                            v = [x.strip().strip("\"'") for x in v.split(",")]
                    elif k in ("year",):
                        try:
                            v = int(v)
                        except Exception:
                            pass
                    meta[k] = v
            body = content[end + 4:].strip()
            return meta, body
    # Legacy markdown without front matter: first line is title heading
    first_nl = content.find("\n")
    title = content[2:first_nl].strip() if content.startswith("# ") and first_nl != -1 else ""
    if title:
        meta["title"] = title
    return meta, content.strip()
