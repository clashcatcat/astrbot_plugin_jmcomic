from __future__ import annotations

import re
from pathlib import Path


def sanitize_file_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "untitled"


def format_output_name(template: str, title: str, album_id: str) -> str:
    formatted = template.replace("{{name}}", sanitize_file_name(title))
    formatted = formatted.replace("{{id}}", album_id)
    return sanitize_file_name(formatted)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
