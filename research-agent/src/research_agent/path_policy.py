"""Shared workspace boundaries; checks do not provide a filesystem transaction."""
import os
import ntpath
import fnmatch
from pathlib import Path


def safe_path(root, name):
    try:
        if not isinstance(name, str) or "\x00" in name:
            return None
        drive, tail = ntpath.splitdrive(name)
        if ":" in tail or (drive and os.name != "nt"):
            return None
        root = Path(root).resolve()
        target = (root / name).resolve()
        return str(target) if target.is_relative_to(root) else None
    except (OSError, ValueError, RuntimeError):
        return None


def glob_parts(pattern):
    pattern = pattern.replace("\\", "/")
    if not pattern or pattern.startswith("/") or ntpath.splitdrive(pattern)[0] or ":" in pattern or "\x00" in pattern:
        raise ValueError("Glob must be relative to workspace")
    parts = tuple(part for part in pattern.split("/") if part not in ("", "."))
    if ".." in parts:
        raise ValueError("Glob escapes workspace")
    return parts


def safe_glob(root, pattern):
    parts = glob_parts(pattern)
    def matches(names, patterns):
        if not patterns:
            return not names
        if patterns[0] == "**":
            return matches(names, patterns[1:]) or bool(names and matches(names[1:], patterns))
        return bool(names and fnmatch.fnmatchcase(names[0], patterns[0]) and matches(names[1:], patterns[1:]))
    for directory, dirs, files in os.walk(root, followlinks=False):
        # Refuse linked directories (including Windows junctions), even internal ones.
        dirs[:] = sorted(name for name in dirs if name != ".git" and
            not Path(directory, name).is_symlink() and
            not getattr(os.path, "isjunction", lambda path: False)(os.path.join(directory, name)) and
            safe_path(root, os.path.relpath(os.path.join(directory, name), root)) is not None)
        for name in dirs + sorted(files):
            absolute = os.path.join(directory, name)
            relative = os.path.relpath(absolute, root).replace("\\", "/")
            if name == ".git" or safe_path(root, relative) is None:
                continue
            if matches(tuple(relative.split("/")), parts):
                yield relative
