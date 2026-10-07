"""``source_refs``: every generated item points back to where it came from (spec §5)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter


class LectureRef(BaseModel):
    type: Literal["lecture"] = "lecture"
    lecture_id: int
    start_s: float
    end_s: float


class MaterialRef(BaseModel):
    type: Literal["material"] = "material"
    material_id: int
    page: int | None = None


SourceRef = Annotated[LectureRef | MaterialRef, Field(discriminator="type")]
SourceRefs = TypeAdapter(list[SourceRef])
