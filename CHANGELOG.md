# Changelog

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
