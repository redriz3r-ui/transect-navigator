# Transect Navigator — v0.1 release candidate

> **v0.2 development workspace:** Stage 7 adds an auditable, verified Survey snapshot
> export through Survey RESULTS: standard GeoPackage, CSV summaries, JSON manifest,
> README and retained original-source bytes where available. Every raw observation,
> review decision, accepted Visit, QA result and operational status is represented;
> stale results cannot be published as current. See [v0.2 Stage 7](docs/v0.2_stage7.md)
> and its [focused sample](samples/survey_snapshot/README.md). Stage 6 supplies
> Survey RESULTS over verified Visit QA: an explicitly selected operational
> completion policy, one status per official plan,
> length-weighted progress/corridor totals, a copied status map, filters, QA drill-down,
> reviewed unresolved-evidence relevance, and verified results save/resume. Statuses
> are operational workflow classifications, **not regulatory/scientific determinations**.
> No automatic association is implemented. The installed v0.1
> workflow and calculation engines remain unchanged. See [v0.2 Stage 6](docs/v0.2_stage6.md),
> [focused Stage 6 sample](samples/survey_results/README.md), [v0.2 Stage 5](docs/v0.2_stage5.md),
> [v0.2 Stage 4](docs/v0.2_stage4.md),
> [v0.2 Stage 3](docs/v0.2_stage3.md),
> [v0.2 Stage 2](docs/v0.2_stage2.md) and the governing
> [architecture design](docs/v0.2_design.md).

**Version 0.1.13-rc1** packages the QGIS-validated v0.1.12 behavior. Transect
Navigator prepares waypoints from planned transects and measures recorded-GPS
deviation for QA/QC. It retains raw observations separately from all derived outputs
and never edits source layers. The release candidate changes documentation and
packaging, not geometry, statistics or export algorithms. See the [changelog](CHANGELOG.md).

The full **283-test suite passed with no failures, errors or skips** using QGIS 4.2.3
on macOS. PREPARE, ANALYZE, RESULTS and both export workflows were also exercised in
the real QGIS GUI with the [known-answer synthetic survey](samples/synthetic_survey/README.md).
That validates this environment and fixture; other QGIS versions, platforms, devices
and real field datasets remain to be validated. The ZIP is for private evaluation;
the [license status](LICENSE) must be resolved before public distribution.

**Interpretation:** Default 3/5/10 m deviation thresholds are editable display/QA
settings, **not scientific or regulatory compliance standards**. GNSS positional
uncertainty can affect apparent deviation. Retain original GPS/device files as the
authoritative raw record; normalized observations, tracks constructed from points,
QA points, event geometries and statistics are derived products. v0.1 has no dedicated
real-time mobile field-navigation interface, no smoothing and no PDF reporting.

## Complete v0.1 workflow

1. Install the ZIP through QGIS **Plugins → Manage and Install Plugins → Install from ZIP**.
2. Load a planned, single-part transect line layer with a stable Transect ID field.
3. In PREPARE, choose the layer/ID, set spacing (10 m by default), and **GENERATE WAYPOINTS**.
4. Choose a new package name and **EXPORT NAVIGATION PACKAGE** (GeoPackage, with optional GeoJSON/GPX/CSV).
5. Conduct the survey using external GPS or field tooling; retain its original recordings.
6. In ANALYZE, import recorded GPX, GeoPackage, GeoJSON or coordinate CSV, or select a loaded point/track layer.
7. Select the planned transect feature and map available timestamp/GNSS accuracy fields.
8. Set user-configurable thresholds and event criteria, then **COMPARE OBSERVATIONS TO TRANSECT**.
9. Inspect the transect-named layer group, signed/absolute deviations, statistics,
   coverage estimate, deviation events and profile in RESULTS.
10. In RESULTS, **EXPORT ANALYSIS PACKAGE** to a new folder containing
    `analysis.gpkg`, `qa_summary.csv` and `README.txt`.

