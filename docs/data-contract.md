# Workout data contract and calculations

The importer creates one local SQLite database containing exactly one selected workout. Schema version is `PRAGMA user_version = 2`. Version 2 adds device provenance and the original offset-bearing workout start time while retaining all version-1 columns. Python 3.9+ and the standard library are sufficient. It does not contact a service, extract an archive to disk, upload an export, or retain unrelated Health records.

The dashboard uses four tables: `race`, `route_points`, `splits`, and `heart_rate`. SQL `NULL` means unavailable. Missing heart rate, calories, elevation or active duration are never converted to zero. `synthetic = 1` means the entire workout is fictional; importing a user-supplied export or GPX sets it to `0` without asserting device authenticity.

## Inputs and selection

```sh
# Inspect workout metadata without creating a database.
python3 scripts/import_workout.py /path/to/export.zip --list

# Use the 1-based index displayed by --list.
python3 scripts/import_workout.py /path/to/export.zip --workout 42 --db runtime/workout.sqlite

# A recorded local calendar date may narrow selection; ambiguity still fails.
python3 scripts/import_workout.py /path/to/export.zip --date 2026-09-19 --list
python3 scripts/import_workout.py /path/to/export.zip --date 2026-09-19 --workout 42 --db runtime/workout.sqlite

# Explicitly choose the chronologically latest Running workout only.
python3 scripts/import_workout.py /path/to/export.zip --latest-running --device-label "Apple Watch Ultra 4" --db runtime/workout.sqlite

# A GPX file can supply the course and timing alone.
python3 scripts/import_workout.py /path/to/race.gpx --db runtime/workout.sqlite

# Replacing the current single-workout database is explicit.
python3 scripts/import_workout.py /path/to/export.zip --workout 42 --db runtime/workout.sqlite --replace
```

The index is stable within the supplied export and remains the same when `--date` filters the listing. A unique match can be selected without `--workout`; two or more matches produce an error. `--date` refers to the date text in the workout's recorded timezone, not the computer's current timezone. `--title` optionally sets the display title. `--latest-running` is an explicit alternative to `--workout`: it first filters to `HKWorkoutActivityTypeRunning`, then compares timezone-aware start instants. It never selects a later walk or other activity. Equal latest timestamps remain ambiguous and fail. This option cannot be combined with `--workout` or `--list`; `--date` can still narrow the candidate set.

Accepted native inputs are a ZIP containing exactly one `export.xml`, an `export.xml` file, or a directory with exactly one of `export.xml` and `apple_health_export/export.xml`. The importer supports the common Apple Health XML `Workout`, `WorkoutStatistics`, `WorkoutEvent`, `WorkoutRoute` and `FileReference` structure. It only loads GPX files linked by the selected workout. It never guesses a route based on a similar filename or date. A missing linked file leaves summary metrics available with a warning and no associated missing route points.

ZIP members are streamed in place. Absolute archive names, `..`, Windows path separators, drive prefixes and network references are rejected. Apple's internal `/workout-routes/...` reference convention is accepted relative to the directory containing `export.xml`. Extracted-folder links are resolved and must remain inside that directory, including through symlinks. Nothing follows HTTP URLs in an export.

Two streaming XML passes are used. The first collects workout metadata and preserves all children until a `Workout` closes. The second keeps only heart-rate records overlapping the chosen workout. Unrelated samples are discarded as their XML entities close. The source ZIP/XML is never copied to the database or repository.

GPX-only imports accept timestamped `trkpt` and `rtept` points. Route shape, elapsed time, GPS distance, elevation when present, pace and splits are available. Health-only summary values and heart rate remain null; GPX extension fields are not imported. Fewer than two valid points or timestamps with no positive duration fail clearly.

## Tables

### `race`

Exactly one row with `id = 1`.

