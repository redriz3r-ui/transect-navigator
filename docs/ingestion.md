# ANALYZE input ingestion (0.1.3)

This guide describes the ingestion-only action introduced in 0.1.3.
Version 0.1.4 also provides a separate [per-observation comparison action](deviation.md).
The ingestion action preserves and normalizes recorded survey inputs. It does **not** calculate
cross-track error, QA/QC statistics, deviation events, coverage or compliance. The
PREPARE workflow and navigation export remain available on the first tab.

## Walkthrough

1. Load the official planned transect in QGIS. Open Transect Navigator and select
   **ANALYZE — inputs**.
2. Choose **Planned Transect Layer**, **Transect ID Field**, then one **Transect feature**.
   The feature selector includes the provider feature ID to disambiguate repeated IDs.
   Filter layers with more than 10,000 planned features before using the selector.
3. Choose a loaded recorded point/line layer, or **File input**. Browse to a file;
   select a GeoPackage/GeoJSON sublayer if necessary. For CSV, select X/longitude and
   Y/latitude columns and verify its CRS (the displayed default is EPSG:4326).
   Click **OPEN INPUT / RELOAD**. File data are detached snapshots at this point;
   reload to capture subsequent changes. Editing file path, sublayer, XY or CRS
   options invalidates the open snapshot. Loaded project layers are captured when
   **INGEST RECORDED GPS DATA** is clicked.
4. Select an optional timestamp field and GNSS accuracy field. GPX file input
   preselects `time`. Other formats do not guess field names. Select a survey/group
   field when the point layer contains multiple independent surveys. Every feature
   allowed by the layer's provider/subset filter is read; selection is ignored.
5. Leave track construction enabled to connect groups with defensible time ordering,
   or disable it to keep only raw points. Existing recorded track geometry is always
   preserved, regardless of the construction checkbox.
6. Click **INGEST RECORDED GPS DATA**. Inspect the inline notice and the detailed
   ingestion validation table. Geometry/CRS failures add no output layers. Timestamp
   anomalies retain observations and produce notices instead of dropping data.

The outputs are **temporary memory layers**. A saved QGIS project alone does not save
these features. Retain original files and save necessary layers explicitly through
QGIS. Analysis-package export is outside this stage. Custom layer properties such
as full GPX text are not guaranteed to survive ordinary vector-file export.

## Data roles and preservation

| Role | Representation |
| --- | --- |
| PLANNED TRANSECT | Native-CRS snapshot of the explicitly chosen feature, original direction and attributes |
| RAW GPS OBSERVATIONS | Native coordinates and attributes; GPX/CSV raw text fields; additional noncolliding source identifiers |
| RAW TRACK | Original native line geometry, GPX segment geometry, or explicitly labeled timestamp-connected observations |
| DERIVED QA/QC DATA | Separate WGS84 normalized observations and a validation table; no final metrics |

Roles are stored as `transect_navigator/data_role` custom properties. Provenance
records plugin/QGIS versions, source layer identifiers, CRS WKT, selected feature,
field mappings, actual coordinate operation descriptions, ordering policy and far
warning settings. CSV/GPX include original path and SHA-256 of the bytes read.
Original feature IDs are retained in noncolliding source/track fields. Provider IDs
on new memory layers are not an observation order. Normalized `Source_ID` links to
the field identified by provenance `raw_source_id_field`.

Input files/layers, their edit buffers and their selections are never edited. The
memory provider's stored attributes and WKB geometries are checked against the
intended snapshot; any coercion or loss aborts. No smoothing, snapping, resampling,
deduplication, geometry repair or dropping of bad-time points occurs. Outputs are
marked read-only as a UI safeguard, not a permanent archive or security boundary.

All input fields are retained, including available horizontal accuracy, HDOP, PDOP,
satellites, fix type and elevation. Unmapped fields stay in raw data. No missing
GNSS field is synthesized, no accuracy unit is guessed, and HDOP/PDOP are not treated
as metres. The optional mapping is preserved as `Accuracy_Raw`, not converted to a
measurement or used for a scientific pass/fail decision.