Both exports refuse an existing package name. See the detailed PREPARE, ANALYZE and
RESULTS instructions below and the [analysis export guide](docs/analysis_export.md).

## Use in QGIS

1. Open Plugins → Transect Navigator → Transect Navigator….
2. Choose a polyline layer and its **Transect ID** field.
3. Set waypoint spacing in metres (default **10 m**; UI range 0.001–1,000,000 m).
4. Choose **Use selected features only** if appropriate. Unchecked means all input
   features, including any current provider/subset filter; selection is then ignored.
   Checked with no selection fails clearly instead of processing everything.
5. Keep geometry vertex order, or check **Reverse direction for this run**.
6. Click **GENERATE WAYPOINTS** to add a complete temporary EPSG:4326 point layer.
   No output destination is required to preview waypoints in QGIS.
7. For a saved navigation package, choose an existing **Output folder**, enter a new
   **Package name**, and choose GeoJSON, GPX and/or CSV companions. GeoPackage is
   always included so source geometry/attributes and navigation data remain together.
8. Click **EXPORT NAVIGATION PACKAGE**. Export generates afresh using current controls;
   it does not reuse a previous result layer that may have been edited. You can export
   directly without first generating a preview. Optionally add the exported waypoint
   layer to QGIS. Completion or validation errors appear in one inline status area.

Each input feature is one transect. IDs are converted to text with outer whitespace
trimmed; blank, NULL, nonfinite numeric and duplicate IDs fail the entire batch.
Identifiers remain case-sensitive. Features are processed by textual ID order for
repeatability. Sequence starts at 1 for each transect; never use provider feature IDs
as navigation order. Sort the attribute table by Transect_ID and Sequence.

## Ingest recorded GPS data

Open **ANALYZE — inputs**, choose a planned layer, ID field and one feature, then
choose a loaded recorded layer or open a GPX/GeoPackage/GeoJSON/CSV file. CSV requires
explicit XY columns and CRS. Select optional timestamp and GNSS accuracy fields,
then **INGEST RECORDED GPS DATA**. An optional group field separates surveys.

The plugin adds separate planned, raw observation, raw track (when available or
safely constructible), normalized observation and validation layers. Raw fields and
native geometry are retained. Tracks constructed from points require complete,
unique timestamps with explicit timezones; missing or duplicate times are reported
without inventing chronology. No smoothing, snapping or final QA/QC calculations.

These are temporary read-only memory snapshots, not saved survey archives. Keep
original GPS files; after comparison use RESULTS analysis export, or save individual
layers explicitly through QGIS. All GNSS fields
remain raw; accuracy units are not inferred. See [the ingestion guide](docs/ingestion.md)
for input contracts, CRS handling, limitations, examples and manual validation.

## Compare observations to the planned transect

Use the same ANALYZE inputs and click **COMPARE OBSERVATIONS TO TRANSECT**. The plugin
creates QA observations retaining original geometry/attributes, plus separate nearest
points and observation-to-plan connectors. Each QA row includes absolute deviation,
signed deviation where defensible, station/progress and available time/accuracy.

**Positive signed deviation means left of the planned direction; negative means
right.** Off-path endpoint matches and ambiguous vertex sides have NULL signs.
Equal nearest locations at different stations fail clearly. Source data are never
smoothed or snapped, and GNSS accuracy is preserved without subtraction or pass/fail
classification.

Analysis uses a local WGS84 azimuthal-equidistant metre projection, a 5 km radius
limit and a geodesically subdivided planned path. This is a local projected
approximation, not an exact ellipsoidal closest-point calculation. The plan must be
simple/open; crossings, overlap, backtracking and multipart plans are rejected.
Read [the deviation guide](docs/deviation.md) for fields, equations, precision,
vertex/endpoint policies, size limits and required real-QGIS validation.

## Distance, direction and CRS contract

