# Validation evidence — v0.1.13-rc1

The release candidate packages the behavior validated as v0.1.12. On macOS with
QGIS 4.2.3 and its bundled Python 3.12, the complete unittest suite was rerun before
RC documentation/packaging changes: **283 tests, 283 passed, 0 failed, 0 errors,
0 skipped**. The final RC suite result is recorded in the release checklist/README.

The installed v0.1.12 plugin also completed the known-answer synthetic survey through
the real QGIS PREPARE, ANALYZE and RESULTS workflows. Navigation export and completed
analysis export were triggered from the plugin UI and reopened through QGIS providers.
The 300 m plan produced 31 waypoints, 61 preserved raw fixes, a 306.826 m track,
2.847 m mean absolute deviation, 270 m / 90% coverage and one 55-second event.
The analysis GeoPackage reopened with the expected counts, CRS, timestamps, GNSS
fields, signed deviations, statistics and intact source files. A separate two-event
case stored one stationary event as **SQL NULL geometry**, while the moving event
retained a two-vertex LineString. Reusing an existing package name was refused
without changing its files. These observations are synthetic-case evidence for this
QGIS/macOS installation, not certification of other platforms or field devices.

Historical development-stage results follow. Their statements about unavailable
QGIS or pending GUI validation describe those earlier stages, not the current RC.

## Synthetic workflow validation — v0.1.9

Recovered audit changes were copied to the persistent repository only after confirming
its v0.1.8 contents matched the pre-audit backup. The persistent recovered build then
passed **266 total: 179 passed, 0 failed, 87 PyQGIS skipped**, matching the audit build.

After adding the synthetic workflow: **277 total: 187 passed, 0 failed, 90 PyQGIS
skipped**. Eight portable tests validate deterministic inputs, independent rational
oracles, full numeric processing, raw-data preservation, chronological ordering,
navigation exports, corruption rejection and no-overwrite. Validation also rejects
nonfinite numeric comparisons and optimized Python that would disable assertions.
Three new real PyQGIS tests cover CSV, GeoJSON and reversed storage order through
completed analysis export and a reopened file-backed QGIS project; they did not run.
The synthetic GPX also passed `xmllint --nonet` against the GPX 1.1 XSD. Regenerated
portable output bytes match the committed run; all Python sources compile and parse
under Python 3.10 syntax rules. Runtime source files are identical to the audit build.

No production-code regression was discovered in this exercise. Plugin runtime code
and version remain unchanged. See the [scenario and expected outputs](../samples/synthetic_survey/README.md).
Portable test success does not establish QGIS provider, UI or GeoPackage runtime
compatibility. Final release-candidate preparation has not been performed.

## Engineering audit validation — v0.1.9

Baseline before edits: **245 total, 166 passed, 0 failed, 79 PyQGIS skips**.
Audited suite: **266 total, 179 passed, 0 failed, 87 PyQGIS skips**.

Thirteen pure regressions cover near-vertex nearest locations under rotation/reversal,
true ambiguity retention, identifiers, large CSV cells/global-limit restoration,
statistics option types, strict analysis timestamps and input table-expansion bounds.
Eight real QGIS regressions cover native geographic checks, ID consistency, failed
publication cleanup, lifecycle and sparse field expansion. They are skipped because
PyQGIS is unavailable. Existing GPX XSD, snapshot, export and numeric tests retain
their normal pass/skip behavior. See [engineering_audit.md](engineering_audit.md).

An additional independent analytic sweep passed 1,368 near-vertex cases across
offsets, signed distances and reversed traversal. Syntax/compilation and reproducible
ZIP source-content checks are included in final verification. These checks do not
substitute for the skipped PyQGIS suite.

## Historical completed-analysis export validation — 0.1.8

Persistent v0.1.7 baseline was tested before edits: **222 total, 155 passed, 0 failed,
67 PyQGIS skips**. Current suite: **245 total, 166 passed, 0 failed, 79 PyQGIS skips**.

Eleven new portable tests cover actual completed-run settings, explicit UTC timestamps,
disabled versus zero event minima, CSV precision/NULLs/text quoting, invalid values,
corruption detection, no-overwrite and failed-publication cleanup. Twelve new PyQGIS
tests cover complete analysis GeoPackage/CSV values, CRS and projected Z preservation,
private snapshot isolation, unavailable tracks/empty outputs, stationary events,
failure cleanup, corrupted-package rejection, mixed-run rejection and the UI action.
A regression test ensures capture does not reapply raw-input feature limits to derived
diagnostic tables, which can contain several notices per original observation.
Further regressions cover mixed event/raw snapshot identities and preservation of
retained GPX document text through the GeoPackage metadata table.
All PyQGIS tests are written but unexecuted: no QGIS/GDAL/runtime compatibility is claimed.