## Supported inputs and limits

- **GPX file:** UTF-8, namespace-qualified 1.0/1.1 tracks and trackpoints. Track and
  segment boundaries are preserved. Routes and standalone waypoints are excluded
  from survey observations with a notice, but remain in the full raw document.
  Each point includes its raw XML and standard time/elevation/GNSS fields; extension
  leaves have namespace-qualified field names. Extension attributes and structures
  remain in raw XML; no device-specific interpretation is claimed. Full document
  text is stored on the raw observation layer. Elevation is retained as a raw field,
  not transformed to another vertical datum. Empty segments generate notices;
  a file without any trackpoints is rejected. A one-point segment produces an
  observation and a notice, never a fake two-point line. Longitude must be in
  [-180,180), latitude in [-90,90]. DTD/entities, excessive XML nesting and other
  encodings are rejected. The reader validates needed structure and coordinates;
  it is not a general-purpose complete GPX schema validator.
- **Loaded point layers:** individual Point geometry, including Z/M. All exposed
  attributes and native geometry are copied. Multipoint and curved inputs are
  rejected. OGR GPX track-point layers preserve their `track_fid`/`track_seg_id`
  boundaries automatically; ordinary similarly named fields are not guessed to be
  GPX. Prefer direct GPX file input to preserve the complete original XML.
- **Loaded line tracks:** LineString/MultiLineString, including Z/M. Original
  line/multipart WKB is preserved. Separate observation points are extracted from
  each vertex, with parent attributes and explicit feature/part/vertex identifiers.
  Parent feature timestamp/accuracy fields are **not** inferred as per-vertex
  timestamps/accuracy. Vertices are the available recorded representation, not a
  claim that the original device fixes are recoverable. Empty tracks/parts fail.
- **GeoPackage / GeoJSON:** QGIS/GDAL vector providers load the selected sublayer;
  the same point/line policy applies. Native provider-exposed values are preserved.
  A provider may already have parsed a source timestamp or omitted unsupported
  data before this plugin sees it; keep the original file. GeometryCollections,
  multipoints, curves, polygons and attribute-only tables fail clearly.
- **CSV:** UTF-8 (optional BOM), comma-separated, quoted cells allowed, explicit XY
  column mapping and CRS required. Every cell is retained as a string, including
  leading zeros, blank values and original timestamp text. Record numbers are
  identifiers, not chronology. Headers must be nonblank and case-insensitively
  unique; rows must match the header. Oversized cells exceeding Python's CSV reader
  limit fail clearly rather than being truncated. No delimiter, epoch unit, axis
  order or CRS inference is attempted.

Audit v0.1.9 also bounds recorded attribute tables to 512 source fields and 5,000,000
attribute cells, including the union of sparse GPX extension fields. Inputs exceeding
these bounds fail explicitly; no fields or observations are silently dropped.

Limits: 100,000 observations/track vertices, 100,000 planned vertices and 50 MiB per
GPX/CSV file. These bound synchronous v0.1 ingestion; very large surveys should be
split deliberately. XML nesting is limited to 64. No new runtime dependencies.

## Chronology and raw tracks

Timestamp normalization accepts ISO date-times with seconds and explicit `Z` or
numeric timezone offsets, to at most six fractional digits. Values are compared as
UTC instants. Local/naive times, dates alone, numeric epochs, leap seconds, unknown
`-00:00` offsets and excess precision remain raw but cannot establish chronology.
Native QGIS date-times with a LocalTime time specification are also treated as
ambiguous; the computer's current timezone is never assumed to be the survey zone.

Within each group, **every** observation must have a valid, unique instant to assign
`Sequence` (1-based) or construct a track. Duplicate instants expressed with different
offsets are still duplicates. Any missing/invalid/duplicate time leaves all sequences
NULL for that group; valid times still have `Timestamp_UTC`. Stable source-ID display
sorting is not presented as chronology. Groups with one point cannot define a track.
Repeated coordinates and duplicate-time observations are retained separately.