Input must have a valid, static projected or 2D geographic CRS. Projected units may
be metres, feet or other units. No coordinate values are assumed to be metres.
The plugin transforms **copies of vertices** to WGS84 longitude/latitude using
`QgsCoordinateTransform` and the current project's transform context. Ballpark/fallback
transforms are disabled; exceptions or detected fallbacks abort. Available PROJ grids
and the selected datum operation still govern transformation accuracy. The original
layer, attributes, geometry, edit buffer and selection are not modified.

The navigation path is explicitly the sequence of **shortest WGS84 ellipsoidal
geodesic legs between transformed input vertices**. `QgsDistanceArea` with an explicit
WGS84 ellipsoid measures leg lengths, projects stations by distance along each leg,
and calculates true bearings. There is no Cartesian degree interpolation and no use
of project display units or project ellipsoid settings. The planar CRS guard from
the scaffold is retained for future planar analysis; PREPARE does not use that guard.

This path model deliberately interprets vertices geodesically. It is not exact
interpolation of a straight projected-grid segment or a straight line in a geographic
map display. Differences can be visible on long, sparsely vertexed lines. If your
survey contract requires grid-straight paths, do not use this mode without agreeing
that interpretation; such a mode is outside this stage. No automatic densification,
repair or smoothing is applied. Heights and magnetic declination are not modeled.

Stations are placed at 0, spacing, 2×spacing, … and the exact end. For 100 m at 10 m,
there are 11 waypoints. For 105 m there are 12, with a final 5 m interval. A positive
line shorter than spacing has two endpoints. Numerical endpoint differences within
0.1 micrometre are coalesced, with tolerance further bounded for tiny intervals.

Direction follows vertex order. Reversing input or choosing reversal swaps start/end
and recalculates stationing and bearings. For a length not divisible by spacing,
interior locations are re-anchored from the new start: a 105 m reversed line has stations
at physical positions 105, 95, …, 5, 0 rather than simply reversing the original list.
At bends, station distance follows the path but bearing points directly to the next
waypoint; it is not necessarily the current leg's tangent. Retraced/self-crossing
paths retain explicit vertex order; coincident consecutive waypoint positions have
NULL bearing, because a direction cannot be defined there.

Accepted geometry is **single-part, 2D LineString** only. Multipart, curves, Z/M,
empty/null geometry, repeated consecutive vertices (zero-length legs), nonfinite
coordinates, unsupported CRS types and dynamic CRS are rejected with an error.
Exact poles and individual geodesic legs over 1,000 km are deliberately unsupported
in this local-survey stage, avoiding ambiguous global/near-antipodal routing.
A batch is limited to 100,000 output waypoints; increase spacing or process smaller
selections if exceeded. Generation is synchronous, so large batches may pause the UI.

## Navigation package and format choices

Every export creates a **new folder**, never overwrites a source or existing package,
and contains `navigation.gpkg` plus `README.txt` with run settings and provenance.
Files are staged and reopened for validation before the folder is published. A failed
write/validation removes staging; an existing destination is refused. Use a new name
for each run. Generation/export is synchronous and capped at 100,000 waypoints.

| File / layer | Contents |
| --- | --- |
| `navigation.gpkg` → `source_transects` | Snapshot of chosen input features with original CRS, geometry and attributes |
| `navigation.gpkg` → `planned_transects` | WGS84 planned vertices in original order with normalized Transect_ID |
| `navigation.gpkg` → `navigation_waypoints` | Generated points with every navigation attribute |
| `planned_transects.geojson`, `navigation_waypoints.geojson` | Optional separate standard FeatureCollections, longitude/latitude in WGS84 |
| `planned_transects.csv` | Optional Transect_ID, EPSG:4326, LINESTRING WKT and start/end coordinates |
| `navigation_waypoints.csv` | Optional full navigation attributes including Latitude and Longitude |
| `navigation.gpx` | Optional GPX 1.1 routes for navigation points and tracks for planned vertices |

