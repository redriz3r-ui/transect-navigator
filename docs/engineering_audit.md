# Engineering audit — v0.1.9

Scope: the complete saved v0.1.8 implementation. No product features added.
Baseline: 245 tests, 166 passed, 0 failed, 79 PyQGIS skips.

## Confirmed defects and corrections

| Finding | Correction and regression evidence |
| --- | --- |
| A valid point near a densification vertex failed as an ambiguous nearest station. `(24.9999,5)` against `(0,0)→(25,0)→(100,0)` reproduced the error. | Exclude clamped vertices that are not local distance minima along incident segments. Independent straight/bent, rotated/translated and reversed fixtures verify 5 m deviation and known station. True distinct-station ambiguity still fails. |
| ANALYZE preserved outer ID whitespace while PREPARE trimmed it; QA/navigation IDs could disagree. ANALYZE accepted nonfinite numeric IDs as text. | Shared identifier normalization for derived IDs, leaving source attributes untouched; pure and real-QGIS regressions. |
| PREPARE only checked geographic bounds after transformation, potentially accepting a silently normalized invalid source longitude. It lacked ANALYZE's degree-unit guard. | Check native geographic bounds and degree units first; runtime tests include WGS84/NAD83 longitude 181 and a geographic CRS using grads. |
| Valid long summary JSON cells exceeded Python's default 128 KiB CSV parser limit. | Shared generated-CSV reader uses the actual staged file size and always restores the global limit. Long-cell and failure regressions run without QGIS. Input CSV limits remain separate. |
| Boolean statistics settings became numeric distances; export timestamp parsing normalized invalid offsets or truncated unsupported precision. | Reject boolean/nonnumeric statistics settings and reuse strict timestamp parsing. Tests include `+01:99`, unknown `-00:00` and excessive precision. |
| PREPARE/ingestion ignored failed `addMapLayer()` return values, risking false success or incomplete groups. | Check registration results and invoke existing rollback. Runtime tests inject failure and check retained source layers/buttons. Optional loading failure after export remains distinct from file-export failure. |
| A bounded GPX file could create an enormous dense table through many distinct sparse extension fields. | Reject inputs over 512 source fields or 5,000,000 attribute cells before dense construction; same policy for CSV and loaded points/track vertices. Nothing is truncated. Boundary, wide-CSV and sparse-extension tests cover the guards. |
| Runtime versions and source-copy verification were duplicated. | Central runtime version with metadata-consistency test; packager reads metadata. PREPARE reuses exact snapshot verification, also verifying generated waypoint writes. |

## Other audit conclusions

- **Architecture:** small lifecycle controller; scientific calculations remain separate
  from UI/QGIS, enforced by import-boundary tests. The larger ingestion/UI functions
  coordinate many input cases but do not justify a risky broad rewrite in this audit.
- **GIS:** WGS84 geodesic PREPARE and bounded local AEQD metre QA are explicit, independent
  of project display units. Raw geometry is never transformed in place. Multipart plan,
  crossings/overlaps and ambiguous station matches fail deliberately. Recorded multipart
  breaks are retained. Endpoint overshoot has absolute distance and NULL lateral sign.
  Positive means left relative to plan direction; negative means right. Independent
  sign/reversal, vertex, endpoint and distance fixtures remain passing.
- **Integrity:** repeated observations and raw/GNSS fields are retained. Missing or
  duplicate timestamps do not establish chronology. Constructed track chords are
  identified as such, without claiming gaps were walked. Recorded vertex order survives
  timestamp conflicts. No source editing, smoothing or raw-point snapping was found.
- **Statistics:** independently specified mean, median, scaled RMSE and type-7 p95
  fixtures pass. Missing/invalid denominators, uneven-sampling bias and partial tracks
  are documented. Coverage is sampled interval support, not compliance. Events respect
  configured gaps/minima; GNSS uncertainty is not subtracted from spatial deviation.
- **Formats/security:** emitted GPX uses namespaces/schema-valid structure and offline
  XSD tests. Input XML rejects DTD/entities and excessive nesting/size. GeoJSON/CSV
  reopen checks and GeoPackage exact value/geometry/CRS checks remain. Provenance is
  stored in ordinary GeoPackage tables. Traversal/reserved names and existing/symlink
  destinations are rejected; failed staging is cleaned up. Raw spreadsheet-like text
  is preserved and documented, not evaluated by the plugin.
- **Lifecycle/UX:** one owned dialog/action, symmetric unload, and user result layers
  retained. Added a real Qt/QGIS lifecycle regression (only iface is a host harness),
  covering repeated init/open/close/unload/reload. Descriptive thresholds and GNSS
  uncertainty remain clearly distinguished from compliance and walking error.

## Remaining limitations and production gate

This is **internally tested experimental code, not production certification**.
QGIS/PyQGIS is absent here: actual CRS/provider/GeoPackage/GUI/lifecycle tests remain
skipped. Metadata version ranges are intended test scope, not runtime certification.
Run all tests inside QGIS and complete the existing manual checklists before field use.
Check Qt5/Qt6, datum grids, provider-specific fields, Z/M, empty/NULL layers and rendering.
No device or cross-platform certification is claimed. Licensing/maintainer decisions
remain outstanding before public distribution.

Deliberate limits remain: 5 km QA radius, ±85° latitude, 2,000 working segments,
2,000,000 comparisons, synchronous processing and memory snapshots. Navigation and
QA lengths use different documented distance models. GeoJSON planned lines retain
vertex order without antimeridian splitting; dateline-safe planar rendering is not
guaranteed. Provider coercion before ingestion cannot be undone; retain device files.
Strict GeoPackage verification can reject unsupported type conversions. Provider
constraints/QGIS styles are not archived. No field navigation, compliance rule or PDF
report was added.
