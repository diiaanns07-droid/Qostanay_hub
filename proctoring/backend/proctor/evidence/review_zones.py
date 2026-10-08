"""Temporary contract 1.1 models; thin adapter to the confirmed A05 zone assessor.

A05 d9832a9042f817e560eef26dbad5c45882399a3c owns all classification rules.
An isolated A08 checkout without A05 explicitly reports an uncalculated zone.
"""
from enum import StrEnum
from importlib import import_module
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
    """Forward inputs unchanged to A05 and validate only the three wire fields.

    A05 is integrated by A01, not merged into the isolated A08 branch. Missing
    A05 is explicit; dependency failures or assessor errors are not hidden.
    """
    try:
        zones = import_module("proctor.fusion.zones")
    except ModuleNotFoundError as exc:
        if exc.name not in {"proctor.fusion", "proctor.fusion.zones"}:
            raise
        return ZoneAssessment(reasons_ru=["Зона не рассчитана. Просмотрите эпизоды вручную."])
    result = zones.assess_session_zone(incidents, summary, config)
    return ZoneAssessment(zone=result.zone, reasons_ru=result.reasons_ru, rule_version=result.rule_version)
