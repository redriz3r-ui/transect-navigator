# Changelog

## Unreleased v0.2 development — Stage 2

- Added immutable Survey/planned-transect records, stable content identities and
  complete planned-layer preflight without changing the v0.1 waypoint engine.
- Added batch Survey PREPARE with explicit unsupported-plan exclusions and preserved
  official geometry/direction, original fields and legacy Waypoint_ID behavior.
- Added a staged, verified, no-overwrite Survey navigation GeoPackage plus existing
  CSV, GeoJSON and GPX companions and a machine-readable manifest.
- Separated exact CRS serialization provenance from semantic/operational identity so
  harmless QGIS custom-CRS WKT normalization does not report source mutation, while
  genuinely different CRS definitions remain rejected.

## 0.1.13-rc1 — release candidate

- Packaged the existing v0.1.12 behavior after the complete 283-test QGIS suite and
  synthetic PREPARE, ANALYZE and completed-analysis export passed in QGIS 4.2.3 on
  macOS. No calculation, geometry or export behavior was changed for this RC.
- Updated installation, workflow, validation and limitation documentation.
- Included README, changelog and explicit license-status text in the installable ZIP.

## 0.1.12 — development patch

- Preserved SQL NULL geometry for stationary deviation events in completed-analysis
  GeoPackage export; retained LineString geometry for moving events.

## 0.1.11 — development patch

- Corrected point-copy construction for QGIS 4.2.3 ingestion and normalized exact
  integer waypoint stations before provider round-trip verification.

## 0.1.10 — development patch

- Corrected the CRS epoch precondition for ordinary WGS 84 datum-ensemble inputs.

Earlier development-stage history and engineering decisions remain in `docs/`.
