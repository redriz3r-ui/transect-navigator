"""Immutable survey setup records and explicit process-local identity registration."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json

SCHEMA_VERSION = 'survey-navigation-1'
IDENTITY_VERSION = 'native-content-sha256-1'


def canonical_json(value: object) -> str:
    """Stable JSON; nonfinite numbers must be explicitly tagged."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def required_text(value: str, label: str) -> str:
    """Reject ambiguous whitespace identity instead of silently rewriting it."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f'{label} must be nonblank text without surrounding whitespace')
    return value


def utc_time(value: str | None = None) -> str:
    """Require a timezone-aware creation instant and normalize it to UTC."""
    instant = datetime.now(timezone.utc) if value is None else datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError('Created UTC requires an explicit timezone')
    return instant.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class PlannedTransect:
    survey_id: str
    transect_id: str | None
    source_content_key: str
    original_feature_key: str  # Provider key for trace only, never portable identity.
    geometry_wkb_hex: str
    geometry_is_null: bool
    crs_wkt: str
    crs_semantic_definition: str
    attributes_json: str
    official_direction: str
    plan_revision: str
    navigation_status: str
    navigation_reason: str
    navigation_length_m: float | None
    qa_model_status: str
    qa_model_reason: str
    qa_length_m: float | None
    qa_crs_wkt: str


@dataclass(frozen=True)
class Survey:
    survey_id: str
    name: str
    schema_version: str
    created_utc: str
    plan_snapshot_digest: str
    plan_snapshot_serialization_digest: str
    display_crs_wkt: str
    display_crs_semantic_definition: str
    source_crs_wkt: str
    source_crs_semantic_definition: str
    source_crs_authid: str
    source_fields_json: str
    id_field: str
    plan_revision: str
    plans: tuple[PlannedTransect, ...]
    identity_version: str = IDENTITY_VERSION


class SurveyRegistry:
    """Explicit local registry; exported survey tables provide durable metadata.

    No hidden global registry or assignment. A conflicting Survey_ID requires a
    new ID rather than replacing the existing snapshot.
    """

    def __init__(self) -> None:
        self._surveys: dict[str, Survey] = {}

    def register(self, survey: Survey) -> None:
        required_text(survey.survey_id, 'Survey_ID')
        existing = self._surveys.get(survey.survey_id)
        if existing is not None and existing != survey:
            raise ValueError('Survey_ID is already registered; existing survey was not replaced')
        self._surveys[survey.survey_id] = survey

    def get(self, survey_id: str) -> Survey:
        return self._surveys[survey_id]
