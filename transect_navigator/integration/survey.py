"""Single-recording survey compatibility seam; no intake or assignment inference."""
from dataclasses import dataclass

from qgis.core import QgsProject

from .analyze import IngestionResult
from .comparison import ComparisonResult, compare_ingested
from .events import EventResult, detect_comparison_events
from .statistics import SummaryResult, summarize_comparison
from ..qaqc.events import EventOptions
from ..qaqc.statistics import StatisticsOptions


@dataclass(frozen=True)
class SingleTransectSurveyResult:
    """Survey identity around unchanged v0.1 inputs and derived result schemas."""

    survey_id: str
    accepted_recording: IngestionResult
    comparison: ComparisonResult
    summary: SummaryResult
    events: EventResult


def analyze_single_transect_survey(
    survey_id: str,
    accepted_recording: IngestionResult,
    *,
    statistics_options: StatisticsOptions = StatisticsOptions(),
    event_options: EventOptions = EventOptions(),
    project: QgsProject | None = None,
) -> SingleTransectSurveyResult:
    """Analyze one recording explicitly accepted for its single planned feature.

    Acceptance is a caller precondition, not inferred from proximity. This seam
    neither imports nor filters observations and publishes no project layers.
    The v0.1 engine retains responsibility for geometry/CRS validation and all
    calculations. Survey identity stays outside legacy layer/export schemas.
    """
    if not isinstance(survey_id, str) or not survey_id.strip():
        raise ValueError('Survey_ID must be a nonblank string')
    project = project if project is not None else QgsProject.instance()
    compared = compare_ingested(accepted_recording, project=project)
    summary = summarize_comparison(
        accepted_recording, compared, options=statistics_options, project=project)
    events = detect_comparison_events(
        accepted_recording, compared, options=event_options, project=project)
    return SingleTransectSurveyResult(
        survey_id, accepted_recording, compared, summary, events)
