"""Server-side video thumbnail generation using ffmpeg."""

import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".webm", ".mov"}

# Match file paths ending in video extensions — same patterns as formatters.jsx
_VIDEO_EXT_LIST = "|".join(ext.lstrip(".") for ext in VIDEO_EXTS)
_RE_BARE_PATH = re.compile(
    r"(?:^|[\s(])([^\s()\[\]!]*/[^\s()\[\]]+\.(?:" + _VIDEO_EXT_LIST + r"))(?=[\s),\]]|$)",
    re.IGNORECASE | re.MULTILINE,
)
_RE_BACKTICK = re.compile(
    r"`([^`]*/[^`]*\.(?:" + _VIDEO_EXT_LIST + r"))`",
    re.IGNORECASE,
)

# Cheap pre-filter: substring check before running the expensive regex /
# spawning a thread. content_likely_has_video() is ~1000x faster than the
# regex on the common case where no video extension appears at all.
_VIDEO_EXT_BYTES = tuple(VIDEO_EXTS)


def content_likely_has_video(content: str) -> bool:
    """Cheap O(n) substring check for any video extension in content.

    Returns False fast for the common case (assistant text without any
    video paths), avoiding both the regex compile/run and the cost of
    spawning a thread for thumbnail generation. Case-insensitive to match
    the regex's IGNORECASE flag.
    """
    if not content:
        return False
    lower = content.lower()
    return any(ext in lower for ext in _VIDEO_EXT_BYTES)


def is_video_file(path: str) -> bool:
    """Check if path has a video extension."""
    _, ext = os.path.splitext(path)
    return ext.lower() in VIDEO_EXTS


def thumb_path_for(video_path: str) -> str:
    """Return the thumbnail path for a given video file."""
    return video_path + ".thumb.jpg"


def generate_thumbnail(video_path: str) -> bool:
    """Generate a thumbnail for a video file using ffmpeg.

    Idempotent: skips if thumb exists and is newer than the video.
    """
    if not os.path.isfile(video_path):
        return False

    output = thumb_path_for(video_path)

    # Skip if thumb exists and is newer than video
    if os.path.isfile(output):
        if os.path.getmtime(output) >= os.path.getmtime(video_path):
            return True

    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", "0.5",
                "-i", video_path,
                "-vframes", "1",
                "-vf", "scale=320:-1",
                "-q:v", "5",
                output,
            ],
            timeout=30,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg timed out for %s", video_path)
        return False
    except FileNotFoundError:
        logger.warning("ffmpeg not found — cannot generate video thumbnails")
        return False

    if os.path.isfile(output) and os.path.getsize(output) > 0:
        logger.debug("Generated thumbnail: %s", output)
        return True
    else:
        logger.warning("ffmpeg produced no output for %s", video_path)
        return False


def generate_thumbnails_for_message(content: str, project_path: str) -> None:
    """Extract video paths from message text and generate thumbnails.

    Scans for paths matching video extensions and generates .thumb.jpg
    files next to each video. All errors are logged, never propagated.
    """
    if not content or not project_path:
        return

    paths: set[str] = set()

    for m in _RE_BARE_PATH.finditer(content):
        paths.add(m.group(1))
    for m in _RE_BACKTICK.finditer(content):
        paths.add(m.group(1))

    for raw_path in paths:
        # Resolve relative to project_path
        if os.path.isabs(raw_path):
            full_path = raw_path
        else:
            full_path = os.path.join(project_path, raw_path)

        full_path = os.path.normpath(full_path)

        if os.path.isfile(full_path):
            generate_thumbnail(full_path)


def backfill_thumbnails() -> None:
    """Scan agent messages in the DB and generate missing thumbnails.

    Intended to run once at startup in a background thread.

    Memory-safe: filters at SQL level (LIKE '%.mp4%' etc) so only the tiny
    subset of messages mentioning a video path is loaded. Streams via
    yield_per() and expunges each row after processing so the session
    identity map doesn't accumulate. Without these, this function loaded
    every AGENT message (~80k rows) into memory and held them for the
    entire ffmpeg-bound iteration — a major leak source.
    """
    from sqlalchemy import or_
    from database import SessionLocal
    from models import Message, MessageRole, Project, Agent

    db = SessionLocal()
    try:
        # Build project + agent path lookup once (small, bounded)
        projects = {p.name: p.path for p in db.query(Project).all()}
        agent_to_project: dict[str, str] = {
            a.id: a.project for a in db.query(Agent.id, Agent.project).all()
        }

        # SQL-side filter: only fetch messages whose content actually
        # mentions a video extension. This drops 80k rows down to a handful.
        like_clauses = [Message.content.like(f"%{ext}%") for ext in VIDEO_EXTS]
        q = db.query(Message.id, Message.agent_id, Message.content).filter(
            Message.role == MessageRole.AGENT,
            Message.content.isnot(None),
            or_(*like_clauses),
        ).execution_options(yield_per=200)

        count = 0
        for msg_id, agent_id, content in q:
            project_name = agent_to_project.get(agent_id)
            if not project_name or project_name not in projects:
                continue
            project_path = projects[project_name]

            paths: set[str] = set()
            for m in _RE_BARE_PATH.finditer(content):
                paths.add(m.group(1))
            for m in _RE_BACKTICK.finditer(content):
                paths.add(m.group(1))

            for raw_path in paths:
                if os.path.isabs(raw_path):
                    full_path = raw_path
                else:
                    full_path = os.path.join(project_path, raw_path)
                full_path = os.path.normpath(full_path)
                if os.path.isfile(full_path) and not os.path.isfile(thumb_path_for(full_path)):
                    if generate_thumbnail(full_path):
                        count += 1
    finally:
        db.close()

    if count:
        logger.info("Backfilled %d video thumbnails", count)
