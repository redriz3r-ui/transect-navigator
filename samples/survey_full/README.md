# SYN02: independent 10 × 10 km reviewed Survey fixture

This is the full Stage 8 known-answer fixture from
[`v0.2_design.md`](../../docs/v0.2_design.md), separate from v0.1 SYN01 and the
focused Stage 2–7 fixtures. The declared site boundary is
`site_boundary.geojson` (10,000 × 10,000 m reference construction near the WGS84
equator). `planned.geojson` has twelve official 1,000 m eastward geodesic lines;
`planned_projected.gpkg` is a companion rendering in EPSG:32631. T05/T06 are 12 m
apart and T11/T12 coincide. The original plan source direction is eastward even when
someone walks westward.

`tools/survey_full_fixture.py` independently constructs WGS84 coordinates using the
published Vincenty direct formula; it imports no Transect Navigator calculation code.
It writes five ordinary returned files with **no Transect_ID field or label**:

| File | Surveyor supplied at import | Intended contents | Raw fixes |
| --- | --- | --- | ---: |
| `A1.csv` | S01 | T01 forward, transition, T02 reverse, transition, T03 partial | 293 |
| `B1.gpx` | S02 | T04 with 500 s / 100 m gap, transition, T05/T06 ambiguity | 208 |
| `B2.gpkg` | S02 | First T07 full visit | 101 |
| `C1.geojson` | S03 | Reverse repeat T07, transition, T08 with deviation | 217 |
| `C2.csv` | S03 | Partial T10, transition, coincident T11/T12 ambiguity, 12 unrelated fixes | 189 |

`expected.json` is the frozen oracle. Its `review_actions` are **separate authored
human-review truth**, keyed by file and one-based inclusive source-row range. They
are not in the returned GPS files and do not imply an automatic assignment system.
For CSV, the 12 unrelated C2 fixes follow a large time/spatial break within that
source's one structural CSV group; review continuity splits them. The design's phrase
“separate recording segment” cannot be literally represented by the existing CSV
reader, which has one structural group per file. No source was silently rewritten or
production parser expanded for this fixture.

| Oracle | Expected |
| --- | ---: |
| Plans / 10 m waypoints | 12 / 1,212 |
| Raw fixes | 1,008 |
| ASSIGNED / AMBIGUOUS / UNASSIGNED | 719 / 202 / 87 |
| UNASSIGNED transitions / unrelated | 75 / 12 |
| Accepted Visits | 9, including two T04 bouts and two T07 passes |
| COMPLETE / INCOMPLETE / REVIEW / NOT_SURVEYED | 4 / 3 / 4 / 1 |
| Confirmed union progress / planned | 6,100 / 12,000 m |
| Project progress lower bound | 50.833333333…% |

T07's two complete Visits contribute 1,000 m of **union support**, not 2,000 m.
T05/T06 and T11/T12 receive no accepted support, because location cannot reveal
surveyor intent. T09 has no observed work. T04 has fixes at stations 0–400 m and
500–1000 m; the station-400 to station-500 timestamps are exactly 500 seconds apart.

T08 contains 101 fixes: endpoints 0 m offset, 11 fixes at stations 400–500 m
at +8 m, and 88 remaining interior fixes alternating ±1 m. Independent arithmetic
gives mean absolute deviation `176/101 = 1.742574257… m`, RMSE
`sqrt(792/101) = 2.800282871… m`, median 1 m, P95 8 m and maximum 8 m. At the
fixture's 5 m event threshold, 30 s minimum duration, 20 m minimum station span
and 30 s / 30 m gap limits, there is one 11-fix event of 100 s and 100 m span.
Three event fixes report 12 m accuracy rather than 2 m; their reported accuracy
does not change geometric deviation or membership. QA classes 3/5/10 m and the
100%-progress/endpoints completion policy are **synthetic test settings**, not
ecological or regulatory standards.

Regenerate deterministically with QGIS's bundled Python and GDAL/PROJ environment:

```shell
python3 -m tools.survey_full_fixture
```

Run `python3 -m unittest tests.qgis.test_survey_full` under the configured QGIS
Python, or `python3 -m unittest discover -s tests -t .` for the full suite. The
integration test uses the real Stage 2–7 interfaces: preflight, PREPARE/navigation
export, five-source intake, exact analyst review, save/reopen, Visit QA, Survey
results, auditable export and QGIS GeoPackage reopening. It checks numeric values,
source bytes, relationships and no-overwrite. Isolated tests cover the pathological
variants without changing the 1,008-fix oracle. This is offscreen QGIS integration,
not ordinary mouse-driven GUI acceptance or real field-device certification.
