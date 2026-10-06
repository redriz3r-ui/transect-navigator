"""Verified survey navigation GeoPackage and unchanged standard companions."""
from dataclasses import asdict
import csv
import json
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET

from qgis.PyQt.QtCore import QMetaType
from qgis.core import Qgis

from .analyze import _table
from .export import write_geopackage_layers
from .survey_prepare import (SurveyNavigation, crs_roundtrip_equivalent,
                             validate_navigation_snapshot)
from .. import __version__
from ..io.exchange import FORMATS, write_exchange_files, validate_exchange_files
from ..io.package import package_destination, publish_package
from ..survey.model import canonical_json


def _records_table(name: str, records: list[dict]):
    """Metadata uses stable text, including explicit JSON for nested/null values."""
    keys = list(records[0])
    return _table(name, tuple((key, QMetaType.Type.QString) for key in keys),
                  [[value if isinstance(value, str) else canonical_json(value)
                    for value in (record[key] for key in keys)] for record in records])


def survey_manifest(result: SurveyNavigation) -> dict:
    """Portable manifest; no mutable QGIS layer IDs or exported FIDs as identity."""
    survey = result.setup.survey
    exclusions = dict(result.exclusions)
    header = asdict(survey)
    del header['plans']
    provenance = json.loads(result.prepared.waypoints.customProperty('transect_navigator/provenance'))
    # Keep measurement and transformation details, excluding legacy in-process IDs.
    for key in ('source_layer_id', 'source_feature_ids'):
        provenance.pop(key, None)
    plans = []
    for plan in survey.plans:
        row = asdict(plan)
        row.update(included=plan.transect_id not in exclusions,
                   exclusion_reason=exclusions.get(plan.transect_id, ''))
        plans.append(row)
    return {'survey': header, 'plans': plans, 'navigation': {
        'Survey_ID': survey.survey_id, 'spacing_m': result.spacing_m,
        'reverse_for_navigation': result.reverse, 'plugin_version': __version__,
        'qgis_version': Qgis.QGIS_VERSION, 'included_plan_count': len(result.prepared.transects),
        'registered_plan_count': len(plans), 'waypoint_count': len(result.prepared.rows),
        'navigation_model': 'WGS84 shortest geodesic legs; original official direction retained',
        'qa_model': 'v0.1 local AEQD / 25 m geodesic subdivision / 5 km radius',
        'provenance_json': canonical_json(provenance)}}


def export_survey_navigation(result: SurveyNavigation, parent: Path, name: str,
                            formats: frozenset[str] | set[str] = FORMATS) -> Path:
    """Publish only after native GIS/metadata and all companion round trips pass.

    planned_transects preserves included native plans without adding/renaming any
    original field. plan_manifest retains EVERY registered plan, including excluded
    geometry as exact WKB hex and all original typed attributes. A package contains
    one survey: its Survey_ID namespaces unchanged human-readable waypoint IDs.
    """
    package_destination(parent, name)
    if not formats <= FORMATS:
        raise ValueError('Unsupported survey navigation companion format')
    validate_navigation_snapshot(result)
    manifest = survey_manifest(result)
    prepared = result.prepared
    layers = [('planned_transects', prepared.source),
              ('navigation_waypoints', prepared.waypoints),
              ('surveys', _records_table('surveys', [manifest['survey']])),
              ('plan_manifest', _records_table('plan_manifest', manifest['plans'])),
              ('navigation_run', _records_table('navigation_run', [manifest['navigation']]))]

    def write(stage: Path) -> None:
        write_geopackage_layers(stage/'survey_navigation.gpkg', layers, prepared.context,
                                crs_equivalent=crs_roundtrip_equivalent)
        write_exchange_files(stage, prepared.transects, prepared.rows, formats)
        validate_exchange_files(stage, prepared.transects, prepared.rows, formats)
        path = stage/'survey_manifest.json'
        path.write_text(canonical_json(manifest)+'\n', encoding='utf-8')
        if json.loads(path.read_text(encoding='utf-8')) != manifest:
            raise ValueError('Survey manifest failed round-trip verification')
        validate_navigation_snapshot(result)
        (stage/'README.txt').write_text(
            'Transect Navigator survey navigation package\n'
            f"Survey_ID: {result.setup.survey.survey_id}\n"
            f"Registered plans: {len(manifest['plans'])}; included: {len(prepared.transects)}\n\n"
            'survey_navigation.gpkg is the authoritative GIS package.\n'
            'planned_transects: INCLUDED plans, native CRS, direction and original fields.\n'
            'navigation_waypoints: legacy WGS84 navigation fields and human-readable IDs.\n'
            'surveys: survey identity, schema, source schema/CRS and snapshot digest.\n'
            'plan_manifest: ALL plans, explicit inclusion/exclusion, original provider keys\n'
            '(trace only), content keys, native WKB hex and tagged original attributes.\n'
            'navigation_run: measurement settings, reverse flag and transformation context.\n'
            'survey_manifest.json mirrors metadata in standard JSON for convenient inspection.\n'
            'Portable plan key: (Survey_ID, Transect_ID); waypoint key: (Survey_ID, Waypoint_ID).\n'
            'Original fields are never replaced by survey metadata. Original provider keys\n'
            'and generated GeoPackage FIDs are not portable identities.\n'
            'Original ID values map through the recorded id_field and legacy trimmed-text rule.\n'
            'Optional CSV/GeoJSON/GPX companions contain INCLUDED WGS84 plans/waypoints.\n'
            'Keep this package together: companions retain legacy schemas, and Survey_ID\n'
            'is provided by the package metadata rather than inserted into waypoint IDs.\n'
            'GPX tracks are planned lines, not field recordings; device extensions may be ignored.\n'
            'Sort waypoints by Transect_ID and Sequence. Final forward bearing is NULL.\n'
            'QA model status is independent of navigation eligibility; no coverage is claimed.\n'
            'Default QA thresholds are not scientific or regulatory compliance standards.\n',
            encoding='utf-8')
    try:
        return publish_package(parent, name, write)
    except (OSError, sqlite3.Error, ET.ParseError, csv.Error) as error:
        raise ValueError(f'Survey export failed: {error}; existing packages were not overwritten') from error