| Column | Meaning |
| --- | --- |
| `id` | Constant primary key `1`. |
| `title` | Display title, optionally supplied with `--title`. |
| `activity_type` | Apple workout type identifier, or `GPX route (activity unspecified)`. |
| `start_time`, `end_time` | UTC ISO 8601 timestamps with milliseconds. GPX-only uses first and last valid point. |
| `started_at_epoch_s` | Same start instant in Unix seconds. |
| `elapsed_s` | End minus start; includes pauses and recording gaps. |
| `active_s` | Apple-reported workout duration converted to seconds; if unavailable, elapsed minus explicit pause intervals when any exist; otherwise null. This is not an independently computed moving time. |
| `recorded_distance_m` | Apple workout total distance, or supported workout distance-statistics sum, converted to meters. Null for GPX-only. |
| `gps_distance_m` | Sum of eligible route-point distances; null with fewer than two valid points. |
| `elevation_gain_m` | Sum of positive elevation differences between connected pairs with elevations; null if there are no such pairs. |
| `avg_hr_bpm`, `max_hr_bpm` | Arithmetic mean and maximum of accepted HR samples. If no samples exist, supported workout HR statistics may supply the summary alone. |
| `energy_kcal` | Apple total workout energy, otherwise the active-energy workout-statistics sum, converted to kcal. No calorie estimate is inferred from distance or HR. |
| `source` | Workout `sourceName`, `GPX import`, or explicit fictional-demo source. |
| `source_version` | Selected workout's exported `sourceVersion`, or null. This is retained as an exported source version; it is not independently identified as an operating-system version. |
| `recorded_start_time` | Original Apple `startDate` string including its numeric UTC offset, or null for GPX/demo. |
| `device_label` | Optional `--device-label` supplied by the user; never treated as inferred or verified hardware identity. |
| `device_metadata_json` | JSON containing `label_provenance`, `export_metadata_available`, `exported_device` and `unique_identifiers_retained`; an empty object is used for the fictional demo. |
| `synthetic` | `1` only for the bundled fictional preview; `0` for imported files. |
| `point_count`, `hr_count` | Number of stored route points and accepted HR samples. |
| `warnings_json` | JSON array of human-readable limitations and skipped-data notices. |
| `pauses_json` | JSON array of `{ "start_s": ..., "end_s": ... }` pause intervals relative to start. |

### `route_points`

| Column | Meaning |
| --- | --- |
| `seq` | Primary key, chronological order beginning at `0`. |
| `elapsed_s` | Seconds since the workout started; pauses are retained. |
| `lat`, `lon` | Original GPX latitude and longitude in degrees. |
| `elevation_m` | GPX elevation in meters, or null. |
| `distance_m` | Cumulative eligible GPS distance since the first included point. |
| `hr_bpm` | Nearest accepted HR sample within 30 seconds, or null. |
| `pace_s_km` | Trailing distance-based pace in seconds/km within the current connected segment, or null. |
| `segment` | Integer segment beginning at `0`; a change means the dashboard must not draw an uninterrupted connection. |
| `recorded_at` | The point's timestamp normalized to UTC ISO 8601. |

GPS coordinates are never fabricated for an imported workout. Invalid/non-finite coordinates, latitude outside ±90°, longitude outside ±180°, missing timestamps or timezone-free timestamps are skipped with a count. Duplicate timestamps are dropped after chronological sorting. Points outside the selected workout interval are excluded.

### `splits`

| Column | Meaning |
| --- | --- |
| `km` | 1-based split number. |
| `distance_m` | Distance within that split, usually 1000; the final split may be shorter. |
| `elapsed_s` | Duration of that split, not cumulative time. |
| `pace_s_km` | Split elapsed seconds divided by split meters, multiplied by 1000. |
| `avg_hr_bpm` | Mean of accepted HR samples whose timestamps fall inside the split interval, or null. |
| `elevation_gain_m` | Positive elevation gain allocated by each connected edge's distance overlapping the split, or null. |
| `is_partial` | `1` for a final split shorter than 1 km; otherwise `0`. |

### `heart_rate`

| Column | Meaning |
| --- | --- |
| `elapsed_s` | Accepted record start relative to workout start, clamped to zero for a record that begins before and overlaps the workout. |
| `bpm` | Heart rate in beats/minute. |
| `source` | Original record `sourceName`. |

The importer accepts `HKQuantityTypeIdentifierHeartRate` records with `count/min` or `bpm` units and timestamps overlapping the chosen interval. It does not use resting HR or walking averages. If any samples match the selected workout's `sourceName`, only that source is retained. Otherwise all available overlapping sources are retained and a warning records the fallback. Finite values greater than zero and at most 300 bpm are accepted; invalid overlapping samples are skipped. A record's duration does not turn it into multiple invented samples. Same-time observations remain separate samples; means are unweighted by time or device.

## Device details and provenance

A display label such as `Apple Watch Ultra 4` comes only from the user via `--device-label`. `device_metadata_json.label_provenance` records `user-provided`. The label is not used to overwrite or reinterpret the archive's device fields.

The selected workout's `device` attribute can independently expose a manufacturer, generic model, hardware code, firmware and software version. The importer retains those allowlisted fields under `exported_device`, as strings. A comma inside a hardware code is preserved. It does not guess a retail model from a hardware identifier or claim that an exported source/software version proves a particular watch model. Missing metadata remains absent, with `export_metadata_available` indicating whether the original attribute was present.

The raw device string is never stored. HKDevice memory addresses, personalized device names, local identifiers, serial numbers, UDI identifiers and other unrecognized fields are omitted. `unique_identifiers_retained` is always `false`. The exported workout source name remains local in `race.source`; the database is excluded from source control.

