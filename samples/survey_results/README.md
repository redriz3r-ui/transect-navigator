# Stage 6 operational-results oracle

This focused eight-plan fixture is separate from SYN01 and from the future full
12-transect Stage 8 survey. `tools/survey_results_fixture.py` imports no production
plugin code. Longitude is independently constructed as `distance / 6378137` radians
on the equator; northing uses the WGS84 equatorial meridional radius. The fixed
0.02 m comparison tolerance covers local-frame/reference approximation over these
100–200 m lines. Counts and statuses are exact.

Eight CSV recordings contain **85 observations: 82 ASSIGNED and 3 AMBIGUOUS**.
Recordings contain no Transect_ID. `expected.json` separately supplies authored
analyst truth and nullable surveyor identity. The three ambiguous fixes are explicitly
reviewed as relevant to T05; no spatial assignment algorithm is used. Raw source bytes,
coordinates, chronology, accuracy and HDOP remain preserved.

| Plan | Scenario | Planned m | Progress m | Corridor m | Status |
| --- | --- | ---: | ---: | ---: | --- |
| T01 | Full forward | 100 | 100 | 100 | COMPLETE |
| T02 | Full reverse | 200 | 200 | 200 | COMPLETE |
| T03 | Partial 0–60 | 100 | 60 | 60 | INCOMPLETE |
| T04 | 0–40 and 60–100; 610-second gap | 100 | 80 | 80 | INCOMPLETE |
| T05 | Three ambiguous fixes | 100 | 0 confirmed | 0 confirmed | REVIEW |
| T06 | Two full Visits, different surveyors/directions | 100 | 100 | 100 | COMPLETE |
| T07 | Full traversal consistently 20 m north | 100 | 100 | 0 | COMPLETE |
| T08 | No recording | 200 | 0 confirmed | 0 confirmed | NOT_SURVEYED |

Independent totals: **1,000 m planned, 640 m progress (64%), 540 m corridor (54%),
740 m accepted track, 8 accepted Visits; statuses 4/2/1/1**. Naively averaging the
eight confirmed progress percentages gives 67.5%, intentionally different from 64%.
T06 contributes 200 m of track but only 100 m of support. T07 is complete despite poor
deviation; T03 is incomplete despite zero deviation. Unknown evaluated coverage for
unobserved/ambiguous-only lines remains NULL inside the Stage 5 record; zero here
means no confirmed support, not a measured zero.

Explicit synthetic completion policy: 100% observed progress, both endpoints, 0 m
endpoint allowance, 0.02 m numerical tolerance. Review continuity gaps: 60 s / 30 m.
QA settings remain 3/5/10 m, coverage station/time gaps 25 m / 60 s. These settings
are **not ecological or regulatory standards**. The fixture does not certify field
survey completion or infer intentions from ambiguous GPS.

Regenerate with `python3 tools/survey_results_fixture.py`. Under configured QGIS
Python, run `python3 -m unittest tests.test_survey_results tests.qgis.test_survey_results`.
The integration fixture verifies all counts, independent lengths/statuses, persistence,
map geometry/CRS, preservation, no-overwrite, stale snapshots and Qt controls. Expected
values are never populated from production QA output. The first development run
caught a handwritten expected-count error (95 instead of the actual constructed
85); the source scenarios and numerical tolerances were unchanged.

Manual GUI inspection remains necessary: open the Survey recordings/review workspace,
run Visit QA, open SURVEY RESULTS, select the policy explicitly, scope unresolved
evidence, build results, inspect the map and filters, select individual lines, and
save/close/reopen the results snapshot. This sample is not a completed Stage 7 export.
