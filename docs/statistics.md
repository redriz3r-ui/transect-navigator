# QA/QC statistics — v0.1.5

The **COMPARE OBSERVATIONS TO TRANSECT** action adds a one-row **QA Summary** table.
It describes observed spatial deviation and reported GNSS uncertainty separately.
There are no scientific or regulatory compliance classifications.

## Options and workflow

Choose the planned feature and recorded input on ANALYZE. Set the user-configurable
QA/QC thresholds A/B/C (defaults 3/5/10 m; A ≤ B ≤ C, nonnegative). Threshold boundaries
are inclusive. Set coverage maximum station gap and time gap (defaults 25 m, 60 s,
both positive), then run comparison. Changes apply to the next run; existing results
retain their settings. The ingestion-only action remains available.

The table is detached, read-only and temporary, like the QA observation layers.
Save needed tables/layers explicitly with QGIS and retain original GPS files.
Summary package export/reporting is not implemented in this stage.

## Observed spatial deviation

Let d₁…dₙ be valid nonnegative finite **absolute** per-observation deviations.

- Planned length: total working-path length from the comparison engine.
- Recorded length: sum of straight vertex-to-vertex chords of available raw track
  parts transformed into the same local AEQD metre frame. Parts are never joined.
  This is recorded geometry length, not certified ground distance walked; jitter
  can inflate it, sparse samples can shorten it, and recorded segments can bridge
  recording gaps. It is not smoothed, gap-filtered or corrected for GNSS accuracy.
- Observation count: all QA observation records, including repeated positions and
  records with unavailable time/accuracy. Valid, missing and invalid deviation
  counts are additional fields.
- Mean: Σd/n. Median: middle sorted value, or mean of the middle two.
- RMSE: √(Σd²/n), relative to zero deviation, **not** standard deviation about the mean.
  Implementation scales by the maximum to avoid overflowing intermediate squares.
- Maximum: largest valid deviation.
- 95th percentile: sort ascending; interpolate at zero-based index 0.95(n−1), the
  linear/type-7 convention. One value returns that value; there is no extrapolation.
- Within A/B/C: 100 × count(d ≤ threshold)/n. Missing and invalid deviations are
  excluded from this denominator, not treated as zero or failures.

Missing/blank values are counted separately from nonnumeric, negative or nonfinite
values. They are excluded explicitly. With no valid deviations, numeric descriptions
and threshold percentages are NULL. Outliers are retained without trimming or
winsorization. The comparison service normally produces finite deviations for all
records; the pure engine also handles incomplete inputs explicitly.

The planned and recorded lengths share the comparison's local projected model, not
PREPARE's exact ellipsoidal leg lengths. See [deviation.md](deviation.md) for the
5 km extent restriction and path approximation. When no track can be constructed
or supplied, length is NULL, including a single isolated observation. A genuine
recorded part with two coincident vertices has zero length. Track vertex count,
origin and status identify a partial recorded representation when some observations
(e.g. one-point GPX segments or unordered groups) have no available line.

## Reported GNSS uncertainty

The selected accuracy field is described separately: valid/missing/invalid count,
mean, median, maximum and p95 of its nonnegative finite numeric values. Table names
use `GNSS_*_Raw`, **not metres**, because device units/semantics were not inferred by
ingestion. Original fields remain in raw/QA layers. The structured descriptive object
also contains the root mean square of reported values; this is not a walking-error
estimate. HDOP/PDOP/fix/satellite fields remain preserved but are not automatically
interpreted as horizontal accuracy. Select a semantically consistent field and
verify units before comparing its numerical descriptions between devices/surveys.

GNSS values are neither subtracted from deviation nor used to reweight/filter it.
Observed spatial deviation and reported positional uncertainty remain distinct.

## Coverage: sampled-progress support

This first-pass estimate uses nearest-point stations along the plan, not a minimum/
maximum station envelope. It supports only explicitly observed short transitions:

1. Separate the ingestion groups; never connect groups. Require at least two
   observations and complete, valid, unique timestamps for an eligible group.
   If chronology is ambiguous, exclude the entire group and count that exclusion.
2. Sort each eligible group by UTC. Examine consecutive observations only. Never
   skip over a bad sample to form a new pair.
3. Accept a pair only when both deviations/stations are valid, stations lie on
   [0, planned length], **both deviations ≤ threshold C**, elapsed time ≤ the
   configured time gap, and absolute station difference ≤ the configured station
   gap. These settings are exploratory QA choices, not survey standards.
4. Each accepted pair supports the closed station interval between its endpoints.
   Merge overlapping intervals across all eligible groups. Sum the union's length;
   divide by planned working length for percent support. Backtracking/revisits do
   not count twice. Stationary pairs add zero length.

No eligible groups means coverage is **NULL/unavailable**. Eligible groups with no
accepted intervals mean **0 supported length**, not proof that nothing was surveyed.
Missing deviation/station at an otherwise timed sample rejects its adjacent pairs.
The summary includes eligible/excluded groups and accepted/rejected pairs so partial
evidence is visible. The denominator remains the entire planned length. Missing
parts are not extrapolated; long timestamp/station gaps are not filled.

For a 100 m plan, stations 20,40,60 with good observations support 40 m (40%). Stations
0 and 100 alone support 0 m under the default 25 m gap limit. Stations 0,20,10,30
support a union of 30 m, not the 50 m accumulated station travel. Known-time points
with no usable deviations support zero intervals and are counted as rejected pairs.

This measures **sampled progress support**, not true continuous path coverage, area
coverage, detection effort, species-survey adequacy or protocol compliance. Passing
near every section does not prove compliance. Both endpoints near the line do not
prove the intermediate walk stayed near it. The method can underestimate sparse
surveys, and cannot detect unobserved excursions between samples.

## Sampling bias

Deviation/threshold and GNSS statistics assign equal weight to each valid record.
Stops, duplicate records and high-rate segments contribute more than sparsely
sampled movement. They are not time-weighted or distance-weighted. For example,
[0,10] m has mean 5 m, whereas nine 0 m observations plus one 10 m observation has
mean 1 m even if spatial travel is similar. This is intentional and disclosed.
Coverage unions avoid counting revisits twice, but still depend strongly on sampling
frequency, missing times, proximity threshold and the two gap settings. These
limitations must accompany interpretation or later reporting.

## Structured API and validation

`qaqc.statistics.summarize` returns an immutable `QaSummary`, with separate deviation,
GNSS and coverage records plus options. `as_dict()` is JSON-ready; NULL remains
`None` and invalid NaN/infinity do not leak into calculated fields.
`integration.statistics.summarize_comparison` consumes a paired ingestion/comparison
and returns that object plus the nonspatial QGIS table. Formula/weighting/model,
parameters, denominators, track status and plugin version are table columns and/or
provenance. This is suitable for later visualization/reporting without reading UI
labels or reverse-engineering settings.

Pure tests use independent known statistics and synthetic station/time sequences.
Real-QGIS tests are written but skipped on this host. In QGIS, run the full suite,
check table NULLs/types/provenance, customize thresholds and gap limits, and inspect
partial multipart tracks and missing-time cases. Check that failed parameter
validation publishes no layers and that raw attributes/geometries remain unchanged.

The percentile convention was checked against [NumPy's documented linear/type-7
quantile definition](https://numpy.org/doc/stable/reference/generated/numpy.quantile.html).
The implementation uses standard Python only; NumPy is not a dependency. Explicit
nonfinite filtering follows the concerns documented in [Python statistics](https://docs.python.org/3/library/statistics.html).
