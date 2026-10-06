# Completed-analysis visualization — v0.1.7

Choose the official planned feature and recording on ANALYZE, configure QA thresholds
and event criteria, then click **COMPARE OBSERVATIONS TO TRANSECT**. Result waypoint
spacing defaults to 10 m and is independently configurable on ANALYZE. This always
generates new waypoints from the selected official plan's original direction; it does
not reuse or infer settings from an earlier PREPARE run. PREPARE navigation export
continues to work independently.

A successful comparison adds **Transect Navigator — [Transect ID]**. Repeat runs
receive `(2)`, `(3)`, etc.; earlier results remain intact. The group contains:

- Planned Transect: dark line, original geometry/CRS and attributes preserved.
- Navigation Waypoints: small outlined points, existing WGS84 geodesic spacing model.
- Raw GPS Track: subdued gray line, only when available or safely constructed.
- Raw GPS Observations: subdued gray points, native geometry and raw attributes.
- QA Observations: colored/shaped using the configured absolute-deviation thresholds.
- Deviation Events: prominent thick purple lines; event records with no line geometry
  remain in the attribute table (for example, a stationary event).
- QA Summary: existing machine-readable table.
- Diagnostics subgroup: normalized points, validation tables, nearest points and
  observation-to-plan connectors, initially hidden and collapsed.

Events and QA points are ordered above reference layers. All styling is applied to
new memory outputs. Input layer styling, geometry, attributes, selection and raw
GPS observations are not changed. Failed publication removes only the new outputs.
No automatic map zoom or project CRS change occurs. Use QGIS's **Zoom to Group** as
needed, and hide source layers if their existing symbology obscures result copies.

## Classes and attributes

Classes use `0 ≤ deviation ≤ A`, `A < deviation ≤ B`, `B < deviation ≤ C`, and
`deviation > C`, matching the inclusive thresholds in statistics. Default A/B/C are
3/5/10 m. Equal thresholds have no duplicate empty classes. Blue circles, light-blue
squares, orange triangles and vermilion diamonds distinguish the map classes;
unavailable/invalid values use a gray cross. **These are user-configurable QA/QC
classes, not regulatory or scientific compliance standards.** The event threshold
is separate from the visualization thresholds.

Use native QGIS Identify Features and attribute tables. QA fields are moved to the
front of the table and receive readable aliases: station, absolute/signed deviation,
timestamp, reported GNSS accuracy, sign status and progress. Event ID, station span,
time/duration, deviation, GNSS and count fields are likewise prioritized. Original
fields remain available; collision-safe QA field mappings select the derived values.
GNSS accuracy retains source units; it is not subtracted from deviation.

## RESULTS tab and profile

The latest successful run supplies the RESULTS tab. It shows the group name and UTC
completion time, planned/available recorded lengths, observation count, mean, median,
RMSE, p95, maximum, threshold percentages, event count and unevaluated groups,
sampled-progress coverage, and separate GNSS descriptions. Missing measurements show
**Unavailable**, never zero. A failed subsequent run leaves the prior result intact;
the header identifies its group/time and the ANALYZE status displays the failure.

The Qt-only profile plots every defined observation against planned station in metres.
Choose absolute or signed deviation. Positive signed deviation is left of the directed
plan, negative is right. Undefined signs are omitted with a visible count. Colors use
absolute deviation classes even in signed mode. Points are never connected, smoothed,
interpolated or downsampled; repeated stations may overlap. The chart represents
distance along the plan, not chronological walking order. It has no zoom/export tools.
The numeric records are detached from layer lifetime, so removing project layers does
not invalidate the summary. Closing/reopening the plugin dialog retains the latest
result during that plugin session; unloading releases the UI without removing outputs.

## Interpretation and limits

Calculation assumptions from the [comparison](deviation.md), [statistics](statistics.md)
and [event](events.md) guides still apply. Statistics are observation-weighted and can
be biased by uneven sampling. Coverage is conservative sampled progress support, not
proof of completed survey protocol. GNSS positional uncertainty remains separate from
observed spatial deviation. An unavailable track is omitted rather than fabricated.

Navigation uses the existing geodesic model; QA uses the existing local metre model,
so their station/length values may differ slightly. For an accepted Z/M planned line,
navigation is calculated from a private XY-only copy; the displayed planned snapshot
retains Z/M. Neither source nor raw data is flattened. No derived QA track is invented;
QA points and event lines communicate deviation.

Map outputs remain temporary memory layers; saving a QGIS project alone is not a data
archive. In v0.1.8, use the [analysis package export](analysis_export.md) in RESULTS
to save the completed run, or save individual layers through QGIS. Retain raw files.
Reports and real-time field navigation are not provided.

## Required manual/runtime validation

QGIS is unavailable in the development environment. The following is **not yet tested**:

1. Run the entire unittest suite using a real QGIS Python environment; the PyQGIS tests
   must execute. Test intended QGIS 3.44/Qt5 and 4.2/Qt6 environments independently.
2. Install the experimental ZIP, open/close/reopen the dialog, disable/re-enable the
   plugin and confirm one menu action, no orphan dialogs and retained result layers.
3. Compare a known 100 m transect with geographic, metre and foot inputs and a different
   display CRS. Inspect the 11 navigation points at 10 m spacing and unchanged sources.
4. Inspect all result layers, drawing order, hidden diagnostics, Identify values and
   table aliases/order. Check exact threshold boundaries and equal thresholds, plus
   collisions with raw fields named like QA attributes.
5. Check event prominence at useful survey map scales and stationary events in tables.
   Check missing timestamps/one observation: no fabricated track; unavailable lengths
   and unevaluated events are reported honestly.
6. Compare summary numbers to QA Summary; toggle signed/absolute profile, inspect
   undefined endpoint signs, zero deviations, repeated stations and outliers. Resize
   the dialog and check high-DPI/dark-theme readability and large-sample responsiveness.
7. Repeat analysis with changed thresholds/spacing: unique groups and current results.
   Force a validation failure: no partial layers or replacement of the prior results.
   Remove result layers and confirm the summary/profile still display safely.

Official API references used: QGIS 3.44
[rule renderer](https://qgis.org/pyqgis/3.44/core/QgsRuleBasedRenderer.html),
[vector layer](https://qgis.org/pyqgis/3.44/core/QgsVectorLayer.html), and
[attribute table configuration](https://qgis.org/pyqgis/3.44/core/QgsAttributeTableConfig.html).
