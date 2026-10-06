# Deviation events — v0.1.6

Events describe contiguous observed exceedance runs. The purpose is to suppress
isolated fixes and identify sustained **observed spatial deviation**. An event is
not proof of true walking error: sustained GNSS bias/multipath can also produce it.
No scientific/regulatory pass/fail standard is invented. Raw data are not smoothed,
snapped, dropped or edited.

## Controls

On ANALYZE, configure the event options and click **COMPARE OBSERVATIONS TO TRANSECT**.
The ingestion-only action remains available and does not detect events.

| Setting | Default | Rule |
| --- | --- | --- |
| Deviation threshold | 5 m | Strictly greater; equality ends a run |
| Minimum duration | 5 s, enabled | Optional, inclusive lower bound |
| Minimum station span | 10 m, disabled | Optional, inclusive lower bound |
| Maximum inter-fix time gap | 60 s | A larger gap splits a run |
| Maximum spatial or station jump | 50 m | A larger XY distance **or** absolute station difference splits a run |

Every event requires **at least two observations**, even when both optional minima
are disabled or set to zero. All enabled minima must pass (AND). Threshold/minima
must be finite and nonnegative; gap limits must be finite and positive. Settings
are user choices for exploration/QA, not ecological or regulatory standards.

## Ordering, continuity and boundaries

1. Separate the ingestion groups (including preserved GPX segments). Never merge
   across them. Groups are processed in sorted group-ID order for reproducibility.
2. Require complete valid unique timestamps in each group, compared as UTC instants.
   If any timestamp is missing, invalid or duplicated, **exclude the entire group**
   and record it in **Deviation Event Validation**. A geometry's vertex order alone
   is not used to invent chronological continuity. This also applies when duration
   filtering is disabled; without time, a temporal recording gap cannot be assessed.
3. Sort the group by time. Build runs of consecutive observations whose absolute
   deviations exceed the event threshold. At/below threshold, or invalid/missing
   deviation/station, closes a run. No skipping over a bad or below-threshold fix.
4. Between consecutive exceedances, split at either an excessive elapsed-time gap,
   excessive Euclidean inter-fix distance in the local QA metre frame, or excessive
   station jump. Checking both XY and station catches lateral GPS jumps and jumps
   between nearby but distant-along-plan locations. Exact gap limits are allowed.
5. Apply the minimum count and enabled duration/span criteria to each run. Rejected
   short runs are not joined later. A run still active at survey end is evaluated
   using only the observed fixes.

No threshold-crossing time or location is interpolated. Start/end are the first/last
observed exceedances, not estimated departure/return moments. A run beginning or
ending at survey boundaries can be incomplete; reported duration is only its observed
span. Irregular sampling can conceal excursions/returns between fixes. Gap limits
reduce unsupported joining but do not certify continuous movement between fixes.

For example, deviations [0,6,8,10,0] m at times [0,5,10,15,20] s produce one default
event from 5 to 15 s: 3 observations, mean 8 m, maximum 10 m and duration 10 s.
[0,20,0] produces no event. [6,6,5,6,6] produces two events because equality is a
return to the accepted threshold boundary. With four above-threshold fixes at
[0,5,100,105] s, the default temporal gap creates two separate events.

## Output fields and geometry

**Deviation Events** is a separate temporary derived LineString layer in the local
metre analysis CRS. Its lines connect participating **original GPS fix locations**
in timestamp order, not nearest points on the plan. They are diagnostic connections,
not evidence of the exact intervening walk. The raw layers and QA points are unchanged.

| Field | Meaning |
| --- | --- |
| Event_ID | `TransectID_E001`, deterministic for identical input/groups/settings |
| Transect_ID / Group_ID | Selected plan and independent recorded group |
| Start_Distance_Along / End_Distance_Along | First/last chronological nearest-point station, metres |
| Event_Length | Maximum station minus minimum station within the event, metres |
| Start_Time / End_Time | First/last observed exceedance in UTC |
| Duration | Elapsed seconds between those observations |
| Mean_Deviation / Maximum_Deviation | Observation-weighted absolute deviations, metres |
| Mean_GNSS_Accuracy | Mean of available nonnegative finite numeric accuracy values, **source units** |
| GNSS_Valid_Count / GNSS_Units | Accuracy denominator and explicit unknown-unit label |
| Observation_Count | Number of participating fixes |
| Source_IDs | JSON array linking all participating raw observations, in event order |
| Geometry_Status | Observation line, or stationary event requiring source-point inspection |

Event_Length is a **station span**, not line geometry length, net signed progress,
or cumulative walking distance. With stations [10,30,10], its value is 20 m while
start/end are both 10 m; revisiting the same stations does not inflate length.
Duration can qualify a stationary off-path run whose span is zero. If every event
position is identical, the event record has **NULL line geometry** and an explicit
Geometry_Status, rather than an invalid zero-length line. Inspect its source points;
there is intentionally no visible event line in that case.

Event IDs are repeatable for the same data and options, including input row shuffles.
They are run-local identifiers, not permanent identities across added data, changed
groups or changed settings. Empty event layers are valid outputs. A layer with zero
events and excluded groups is not evidence that no deviation occurred: read the
validation table and inline excluded-group count.

Available GNSS accuracy is descriptive and separate from deviation. Missing or
invalid values are excluded from its mean; when none are valid, the mean is NULL.
Accuracy is not subtracted from deviation or used to qualify events. Means weight
each participating observation equally, so high-rate sampling and stops can bias
them; durations use actual timestamps, never count × an assumed sample interval.

Provenance stores event options, eligible/excluded groups, rejected run count,
time/spatial split counts, plugin version, source comparison context, definitions
and units. The event layer and validation table are read-only memory outputs;
retain raw files and save outputs explicitly through QGIS. A project alone does
not archive memory-layer features. No new file format or report is introduced.

## Tests and remaining validation

Pure synthetic tests exercise singleton spikes, sustained deviations, separated
runs, gaps, missing/duplicate timestamps, equality, return to plan, incomplete tracks,
optional minima and AND logic, irregular sampling, reversals/backtracking of station,
stationary events, invalid samples, missing GNSS fields and deterministic IDs.

Real QGIS tests are written but skipped on this development host. Before operational
use, run the complete suite in QGIS and manually:

- Inspect event vertices against the unsmoothed raw fixes and source-ID lists.
- Change every option and verify new results use current settings.
- Test GPX segment boundaries, unknown timestamps and both time/spatial gaps.
- Inspect empty and partially unevaluable runs; ensure exclusion diagnostics remain visible.
- Inspect stationary event records with NULL geometry; do not interpret their lack
  of a visible line as absence of an event.
- Verify native source fields/WKB, selection and edit buffers remain unchanged.
- Exercise UI scrolling, error recovery and failed runs adding no partial outputs.

Existing comparison limitations (simple directed plan, local 5 km projection scope,
geodesic subdivision approximation and ambiguous nearest-station rejection) still
apply. This stage provides event detection only; dedicated event styling, reporting
and field navigation are not implemented.
