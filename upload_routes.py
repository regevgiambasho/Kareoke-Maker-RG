from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from project_store import get_project


UPLOAD_ROOT = Path(
    os.environ.get(
        "KARAOKE_UPLOAD_DIR",
        str(Path.home() / ".karaoke_maker_v2" / "uploads"),
    )
).expanduser()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

MAX_CHUNK_BYTES = int(os.environ.get("KARAOKE_MAX_CHUNK_BYTES", 5 * 1024 * 1024))
MAX_FILE_BYTES = int(os.environ.get("KARAOKE_MAX_FILE_BYTES", 2 * 1024 * 1024 * 1024))
UPLOAD_TTL_SECONDS = int(os.environ.get("KARAOKE_UPLOAD_TTL_SECONDS", 24 * 3600))

ALLOWED_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
}


def _secret() -> bytes:
    path = UPLOAD_ROOT / ".upload_secret"
    if path.exists():
        return path.read_bytes()
    value = secrets.token_bytes(32)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(value)
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except Exception:
        pass
    return value


def _safe_filename(name: str) -> str:
    base = Path(name or "upload.bin").name
    base = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', "_", base).strip(" .")
    return base or "upload.bin"


def _valid_upload_id(upload_id: str) -> bool:
    return bool(re.fullmatch(r"[a-f0-9]{40}", upload_id or ""))


def _upload_id(fingerprint: str) -> str:
    return hmac.new(
        _secret(),
        fingerprint.encode("utf-8", errors="ignore"),
        hashlib.sha256,
    ).hexdigest()[:40]


def _session_dir(upload_id: str) -> Path:
    if not _valid_upload_id(upload_id):
        raise ValueError("Invalid upload ID")
    return UPLOAD_ROOT / upload_id


def _manifest_path(upload_id: str) -> Path:
    return _session_dir(upload_id) / "manifest.json"


