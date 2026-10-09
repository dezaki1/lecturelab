"""Card browser (filter, bulk actions, duplicates, merge), exports, and the home dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.db import get_session
from backend.core.events import bus
from backend.exports.cards import to_apkg, to_csv
from backend.study import cards as cards_mod
from backend.study.dashboard import dashboard

router = APIRouter(prefix="/api", tags=["cards"])


class BulkRequest(BaseModel):
    ids: list[int] = Field(min_length=1)
    action: Literal["suspend", "unsuspend", "delete", "forget"]


class MergeRequest(BaseModel):
    keep_id: int
    merge_ids: list[int] = Field(min_length=1)


@router.get("/cards")
def list_cards(
    subject_id: int | None = None,
    lecture_id: int | None = None,
    state: str | None = None,
    q: str = "",
    sort: Literal["lecture", "due", "newest", "lapses"] = "lecture",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if state and state not in cards_mod.STATES:
        raise HTTPException(422, f"state must be one of {cards_mod.STATES}")
    return cards_mod.browse(
        session,
        subject_id=subject_id,
        lecture_id=lecture_id,
        state=state,
        query=q,
        sort=sort,
        offset=offset,
        limit=limit,
    )


@router.post("/cards/bulk")
def bulk_cards(req: BulkRequest, session: Session = Depends(get_session)) -> dict[str, int]:
    n = cards_mod.bulk(session, req.ids, req.action)
    bus.publish("review", {})  # due counts may have changed
    return {"changed": n}


@router.get("/cards/duplicates")
def duplicates(
    subject_id: int | None = None, session: Session = Depends(get_session)
) -> list[list[dict[str, Any]]]:
    groups = cards_mod.duplicate_groups(session, subject_id)
    return [cards_mod.describe(session, cards_mod.selected_cards(session, g)) for g in groups]


@router.post("/cards/merge")
def merge_cards(req: MergeRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        card = cards_mod.merge(session, req.keep_id, req.merge_ids)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    bus.publish("review", {})
    return cards_mod.describe(session, [card])[0]


@router.get("/cards/export")
def export_cards(
    format: Literal["apkg", "csv"] = "apkg",
    subject_id: int | None = None,
    lecture_id: int | None = None,
    state: str | None = None,
    q: str = "",
    ids: list[int] | None = Query(None),
    include_suspended: bool = False,
    session: Session = Depends(get_session),
) -> Response:
    cards = cards_mod.matching(
        session, subject_id=subject_id, lecture_id=lecture_id, state=state, query=q, ids=ids
    )
    if not include_suspended and state != "suspended":
        cards = [c for c in cards if not c.suspended]
    if not cards:
        raise HTTPException(404, "No cards to export")
    stamp = datetime.now().strftime("%Y-%m-%d")
    if format == "csv":
        body, media, ext = to_csv(session, cards), "text/csv; charset=utf-8", "csv"
    else:
        body, media, ext = to_apkg(session, cards), "application/octet-stream", "apkg"
    return Response(
        body,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="lecturelab-cards-{stamp}.{ext}"',
            "X-Card-Count": str(len(cards)),
        },
    )


@router.get("/dashboard")
def get_dashboard(session: Session = Depends(get_session)) -> dict[str, Any]:
    return dashboard(session)