Python 3.10 syntax/compilation, deterministic plugin ZIP rebuild and exact runtime
source-to-archive comparisons pass. Navigation export tests continue to pass/skip
according to their existing runtime requirements.

See [analysis_export.md](analysis_export.md) for required manual checks and strict
round-trip limitations. This remains an experimental increment, not a release candidate.

## Historical visualization validation — 0.1.7

Persistent v0.1.6 was tested before edits: **204 total, 146 passed, 0 failed, 58 skipped**.
Current suite: **222 total, 155 passed, 0 failed, 67 PyQGIS skips**.

Nine new independent presentation tests cover exact threshold boundaries, equal
thresholds, invalid/missing values, expression quoting, profile order/duplicates/NULL
signs, nondegenerate bounds and independently specified summary numbers.
Nine new real-PyQGIS tests cover group structure, waypoint counts/spacing, styling,
exact expression boundaries, field collisions, unchanged sources/raw geometry,
repeat runs, publication rollback, Z-preserving plans, missing chronology and Qt
snapshot/profile rendering. These are unexecuted, not simulated successes.
The existing comparison UI test now expects the transect-named group and waypoints.

No QGIS runtime or visual compatibility is claimed. See [visualization.md](visualization.md)
for the outstanding manual checks. This remains an experimental increment, not a release candidate.

## Historical deviation-event validation — 0.1.6

Persistent v0.1.5 baseline before changes: **178 tests, 125 passed, 0 failed, 53 skipped**.
Current full suite: **204 tests, 146 passed, 0 failed, 58 PyQGIS skips**.

21 new pure tests use explicit synthetic event sequences: isolated spikes, sustained
runs, separate events, threshold equality, return to plan, incomplete runs, time and
XY/station gaps, exact boundaries, optional AND criteria, irregular sampling,
missing/duplicate chronology, backtracking spans, stationary runs, bad measurements,
missing GNSS accuracy and deterministic grouping/IDs.

Five new real-PyQGIS tests cover event attribute/geometry values, raw preservation,
unknown chronology diagnostics, stationary events, empty output and UI controls.
They are written but unexecuted here. The comparison UI test expects the two new
derived outputs. QGIS remains unavailable; no runtime or device compatibility is
claimed. Required manual checks are in [events.md](events.md).

Python 3.10 syntax/compilation, deterministic ZIP and exact source-content checks
pass. The stage remains an experimental development increment, not a release candidate.

---

## Historical statistics validation

# Statistics validation — 0.1.5

Persistent v0.1.4 tested before edits: **150 tests, 102 passed, 0 failed, 48 skipped**.
Current full suite: **178 tests, 125 passed, 0 failed, 53 PyQGIS skips**.

23 new pure tests exercise known mean/median/RMSE/type-7 percentile values, inclusive
custom thresholds, absent/invalid values and denominators, outliers, uneven sampling,
large-value RMS stability, multipart track lengths, short/incomplete tracks,
interval unions, backtracking, timing/station gaps, missing/duplicate chronology,
invalid stations and strict JSON serialization. A test initially expected unknown
coverage for known-time samples with missing deviations; the documented rule is
zero supported intervals, with rejected pairs counted, so the assertion was corrected.

Five new true-PyQGIS tests cover summary table values/metadata, custom thresholds,
separate GNSS values, absent tracks/accuracy, raw preservation and UI validation.
These and all earlier PyQGIS tests remain unexecuted because QGIS is unavailable.
The comparison UI test now expects the added summary table. No QGIS runtime or device
compatibility is claimed. See [statistics.md](statistics.md) for manual validation.

Python syntax/compilation and deterministic ZIP/source checks pass. v0.1.5 remains
an experimental development build, not a release candidate.

---

## Historical comparison validation

# Per-observation comparison validation — 0.1.4

Persistent v0.1.3 inspected before changes. Baseline: **113 tests, 80 passed,
0 failed, 33 skipped**. Current: **150 tests, 102 passed, 0 failed, 48 skipped**.
All skips are real PyQGIS cases; QGIS/PyQGIS is still unavailable on this host.

The 22 new executed tests use independent known geometry: ±1 m, 5 m, oblique
3–4–5 basis, rotated/transformed frames, reversal, vertices, clamped endpoint
overshoot, distinct nearest-station ties, multiple observations, invalid topology,
subdivision invariance and bounded computation. The entire existing suite passed.

15 new real-QGIS cases are written but not executed. They include geographic
WGS84 analytical fixtures, geographic vertices/reversal/endpoints, feet CRS,
missing GNSS accuracy/time, preserved native Z/M and attributes, connector lengths,
colliding fields, changed raw geometry, extent limits and UI atomic publication.
Neither runtime compatibility nor device compatibility is claimed. The projection
approximation has not been certified as an exact ellipsoidal nearest-point solver.

