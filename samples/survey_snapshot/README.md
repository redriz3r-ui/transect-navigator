# Stage 7 export fixture

This focused eight-plan audit fixture extends the independently authored Stage 6
Survey results fixture; it is **not** the future full Stage 8 12-line survey. Its
coordinates, decisions and expected totals are authored by
`tools/survey_snapshot_fixture.py` and the test fixture, without using production
Transect Navigator calculations to generate the oracle.

The Stage 6 eight file sources contain 85 fixes: 82 assigned, three ambiguous. The
Stage 7 additions are a three-fix stationary accepted Visit (20 m north of T07), a
four-fix source with two transition points and two unrelated points (one missing time),
and a two-point loaded QGIS PointZ layer with no original device-file bytes. They add
two known and one unknown surveyor. Review keeps every point; it does not infer any
Transect_ID. T07 retains a moving event and gains a stationary event with SQL NULL
geometry. The loaded layer tests honest unavailable-source provenance and Z retention.

| Quantity | Authored expected |
| --- | ---: |
| Planned transects / 10 m waypoints | 8 / 108 |
| File sources / loaded snapshots / recordings | 10 / 1 / 11 |
| Raw observations | 94 |
| ASSIGNED / AMBIGUOUS / UNASSIGNED | 85 / 3 / 6 |
| UNASSIGNED with TRANSITION role | 2 |
| QA observations / events | 85 / 2 (one moving, one stationary) |
| COMPLETE / INCOMPLETE / REVIEW / NOT_SURVEYED | 4 / 2 / 1 / 1 |
| Planned / accepted track distance | 1,000 m / 740 m |
| Confirmed progress / QA corridor | 640 m / 540 m |

`expected.json` records the oracle. The original Stage 6 `expected.json` supplies
the exact author-reviewed assignment ranges and remains unchanged. Original CSV/
GeoJSON inputs in this directory are immutable fixture files during testing.

Regenerate with `python3 -m tools.survey_snapshot_fixture`. Under the configured QGIS
Python, run `python3 -m unittest tests.qgis.test_survey_snapshot_export`. Tests
exercise native source CRS/Z and bytes, all dispositions, decision/Visit/QA relations,
NULL and moving event geometry, summaries, current/stale gating, no-overwrite, failure
cleanup, corruption detection and an offscreen RESULTS export click. The fixture is
small: it is an integrity/round-trip oracle, not a field-scale performance claim.
