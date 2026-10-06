# Simple Stage 2 multi-transect PREPARE fixture

Twelve separate eastward 100 m WGS84 equatorial arcs. Longitude is independently constructed as degrees(distance / 6378137), using the WGS84 equatorial semi-major axis. Starts are 200 m apart. This is a small PREPARE-only fixture, not the full Stage 3 multi-person survey. SYN01 is unchanged.

At 10 m spacing expect 12 plans, 132 waypoints, sequences 1–11 per plan, station 0–100 m, bearing 90° and final NULL. Reverse PREPARE gives 270° and swaps waypoint endpoints without changing official geometries. Use 1e-6 m length/station and 1e-7 degree coordinate/bearing tolerances.

Each plan includes original Team/Priority fields and an intentionally conflicting Survey_ID field. It must remain original-owner-value in preserved native plans, while survey identity lives separately in metadata.

Run `python -m unittest tests.qgis.test_survey_prepare -v` in the configured QGIS environment. The tests generate/reopen GeoPackage, GeoJSON, GPX and CSV in temporary directories and numerically verify counts, IDs, sequence and endpoints. See docs/v0.2_stage2.md for scripting usage.