All plugin/test/tool files pass Python 3.10 syntax parsing and compilation. The
experimental ZIP is checked for exact current source contents and deterministic
packaging. This is a development increment, not a release candidate. Required
manual validation and limitations are in [deviation.md](deviation.md).

---

## Historical ingestion validation

# ANALYZE ingestion validation — 0.1.3

Persistent repository verified before modification: v0.1.2 PREPARE/export, no ingestion
modules. Baseline: **58 tests, 44 passed, 0 failed, 14 PyQGIS skips**. Unfinished drafts
existed outside the project; they were reviewed, corrected and tested in a fresh
baseline copy before completed work was saved to the persistent project.

Current suite: **113 tests, 80 passed, 0 failed, 33 PyQGIS skips** (19 new QGIS cases).
The 36 new pure tests include chronology, preservation, malformed/oversized input,
CSV snapshot consistency, synthetic GPX/CSV-to-normalization pipelines and official
GPX 1.1 XSD validation. Previous PREPARE/export tests remain in the suite.

No QGIS app or importable PyQGIS was found. New provider, CRS, native-geometry and UI
checks are written but **not executed**. See [ingestion guide](ingestion.md) for the
exact test coverage, supported contracts and required manual checks. No device
compatibility or QGIS runtime success is claimed. No release candidate was created.

Corrections during review: CSV now reads one coherent byte snapshot; XML nesting
and encoding are bounded; CSV field-name collisions fail; every raw layer write is
verified for geometry/attribute loss; file-option changes invalidate stale input;
file-backed OGR input is detached at Open Input; native track parent time/accuracy
is not mistaken for per-fix data; original source IDs and multipart breaks survive.

All plugin/test/tool Python sources were syntax checked (including Python 3.10
syntax). Deterministic archive/content checks run in the existing scaffold tests.

---

## Historical PREPARE/export validation (0.1.2)


Baseline inspected and tested before editing: **37 tests, 28 passed, 9 PyQGIS skips**.
No regressions were found. The project folder still has no Git metadata.

Current environment: macOS, Python 3.13.0, system xmllint. No importable QGIS/PyQGIS,
GDAL Python bindings or lxml. No QGIS runtime successes are claimed.

Historical suite: **58 tests, 44 passed, 14 PyQGIS skips**.

Executed successfully:

- All existing independent geometry, bearing, stationing and packaging checks.
- Actual CSV read-back: UTF-8 IDs, quoting, coordinate columns, numeric station fields,
  blank NULL bearings, and planned WKT/start/end coordinate preservation, including
  WKT cells longer than the standard CSV parser limit (restoring the global limit).
- Actual GeoJSON read-back: separate FeatureCollections, WGS84 coordinate order,
  exact IDs, native numeric attributes and JSON null bearings.
- Actual GPX parse/content checks: routes/points, planned tracks/vertices, ordered
  names, complete survey extensions, no fabricated timestamps/elevations.
- Two GPX validations using the downloaded official 1.1 XSD and xmllint --nonet,
  including +180/near-180 longitude handling. Survey extensions also compared to
  original records; schema validation alone does not validate extension semantics.
- Multiple transects and shuffled input ordering produce identical exchange bytes.
- Invalid coordinates, control characters, nonfinite values, inconsistent sequence,
  unknown formats and empty export batches fail before serialization.
- Altered CSV waypoint/plan contents and GPX attributes are detected on verification.
- New-folder publication, existing-package refusal, concurrent destination creation,
  failed writer cleanup, failed publish cleanup, invalid names and symlink rejection.
- Python syntax compilation, Python 3.10 syntax parsing and deterministic plugin ZIP.

A publication test initially compared macOS /var and /private/var path aliases as
strings. It was corrected to compare resolved paths, then the full suite passed.
The GPX longitude boundary was normalized explicitly to match its schema's exclusive
upper bound. No workaround hides invalid geographic output.

Written but NOT executed here:

- QGIS generation, CRS and geographic/foot-unit integration checks from prior stage.
- GeoPackage native-source round-trip (including a source field named fid), original
  WKB/attributes/CRS verification, all requested fields and output layer counts.
- Run snapshot isolation, provider export failure cleanup, current-controls export.
- Actual dialog generation/export, button recovery, missing inputs/CRS errors and
  inline feedback; file-backed output-layer loading.

Manual QGIS 3.44/4.2 testing must cover installation, the native UI, all export formats,
GPX client interpretation, file provider/edit-buffer preservation, datum-grid failures,
locked/unwritable/disk-full destinations and local/network filesystem publication.
Windows publication uses its native non-overwriting rename and was not exercised on
this macOS host. Processing-provider registration is deliberately deferred.

At that stage, scope stopped at PREPARE and navigation package export; no mobile navigation or QA/QC.
