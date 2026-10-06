# Completed-analysis export — v0.1.8

## Use

1. Run **COMPARE OBSERVATIONS TO TRANSECT** on ANALYZE.
2. Inspect the completed run on RESULTS. Its group name/time identify what will export.
3. Choose an existing **Analysis output folder** and a new **Package name**.
4. Click **EXPORT ANALYSIS PACKAGE (GeoPackage + CSV)**.

The plugin creates a new directory containing `analysis.gpkg`, `qa_summary.csv`, and
`README.txt`. Reusing a package name fails clearly; no existing package or user file
is overwritten. Creation and validation occur in temporary staging beside the chosen
destination. Only a fully verified package is published. Failures clean up staging
and leave the completed run available for retry. Errors appear inline, without modal
message spam. Very large exports run synchronously and may temporarily occupy the UI.

Export uses private read-only copies captured before the completed analysis was added
to the project. It does not reread current controls, source layers or displayed result
layers. Changing thresholds after analysis requires a new comparison to create a new
result; exporting the prior result correctly retains the prior settings. Removing or
editing displayed results does not alter the export snapshot. Only the latest successful
run is retained in the dialog for export. Unloading the plugin releases that snapshot;
export first or rerun analysis after reloading. Private copies add memory overhead.

## Self-contained GeoPackage

| Table/layer | Contents |
| --- | --- |
| `planned_transect` | Selected official feature, native CRS/geometry/attributes |
| `navigation_waypoints` | Generated WGS84 navigation points, IDs, sequence, distance and bearing |
| `raw_gps_observations` | Preserved analysis input observations, native CRS, raw fields, GNSS and timestamps |
| `raw_track` | Recorded or safely constructed track, when available |
| `qa_observations` | Original observation geometry/attributes plus derived QA fields |
| `deviation_events` | Event geometry and attributes, including stationary records with NULL geometry |
| `qa_summary` | One scalar statistics/settings record; machine-readable without the UI |
| `analysis_metadata` | CRS WKT, geometry type, fields, counts, data role and JSON provenance per layer |
| `normalized_observations` | Normalized observation identity, chronology and ingestion status |
| `ingestion_validation` | Input notices, including missing timestamps and ordering limitations |
| `qa_nearest_locations` | Separate nearest locations in the QA metre frame |
| `qa_connectors` | Separate observation-to-nearest lines |
| `event_validation` | Groups not evaluable for events |

Unavailable `raw_track` is omitted, with an explicit `Included=0` row in
`analysis_metadata`. No chronology or track is fabricated. Empty event/diagnostic
layers are retained so zero events can be distinguished from missing evaluation;
consult event validation and summary counts. There are no external file dependencies
for these analysis outputs. Original device files should still be retained: ingested
fields cannot preserve information the input provider/device never supplied.

Each spatial layer retains its own CRS; export does not reproject, smooth, snap or
modify geometry. Planned/raw geometry keeps Z/M where supported without conversion;
an unpreservable geometry/attribute fails verification rather than silently losing
information. Native raw data and local-metre QA diagnostics deliberately use different
CRSs. Exact original CRS WKT is also stored in metadata for interpretation.

Original field names and values remain intact, including fields called `fid` or `geom`.
The writer selects distinct internal primary-key/geometry column names. GeoPackage
row IDs may change; join using preserved source-ID fields and the mappings in provenance,
not provider row IDs. QA field collisions retain their existing documented mapping.
The retained GPX document text (when supplied by ingestion) and raw-track origin
are explicitly included in JSON provenance; custom properties are not assumed to
survive an OGR export automatically. Runtime layer IDs in provenance identify the
captured run but are not portable join keys.
Raw identifiers are not forcibly renamed to export conventions; derived layer/table
names are fixed and readable.

## Reproducible settings and summary CSV

`qa_summary` preserves existing statistics, definitions, track availability, threshold
percentages and GNSS descriptions, and adds the completed run's:

- waypoint spacing;
- event deviation threshold, optional minimum duration/station span and maximum time/spatial gaps;
- event count, unevaluated group count and complete event-detection diagnostics as JSON;
- UTC analysis completion time, plugin version and export schema version.

QA thresholds A/B/C, coverage corridor/gap limits, percentile method and observation
weighting are already in the summary and remain included. NULL optional event minima
mean **disabled**, while numeric zero remains zero. `analysis_metadata.Provenance_JSON`
retains ingestion mappings, source identifiers, coordinate operations, QA CRS/model,
subdivision/radius limits, sign convention and event definitions. These are standard
GeoPackage attribute tables and JSON field values, not a separate custom file format.
They are discoverable in QGIS/OGR and ordinary SQLite clients. The package contains
the completed values/settings; it does not automatically rerun the analysis.

The UTF-8 summary CSV has one header and one data row matching `qa_summary` (excluding
the GeoPackage's generated row ID). NULL values are blank; zero values are numeric.
Floats retain full serialization precision rather than the rounded UI display.
JSON diagnostics remain valid quoted CSV cells. Import identifier/text columns as text
in spreadsheet software; leading zeros and literal formula-like text are preserved.

Default thresholds are not scientific/regulatory compliance standards. Observed
spatial deviation and reported GNSS uncertainty remain separate. Accuracy uses source
units; it is not subtracted from deviation. Coverage remains conservative sampled
progress support. Refer to the existing algorithm guides for interpretation.

## Verification and limitations

Before publication, every GeoPackage layer is reopened through QGIS/OGR and compared
against the export snapshot: feature counts, CRS, field names, every native geometry
WKB and every original field value/type representation. Metadata/summary rows receive
the same checks. SQLite integrity, foreign keys and `gpkg_contents` registration are
checked. The CSV is reopened and compared cell-by-cell. Unsupported driver conversions
abort export. This strict policy can reject provider-specific field types or datetime
representations; no lossy fallback is performed. Original provider schema constraints,
styles, attribute-form configuration, project layer tree and the profile chart are not
embedded. PDF reporting is not implemented.

Pure settings/CSV/publication tests run without QGIS. GeoPackage, CRS, Z-geometry,
NULL/empty-layer and UI tests executed in the complete QGIS 4.2.3/macOS suite.
The installed v0.1.12 plugin also exported the known-answer synthetic run through
RESULTS; all GeoPackage layers reopened with expected values and source data intact.
The separate stationary/moving event scenario confirmed SQL NULL and LineString
geometry respectively. Other QGIS versions, platforms and field datasets remain
unvalidated. See [validation evidence](validation.md).

Manual/runtime checklist:

1. Run the full suite in real QGIS Python; install the experimental ZIP and complete a
   known comparison. Export from RESULTS and reopen `analysis.gpkg` in a new project.
2. Verify table counts/IDs, native geographic/projected CRS, Z/M preservation, raw
   timestamps/GNSS, nearest locations, event attributes and summary numbers.
3. Inspect `qa_summary` and JSON provenance for changed spacing, QA thresholds,
   event minima and gap limits. Check disabled minima versus numeric zero and missing
   raw-track metadata. Check stationary events and empty validation tables.
4. Compare CSV cells with the GeoPackage summary. Delete displayed layers/change
   controls before export and verify the completed run still exports unchanged.
5. Reuse an existing package name; test unwritable destinations/file locks. Expect
   a clear inline failure, intact prior files and no partial published package.
6. Test dialog resizing/scrolling, close/reopen and plugin unload/reload. Verify that
   navigation export and visualization continue working.

The implementation follows the documented QGIS
[vector writer API](https://qgis.org/pyqgis/3.44/core/QgsVectorFileWriter.html).
