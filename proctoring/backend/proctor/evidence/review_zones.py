"""Temporary A08 wire models and zone adapter, pending A01 contract 1.1 / A05 SHA.

No classification rule is implemented here. Until the captain confirms A05's
delivery, a zone is explicitly uncalculated (None), never implicitly green.
"""
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from proctor_contracts.v1 import Incident, SessionInfo, SessionSummary as ContractSessionSummary


class ReviewZone(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    GREY = "grey"


class ZoneAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    zone: ReviewZone | None = None
    reasons_ru: list[str] = Field(default_factory=list, max_length=3)
    rule_version: str | None = None


class SessionSummary(ContractSessionSummary):
    """TEMPORARY: replace with A01's model after the confirmed 1.1 baseline."""
    review_zone: ReviewZone | None = None
    review_zone_reasons_ru: list[str] = Field(default_factory=list, max_length=3)
    review_zone_rule_version: str | None = None


class SessionOverviewRow(BaseModel):
    """TEMPORARY: names match the captain's specification; null zone is explicit."""
    model_config = ConfigDict(extra="forbid")
    session: SessionInfo
    review_zone: ReviewZone | None = None
    reasons_ru: list[str] = Field(default_factory=list, max_length=3)
    incidents_total: int = Field(ge=0)
    incidents_by_priority: dict[str, int]
    pending_reviews: int = Field(ge=0)


ZONE_ORDER = {ReviewZone.RED: 0, ReviewZone.YELLOW: 1, ReviewZone.GREY: 2, ReviewZone.GREEN: 3, None: 4}
ZONE_LABEL = {ReviewZone.RED: "Проверить в первую очередь", ReviewZone.YELLOW: "Требует внимания",
              ReviewZone.GREY: "Недостаточно данных", ReviewZone.GREEN: "Без замечаний",
              None: "Зона не рассчитана"}


def assess_session_zone(incidents: list[Incident], summary: ContractSessionSummary,
                        config: Any = None) -> ZoneAssessment:
    """Same signature as proctor.fusion.zones.assess_session_zone; no local rule copy.

    Switch ONLY this adapter to the public A05 function on the confirmed SHA.
    Current captain-approved behavior: null zone; reports/overview remain usable.
    """
    return ZoneAssessment(reasons_ru=["Зона не рассчитана. Просмотрите эпизоды вручную."])