GeoPackage is the authoritative GIS copy. The `source_transects` layer preserves all
input fields; other formats contain the normalized IDs and navigation fields, not
all unrelated source attributes. Provider feature IDs may change; original feature
IDs are recorded in the package README provenance. If GeoPackage cannot preserve a
source field type or its value, export fails with a validation error instead of
silently dropping it. The native source snapshot uses original direction even when
navigation stationing is reversed. Cross-reference using the chosen ID field and
normalized Transect_ID (text, trimmed at the edges).

CSV uses UTF-8, a header, normal CSV quoting, decimal-point numeric fields, and blank
values for NULL bearing. Import IDs as text in spreadsheet applications to retain
leading zeros and literal text. GeoJSON encodes NULL as JSON null. GeoJSON keeps the
input vertex sequence; antimeridian-crossing lines are not split automatically, so
map renderers may draw wrapped lines differently. Consult the GeoPackage/GPX coordinates.

### GPX behavior and limitations

The writer emits actual namespace-qualified **GPX 1.1 XML**, using WGS84 coordinates,
`version`/`creator` attributes, and schema-defined element order. Each transect has a
named `rte` with ordered `rtept` navigation points; each point's `name` is its
Waypoint_ID. Planned input vertices appear in a named `trk`/`trkseg`, explicitly
labeled as a **plan**, never as recorded GPS observations. No times, elevations or
GPS accuracy values are invented. +180 longitude is encoded as equivalent -180
because GPX's longitude range excludes +180. Coordinates use decimal notation.

GPX has no native fields for our survey sequence, distance along or forward bearing.
Those values use the format's allowed `extensions` element, in namespace
`urn:transect-navigator:1`, with the same field names as the waypoint layer. A readable
`desc` also carries the values. NULL bearing is omitted. This is a standard GPX file
with permitted extensions, not a new file format. Devices/apps may ignore extensions,
shorten names or interpret routes differently; retain the GeoPackage/CSV as the full
attribute record. Device compatibility and mobile navigation are outside this stage.