## Metric and replay strategy

Distance uses the haversine great-circle formula with mean Earth radius 6,371,008.8 meters. Apple-reported distance is retained separately because watch sensor fusion and this GPS-only calculation can differ. Elevation gain is an unsmoothed sum, so GPS altitude noise can overstate climbing; it is not a survey-grade or barometric correction.

No distance is added across GPX segment/file changes, consecutive samples more than 60 seconds apart, an explicit pause interval, or an implied point-to-point speed greater than 25 m/s (90 km/h). These constants are deliberately visible in `scripts/import_workout.py`. They suit this running/racing visualization; a fast cycling or motor-sport import may need different thresholds. The omitted interval still exists on the elapsed-time clock. A dashboard should hold at the last known position until the next segment begins instead of animating an invented straight-line journey.

Pause/resume events include `Pause`/`Resume` and `MotionPaused`/`MotionResumed` suffixes. Events are sorted, clipped to workout bounds, and paired. An unmatched final pause extends to workout end with a warning. The original workout duration takes precedence over a derived duration. Apple can report a fractional-second duration while its start/end strings are rounded to whole seconds. Differences up to one second are retained as reported; a duration more than one second greater than total elapsed time is discarded with a warning.

Point pace uses an approximately trailing 30-second window. Its anchor is the latest sample at or before the window boundary; sparse cadence can make the actual window slightly longer. The window restarts at a segment break and needs at least 5 meters of eligible displacement. It is a smoothed pace, not a raw instantaneous measurement. No pace is extrapolated across pauses or gaps.

One-kilometer split crossings are linearly interpolated between consecutive eligible distance samples. Split elapsed time includes intervening pauses/gaps; timing starts at workout elapsed zero. A final remainder is explicitly marked partial, and its pace is normalized to a kilometer. Split coverage ends when the final route distance is first reached, so a recording tail after the last moving GPS point can appear in overall elapsed time without appearing in split durations. HR is averaged from actual samples in each split interval; it is not averaged from repeated nearest-sample display values. Split elevation distributes a positive elevation delta by edge overlap; a zero-distance elevation change cannot be allocated and is omitted from split elevation, although it can contribute to overall unsmoothed gain.

No fitness score, medical interpretation, estimated maximum HR, inferred calories or invented missing samples are produced.

## Local storage and replacement

Database creation is transactional in a temporary sibling file, followed by an integrity check and atomic placement. Without `--replace`, an existing file is never overwritten, including one created by another process during import. With `--replace`, a successfully completed import replaces the single-workout file atomically. Existing SQLite WAL/SHM sidecars cause replacement to be refused; stop the writer and checkpoint it first.

The file is created with owner-only permissions (`0600`). A native Grafana process running under the same OS user can read it. For a container or a separate service account, mount the file read-only and grant only the access required by that Grafana process. Do not make the Health export or raw database publicly readable. Raw ZIP/XML/GPX, imported databases, local photos and credentials belong in ignored local paths, not in GitHub.

Grafana reads this completed database through its SQLite data-source plugin. Browser animation replays historical samples; it does not mean the Apple Watch is transmitting live telemetry. Refreshing Grafana queries does not poll Apple Health. After replacing a database with another workout, rerun the installer with the same database path and `--overwrite` to update the absolute time range and native Geomap segment layers, then reopen the dashboard without old `from`/`to` URL overrides. A data-source connection that still holds the old inode may require reconnecting before installation sees the new file.

## Fictional preview and validation

```sh
python3 scripts/make_demo.py --db runtime/workout.sqlite
python3 -B -m unittest discover -s tests -v
```

The deterministic demo invents a 10,000-meter loop at an arbitrary geographic anchor, 601 route samples, 308 HR readings, an 82-minute elapsed duration, an 80-minute active duration, a two-minute pause, elevation and calorie values. It is not a real race route, does not use the supplied photo's location, and is clearly identified by `race.synthetic = 1`, source, title and warnings.

The tests create their own non-personal Health/GPX fixtures. They verify source/window-limited HR, nested route/statistics/event preservation, timezones, unit conversion, pause and discontinuity handling, missing-data nulls, interpolated kilometer splits and partials, ambiguous-selection refusal, explicit latest-running selection across timezones and activity types, device-label provenance and identifier exclusion, unsafe ZIP/reference/symlink rejection, deterministic demo metrics, SQLite integrity, and explicit replacement behavior. Native Apple export compatibility has also been checked against the user's supplied Health archive: the explicitly requested latest Running activity, its linked GPX, matching-source heart-rate records, workout statistics and device metadata imported successfully. The archive, selected route, SQLite database and private device/source data stay local and are excluded from source control. The test suite continues to use generated fixtures only.
