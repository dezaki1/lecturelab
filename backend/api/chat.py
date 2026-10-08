"""Search and chat endpoints. Chat replies stream as Server-Sent Events from a POST."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from backend.ai.providers import get_provider
from backend.ai.types import LLMError
from backend.core.db import get_session, session_scope
from backend.core.models import ChatMessage, ChatThread, Lecture, Subject, utcnow
from backend.search import chat as chat_service
from backend.search.index import queue_index
from backend.search.retrieve import search

router = APIRouter(prefix="/api", tags=["search", "chat"])


@router.get("/search")
def search_endpoint(
    q: str,
    subject_id: int | None = None,
    lecture_id: int | None = None,
    limit: int = 20,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    if not q.strip():
        return []
    hits = search(session, q, subject_id=subject_id, lecture_id=lecture_id, limit=min(limit, 50))
    return [h.as_dict() for h in hits]


class ThreadCreate(BaseModel):
    scope: str = Field(default="subject", pattern="^(lecture|subject|all)$")
    subject_id: int | None = None
    lecture_id: int | None = None
    grounded: bool = True
    date_from: date | None = None
    date_to: date | None = None


class ThreadUpdate(BaseModel):
    title: str | None = None
    grounded: bool | None = None
    date_from: date | None = None
    date_to: date | None = None


class MessageRead(BaseModel):
    id: int
    role: str
    content: str
    citations: list[dict[str, Any]]
    model: str
    cost_usd: float
    error: str | None
    created_at: datetime


class ThreadRead(BaseModel):
    id: int
    scope: str
    subject_id: int | None
    lecture_id: int | None
    title: str
    grounded: bool
    date_from: date | None
    date_to: date | None
    updated_at: datetime
    scope_label: str = ""
    messages: list[MessageRead] = Field(default_factory=list)


class Ask(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


def _scope_label(s: Session, t: ChatThread) -> str:
    if t.scope == "lecture" and t.lecture_id:
        lec = s.get(Lecture, t.lecture_id)
        return f"Lecture: {lec.title}" if lec else "Lecture"
    if t.scope == "subject" and t.subject_id:
        sub = s.get(Subject, t.subject_id)
        return sub.name if sub else "Subject"
    return "All subjects"


def _read(s: Session, t: ChatThread, with_messages: bool = False) -> dict[str, Any]:
    data = {**t.model_dump(), "scope_label": _scope_label(s, t)}
    if with_messages:
        data["messages"] = s.exec(
            select(ChatMessage).where(ChatMessage.thread_id == t.id).order_by(col(ChatMessage.id))
        ).all()
    return data


def _thread(s: Session, thread_id: int) -> ChatThread:
    t = s.get(ChatThread, thread_id)
    if t is None:
        raise HTTPException(404, "Chat not found")
    return t


@router.get("/chat/threads", response_model=list[ThreadRead])
def list_threads(
    subject_id: int | None = None,
    lecture_id: int | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    q = select(ChatThread).order_by(col(ChatThread.updated_at).desc())
    if subject_id is not None:
        q = q.where(ChatThread.subject_id == subject_id)
    if lecture_id is not None:
        q = q.where(ChatThread.lecture_id == lecture_id)
    return [_read(session, t) for t in session.exec(q.limit(200)).all()]


@router.post("/chat/threads", response_model=ThreadRead, status_code=201)
def create_thread(data: ThreadCreate, session: Session = Depends(get_session)) -> dict[str, Any]:
    if data.scope == "lecture":
        lec = session.get(Lecture, data.lecture_id) if data.lecture_id else None
        if lec is None:
            raise HTTPException(422, "lecture_id is required for a lecture chat")
        data.subject_id = lec.subject_id
    if data.scope == "subject" and (
        not data.subject_id or not session.get(Subject, data.subject_id)
    ):
        raise HTTPException(422, "subject_id is required for a subject chat")
    if data.scope == "all":
        data.subject_id = data.lecture_id = None
    t = ChatThread(**data.model_dump())
    session.add(t)
    session.commit()
    session.refresh(t)
    return _read(session, t, True)


@router.get("/chat/threads/{thread_id}", response_model=ThreadRead)
def get_thread(thread_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    return _read(session, _thread(session, thread_id), True)


@router.patch("/chat/threads/{thread_id}", response_model=ThreadRead)
def update_thread(
    thread_id: int, data: ThreadUpdate, session: Session = Depends(get_session)
) -> dict[str, Any]:
    t = _thread(session, thread_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    session.add(t)
    session.commit()
    session.refresh(t)
    return _read(session, t, True)


@router.delete("/chat/threads/{thread_id}", status_code=204)
def delete_thread(thread_id: int, session: Session = Depends(get_session)) -> Response:
    t = _thread(session, thread_id)
    for m in session.exec(select(ChatMessage).where(ChatMessage.thread_id == thread_id)).all():
        session.delete(m)
    session.delete(t)
    session.commit()
    return Response(status_code=204)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/chat/threads/{thread_id}/messages")
def ask(thread_id: int, data: Ask) -> StreamingResponse:
    with session_scope() as s:
        _thread(s, thread_id)

    def stream() -> Iterator[str]:
        with session_scope() as s:
            thread = _thread(s, thread_id)
            question = data.content.strip()
            user = ChatMessage(thread_id=thread_id, role="user", content=question)
            s.add(user)
            if thread.title == "New chat":
                thread.title = question[:70] + ("…" if len(question) > 70 else "")
            thread.updated_at = utcnow()
            s.add(thread)
            s.commit()
            s.refresh(user)
            yield _sse("user", MessageRead.model_validate(user.model_dump()).model_dump())
            try:
                provider = get_provider()
                for kind, payload in chat_service.run_turn(s, provider, thread, question):
                    if kind == "delta":
                        yield _sse("delta", {"text": payload})
                    elif kind == "sources":
                        yield _sse("sources", payload)
                    else:
                        yield _sse(
                            "done", MessageRead.model_validate(payload.model_dump()).model_dump()
                        )
            except LLMError as e:
                s.rollback()
                err = ChatMessage(thread_id=thread_id, role="assistant", content="", error=str(e))
                s.add(err)
                s.commit()
                yield _sse("error", {"message": str(e)})

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.post("/chat/messages/{message_id}/flashcards")
def message_flashcards(message_id: int, session: Session = Depends(get_session)) -> dict[str, int]:
    msg = session.get(ChatMessage, message_id)
    if msg is None or msg.role != "assistant":
        raise HTTPException(404, "Answer not found")
    try:
        n = chat_service.flashcards_from_message(session, get_provider(), msg)
    except LLMError as e:
        raise HTTPException(409, str(e)) from e
    return {"created": n}


@router.post("/search/reindex")
def reindex_all(session: Session = Depends(get_session)) -> dict[str, int]:
    """Re-queue indexing for every transcribed lecture (e.g. after changing chunking)."""
    n = 0
    for lecture in session.exec(select(Lecture)).all():
        if lecture.stages.get("transcribe") == "done":
            queue_index(session, lecture.id)  # type: ignore[arg-type]
            n += 1
    return {"queued": n}