The development suite validates emitted GPX against the official
[Topografix GPX 1.1 schema](https://www.topografix.com/GPX/1/1/), plus attribute and
geometry checks. Schema validity is not a claim of compatibility with every receiver.

## Example: transect layer → 10 m waypoints → navigation package

1. Load your 2D line layer (for example `planned_survey`) and choose its ID field
   (for example `Transect_ID`, with value `T04`).
2. Leave spacing at **10 m**, confirm the feature scope and direction, and click
   **GENERATE WAYPOINTS**. Inspect the temporary points and sort by Transect_ID/Sequence.
3. Choose an output folder and package name **survey_10m**. Leave all companions
   selected, then click **EXPORT NAVIGATION PACKAGE**.
4. Open `survey_10m/navigation.gpkg` in QGIS to inspect source, plan and waypoints.
   Use `navigation.gpx` where routes are supported, and `navigation_waypoints.csv`
   for coordinate tables. The package README documents the run settings.

A transect measuring exactly 100 m under the WGS84 path model yields T04_001 through
T04_011, stations 0–100 m, with NULL final bearing. A projected-grid segment labelled
100 m need not have exactly the same ellipsoidal length; the stated measurement model
controls waypoint counts and final short intervals.

## Output attributes

| Field | Meaning |
| --- | --- |
| Transect_ID | Source identifier represented as text |
| Waypoint_ID | ID plus sequence padded to at least 3 digits, e.g. T04_001 |
| Sequence | 1-based order within the directed transect |
| Distance_Along_m | Cumulative ellipsoidal station distance from the chosen start |
| Total_Length_m | Sum of ellipsoidal leg lengths |
| Latitude | WGS84 latitude in degrees |
| Longitude | WGS84 longitude in degrees; point X is longitude, Y is latitude |
| Bearing_Next_deg | Initial true bearing clockwise from north [0,360); NULL at end or coincident next point |

The temporary output layer's `transect_navigator/provenance` custom property records source
CRS WKT, source layer/feature IDs, ID field, spacing, direction, scope, distance model,
coordinate operation and plugin/QGIS versions. Keep the source data and QGIS project
for reproducibility. The package README also records this provenance, and the
GeoPackage contains the source snapshot; custom properties alone may not survive
arbitrary external exports.

## Install and update

From the project directory run:

```sh
python3 tools/package_plugin.py
```

In QGIS, use Plugins → Manage and Install Plugins → Install from ZIP and select
`dist/transect_navigator-0.1.13-rc1.zip`. Enable the plugin. For an update, disable
the old plugin, install the new ZIP and restart QGIS so imported Python modules are
refreshed. Older ZIP versions in `dist/` are historical development builds.

Alternatively copy the `transect_navigator` folder into the active profile's
`python/plugins` directory (Settings → User Profiles → Open Active Profile Folder),
then restart and enable it. The archive has one package root, no bytecode and no
third-party pip dependencies. It includes README, changelog and explicit license-status
text. It is intended for private/local installation. No icon or compiled Qt resources
are referenced by the plugin; QGIS uses its default plugin icon. A public release
still needs owner-approved licensing and maintainer contact/repository details.

## Tests and current environment

Run from the project root:

```sh
python3 -m unittest discover -s tests -t . -v
python3 -m compileall -q transect_navigator tests tools
python3 tools/package_plugin.py
```

Use Python 3.10+ for independent tests. For the complete suite, use QGIS's configured
Python with real PyQGIS (headless hosts may need `QT_QPA_PLATFORM=offscreen`). On this
macOS host the bundled QGIS 4.2.3 Python 3.12 runtime executed **283 tests: 283 passed,
0 failed, 0 errors, 0 skipped**. Missing PyQGIS produces explicit skips rather than
simulated successes. The independent Cartesian metric isolates core calculations;
the QGIS tests exercise actual CRS, providers, GUI widgets and GeoPackage round trips.
See [validation evidence](docs/validation.md). QGIS 3.44 and other 4.x builds are
intended targets, but have not been runtime-validated. This directory is not a Git
repository.

The export tests also write and reopen CSV/GeoJSON/GPX, compare records and planned
geometry, test failed-write cleanup and no-overwrite behavior, and run GPX XSD validation
using `xmllint --nonet`. The official XSD is in `tests/schemas/` for offline tests.
If xmllint is unavailable those XSD checks explicitly skip; the plugin does not
require xmllint at runtime. GeoPackage checks use actual PyQGIS/GDAL.

## Scripting and Processing

Core operations are callable from QGIS Python without the dialog:

```python
from pathlib import Path
from transect_navigator.integration.prepare import prepare_navigation
from transect_navigator.integration.export import export_navigation_package

prepared = prepare_navigation(iface.activeLayer(), 'Transect_ID', 10.0)
folder = export_navigation_package(prepared, Path('/choose/an/existing/output/folder'),
                                   'survey_10m', {'CSV', 'GeoJSON', 'GPX'})
```

`prepare_waypoint_layer` remains available for earlier callers that only need a layer.
A Processing provider is deferred beyond v0.1; the existing service API can underpin
it without moving algorithms into UI code.

## RC manual smoke test in QGIS

Use a clean QGIS profile and a new disposable output folder. This check is for the
**RC ZIP itself**; previous runtime validation used the installed 0.1.12 build.

1. Install `transect_navigator-0.1.13-rc1.zip`, enable it, open/close/reopen the dialog,
   then disable/re-enable the plugin. Confirm one menu action and no Python exception.
2. Load `samples/synthetic_survey/planned_transect.geojson`; confirm EPSG:4326 and one
   `SYN01` feature. In PREPARE, generate 10 m waypoints. Confirm **31** points, IDs
   `SYN01_001`–`SYN01_031`, stations 0–300 m, 90° forward bearings and a NULL final bearing.
3. Export all PREPARE formats to a new package. Reopen GeoPackage, GeoJSON and GPX in
   QGIS; inspect CSV. Confirm 31 ordered points and the original plan remains unchanged.
4. In ANALYZE, open `samples/synthetic_survey/gps_observations.csv` with Longitude X,
   Latitude Y, EPSG:4326, `Timestamp` and `Horizontal_Accuracy_m`. Select `SYN01`.
5. Apply the settings in the [synthetic scenario](samples/synthetic_survey/README.md)
   and compare. Confirm 61 raw and QA points, one raw track, one 55-second event,
   mean 2.847 m, median 0.750 m, RMSE 4.830 m, P95 12 m, maximum 14 m and
   coverage 270 m / 90%. Inspect signed sides, layer styling and RESULTS profile.
6. Export completed analysis. Reopen every table/layer in `analysis.gpkg` and the
   summary CSV; confirm counts, CRS, settings, times, GNSS fields and NULL semantics.
   Reusing the package name must fail without changing existing files.
7. Verify source files/loaded source layers still have their original geometries and
   attributes. Confirm derived QA layers are separately labelled and editable
   thresholds are not presented as compliance standards.

For wider compatibility evaluation, repeat on other QGIS versions/platforms and
real device data. Include geographic/projected/feet CRS, reversed/multipart/invalid
lines, missing/duplicate timestamps, GPX device variants, locked/unwritable export
folders and local/network filesystems. These wider cases are **not** certified by the
single synthetic macOS runtime run.

Architecture: `navigation/waypoints.py` owns stationing; `integration/geodesy.py` owns
QGIS measurement; `integration/prepare.py` snapshots and generates; `io/` serializes
and publishes standard files; `integration/export.py` writes/verifies GeoPackage and
coordinates the package; `ui/` collects choices. `plugin.py` only handles lifecycle.

Ingestion architecture: `analysis/observations.py` validates chronology without QGIS;
`io/recorded.py` reads bounded raw files; `integration/recorded.py` snapshots native
data; `integration/analyze.py` coordinates CRS checks and normalization;
`ui/analyze_panel.py` handles controls and publishes completed layers.

Comparison architecture: `qaqc/deviation.py` owns the independent directed nearest-point
algorithm; `integration/comparison.py` establishes the local metre frame and creates
detached QA/diagnostic layers. See `docs/validation.md` for current test evidence.


## QA/QC statistics (0.1.6)

The comparison action also creates a **QA Summary** table with planned/recorded
length, observation count, mean/median/RMSE/max/95th-percentile absolute deviation,
and percentages within editable thresholds A/B/C (defaults **3/5/10 m**, inclusive).
These are **user-configurable QA/QC thresholds, not scientific or regulatory
compliance standards**. Set A ≤ B ≤ C before running comparison.

Reported GNSS accuracy has separate descriptive fields in source units; it is never
subtracted from observed spatial deviation. Missing track length/accuracy stays NULL.
Statistics weight each valid observation equally, so uneven GPS sampling and stops
can materially bias them. Outliers are retained.

Coverage is a conservative **sampled-progress support estimate**: union of short
station intervals between consecutive uniquely timed observations, both within
threshold C. Editable maximum station/time gaps default to **25 m / 60 s**. It does
not prove survey protocol compliance, or that the space between samples was walked.
See [statistics definitions and limitations](docs/statistics.md) for exact formulas,
denominators, missing-value policies, track-length model and manual QGIS checks.


## Deviation events (0.1.6)

The comparison action now adds **Deviation Events** and **Deviation Event Validation**.
Configure an exceedance threshold (default **5 m**), optional minimum duration
(default **5 s**, enabled), optional minimum station span (default **10 m**, disabled),
and maximum time/spatial gaps (defaults **60 s / 50 m**). All enabled minima use AND
logic, and every event requires at least two consecutive above-threshold fixes.
These are descriptive user choices, not scientific/regulatory compliance standards.

At-or-below-threshold fixes end a run. Gaps split runs; unknown/duplicate chronology
excludes its entire group with a visible validation record. No smoothing or raw-data
changes occur. Event length means station span, not walked distance; observed event
duration does not extrapolate across unobserved boundaries. Persistent GNSS error can
still look like a sustained event. See [event rules and tests](docs/events.md).
