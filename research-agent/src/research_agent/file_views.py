"""Filesystem-owned version/coverage state and bounded, strict UTF-8 scanning."""
import codecs
import hashlib
import json
import os
from pathlib import Path

from research_agent.state_components import StateComponent

MAX_FILE_BYTES = 64 * 1024 * 1024
FILE_VIEWS = StateComponent("filesystem.views", dict, owner="filesystem", lifetime="session",
    value_type=dict, encode=dict, decode=dict, copy_on_read=True)


class FileViewError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _signature(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns if os.name != "nt" else 0


def fingerprint(path):
    """No metadata cache: every returned version comes from actual bytes."""
    with open(path, "rb") as stream:
        initial = os.fstat(stream.fileno())
        digest, size = hashlib.sha256(), 0
        while chunk := stream.read(65536):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise FileViewError("FILE_TOO_LARGE", "File exceeds 64 MiB safety limit")
            digest.update(chunk)
        if _signature(initial) != _signature(os.fstat(stream.fileno())) or _signature(initial) != _signature(os.stat(path)):
            raise FileViewError("FILE_CHANGED_DURING_READ", "File changed while reading; read again")
        return digest.hexdigest()


def scan(path, *, start_line=1, end_line=None, column=1, limit=8000):
    """Hash and extract from one handle, buffering at most limit+1 characters.

    Lines/columns are 1-based Unicode positions; coverage uses internal half-open
    character offsets in strictly decoded UTF-8, preserving BOM and line endings.
    """
    if start_line < 1 or column < 1 or (end_line is not None and end_line < start_line) or not 1 <= limit <= 16000:
        raise FileViewError("INVALID_RANGE", "Invalid line range, column or character limit")
    digest, decoder = hashlib.sha256(), codecs.getincrementaldecoder("utf-8")("strict")
    line, col, absolute, size = 1, 1, 0, 0
    selected, selected_size, begin = [], 0, None
    last_character, bom = "", False

    def consume(text):
        nonlocal line, col, absolute, selected_size, begin, last_character
        pieces = text.split("\n")
        for index, part in enumerate(pieces):
            piece = part + ("\n" if index < len(pieces) - 1 else "")
            if not piece:
                continue
            if line == start_line and piece.endswith("\n") and column > col + len(piece) - 1:
                raise FileViewError("INVALID_RANGE", "Requested column is outside line")
            if line >= start_line and (end_line is None or line <= end_line):
                skip = max(0, column - col) if line == start_line else 0
                skip = min(skip, len(piece))
                if skip < len(piece) and selected_size < limit + 1:
                    if begin is None:
                        begin = (absolute + skip, line, col + skip)
                    value = piece[skip:skip + limit + 1 - selected_size]
                    selected.append(value)
                    selected_size += len(value)
            absolute += len(piece)
            last_character = piece[-1]
            if piece.endswith("\n"):
                line, col = line + 1, 1
            else:
                col += len(piece)

    with open(path, "rb") as stream:
        initial = os.fstat(stream.fileno())
        if initial.st_size > MAX_FILE_BYTES:
            raise FileViewError("FILE_TOO_LARGE", "File exceeds 64 MiB safety limit")
        try:
            while chunk := stream.read(65536):
                if size == 0:
                    bom = chunk.startswith(codecs.BOM_UTF8)
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise FileViewError("FILE_TOO_LARGE", "File exceeds 64 MiB safety limit")
                digest.update(chunk)
                if b"\0" in chunk:
                    raise FileViewError("BINARY_FILE", "Binary/NUL content cannot be read or edited as text")
                consume(decoder.decode(chunk))
            consume(decoder.decode(b"", final=True))
        except UnicodeDecodeError as exc:
            raise FileViewError("UNSUPPORTED_ENCODING", "Only strict UTF-8 / UTF-8 BOM text is supported") from exc
        if _signature(initial) != _signature(os.fstat(stream.fileno())) or _signature(initial) != _signature(os.stat(path)):
            raise FileViewError("FILE_CHANGED_DURING_READ", "File changed while reading; read again")
    total_lines = line - int(last_character == "\n") if absolute else 0
    if begin is None:
        if (absolute and (start_line > total_lines or column != 1)) or (not absolute and (start_line != 1 or column != 1)):
            raise FileViewError("INVALID_RANGE", "Requested position is outside file")
        begin = (absolute, start_line, column)
    content = "".join(selected)
    truncated = len(content) > limit
    if truncated:
        boundary = content.rfind("\n", 0, limit)
        # Prefer whole lines. Only a single oversized line needs a column cursor.
        content = content[:boundary + 1] if boundary >= 0 else content[:limit]
        if content.endswith("\r"):
            content = content + "\n" if "".join(selected)[len(content):len(content) + 1] == "\n" else content
    next_line = begin[1] + content.count("\n")
    next_column = len(content.rsplit("\n", 1)[-1]) + 1 if "\n" in content else begin[2] + len(content)
    stop = begin[0] + len(content)
    has_more = stop < absolute
    end_actual = next_line - int(content.endswith("\n")) if content else None
    partial = bool(truncated and content and not content.endswith("\n"))
    return dict(content=content, version=digest.hexdigest(), sha256=digest.hexdigest(),
        file_stamp=list(_signature(initial)),
        range_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        encoding="utf-8-bom" if bom else "utf-8", bytes=size, total_lines=total_lines,
        total_characters=absolute, size=len(content), lines=(end_actual - begin[1] + 1) if content else 0,
        start_line=begin[1], end_line=end_actual, start_column=begin[2],
        range={"start": {"line": begin[1], "column": begin[2]},
               "end": {"line": next_line, "column": next_column}},
        offset=begin[0], next_offset=stop, has_more=has_more,
        complete=not truncated, truncated_lines=[end_actual] if partial else [],
        next_line=next_line if has_more else None,
        next_cursor={"line": next_line, "column": next_column} if has_more else None)


def identity(state, full_path):
    return json.dumps([str(Path(state.workspace_dir).resolve()), getattr(state, "active_chat_id", ""),
                       os.path.normcase(str(Path(full_path).resolve()))])


def views(state):
    if FILE_VIEWS.key not in state.components.keys():
        state.components.inject(FILE_VIEWS)
    return state.components.get(FILE_VIEWS.key)


def save_view(state, full_path, record):
    state.components.update(FILE_VIEWS.key,
        lambda data: dict(data, **{identity(state, full_path): record}), owner="filesystem")


def merge_ranges(ranges):
    merged = []
    for start, stop in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(stop, merged[-1][1])
        else:
            merged.append([start, stop])
    return merged


def record_read(state, full_path, path, result):
    previous = views(state).get(identity(state, full_path), {})
    same = previous.get("version") == result["version"]
    coverage = merge_ranges((previous.get("coverage", []) if same else []) + [[result["offset"], result["next_offset"]]])
    spans = (previous.get("read_coverage", []) if same else []) + [result["range"]]
    combined = []
    position = lambda value: (value["line"], value["column"])
    for span in sorted(spans, key=lambda value: position(value["start"])):
        if combined and position(span["start"]) <= position(combined[-1]["end"]):
            if position(span["end"]) > position(combined[-1]["end"]):
                combined[-1]["end"] = span["end"]
        else:
            combined.append(dict(span))
    spans = combined
    record = dict(path=path, version=result["version"], disk_version=result["version"],
                  coverage=coverage, read_coverage=spans, stale=False,
                  file_stamp=result["file_stamp"], disk_stamp=result["file_stamp"])
    save_view(state, full_path, record)
    result["read_coverage"] = spans


def covered(record, start, stop):
    return any(a <= start and stop <= b for a, b in record.get("coverage", []))


def probe(state):
    """Append-only notice data. Never retrieve source or refresh read versions."""
    changes = []
    workspace = str(Path(state.workspace_dir).resolve())
    chat = getattr(state, "active_chat_id", "")
    for key, record in views(state).items():
        root, session, full_path = json.loads(key)
        if (root, session) != (workspace, chat):
            continue
        from research_agent.path_policy import safe_path
        target = safe_path(workspace, record["path"])
        try:
            current = list(_signature(os.stat(target))) if target else None
        except OSError:
            current = None
        if current != record.get("disk_stamp"):
            updated = dict(record, disk_version=None, disk_stamp=current, stale=True)
            save_view(state, full_path, updated)
            changes.append(dict(path=record["path"], previous_version=record["version"], version=None))
    return changes
