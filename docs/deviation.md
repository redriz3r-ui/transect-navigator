# Per-observation deviation analysis — 0.1.4

This stage compares preserved GPS observations with one official directed planned
transect. It does not infer true walking error, classify compliance, smooth GPS,
infer whether an exceedance reflects true walking error. Later stages add
[descriptive events](events.md) and a separate
[descriptive statistics table](statistics.md) from these measurements.

## Run it

On **ANALYZE — inputs**, choose the plan, ID field and one planned feature, then
select/open recorded data and map optional timestamp/accuracy fields as described
in [the ingestion guide](ingestion.md). Click **COMPARE OBSERVATIONS TO TRANSECT**.
The operation captures the current controls and performs ingestion and comparison
before adding any layers. It does not reuse a stale previously published preview.
**INGEST RECORDED GPS DATA** remains available as an input-only action.

All observations are compared, including repeated coordinates and observations with
missing/duplicate timestamps. Time ordering is not needed for spatial comparison.
An unsupported geometry, transformation failure or ambiguous nearest station aborts
the entire comparison with an inline error and adds no partial result.

The group contains the ingestion outputs plus:

- **QA Observations:** original raw point geometry, native CRS, Z/M and all raw
  attributes, followed by derived QA fields.
- **QA Nearest Locations:** separate nearest-point geometries in the local metre CRS.
- **QA Observation Connectors:** separate straight local-metre lines from each
  observation to its nearest location. Exactly zero-length connectors are omitted.

Source data and ingestion layers remain unchanged. A QA point is **not** snapped to
its nearest location. Only the new diagnostic point represents that location.
These layers are temporary memory outputs; retain raw files and save outputs through
QGIS as needed. A project file alone does not archive memory-layer features.

## Attributes

Audit v0.1.9 corrects nearest-candidate handling near subdivision vertices: a clamped
vertex which is not a local distance minimum along its incident segments is excluded
before ambiguity testing. This avoids false ambiguity near an otherwise straight
transect while preserving rejection of genuinely competing nearest stations.

| Field | Meaning |
| --- | --- |
| QA_Transect_ID | Selected plan's identifier |
| QA_Source_ID | Link to the raw observation's preserved source identifier |
| QA_Deviation_m | Unsigned distance to the finite planned working polyline |
| QA_Signed_m | Positive left, negative right; NULL where sign is not defensible |
| QA_Station_m | Cumulative distance from the first planned vertex to the nearest location |
| QA_Progress_pct | 100 × station / planned working length, bounded to 0–100 |
| QA_Plan_Length_m | Total length in the same working distance model |
| QA_Time_UTC | Normalized timestamp when interpretable; original time remains in raw fields |
| QA_Accuracy_Raw | Mapped GNSS accuracy value, without unit conversion or adjustment |
| QA_Sign_Status | `left`, `right`, `on_transect`, `endpoint_no_lateral_sign`, or `vertex_side_ambiguous` |
| QA_Nearest_X_m / QA_Nearest_Y_m | Nearest location in the recorded local analysis CRS, **not** the QA layer's native CRS |

If an input already contains a QA field name, the new field receives trailing
underscores. `qa_field_mapping` in provenance records the actual output names; no
input field is overwritten. Diagnostic layers link by Source_ID and Transect_ID.
Missing GNSS accuracy remains NULL. Parent line-feature accuracy is retained as a
parent attribute, not invented as per-vertex accuracy. Reported GNSS uncertainty
is not subtracted from observed deviation or converted into a pass/fail standard.

## Distance and CRS model

1. Native input coordinates are validated and copied to WGS84 through the ingestion
   adapter. Static projected inputs (including feet) and geographic degree inputs
   are accepted. No calculation treats longitude/latitude degrees as metres.
2. Choose the lexicographically smallest original planned WGS84 `(longitude, latitude)`
   vertex as the origin. This choice is independent of traversal direction, so
   reversing a plan retains the same analysis frame.
3. Create a **WGS84 ellipsoidal azimuthal-equidistant projection** in metres with that
   origin, using QGIS/PROJ. All planned working vertices and observations must fall
   within **5,000 m** of its origin. Latitudes beyond ±85° are rejected. This is a
   deliberately local analysis model, not a globally suitable projection.
4. As in PREPARE, consecutive original vertices define shortest WGS84 geodesic legs.
   Subdivide each leg into equal pieces no longer than **25 m**, then project those
   sample locations. These subdivisions exist only in the private working path;
   no source geometry is densified or altered.
5. Calculate Euclidean nearest points, deviations, station and total length on that
   local projected polyline. Progress uses this same length, rather than mixing a
   planar station with an ellipsoidal denominator.

