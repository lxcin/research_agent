"""External skill loader — YAML header + Markdown body."""
import re
import yaml
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class ExternalSkill:
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    body: str = ""
    enabled: bool = True
    file_path: str = ""


def parse_skill_file(filepath: str) -> ExternalSkill | None:
    """Parse a .md file with YAML header."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None

    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if not m:
        return None

    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None

    if not isinstance(meta, dict) or "name" not in meta:
        return None

    return ExternalSkill(
        name=meta.get("name", ""),
        description=meta.get("description", ""),
        triggers=meta.get("triggers", []),
        body=m.group(2).strip(),
        enabled=meta.get("enabled", True),
        file_path=filepath,
    )


def load_skills_from_dir(dir_path: str) -> list[ExternalSkill]:
    """Load all .md skill files from a directory."""
    skills = []
    p = Path(dir_path)
    if not p.is_dir():
        return skills
    for f in sorted(p.glob("*.md")):
        skill = parse_skill_file(str(f))
        if skill:
            skills.append(skill)
    return skills


def matched_skills(skills: list[ExternalSkill], user_input: str) -> list[ExternalSkill]:
    """Skills whose triggers match the input (used for injection and usage stats)."""
    low = (user_input or "").lower()
    out = []
    for skill in skills:
        if not skill.enabled or not skill.triggers:
            continue
        if any(t.lower() in low for t in skill.triggers):
            out.append(skill)
    return out


def get_active_skills_context(skills: list[ExternalSkill], user_input: str) -> str:
    """Return context to inject for matching skills."""
    matched = matched_skills(skills, user_input)
    if not matched:
        return ""
    return "\n\n".join(f"## {s.name}\n{s.body}" for s in matched)