def _read_manifest(upload_id: str) -> dict[str, Any] | None:
    path = _manifest_path(upload_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_manifest(upload_id: str, data: dict[str, Any]) -> None:
    folder = _session_dir(upload_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "manifest.json"
    temp = folder / "manifest.json.tmp"
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)


def _received_chunks(upload_id: str, total_chunks: int) -> list[int]:
    folder = _session_dir(upload_id) / "parts"
    if not folder.exists():
        return []
    out: list[int] = []
    for part in folder.glob("*.part"):
        try:
            idx = int(part.stem)
        except Exception:
            continue
        if 0 <= idx < total_chunks:
            out.append(idx)
    return sorted(set(out))


def _cleanup_old_uploads() -> None:
    now = time.time()
    for folder in UPLOAD_ROOT.iterdir():
        if not folder.is_dir():
            continue
        try:
            age = now - folder.stat().st_mtime
            if age > UPLOAD_TTL_SECONDS:
                shutil.rmtree(folder, ignore_errors=True)
        except Exception:
            pass


async def upload_init(request: Request):
    _cleanup_old_uploads()
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    filename = _safe_filename(str(data.get("filename") or "upload.bin"))
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return JSONResponse({"error": "Unsupported file type"}, status_code=400)

    try:
        size = int(data.get("size") or 0)
        chunk_size = int(data.get("chunk_size") or 0)
        total_chunks = int(data.get("total_chunks") or 0)
    except Exception:
        return JSONResponse({"error": "Invalid upload metadata"}, status_code=400)

    if size <= 0 or size > MAX_FILE_BYTES:
        return JSONResponse({"error": "File size is not allowed"}, status_code=400)
    if chunk_size <= 0 or chunk_size > MAX_CHUNK_BYTES:
        return JSONResponse({"error": "Chunk size is too large"}, status_code=400)
    if total_chunks <= 0 or total_chunks > 100000:
        return JSONResponse({"error": "Invalid chunk count"}, status_code=400)

    fingerprint = str(data.get("fingerprint") or f"{filename}:{size}:{total_chunks}")
    upload_id = _upload_id(fingerprint)
    folder = _session_dir(upload_id)
    parts = folder / "parts"
    parts.mkdir(parents=True, exist_ok=True)

    manifest = _read_manifest(upload_id)
    if manifest:
        # Reuse only if it is the same logical file.
        if (
            int(manifest.get("size") or -1) != size
            or str(manifest.get("filename") or "") != filename
            or int(manifest.get("total_chunks") or -1) != total_chunks
        ):
            shutil.rmtree(folder, ignore_errors=True)
            parts.mkdir(parents=True, exist_ok=True)
            manifest = None

    if not manifest:
        manifest = {
            "upload_id": upload_id,
            "filename": filename,
            "size": size,
            "chunk_size": chunk_size,
            "total_chunks": total_chunks,
            "created_at": time.time(),
            "updated_at": time.time(),
            "completed": False,
        }
        _write_manifest(upload_id, manifest)

    completed_path = folder / "completed" / filename
    completed = bool(manifest.get("completed") and completed_path.exists())

    return JSONResponse({
        "upload_id": upload_id,
        "filename": filename,
        "size": size,
        "received": _received_chunks(upload_id, total_chunks),
        "completed": completed,
    })


async def upload_chunk(request: Request):
    upload_id = request.path_params["upload_id"]
    try:
        index = int(request.path_params["index"])
    except Exception:
        return JSONResponse({"error": "Invalid chunk index"}, status_code=400)

    if not _valid_upload_id(upload_id):
        return JSONResponse({"error": "Invalid upload ID"}, status_code=400)

    manifest = _read_manifest(upload_id)
    if not manifest:
        return JSONResponse({"error": "Upload session not found"}, status_code=404)

    total_chunks = int(manifest["total_chunks"])
    if index < 0 or index >= total_chunks:
        return JSONResponse({"error": "Chunk index out of range"}, status_code=400)

    body = await request.body()
    if not body or len(body) > MAX_CHUNK_BYTES:
        return JSONResponse({"error": "Chunk is empty or too large"}, status_code=413)

    parts = _session_dir(upload_id) / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    target = parts / f"{index}.part"
    temp = parts / f"{index}.part.tmp"
    temp.write_bytes(body)
    os.replace(temp, target)

    manifest["updated_at"] = time.time()
    _write_manifest(upload_id, manifest)

    return JSONResponse({"ok": True, "index": index, "bytes": len(body)})


async def upload_status(request: Request):
    upload_id = request.path_params["upload_id"]
    if not _valid_upload_id(upload_id):
        return JSONResponse({"error": "Invalid upload ID"}, status_code=400)
    manifest = _read_manifest(upload_id)
    if not manifest:
        return JSONResponse({"error": "Upload session not found"}, status_code=404)

    return JSONResponse({
        "upload_id": upload_id,
        "received": _received_chunks(upload_id, int(manifest["total_chunks"])),
        "completed": bool(manifest.get("completed")),
    })


async def upload_complete(request: Request):
    upload_id = request.path_params["upload_id"]
    if not _valid_upload_id(upload_id):
        return JSONResponse({"error": "Invalid upload ID"}, status_code=400)

    manifest = _read_manifest(upload_id)
    if not manifest:
        return JSONResponse({"error": "Upload session not found"}, status_code=404)

    total_chunks = int(manifest["total_chunks"])
    expected_size = int(manifest["size"])
    filename = _safe_filename(str(manifest["filename"]))
    received = _received_chunks(upload_id, total_chunks)

    if len(received) != total_chunks or received != list(range(total_chunks)):
        return JSONResponse({
            "error": "Upload is incomplete",
            "received": received,
        }, status_code=409)

    folder = _session_dir(upload_id)
    completed_dir = folder / "completed"
    completed_dir.mkdir(parents=True, exist_ok=True)
    target = completed_dir / filename
    temp = completed_dir / f"{filename}.tmp"

    with temp.open("wb") as out:
        for index in range(total_chunks):
            part = folder / "parts" / f"{index}.part"
            with part.open("rb") as src:
                shutil.copyfileobj(src, out, length=1024 * 1024)

    actual_size = temp.stat().st_size
    if actual_size != expected_size:
        temp.unlink(missing_ok=True)
        return JSONResponse({
            "error": "Final file size mismatch",
            "expected": expected_size,
            "actual": actual_size,
        }, status_code=409)

    os.replace(temp, target)
    manifest["completed"] = True
    manifest["completed_at"] = time.time()
    manifest["updated_at"] = time.time()
    _write_manifest(upload_id, manifest)

    # Parts are no longer needed once the assembled file is verified.
    shutil.rmtree(folder / "parts", ignore_errors=True)

    return JSONResponse({
        "upload_id": upload_id,
        "filename": filename,
        "size": actual_size,
        "completed": True,
    })


async def upload_delete(request: Request):
    upload_id = request.path_params["upload_id"]
    if not _valid_upload_id(upload_id):
        return JSONResponse({"error": "Invalid upload ID"}, status_code=400)
    shutil.rmtree(_session_dir(upload_id), ignore_errors=True)
    return JSONResponse({"ok": True})


def resolve_completed_upload(upload_id: str) -> Path | None:
    if not _valid_upload_id(upload_id):
        return None
    manifest = _read_manifest(upload_id)
    if not manifest or not manifest.get("completed"):
        return None
    filename = _safe_filename(str(manifest.get("filename") or ""))
    target = _session_dir(upload_id) / "completed" / filename
    if not target.exists() or not target.is_file():
        return None
    if target.stat().st_size != int(manifest.get("size") or -1):
        return None
    return target.resolve()



async def download_project_file(request: Request):
    project_id = str(request.path_params.get("project_id") or "")
    kind = str(request.path_params.get("kind") or "")

    if not re.fullmatch(r"[A-Za-z0-9._-]{8,120}", project_id):
        return JSONResponse({"error": "Invalid project ID"}, status_code=400)

    output_map = {
        "video": ("video", "video/mp4"),
        "instrumental": ("instrumental", "audio/wav"),
        "subtitles": ("subtitles", "text/plain; charset=utf-8"),
    }
    if kind not in output_map:
        return JSONResponse({"error": "Unknown download type"}, status_code=404)

    project = get_project(project_id)
    if not project:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    output_key, media_type = output_map[kind]
    outputs = project.get("outputs") or {}
    raw_path = outputs.get(output_key)
    if not raw_path:
        return JSONResponse({"error": "Output not available"}, status_code=404)

    path = Path(raw_path).expanduser().resolve()
    project_dir_raw = project.get("project_dir")
    if not project_dir_raw:
        return JSONResponse({"error": "Project folder not available"}, status_code=404)

    project_dir = Path(project_dir_raw).expanduser().resolve()

    try:
        path.relative_to(project_dir)
    except ValueError:
        return JSONResponse({"error": "Invalid output path"}, status_code=403)

    if not path.exists() or not path.is_file():
        return JSONResponse({"error": "Output file not found"}, status_code=404)

    filename = path.name
    return FileResponse(
        path=str(path),
        filename=filename,
        media_type=media_type,
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "private, no-store",
        },
    )

def routes() -> list[Route]:
    return [
        Route("/api/upload/init", upload_init, methods=["POST"]),
        Route("/api/upload/chunk/{upload_id:str}/{index:int}", upload_chunk, methods=["POST"]),
        Route("/api/upload/status/{upload_id:str}", upload_status, methods=["GET"]),
        Route("/api/upload/complete/{upload_id:str}", upload_complete, methods=["POST"]),
        Route("/api/upload/{upload_id:str}", upload_delete, methods=["DELETE"]),
        Route(
            "/api/download/{project_id:str}/{kind:str}",
            download_project_file,
            methods=["GET", "HEAD"],
        ),
    ]