This is a **local projected approximation** to the geodesic plan, not an exact
ellipsoidal closest-point solver. AEQD preserves origin-to-point distances, not
all distances between arbitrary points. The strict local extent limits projection
distortion; 25 m subdivisions limit chord approximation. Neither limit is a claim
of survey measurement accuracy, and no certified error bound is asserted. Small
numeric differences from PREPARE's ellipsoidal station/length values are expected.
If an official survey requires native-grid straight segments or exact geodesic
closest-point values, that interpretation needs a separately agreed/tested model.

Transforms use the project's explicit context with fallback/ballpark operations
disabled. Provenance records the analysis CRS WKT, origin, subdivision, radius,
coordinate operation, plugin version, sign convention and input mappings. Available
datum grids and correctly declared source CRSs still determine transformation
quality. Raw Z/M remain intact but play no part in horizontal deviation.

## Nearest point, station and sign

For each directed segment A→B and observation P, project onto the supporting line
and clamp its fractional coordinate to [0,1]. Select the smallest Euclidean distance
over all closed segments. Station is the sum of previous segment lengths plus the
selected fraction of that segment. Nothing enforces monotonic progress: a surveyor
can backtrack, and independent observations can have decreasing station values.

For a unique segment-interior nearest location, the sign comes from
`(Bx−Ax)(Py−Ay) − (By−Ay)(Px−Ax)`: positive is **left** when facing from A toward B,
negative is **right**. Magnitude is the absolute nearest distance. An eastbound
transect therefore assigns north-side observations positive values. Reversing the
plan negates defensible signs and replaces station s by L−s.

At an interior vertex, both incident directed segments must agree strictly on the
side before a sign is assigned. If one side test is zero or the sides disagree,
absolute distance and station are still returned but signed distance is NULL with
an explanatory status. Equal minima at **different** nearest stations (for example,
an observation on the inside bisector of a right-angle bend) are ambiguous and fail
clearly rather than choosing whichever segment happens to be iterated first.

Before the start or after the end, the nearest location is the endpoint and the
absolute distance includes overshoot. For example, 3 m beyond an endpoint and 4 m
to its side yields **5 m**, not 4 m. Endpoint-clamped off-path observations have
NULL signed distance because that magnitude is not purely lateral cross-track
error. Even a perpendicular projection exactly onto an endpoint uses this
conservative policy. Points exactly on the path/endpoints have signed zero.

A **1e-7 m computational tolerance** handles coincidence, near-zero segments and
nearest-distance ties. Observations within this tolerance have signed zero while
their unrounded absolute distance and original geometry remain unchanged. This is
numerical precision handling, not GPS smoothing or an ecological threshold.

## Rejections and limits

Only one open, simple, nonempty planned LineString is supported (native Z/M can be
retained). Planned multipart, curves, zero-length/repeated adjacent vertices,
backtracking overlap, self-crossings, closure and nonadjacent touching/overlap are
rejected. Validation operates on the working polyline; very close features within
the numerical tolerance are treated conservatively as touching. Nearest-station
ambiguities are rejected even on otherwise simple plans. Recorded multipart track
parts remain separated during ingestion; their observation points can be compared.

The working path is capped at **2,000 segments**, and a run at **2,000,000
point/segment comparisons** to bound the synchronous operation. Split large surveys
deliberately rather than silently downsampling them. The origin/radius policy is
conservative: some geographically compact plans may still need splitting. No
automatic topology repair, axis guessing or GNSS-accuracy adjustment is performed.

## Tests and remaining runtime validation

Independent pure tests cover exact-on-line, ±1 m, 5 m, an oblique 3–4–5 orthonormal
fixture, rotations/translations, reversal, multi-segment stations, vertices,
3–4–5 endpoint overshoot, ties, repeated observations, subdivision invariance and
invalid paths. Expectations are analytical, not copies of the projection code.

Real PyQGIS tests cover geographic inputs with independently calculated WGS84
small equatorial offsets, reversal, vertices, endpoints, projected-foot inputs,
native Z/M/WKB and attributes, missing time/accuracy, connector lengths, field
collisions, stale raw geometry and UI publication. They skip explicitly without
QGIS and have **not run on this development host**. Required before field use:

- Run all QGIS tests in a real supported QGIS environment.
- Inspect QA/nearest/connectors over the original plan and raw GPS with differing
  source and display CRSs; check metre fields and the stored analysis CRS.
- Verify bends, reversal, endpoints and NULL signs in the attribute table.
- Exercise missing-grid transformations, data near extent limits/date-line wrapping,
  unsupported topology and error recovery; failed runs must leave sources and
  project output layers unchanged.
- Check real device timestamp/accuracy fields and retain originals. Projection/model
  limitations and GNSS positional uncertainty both remain relevant.

References reviewed: [PROJ AEQD](https://proj.org/en/stable/operations/projections/aeqd.html),
[QGIS CRS creation](https://qgis.org/pyqgis/3.44/core/QgsCoordinateReferenceSystem.html),
and [QgsDistanceArea](https://qgis.org/pyqgis/3.44/core/QgsDistanceArea.html).
API documentation review is not runtime validation.
