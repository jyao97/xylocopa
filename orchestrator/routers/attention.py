"""Attention assistant API — job CRUD + natural-language compilation.

Endpoint shape mirrors the two-step the UI needs:

    POST /api/attention/compile   natural language → spec + confirm sentence
    POST /api/attention/jobs      persist a (reviewed) spec

Splitting compile from create is what makes the confirmation preview
possible. An LLM that resolves "in an hour" to the wrong hour is the most
likely and least visible failure in this feature, so nothing is persisted
until the user has seen the resolved time.

`GET /api/attention/capabilities` exposes the live registries so the UI can
render quick presets without hardcoding a copy of them.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import attention  # noqa: F401  — populates the registries on import
from attention.compiler import (
    CompileError,
    chat_request,
    compile_request,
    validate_spec,
)
from attention.registry import ACTIONS, SIGNALS, TRIGGERS
from database import get_db
from models import AttentionJob
from utils import utcnow as _utcnow

logger = logging.getLogger("orchestrator.attention")

router = APIRouter(tags=["attention"])

# Guards the panel and the scheduler against unbounded growth from a stuck
# client loop. Well past any plausible manual use.
MAX_ACTIVE_JOBS = 200

# Default floor between two fires of a condition-driven job. 5 minutes is
# short enough that a genuine "the agent finished" ping still feels live, and
# long enough that a flapping signal can't turn into a push storm.
DEFAULT_SIGNAL_COOLDOWN_SECONDS = 300


def _naive(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CompileRequest(BaseModel):
    text: str = Field(..., max_length=2000)


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., max_length=2000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=32)


class JobCreate(BaseModel):
    kind: str = "reminder"
    title: str = Field("", max_length=300)
    source_text: str | None = Field(None, max_length=2000)
    trigger_type: str
    trigger_config: dict = Field(default_factory=dict)
    action_type: str
    action_config: dict = Field(default_factory=dict)
    agent_id: str | None = None
    project_name: str | None = None
    max_fires: int | None = None
    min_interval_seconds: int | None = None
    expires_at: datetime | None = None


class JobPatch(BaseModel):
    title: str | None = Field(None, max_length=300)
    status: str | None = None
    trigger_config: dict | None = None
    action_config: dict | None = None
    max_fires: int | None = None
    min_interval_seconds: int | None = None
    expires_at: datetime | None = None


def _serialize(job: AttentionJob) -> dict:
    def _load(raw):
        try:
            return json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}

    trigger = TRIGGERS.get(job.trigger_type)
    action = ACTIONS.get(job.action_type)
    return {
        "id": job.id,
        "kind": job.kind,
        "title": job.title,
        "source_text": job.source_text,
        "trigger_type": job.trigger_type,
        "trigger_config": _load(job.trigger_config),
        "action_type": job.action_type,
        "action_config": _load(job.action_config),
        "status": job.status,
        "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
        "last_fired_at": job.last_fired_at.isoformat() if job.last_fired_at else None,
        "fire_count": job.fire_count,
        "max_fires": job.max_fires,
        "min_interval_seconds": job.min_interval_seconds,
        "last_error": job.last_error,
        "agent_id": job.agent_id,
        "project_name": job.project_name,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "expires_at": job.expires_at.isoformat() if job.expires_at else None,
        # Denormalized so the panel doesn't need a copy of the registries
        # to describe a job it has never seen.
        "recurring": bool(trigger.recurring) if trigger else False,
        "costly": bool(action.costly) if action else False,
    }


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

@router.get("/api/attention/capabilities")
def capabilities():
    """The live registries — lets the UI stay in sync with the backend."""
    return {
        "triggers": [
            {
                "name": t.name,
                "description": t.description,
                "config_schema": t.config_schema,
                "recurring": t.recurring,
            }
            for t in TRIGGERS.values()
        ],
        "actions": [
            {
                "name": a.name,
                "description": a.description,
                "config_schema": a.config_schema,
                "costly": a.costly,
            }
            for a in ACTIONS.values()
        ],
        "signals": [
            {
                "name": s.name,
                "description": s.description,
                "value_kind": s.value_kind,
                "params": s.params,
            }
            for s in SIGNALS.values()
        ],
    }


# ---------------------------------------------------------------------------
# Compile
# ---------------------------------------------------------------------------

@router.post("/api/attention/compile")
async def compile_endpoint(body: CompileRequest, db: Session = Depends(get_db)):
    """Turn natural language into a job spec. Persists nothing.

    422 carries the assistant's own explanation of what it needs, which the
    panel shows verbatim — that is more useful than a generic parse error.
    """
    try:
        spec = await compile_request(body.text, db)
    except CompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"spec": spec}


class CharGenRequest(BaseModel):
    text: str = Field(..., max_length=300)


@router.post("/api/attention/character")
async def generate_character_endpoint(body: CharGenRequest):
    """Design a new orb character with a strong model. Persists nothing —
    the client previews the returned definition and stores it locally.
    Every design passes validate_character(), the only gate between model
    output and the renderer."""
    from attention.chargen import CharGenError, generate_character

    try:
        character = await generate_character(body.text)
    except CharGenError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"character": character}


@router.post("/api/attention/chat")
async def chat_endpoint(body: ChatRequest, db: Session = Depends(get_db)):
    """One turn of the bubble conversation. Persists nothing.

    Returns {"say": <reply text>, "spec": <job proposal or null>}. A spec is
    only ever a *proposal* — the bubble renders Create/Cancel under the
    reply and POSTs /jobs on confirm, so the same review gate protects both
    the chat path and the one-shot compile path.
    """
    try:
        result = await chat_request(
            [m.model_dump() for m in body.messages], db,
        )
    except CompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.get("/api/attention/jobs")
def list_jobs(
    status: str | None = None,
    include_finished: bool = False,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(AttentionJob)
    if status:
        q = q.filter(AttentionJob.status == status)
    elif not include_finished:
        q = q.filter(AttentionJob.status.in_([
            AttentionJob.STATUS_ACTIVE, AttentionJob.STATUS_PAUSED,
            AttentionJob.STATUS_ERROR,
        ]))
    # Soonest-due first, then newest — a due reminder should top the panel.
    jobs = (
        q.order_by(
            AttentionJob.next_run_at.asc().nullslast(),
            AttentionJob.created_at.desc(),
        )
        .limit(max(1, min(limit, 300)))
        .all()
    )
    active = (
        db.query(AttentionJob)
        .filter(AttentionJob.status == AttentionJob.STATUS_ACTIVE)
        .count()
    )
    return {"jobs": [_serialize(j) for j in jobs], "active_count": active}


@router.post("/api/attention/jobs")
def create_job(body: JobCreate, db: Session = Depends(get_db)):
    """Persist a job. Runs the same validation gate as the compiler.

    Accepts both compiler output and hand-built specs from the explicit
    form, so there is exactly one place a malformed job can be rejected.
    """
    active = (
        db.query(AttentionJob)
        .filter(AttentionJob.status == AttentionJob.STATUS_ACTIVE)
        .count()
    )
    if active >= MAX_ACTIVE_JOBS:
        raise HTTPException(
            status_code=429,
            detail=f"too many active jobs ({active}) — finish or delete some first",
        )

    try:
        spec = validate_spec({
            "kind": body.kind,
            "title": body.title or (body.source_text or "")[:80] or "Reminder",
            "trigger_type": body.trigger_type,
            "trigger_config": body.trigger_config,
            "action_type": body.action_type,
            "action_config": body.action_config,
        })
    except CompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    trigger = TRIGGERS[spec["trigger_type"]]
    now = _naive(_utcnow())
    try:
        next_run = _naive(trigger.initial(spec["trigger_config"], now))
    except Exception as exc:
        logger.warning("attention: trigger %s.initial failed: %s", trigger.name, exc)
        raise HTTPException(status_code=422, detail=f"could not schedule: {exc}")

    if trigger.name != "probe" and next_run is None:
        raise HTTPException(
            status_code=422,
            detail="that trigger produced no run time — check the time you gave",
        )

    max_fires = body.max_fires
    if max_fires is None and not trigger.recurring:
        max_fires = 1  # one-shot triggers retire after their single fire

    # Condition-driven jobs get a cooldown unless the caller set one. A watch
    # fires on whatever the world does, so without a floor a flapping signal
    # becomes a push storm — observed with `changed` on agent.last_message_at,
    # which sent 8 notifications for a single agent turn. Clock-driven
    # triggers already have an explicit period and need no floor.
    min_interval = body.min_interval_seconds
    if min_interval is None and trigger.name == "signal":
        min_interval = DEFAULT_SIGNAL_COOLDOWN_SECONDS

    job = AttentionJob(
        kind=spec["kind"],
        title=spec["title"],
        source_text=body.source_text,
        trigger_type=spec["trigger_type"],
        trigger_config=json.dumps(spec["trigger_config"]),
        action_type=spec["action_type"],
        action_config=json.dumps(spec["action_config"]),
        status=AttentionJob.STATUS_ACTIVE,
        next_run_at=next_run,
        max_fires=max_fires,
        min_interval_seconds=min_interval,
        agent_id=body.agent_id,
        project_name=body.project_name,
        expires_at=_naive(body.expires_at),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    logger.info(
        "attention: created job %s (%s × %s) next_run=%s",
        job.id, job.trigger_type, job.action_type, job.next_run_at,
    )
    return _serialize(job)


@router.patch("/api/attention/jobs/{job_id}")
def patch_job(job_id: str, body: JobPatch, db: Session = Depends(get_db)):
    job = db.get(AttentionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if body.title is not None:
        job.title = body.title
    if body.max_fires is not None:
        job.max_fires = body.max_fires
    if body.min_interval_seconds is not None:
        job.min_interval_seconds = body.min_interval_seconds
    if body.expires_at is not None:
        job.expires_at = _naive(body.expires_at)

    if body.trigger_config is not None or body.action_config is not None:
        try:
            spec = validate_spec({
                "kind": job.kind,
                "title": job.title or "Reminder",
                "trigger_type": job.trigger_type,
                "trigger_config": (
                    body.trigger_config
                    if body.trigger_config is not None
                    else json.loads(job.trigger_config or "{}")
                ),
                "action_type": job.action_type,
                "action_config": (
                    body.action_config
                    if body.action_config is not None
                    else json.loads(job.action_config or "{}")
                ),
            })
        except CompileError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        job.trigger_config = json.dumps(spec["trigger_config"])
        job.action_config = json.dumps(spec["action_config"])
        # A retimed job must be rescheduled, and its `changed` baselines are
        # meaningless against a new condition — clear them.
        trigger = TRIGGERS[job.trigger_type]
        if body.trigger_config is not None:
            job.next_run_at = _naive(
                trigger.initial(spec["trigger_config"], _naive(_utcnow()))
            )
            job.last_value = None

    if body.status is not None:
        allowed = {
            AttentionJob.STATUS_ACTIVE, AttentionJob.STATUS_PAUSED,
            AttentionJob.STATUS_DONE,
        }
        if body.status not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"status must be one of {', '.join(sorted(allowed))}",
            )
        resuming = (
            body.status == AttentionJob.STATUS_ACTIVE
            and job.status != AttentionJob.STATUS_ACTIVE
        )
        job.status = body.status
        if body.status == AttentionJob.STATUS_DONE:
            job.next_run_at = None
        elif resuming and job.next_run_at is None:
            # Resuming a retired job needs a fresh run time, otherwise it
            # sits "active" forever without ever being selected by the tick.
            trigger = TRIGGERS.get(job.trigger_type)
            if trigger is not None:
                try:
                    cfg = json.loads(job.trigger_config or "{}")
                except (TypeError, ValueError):
                    cfg = {}
                job.next_run_at = _naive(trigger.initial(cfg, _naive(_utcnow())))
            if job.next_run_at is None:
                raise HTTPException(
                    status_code=422,
                    detail="this job's time has passed — create a new one instead",
                )
            job.last_error = None

    db.commit()
    db.refresh(job)
    return _serialize(job)


@router.delete("/api/attention/jobs/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db)):
    """Retire a job.

    Hard delete: an attention job holds no history worth keeping (its
    outcome already went out as a push), and leaving tombstones would clutter
    the panel that exists to show what is still pending.
    """
    job = db.get(AttentionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    db.delete(job)
    db.commit()
    return {"status": "ok", "id": job_id}


@router.post("/api/attention/jobs/{job_id}/snooze")
def snooze_job(job_id: str, minutes: int = 15, db: Session = Depends(get_db)):
    """Push a job's next run out by `minutes` from now."""
    if minutes < 1 or minutes > 60 * 24 * 30:
        raise HTTPException(status_code=422, detail="minutes must be 1..43200")
    job = db.get(AttentionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job.next_run_at = _naive(_utcnow()) + timedelta(minutes=minutes)
    job.status = AttentionJob.STATUS_ACTIVE
    job.last_error = None
    # A one-shot that already fired has fire_count == max_fires, so it would
    # retire again the instant it fires. Give the snoozed run headroom.
    if job.max_fires is not None and job.fire_count >= job.max_fires:
        job.max_fires = job.fire_count + 1
    db.commit()
    db.refresh(job)
    return _serialize(job)


@router.post("/api/attention/jobs/{job_id}/run-now")
async def run_now(job_id: str, db: Session = Depends(get_db)):
    """Fire a job immediately — the 'does this actually work' button."""
    from attention.registry import ACTIONS as _ACTIONS

    job = db.get(AttentionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    action = _ACTIONS.get(job.action_type)
    if action is None:
        raise HTTPException(status_code=422, detail=f"unknown action {job.action_type!r}")

    try:
        outcome = await action.run(job, db)
    except Exception as exc:
        job.last_error = str(exc)[:500]
        db.commit()
        raise HTTPException(status_code=500, detail=f"action failed: {exc}")

    job.last_fired_at = _naive(_utcnow())
    job.fire_count = (job.fire_count or 0) + 1
    job.last_error = None
    # A manual test must not consume the scheduled fire the user is waiting
    # for, so lift the cap by one instead of letting the job retire here.
    if job.max_fires is not None and job.fire_count >= job.max_fires:
        job.max_fires = job.fire_count + 1
    db.commit()
    db.refresh(job)
    return {"status": "ok", "outcome": outcome, "job": _serialize(job)}
