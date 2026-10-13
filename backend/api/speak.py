"""Teach-back and oral exam sessions, voice answers (speech-to-text) and the examiner's voice."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.ai.types import LLMError
from backend.core.config import get_settings
from backend.core.db import get_session
from backend.core.models import SpeakSession, Subject
from backend.core.state import load_state, update_state
from backend.study import speak
from backend.voice import tts
from backend.voice.stt import SttError, get_transcriber

router = APIRouter(tags=["speak"])

MAX_CLIP_BYTES = 25_000_000
SAMPLE = "Hello. I'm your examiner today. Let's start with the first question."
EXTENSIONS = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/wav": ".wav"}


class StartRequest(BaseModel):
    subject_id: int
    mode: Literal["teach", "oral"]
    topics: list[str] = Field(default_factory=list, max_length=20)
    questions: int = Field(default=3, ge=1, le=8)
    voice: bool = True


class AnswerRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    spoken: bool = False


class VoiceSettings(BaseModel):
    voice: str | None = None
    rate: float | None = Field(default=None, ge=0.6, le=1.6)


class DownloadRequest(BaseModel):
    voice: str


def _fail(e: Exception) -> HTTPException:
    msg = str(e)
    return HTTPException(404 if msg == "Session not found" else 422, msg)


# Sessions -------------------------------------------------------------------------------------


@router.get("/api/speak/topics")
def list_topics(subject_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return speak.topics(session, subject_id)


@router.get("/api/speak/sessions")
def list_sessions(
    subject_id: int | None = None, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    return speak.listing(session, subject_id)


@router.post("/api/speak/sessions", status_code=201)
def start(req: StartRequest) -> dict[str, Any]:
    try:
        return speak.start(req.subject_id, req.mode, req.topics, req.questions, req.voice)
    except (speak.SpeakError, LLMError) as e:
        raise _fail(e) from e


@router.get("/api/speak/sessions/{session_id}")
def get(session_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    sess = session.get(SpeakSession, session_id)
    if sess is None:
        raise HTTPException(404, "Session not found")
    return speak.session_dict(sess)


@router.post("/api/speak/sessions/{session_id}/answer")
def answer(session_id: int, req: AnswerRequest) -> dict[str, Any]:
    try:
        return speak.answer(session_id, req.text, req.spoken)
    except (speak.SpeakError, LLMError) as e:
        raise _fail(e) from e


@router.post("/api/speak/sessions/{session_id}/finish")
def finish(session_id: int) -> dict[str, Any]:
    try:
        return speak.finish(session_id)
    except (speak.SpeakError, LLMError) as e:
        raise _fail(e) from e


@router.delete("/api/speak/sessions/{session_id}", status_code=204)
def delete(session_id: int, session: Session = Depends(get_session)) -> None:
    sess = session.get(SpeakSession, session_id)
    if sess is None:
        raise HTTPException(404, "Session not found")
    session.delete(sess)
    session.commit()


@router.get("/api/speak/sessions/{session_id}/turns/{index}/audio")
def turn_audio(
    session_id: int, index: int, session: Session = Depends(get_session)
) -> FileResponse:
    sess = session.get(SpeakSession, session_id)
    if sess is None or not 0 <= index < len(sess.turns):
        raise HTTPException(404, "Turn not found")
    return _speak(sess.turns[index]["text"])


# Voice ----------------------------------------------------------------------------------------


def _speak(text: str, voice: str | None = None) -> FileResponse:
    try:
        path = tts.synthesize(text, voice)
    except tts.TtsError as e:
        raise HTTPException(409, str(e)) from e
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "max-age=86400"})


@router.get("/api/voice/sample")
def sample(voice: str | None = None) -> FileResponse:
    return _speak(SAMPLE, voice)


@router.post("/api/voice/transcribe")
async def transcribe(
    request: Request, subject_id: int | None = None, session: Session = Depends(get_session)
) -> dict[str, Any]:
    data = await request.body()
    if not data:
        raise HTTPException(422, "No audio received")
    if len(data) > MAX_CLIP_BYTES:
        raise HTTPException(413, "Answers are limited to about 10 minutes")
    subject = session.get(Subject, subject_id) if subject_id else None
    language = subject.language if subject and subject.language != "auto" else None
    vocabulary = list(subject.vocabulary) if subject else []
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip()
    folder = get_settings().data_dir / "tmp" / "voice"
    folder.mkdir(parents=True, exist_ok=True)
    clip = folder / f"{uuid.uuid4().hex}{EXTENSIONS.get(ctype, '.webm')}"
    clip.write_bytes(data)
    try:
        from starlette.concurrency import run_in_threadpool

        out = await run_in_threadpool(get_transcriber().transcribe, clip, language, vocabulary)
    except SttError as e:
        raise HTTPException(502, f"Speech recognition failed: {e}") from e
    finally:
        clip.unlink(missing_ok=True)
    return {"text": out.get("text", ""), "language": out.get("language")}


# Voice settings (PC only: the LAN guard blocks writes under /api/settings) ----------------------


def apply_saved_settings(settings) -> None:  # type: ignore[no-untyped-def]
    saved = load_state().get("voice") or {}
    for k in ("voice", "rate"):
        if k in saved:
            setattr(settings.voice, k, saved[k])


def _voice_status() -> dict[str, Any]:
    cfg = get_settings().voice
    tc = get_settings().transcription
    return {
        "voice": cfg.voice,
        "rate": cfg.rate,
        "voices": tts.catalog(),
        "tts_ready": tts.ready(),
        "stt": {"model": tc.model, "device": tc.device, "loaded": get_transcriber().loaded},
    }


@router.get("/api/settings/voice")
def voice_settings() -> dict[str, Any]:
    return _voice_status()


@router.put("/api/settings/voice")
def put_voice_settings(req: VoiceSettings) -> dict[str, Any]:
    cfg = get_settings().voice
    if req.voice is not None:
        if req.voice not in tts.VOICES:
            raise HTTPException(422, f"Unknown voice {req.voice!r}")
        cfg.voice = req.voice
    if req.rate is not None:
        cfg.rate = round(req.rate, 2)
    update_state(voice={"voice": cfg.voice, "rate": cfg.rate})
    return _voice_status()


@router.post("/api/settings/voice/download", status_code=202)
def download(req: DownloadRequest) -> dict[str, bool]:
    try:
        tts.start_download(req.voice)
    except tts.TtsError as e:
        raise HTTPException(422, str(e)) from e
    return {"started": True}
