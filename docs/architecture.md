# PREPARE workflow and package architecture (0.1.2)

The lifecycle controller and lazy factory remain unchanged. `prepare_navigation`
creates one PreparedNavigation result: a detached source-feature memory snapshot,
WGS84 planned vertices, immutable waypoint records, the generated point layer and a
copy of the transform context. UI and export use that same run. The existing
`prepare_waypoint_layer` API remains a compatibility wrapper.

Export from the dialog always regenerates from the current source, scope, spacing
and direction. It never exports an old, possibly edited preview layer. Native source
snapshots preserve original direction and attributes, while generated route points
follow the selected navigation direction. Only successfully completed layers/files
are added or published. Source edits, geometry and selection are never modified. The detached snapshot is
also checked against original feature geometry and values before use, so an unsupported
memory-provider field conversion cannot silently corrupt the archival source copy.

- `navigation/waypoints.py`: QGIS-independent stationing and records.
- `integration/geodesy.py`: WGS84 ellipsoid measurement through QgsDistanceArea.
- `integration/prepare.py`: source validation, snapshot, transforms and generation.
- `io/exchange.py`: deterministic CSV, GeoJSON and GPX plus content verification.
- `io/package.py`: destination validation, private staging and exclusive publication.
- `integration/export.py`: GeoPackage writer, actual reopen/geometry/field/CRS checks,
  SQLite integrity verification, and coordination of exchange files/provenance text.
- `ui/main_dialog.py`: grouped inputs/output options, Generate/Export actions and a
  single plain-text inline status area. No calculation or serialization lives here.

GeoPackage is always included to preserve the native source geometry/CRS and every
original attribute alongside normalized WGS84 plan and waypoint layers. Three layers
avoid conflicting with an existing source field named Transect_ID. Generated database
row-ID/geometry columns use names that cannot collide with source fields. Reopened
field values and WKB geometry are compared; unsupported conversions abort explicitly.
External feature IDs may change; provenance records the original feature IDs.

CSV/GeoJSON carry separate plan/point files; GPX uses standard route and track
structures. GPX survey fields require namespaced extensions, which are permitted
by the format and verified independently. The companion GeoPackage remains the
complete record even when a GPX client ignores extensions. No custom data container
or manifest format was invented; run context is readable README.txt.

Files are written into a private temporary directory beside the target. After every
format validates, the directory is published. POSIX reserves an empty destination
exclusively before replacing it with the staged directory. Windows uses rename,
which fails if the destination exists. Failed writes clean up staging; failed POSIX
publication removes only the still-empty reservation. Existing files are never an
allowed overwrite target. Filesystem-specific behavior still needs desktop testing.

The Processing provider remains deferred because neither supported QGIS target is
available for lifecycle/export validation. The service functions already support
scripting and provide a direct future Processing integration point. No field/mobile
navigation, GPS QA/QC, compliance threshold or reporting functionality was added.

## Official API and format references reviewed

- [GPX 1.1](https://www.topografix.com/GPX/1/1/) and its official XSD (vendored under
  tests/schemas only): WGS84, root requirements, element order, ranges and extensions.
