"""File serving routes — project files, thumbnails, uploads."""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from config import UPLOADS_DIR
from database import get_db
from models import Project

logger = logging.getLogger("orchestrator")

router = APIRouter(tags=["files"])

_THUMB_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_THUMB_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
_MP4_COMPAT_LOCKS: dict[str, asyncio.Lock] = {}
_VIDEO_THUMB_LOCKS: dict[str, asyncio.Lock] = {}


def _mp4_has_faststart(path: str) -> bool:
    """True if the mp4 has its moov atom before mdat (streamable layout).

    iOS Safari's <video> tag refuses to play HTTP-streamed mp4s where moov
    sits at the end — it can't locate the index without downloading the
    whole file first.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(64 * 1024)
        moov = head.find(b"moov")
        mdat = head.find(b"mdat")
        if moov == -1:
            # moov not in the first 64 KB → definitely at the end
            return False
        if mdat == -1:
            return True
        return moov < mdat
    except OSError:
        return True  # don't try to remux on read errors


async def _probe_video(path: str) -> dict | None:
    """Returns first video stream's codec_name/profile/level/width/height,
    or None on probe failure (e.g. ffprobe missing, file unreadable)."""
    if not shutil.which("ffprobe"):
        return None
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=codec_name,profile,level,width,height",
               "-of", "json", path]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        streams = (json.loads(stdout) or {}).get("streams") or []
        return streams[0] if streams else None
    except (OSError, ValueError, asyncio.CancelledError):
        return None


def _ios_can_decode(probe: dict | None) -> bool:
    """True if iOS Safari hardware decode is likely to accept this stream.

    H.264 hardware decode caps at level 5.2 (level_idc 52); we use 4.2
    as the conservative threshold so older iPads also play. Odd dimensions
    occasionally trip the decoder even on supported codecs.
    """
    if not probe:
        return True  # probe unavailable — don't trigger expensive transcode
    codec = (probe.get("codec_name") or "").lower()
    if codec not in ("h264", "hevc"):
        return False
    try:
        level = int(probe.get("level") or 0)
        w = int(probe.get("width") or 0)
        h = int(probe.get("height") or 0)
    except (TypeError, ValueError):
        return False
    if w <= 0 or h <= 0 or w % 2 or h % 2:
        return False
    if codec == "h264" and level > 42:
        return False
    if codec == "hevc" and level > 156:  # HEVC level 5.2
        return False
    return True


async def _ensure_ios_compat_mp4(full_path: str) -> str:
    """If `full_path` is an mp4 iOS Safari can't stream/decode, return a
    cached compatible copy. Falls back to `full_path` on errors.

    Two paths:
      - Remux (`-c copy`): codec/level/dims OK but moov is at the end —
        fast, lossless, just rewrites with `+faststart`.
      - Transcode (libx264 main@4.0): codec/level/dims unsupported by iOS
        hardware decode. Slower but produces a universally playable file.
    Result is cached next to the original under `.thumbcache/<name>.ios.mp4`.
    """
    probe = await _probe_video(full_path)
    is_compat = _ios_can_decode(probe)
    has_faststart = _mp4_has_faststart(full_path)
    if is_compat and has_faststart:
        return full_path
    if not shutil.which("ffmpeg"):
        return full_path

    cache_dir = os.path.join(os.path.dirname(full_path), ".thumbcache")
    cached = os.path.join(cache_dir, os.path.basename(full_path) + ".ios.mp4")
    try:
        if os.path.isfile(cached) and os.path.getmtime(cached) >= os.path.getmtime(full_path):
            return cached
    except OSError:
        pass

    lock = _MP4_COMPAT_LOCKS.setdefault(full_path, asyncio.Lock())
    async with lock:
        # Re-check after acquiring the lock — another request may have built it.
        try:
            if os.path.isfile(cached) and os.path.getmtime(cached) >= os.path.getmtime(full_path):
                return cached
        except OSError:
            pass

        try:
            os.makedirs(cache_dir, exist_ok=True)
            tmp = cached + ".tmp"
            if is_compat:
                cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", full_path,
                       "-c", "copy", "-movflags", "+faststart", "-f", "mp4", tmp]
            else:
                # libx264 main@4.0 + yuv420p covers every iOS device since
                # iPhone 5s. Even-pixel scale guards against odd dimensions.
                cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", full_path,
                       "-c:v", "libx264", "-profile:v", "main", "-level:v", "4.0",
                       "-pix_fmt", "yuv420p", "-preset", "veryfast",
                       "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                       "-c:a", "aac", "-b:a", "128k",
                       "-movflags", "+faststart", "-f", "mp4", tmp]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            _, stderr = await proc.communicate()
            if proc.returncode != 0 or not os.path.isfile(tmp):
                logger.warning("ios-compat conversion failed for %s (transcode=%s): %s",
                               full_path, not is_compat,
                               stderr.decode("utf-8", "replace")[:300])
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                return full_path
            os.replace(tmp, cached)
            return cached
        except (OSError, asyncio.CancelledError) as e:
            logger.warning("ios-compat conversion error for %s: %s", full_path, e)
            return full_path


async def _extract_video_thumb(src: str, dest: str) -> str | None:
    """Extract a poster frame from a video file via ffmpeg, cache as JPEG.
    Returns the cache path on success, None on failure."""
    if not shutil.which("ffmpeg"):
        return None
    lock = _VIDEO_THUMB_LOCKS.setdefault(src, asyncio.Lock())
    async with lock:
        try:
            if os.path.isfile(dest) and os.path.getmtime(dest) >= os.path.getmtime(src):
                return dest
        except OSError:
            pass
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".tmp"
            # -ss 0.5 picks an early non-black frame; scale caps at 1200px wide.
            # -f image2 forces JPEG container since the .tmp suffix on the
            # output path defeats ffmpeg's extension-based format detection.
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.5",
                   "-i", src, "-frames:v", "1",
                   "-vf", "scale='min(1200,iw)':-2",
                   "-q:v", "4", "-f", "image2", tmp]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            _, stderr = await proc.communicate()
            if proc.returncode != 0 or not os.path.isfile(tmp):
                logger.debug("video thumb failed for %s: %s",
                             src, stderr.decode("utf-8", "replace")[:200])
                try: os.unlink(tmp)
                except OSError: pass
                return None
            os.replace(tmp, dest)
            return dest
        except (OSError, asyncio.CancelledError) as e:
            logger.debug("video thumb error for %s: %s", src, e)
            return None


def _serve_file_with_range(full_path: str, media_type: str, request: Request):
    """Return a FileResponse with built-in Range request support."""
    return FileResponse(full_path, media_type=media_type)


def _resolve_project_file(project: str, path: str, db) -> str:
    """Resolve a project-relative path to an absolute filesystem path.

    Raises HTTPException(404) if the file cannot be found.
    """
    proj = db.get(Project, project)
    if not proj:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    base_dir = os.path.realpath(proj.path)
    base_name = os.path.basename(base_dir)

    # Normalise the requested path
    clean = path
    _dbl = re.match(r"api/files/[^/]+/(.+)", clean)
    if _dbl:
        clean = _dbl.group(1)
    if clean.startswith(base_dir + "/"):
        clean = clean[len(base_dir) + 1:]
    elif clean.startswith(base_name + "/"):
        clean = clean[len(base_name) + 1:]

    full_path = os.path.realpath(os.path.join(base_dir, clean))
    if not full_path.startswith(base_dir + os.sep):
        # Path may be an absolute path with leading / stripped by the URL router.
        # Reconstruct it and check if it falls inside any registered project dir.
        abs_candidate = os.path.realpath("/" + clean)
        resolved_abs = False
        for p in db.query(Project).all():
            p_base = os.path.realpath(p.path)
            if abs_candidate.startswith(p_base + os.sep) and os.path.isfile(abs_candidate):
                full_path = abs_candidate
                resolved_abs = True
                break
        if not resolved_abs:
            raise HTTPException(status_code=400, detail="Invalid path")

    # Fallback: try the original path as-is if normalised version doesn't exist
    if not os.path.isfile(full_path):
        fallback = os.path.realpath(os.path.join(base_dir, path))
        if fallback.startswith(base_dir + os.sep) and os.path.isfile(fallback):
            full_path = fallback
        else:
            # Fallback 2: walk one level of subdirectories
            found = False
            for entry in os.listdir(base_dir):
                sub = os.path.join(base_dir, entry)
                if not os.path.isdir(sub):
                    continue
                candidate = os.path.realpath(os.path.join(sub, clean))
                if candidate.startswith(base_dir + os.sep) and os.path.isfile(candidate):
                    full_path = candidate
                    found = True
                    break
            # Fallback 3: search all registered project directories
            if not found:
                for other in db.query(Project).filter(Project.name != project).all():
                    other_base = os.path.realpath(other.path)
                    for root_candidate in [other_base]:
                        candidate = os.path.realpath(os.path.join(root_candidate, clean))
                        if candidate.startswith(other_base + os.sep) and os.path.isfile(candidate):
                            full_path = candidate
                            found = True
                            break
                        if os.path.isdir(root_candidate):
                            for entry in os.listdir(root_candidate):
                                sub = os.path.join(root_candidate, entry)
                                if not os.path.isdir(sub):
                                    continue
                                candidate = os.path.realpath(os.path.join(sub, clean))
                                if candidate.startswith(other_base + os.sep) and os.path.isfile(candidate):
                                    full_path = candidate
                                    found = True
                                    break
                        if found:
                            break
                    if found:
                        break

            if not found:
                raise HTTPException(status_code=404, detail="File not found")

    return full_path


def _write_bytes(path: str, data: bytes):
    with open(path, "wb") as f:
        f.write(data)


def _try_resolve(project: str, path: str, db) -> str | None:
    """Non-raising version of _resolve_project_file. Returns abs path or None."""
    try:
        return _resolve_project_file(project, path, db)
    except HTTPException:
        return None


@router.post("/api/files/exists-batch")
async def files_exists_batch(payload: dict, db: Session = Depends(get_db)):
    """Batch-probe file existence + stat for media URLs.

    Body: {"items": [{"project": str, "path": str} | {"upload": str}, ...]}
    Returns: {"results": [{"exists": bool, "size": int|null, "mtime": float|null}, ...]}
    Order matches input order.
    """
    items = payload.get("items") or []
    results = []
    for it in items:
        it = it or {}
        full = None
        if it.get("upload"):
            safe_name = os.path.basename(it["upload"])
            cand = os.path.join(UPLOADS_DIR, safe_name)
            if os.path.isfile(cand):
                full = cand
        elif it.get("project") and it.get("path"):
            full = _try_resolve(it["project"], it["path"], db)
        if full and os.path.isfile(full):
            try:
                st = os.stat(full)
                results.append({"exists": True, "size": st.st_size, "mtime": st.st_mtime})
                continue
            except OSError:
                pass
        results.append({"exists": False, "size": None, "mtime": None})
    return {"results": results}


@router.get("/api/files/{project}/{path:path}")
async def serve_project_file(project: str, path: str, request: Request,
                             download: bool = False, db: Session = Depends(get_db)):
    """Serve a file from a project's directory (images, videos, etc.)."""
    import mimetypes
    full_path = _resolve_project_file(project, path, db)
    media_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    if download:
        return FileResponse(full_path, media_type=media_type,
                            filename=os.path.basename(full_path))
    # iOS Safari needs moov-at-front mp4 to stream and rejects H.264 levels
    # above ~5.2 — remux or transcode on demand, with on-disk caching.
    if media_type == "video/mp4":
        full_path = await _ensure_ios_compat_mp4(full_path)
    return _serve_file_with_range(full_path, media_type, request)


