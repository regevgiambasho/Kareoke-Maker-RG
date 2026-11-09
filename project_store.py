from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_ROOT = Path(
    os.environ.get("KARAOKE_DATA_DIR", str(Path.home() / ".karaoke_maker_v2"))
).expanduser()
PROJECTS_ROOT = DATA_ROOT / "projects"
CACHE_ROOT = DATA_ROOT / "cache"
HISTORY_FILE = DATA_ROOT / "projects.json"

for folder in (DATA_ROOT, PROJECTS_ROOT, CACHE_ROOT):
    folder.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_history() -> list[dict[str, Any]]:
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def list_projects() -> list[dict[str, Any]]:
    items = _read_history()
    return sorted(items, key=lambda x: x.get("updated_at", ""), reverse=True)


def get_project(project_id: str) -> dict[str, Any] | None:
    for item in _read_history():
        if item.get("project_id") == project_id:
            return item
    return None


def upsert_project(project: dict[str, Any]) -> dict[str, Any]:
    items = _read_history()
    now = _now()
    project = dict(project)
    project.setdefault("created_at", now)
    project["updated_at"] = now

    replaced = False
    for i, item in enumerate(items):
        if item.get("project_id") == project.get("project_id"):
            project.setdefault("created_at", item.get("created_at", now))
            items[i] = {**item, **project}
            replaced = True
            break

    if not replaced:
        items.append(project)

    _atomic_json(HISTORY_FILE, items)
    return project


def delete_project(project_id: str, delete_files: bool = True) -> None:
    items = [x for x in _read_history() if x.get("project_id") != project_id]
    _atomic_json(HISTORY_FILE, items)

    if delete_files:
        target = PROJECTS_ROOT / project_id
        if target.exists():
            import shutil
            shutil.rmtree(target, ignore_errors=True)


def clear_history_missing_files() -> None:
    items = []
    for item in _read_history():
        p = item.get("project_dir")
        if p and Path(p).exists():
            items.append(item)
    _atomic_json(HISTORY_FILE, items)