- [GDAL GPX driver](https://gdal.org/en/stable/drivers/vector/gpx.html): routes, tracks,
  route/track point representation and client-dependent extension behavior.
- [QgsVectorFileWriter](https://api.qgis.org/api/3.44/classQgsVectorFileWriter.html):
  writeAsVectorFormatV3, driver selection and creation/append-layer policies.
- [QgsDistanceArea](https://api.qgis.org/api/3.44/classQgsDistanceArea.html) and
  [QgsCoordinateTransform](https://api.qgis.org/api/3.44/classQgsCoordinateTransform.html):
  inherited PREPARE geodesy and context policies.

README retains the exact CRS/path interpretation and all geometry rejection policies.
Documentation review is not QGIS runtime evidence. Pure serialization/schema tests
are real file tests; QGIS/GDAL integration cases skip only when PyQGIS is unavailable.


## ANALYZE input ingestion (0.1.3)

PREPARE service APIs remain unchanged. The main dialog hosts PREPARE and a separate
ANALYZE input panel; plugin lifecycle code still owns only menus/dialog lifetime.

- `analysis/observations.py`: immutable observation/issue/normalization records,
  explicit-timezone ordering and conservative far-screen predicate; no QGIS imports.
- `io/recorded.py`: bounded raw GPX/CSV reading and source-byte checksums, no UI/QGIS.
- `integration/recorded.py`: provider sublayers, detached native snapshots with
  verified writes, GPX segment geometry and track-vertex extraction.
- `integration/analyze.py`: orchestration of plan selection, explicit CRS transforms,
  raw/derived role separation, ellipsoidal input screen and normalization outputs.
- `ui/analyze_panel.py`: controls, field mapping, one inline status area and completed
  result publication. No chronology or distance algorithm is implemented in the UI.

`IngestionResult` holds planned/raw observations/optional raw track plus separate
normalized observations and a validation table. Service calls do not add layers to
the project. `RecordingSource` carries source-ID links and structural segment order;
provider FIDs are never treated as timestamps. Pure core boundaries are checked by
AST tests. There is no Processing registration, final QA/QC metric or report yet.

See [ingestion contracts](ingestion.md) for exactly what constitutes a raw track,
missing/duplicate-time handling and the intentionally conservative far warning.


## Per-observation comparison (0.1.4)

`qaqc/deviation.py` implements pure local-metre directed nearest-point projection,
simple/open topology checks, tie rejection, station/progress and explicit side rules.
It reuses `geometry/planar.py`'s closed-segment primitive. It does not import QGIS or
UI modules, infer chronology, smooth observations or adjust GNSS uncertainty.

`integration/comparison.py` accepts a coherent IngestionResult, constructs a local
WGS84 AEQD metre frame, subdivides geodesic planned legs in working memory, calls
the pure engine and builds verified native QA points plus projected nearest-point
and connector layers. Raw layers and the official plan are never edited. Output
field collisions use an explicit mapping in provenance. All distance fields use
the same documented planar model, distinct from PREPARE's exact ellipsoidal leg
measurements. See [comparison contracts](deviation.md).

The ANALYZE UI's comparison action performs fresh ingestion and comparison before
publishing any layers. The ingestion-only action and PREPARE remain available.
Subsequent stages below add summary statistics, events, visualization and analysis
export. PDF reporting remains unimplemented. Real runtime checks require QGIS.


## QA/QC statistics (0.1.5)

`qaqc/statistics.py` owns immutable summary/option records, missing-value handling,
observation-weighted formulas, interpolated percentiles, multipart vertex-chord
length and interval-union progress support. It has no QGIS/UI dependencies and
requires no NumPy installation.

`integration/statistics.py` consumes the paired ingestion/comparison results,
measures raw track parts in the recorded local metre frame, and creates a verified
one-row nonspatial QA Summary table. GNSS descriptions remain separate from spatial
deviation, with unknown source units explicitly labeled. Parameters, valid/excluded
counts and formulas are machine-readable columns and provenance. The ANALYZE panel
collects three thresholds and two coverage gap limits. All outputs are completed
before project publication; no new Processing provider or reporting UI is added.


## Completed analysis export (0.1.8)

`integration/analysis_export.py` captures private read-only copies from a single completed
run, including new navigation waypoints. Capture occurs before project publication, so
a capture failure cannot leave a partially published analysis. The UI retains this
export payload independently of displayed layer lifetime and mutable controls. It costs
additional memory; unloading or replacing the latest result releases the old payload.

The shared `integration/export.py::write_geopackage_layers` writer is used by both
PREPARE and completed-analysis exports. It verifies geometry, field values, CRS and
SQLite structure before the existing atomic package publisher makes outputs visible.
`io/analysis_summary.py` owns portable scalar settings and strict CSV round-trip checks;
it imports no QGIS/UI code. Metadata is exported as a normal nonspatial GeoPackage
table with JSON provenance, not as QGIS custom properties that OGR would discard.
`ui/analysis_export_panel.py` owns destination controls and inline status only.
Analysis calculations and their scientific assumptions remain unchanged.

## Visualization (0.1.7)

`presentation/results.py` is QGIS-independent: shared class rules, summary formatting
and scatter data, with no recomputation of analysis statistics. `integration/visualization.py`
styles only fresh output layers and publishes one transactional result group. It creates
navigation waypoints through the established PREPARE service using a private XY input.
`ui/results_panel.py` owns a read-only table and Qt-painted profile. The main dialog
owns this child tab; its data are immutable calculation records rather than live layer
references. Analysis services remain independent of these presentation modules.

## Deviation events (0.1.6)

`qaqc/events.py` owns immutable event options/records and contiguous exceedance
segmentation. It consumes the existing summary-observation representation plus
local-metre observation coordinates, reuses timestamp parsing and descriptive
statistics, and never imports QGIS/UI. Complete unique-time groups are evaluated;
unknown chronology remains explicit, not silently replaced by provider order.

`integration/events.py` consumes paired ingestion/comparison outputs and creates a
separate derived event line layer and validation table. Lines use the participating
raw-fix positions projected to the existing analysis CRS. Stationary events retain
records with NULL line geometry and explicit status. Source IDs, options, group
exclusions, split counts and interpretation are recorded in fields/provenance.
The ANALYZE UI gathers options and creates all results before project publication.
See [event contracts](events.md). No event smoothing, field navigation or reports.
