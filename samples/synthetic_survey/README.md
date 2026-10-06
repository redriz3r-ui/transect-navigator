# Reproducible synthetic survey

This deterministic fixture exercises navigation preparation, recorded-data ingestion,
chronological ordering, deviation, descriptive statistics, coverage, events and export.
It is synthetic test data, not a real wildlife survey or a protocol-compliance example.

## Scenario and inputs

`planned_transect.geojson` contains one 300 m eastward transect, ID `SYN01`.
`gps_observations.csv` and `gps_observations.geojson` contain the same 61 fixes,
five metres apart along the transect and five seconds apart in time. UTC timestamps
run from 2026-01-01 12:00:00 to 12:05:00. All geographic coordinates are WGS84
(EPSG:4326), longitude then latitude. The equatorial location makes independent
distance expectations possible; it is not a suggested field-survey location.

| Stations (m) | Intended observations |
| --- | --- |
| 0–95 | Small signed jitter, at most 0.75 m |
| 100–150 | Gradual northward drift, 0.8–4.8 m |
| 155–210 | Sustained northward deviation, 6–14 m |
| 215–230 | Return toward the transect, 4.4–1.4 m |
| 235–300 | Small jitter, ending on the transect |

Positive signed deviation is left of the directed transect (north for this eastward
line); negative is right. Fixes at stations 170–190 report horizontal accuracy
8, 10, 12, 10 and 8 m; other fixes report 2 m. HDOP, PDOP, satellite count, fix
type and elevation are also present and retained. These fields are deliberately
synthetic. Reported uncertainty is never subtracted from spatial deviation.

## Settings and independent expectations

Use 10 m waypoint spacing; user-configurable QA thresholds 3, 5 and 10 m;
coverage maximum station gap 25 m and time gap 60 s. Set event threshold 5 m,
enable both minimum duration 15 s and minimum station span 15 m, and set event
maximum time gap 20 s and spatial/station jump 20 m. These are test settings,
not scientific or regulatory standards.

| Quantity | Expected result |
| --- | --- |
| Waypoints | 31, `SYN01_001` through `SYN01_031`, stations 0–300 every 10 m |
| Forward bearings | 90°; final waypoint has no next bearing |
| Raw observations | 61, all original fields preserved |
| Raw track | One chronological, unsmoothed line, approximately 306.826130 m |
| Mean absolute deviation | 173.65 / 61 = 2.846721311 m |
| Median / maximum | 0.75 m / 14 m |
| RMSE | sqrt(1423.2925 / 61) = 4.830389626 m |
| 95th percentile | 12 m, linear sample-quantile interpolation |
| Within 3 / 5 / 10 m | 42 / 49 / 56 observations out of 61 |
| Coverage support | 270 m, 90%; 54 accepted adjacent pairs, 6 rejected |
| Events | One, `SYN01_E001`, stations 155–210 m, 12 observations |
| Event duration | 55 s, 12:02:35–12:03:30 UTC |
| Event mean / maximum | 118 / 12 = 9.833333333 m / 14 m |
| Mean reported accuracy | 160 / 61 = 2.622950820 m; event mean 62 / 12 m |

Coverage counts the union of supported station intervals between eligible consecutive
fixes. The five fixes exceeding threshold C (10 m) exclude six adjoining intervals,
leaving the 165–195 m interval unsupported. Reaching both endpoints therefore does
not produce 100% coverage. This is sampled-progress support, not proof of survey
compliance. Deviation statistics are observation-weighted; this fixture uses even
sampling, whereas uneven sampling can bias real-world statistics toward dense fixes.

`expected.json` stores the full offset/station sequence and independently specified
numeric oracle. Its sums and counts are separately tested with rational arithmetic.
Coordinates use equatorial WGS84 radius formulas. Only the portable fixture runner
uses their analytic inverse. Production QGIS analysis still uses its actual CRS and
metric-frame transformations. The full QGIS test allows 0.0001 m numerical tolerance
for these short, near-equatorial geometries; this is not a general accuracy claim.

## Run from the repository root

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t .
python3 -m tools.synthetic_survey --check
python3 -m tools.synthetic_survey --portable /tmp/transnav-synthetic-new-run
python3 -m tools.synthetic_survey --generate /tmp/transnav-synthetic-new-inputs
```

Output directories must not already exist. No existing package or source data is
overwritten. Run normal Python: optimized `-O` execution is explicitly rejected by
the validation runners because it disables assertions.

`portable_run/` is a checked-in successful portable run. It includes validated
navigation CSV/GeoJSON/GPX, raw/QA/track/event GeoJSON previews, a summary CSV and
`validation.json` explicitly recording that QGIS did not execute. The preview files
are fixture exports, not evidence that the production GeoPackage service ran.
Original raw attributes and unsnapped coordinates are compared on read-back.

## Full QGIS workflow

With a configured QGIS Python environment, run:

```sh
QT_QPA_PLATFORM=offscreen python3 -m tools.qgis_synthetic_survey --output /tmp/transnav-qgis-new-run
```

This runner uses real plugin services and providers for navigation GeoPackage and
exchange exports, CSV ingestion, track construction, CRS transformations, comparison,
statistics, events, styled results and completed-analysis GeoPackage/CSV export.
It checks numeric results, source hashes, raw geometry/attribute preservation,
SQLite integrity, layer names/CRS, summary parameters and exported values.
It writes a file-backed `synthetic_survey.qgz` with relative paths, then reopens it
and checks layer validity. Only a completed run writes `validation.json` claiming
QGIS execution. Failed runs leave their new directory for inspection.

Three integration tests run the real pipeline using CSV, GeoJSON, and reversed CSV
storage order. The latter must retain original source records while restoring track
chronology from timestamps. They skip explicitly if PyQGIS is unavailable. No QGIS
project or GeoPackage has been fabricated as a substitute for those runtime tests.

## Manual QGIS validation

1. Install the experimental plugin and load `planned_transect.geojson`.
2. In PREPARE select `Transect_ID`, use 10 m spacing, generate waypoints, then export
   navigation data to a new package. Check IDs, endpoint inclusion and eastward order.
3. In ANALYZE select `SYN01`; import `gps_observations.csv` with Longitude/Latitude
   in EPSG:4326 (or load the companion GeoJSON). Choose `Timestamp` and
   `Horizontal_Accuracy_m`, and enable raw-track construction.
4. Apply the settings above, run analysis and compare RESULTS to the expected table.
5. Inspect the named group, raw points/track, left/right signed values, class legend,
   prominent event, attribute presentation and unsmoothed profile. Confirm raw source
   data and styling remain unchanged. Threshold colours do not indicate compliance.
6. Export the completed analysis and inspect all spatial layers, summary CSV and
   recorded parameters. If the automated QGIS runner was used, open its `.qgz`, then
   move its entire output folder and confirm relative layer paths still resolve.
7. Check plugin unload/reload and repeat the workflow without duplicate actions.

The portable workflow and the full 283-test QGIS 4.2.3/macOS suite passed. The
installed v0.1.12 plugin also completed this synthetic PREPARE, ANALYZE, RESULTS and
completed-analysis export in the real QGIS GUI. Exported data reopened with the known
numeric answers and preserved raw fields. This straight, short, equatorial fixture
does not establish other CRS/device/platform compatibility; edge-case testing and
real field validation remain necessary. Raw GPS files should always be retained.