GPX/native track vertex order is recorded geometry order. A conflict with available
timestamps is reported; the raw line is not reordered. Normalized observation
sequence can therefore intentionally differ from original track geometry order.
For point-only sources with complete times, a constructed track connects unchanged
native vertices, separately per group; its `track_origin` and `Order_Basis` explain
that it was constructed. Such connecting segments do not prove where the surveyor
walked between fixes. Time/spatial gaps are not interpolated into extra observations
or used to calculate duration, speed, coverage or deviation. Split independent
sessions with a grouping field; configurable gap/event handling is a later stage.
Geographic tracks crossing the date line may require QGIS map-wrap settings.

## CRS and very-far validation

Both inputs need a valid static projected or 2D geographic CRS; angular inputs must
use degrees. Dynamic, geocentric, vertical and compound CRSs require additional
policies and are rejected. Raw Z/M values are preserved, not transformed or interpreted
as time/elevation accuracy. Native XY must be finite; geographic bounds are checked
before transformation as well as after it. Missing/empty observation geometry fails
rather than silently losing records. The planned feature must be a nonempty,
positive-length single-part LineString (Z/M retained, horizontal XY used for the
screen). Planned multipart/curved geometry is rejected as ambiguous.

Only working XY copies are transformed to EPSG:4326 using the current project
transform context, with fallback/ballpark transforms disabled. Different declared
CRSs produce an informational notice. Source CRS, geometry and coordinates remain
untouched. Incorrect but syntactically valid CRS declarations cannot be diagnosed
reliably: verify the source CRS and inspect placement in QGIS.

The default **10,000 m very-far warning** is an adjustable input sanity screen, not
a QA/QC threshold. WGS84 ellipsoidal distances measure the planned geodesic legs and
distance from each observation to the plan's first vertex. For planned length L and
anchor distance D, `D - L > warning distance` proves the observation is farther than
the warning distance from the entire planned path, by the triangle inequality.
The screen is conservative: it can miss distant observations, especially for long
or winding plans. No warning is **not** proof of proximity. No degree-as-metre
calculation, nearest-point metric or final QA/QC result is produced at this stage.

## Validation evidence and manual work

The synthetic GPX fixture is not a device export. Its first segment deliberately
has times 2s, 0s, 1s (expected normalized source order 2, 3, 1), a second segment has
one point without time, and a third is empty. It includes raw GNSS fields and an
accuracy extension. Its official GPX 1.1 XSD validation and pure ingestion tests run
locally. GPX 1.0 has a small synthetic parsing test, not device certification.

Real PyQGIS tests are in `tests/qgis/test_ingestion.py` and skip explicitly when QGIS
is absent. They cover source preservation, loaded point ordering, native Z/M,
multipart separation, projected metre/foot and geographic CRS, the geodesic far
screen, CSV/GPX files, GeoPackage/GeoJSON provider loading and dialog publication.
None has been executed on this development host. Before using real survey data:

- Run the full suite in QGIS's Python environment, offscreen if supported.
- Visually test tab layout, selectors, scrolling, lifecycle/reload, inline errors,
  all file-dialog paths and recovery after invalid input.
- Inspect raw WKB/attributes and original edits/selection before/after ingestion.
- Check real device GPX extensions, provider timestamp types/timezones and GDAL
  field exposure; compare with the original file, not only map appearance.
- Test missing datum grids, geographic/feet layers, project display CRS changes,
  malformed geometry and the far warning. A failed batch must add no layers.
- Confirm saving selected temporary outputs through QGIS meets your retention needs;
  retain originals regardless. GNSS positional uncertainty remains part of the data.

Reviewed references: [QGIS memory layers](https://qgis.org/pyqgis/3.44/core/QgsMemoryProviderUtils.html),
[provider sublayers](https://qgis.org/pyqgis/3.44/core/QgsProviderRegistry.html),
[field selector](https://qgis.org/pyqgis/3.44/gui/QgsFieldComboBox.html),
[GPX 1.1 schema](https://www.topografix.com/GPX/1/1/), and
[GDAL GPX driver](https://gdal.org/en/stable/drivers/vector/gpx.html).
Documentation review does not establish runtime compatibility.