@router.get("/api/thumbs/{project}/{path:path}")
async def serve_thumbnail(project: str, path: str, request: Request, db: Session = Depends(get_db)):
    """Serve a resized thumbnail (max 1200px, JPEG q80) with filesystem caching."""
    import mimetypes
    full_path = _resolve_project_file(project, path, db)

    _, ext = os.path.splitext(full_path)
    ext = ext.lower()
    is_image = ext in _THUMB_IMAGE_EXTS
    is_video = ext in _THUMB_VIDEO_EXTS
    if not (is_image or is_video):
        media_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
        return _serve_file_with_range(full_path, media_type, request)

    # Cache path: .thumbcache/<filename>.thumb.jpg next to the original
    cache_dir = os.path.join(os.path.dirname(full_path), ".thumbcache")
    thumb_file = os.path.join(cache_dir, os.path.basename(full_path) + ".thumb.jpg")

    if os.path.isfile(thumb_file) and os.path.getmtime(thumb_file) >= os.path.getmtime(full_path):
        return FileResponse(thumb_file, media_type="image/jpeg")

    if is_video:
        thumb = await _extract_video_thumb(full_path, thumb_file)
        if thumb:
            return FileResponse(thumb, media_type="image/jpeg")
        media_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
        return _serve_file_with_range(full_path, media_type, request)

    try:
        from PIL import Image
        os.makedirs(cache_dir, exist_ok=True)
        with Image.open(full_path) as img:
            img.thumbnail((1200, 1200))
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            img.save(thumb_file, "JPEG", quality=80)
        return FileResponse(thumb_file, media_type="image/jpeg")
    except (ImportError, IOError, OSError, ValueError) as e:
        logger.debug("Thumbnail generation failed for %s: %s", full_path, e)
        media_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
        return _serve_file_with_range(full_path, media_type, request)


@router.post("/api/upload")
async def upload_file(request: Request):
    """Upload a file (multipart form data). Returns filename, original_name, path, size."""
    from uuid import uuid4
    from fastapi import UploadFile, File

    form = await request.form()
    file: UploadFile = form.get("file")
    if not file:
        raise HTTPException(status_code=400, detail="No file provided")

    content = await file.read()

    # Sanitize original filename
    original_name = os.path.basename(file.filename or "upload")
    original_name = re.sub(r'[^\w.\- ]', '_', original_name)
    unique_name = f"{uuid4().hex[:12]}_{original_name}"

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    dest = os.path.join(UPLOADS_DIR, unique_name)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _write_bytes(dest, content))

    return {
        "filename": unique_name,
        "original_name": original_name,
        "path": dest,
        "size": len(content),
    }


@router.get("/api/uploads/{filename}")
async def serve_upload(filename: str, request: Request):
    """Serve an uploaded file."""
    import mimetypes
    safe_name = os.path.basename(filename)
    full_path = os.path.join(UPLOADS_DIR, safe_name)
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    return _serve_file_with_range(full_path, media_type, request)
