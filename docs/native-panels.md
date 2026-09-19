# Native Grafana analysis panels

The dashboard combines the animated race journal with **10 native Grafana panels using five built-in visualization types**. The animated panel retains the person-and-burro replay, course traversal, watch-pause behavior, and synchronized moments. The native panels provide Grafana's standard chart interaction, table inspection, map navigation, and editable visualization options. No additional visualization plugin is required for these ten panels.

The data remains in the same local, read-only SQLite source, `burro-race-sqlite`. These panels query the single imported workout; they do not connect to the Apple Watch or poll Apple Health. See [the data contract](data-contract.md) for ingestion and metric definitions.

| Query | Native panel | What it shows |
| --- | --- | --- |
| `N1` | Stat: Distance · Watch & GPS | Apple-reported and independently calculated GPS distance, in km. |
| `N2` | Stat: Time · elapsed & active | Full elapsed time and available active duration, formatted as hours/minutes/seconds. |
| `N3` | Stat: Heart rate · mean & peak | Accepted sample mean and observed maximum, in bpm. |
| `N4` | Stat: Recorded energy | Workout-reported energy in kcal; no calorie estimate is generated. |
| `N5` | Time series: Heart rate · recorded timeline | Individual HR observations at their original workout timestamps. |
| `N6` | Time series: Pace · rolling GPS | The importer's approximately trailing 30-second pace in decimal min/km. |
| `N7` | Time series: Elevation · GPS profile | Recorded elevations in meters, with disconnected route intervals preserved. |
| `N8` | Bar chart: Kilometer split pace | Elapsed pace per split. Lower is faster; `*` marks a partial final km. |
| `N9` | Table: Kilometer splits · detail | Distance, elapsed time, normalized pace, mean HR, climb, and explicit full/partial coverage. |
| `N10` | Geomap: Recorded course | Interactive OpenStreetMap with recorded route segments; the direct-import template safely uses GPS point markers. |

The four core query references `A`–`D` still feed the animated first panel. New panels use globally unique `N1`–`N10` references. Multiple GPS segments add `N10_S1`, `N10_S2`, and so on to the installed Geomap only, while the dashboard still contains ten native panels.

## Accurate historical time

The SQLite plugin expects numeric timestamps in **Unix seconds**. The native timelines use expressions such as:

```sql
SELECT r.started_at_epoch_s + h.elapsed_s AS time,
       h.bpm AS "Heart rate"
FROM heart_rate h CROSS JOIN race r
ORDER BY time
```

Each timeline query has `timeColumns: ["time"]` and the SQLite plugin's exact `queryType: "time series"` value, including the space. Table, Stat, Bar chart and Geomap queries use `queryType: "table"`; Geomap also converts its `time` column for chronological hover information. The Grafana query API's outer `from` and `to` range uses milliseconds, which is different from the SQL timestamp column. The installed plugin's behavior matches its [documented timestamp conversion](https://grafana.com/grafana/plugins/frser-sqlite-datasource/).

At installation, `install_grafana.py` reads the selected workout's `start_time` and `end_time` from query `A`, verifies they are timezone-aware and ordered, and saves those absolute bounds in the installed dashboard. The committed export keeps generic relative bounds and contains no personal race timestamps. Native charts therefore initially show the historical race after installation, even when the race is not recent.

The panel queries intentionally return the complete single-workout dataset rather than applying a SQL time filter. The chart's time axis can zoom to a smaller interval, while summary Stat panels continue to represent the entire workout. The visible Grafana time picker and shared chart crosshair make timeline inspection available. These are historical, full-workout native queries with normal Grafana interactions: the animated replay's Play, speed and scrub controls affect only the custom animated panel, not the native charts or map.

An existing browser URL containing `from=now-15m&to=now` overrides the saved dashboard range. Open the installer-returned canonical dashboard link, remove those URL parameters, or select the recorded workout range if charts appear empty after installation.

## Gaps, nulls, and units

No native panel substitutes zero for unavailable HR, calories, elevation, duration, or pace. Standard missing-value text is `—`.

The HR query adds a null break after gaps longer than 60 seconds. The elevation query adds a null break when the importer's route `segment` changes. These null rows only instruct Grafana to disconnect a trace; they are not invented measurements and are not written back to SQLite. Rolling pace already contains nulls at segment starts and when insufficient distance is available. Time series use `spanNulls: false`.

Pace charts and the split table show **decimal minutes per kilometer**: `8.50 min/km` means eight minutes thirty seconds per kilometer. The animated panel can display the same pace as `8:30`. High pace values during very slow movement are retained, not clipped to a convenient running range; they represent more minutes needed per kilometer. Split pace uses elapsed time, including intervening pauses, and normalizes a partial final split to one kilometer. The table reports the shorter split's actual distance and marks it `Partial`.

Heart-rate colors are decorative, without age-derived training zones or an inferred maximum HR. Overall climb remains the importer's unsmoothed GPS elevation gain, not a corrected barometric measurement. Native Stat panels retain both Watch distance and GPS distance so the difference stays visible.

## Native Geomap and connected segments

The native Geomap uses Grafana's built-in `osm-standard` basemap and explicit latitude/longitude columns. It fits its view to the returned data and enables pan, zoom, scale, attribution and measurement controls. Grafana documents these built-in [Geomap location and layer options](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/geomap/).

Grafana 12.1.1's built-in Route layer consumes one dataframe and constructs one line through its rows. Passing every workout point into that line would incorrectly bridge GPS gaps and watch-pause segment boundaries. The installer prevents this:

1. It reads segment identifiers from core query `B`.
2. It creates one `SELECT ... WHERE p.segment = ... ORDER BY p.seq` query per connected segment.
3. It creates a Route layer for each query with `filterData: {"id": "byRefId", "options": "N10..."}`.
4. Every layer receives only its own chronological points. No line joins different segments.

This uses the Route layer already exposed by Grafana 12.1.1; no experimental-feature switch or server setting is enabled. The serialized source export initially uses dense point markers instead, so a manual import remains truthful before the installer knows the actual segment inventory. An empty route keeps that safe empty-marker configuration.

OpenStreetMap tiles are external network requests from the browser and require internet access. Their requested tile areas reveal the viewed map region to the tile provider. Workout measurements remain served by local Grafana/SQLite; no Health archive is sent to OpenStreetMap. The native Geomap also uses the basemap for the fictional demo's invented coordinates. This differs from any synthetic-preview tile suppression in the custom animated panel.

## Build, install, and switch workouts

```sh
python3 scripts/build_dashboard.py
python3 scripts/install_grafana.py \
  --url http://127.0.0.1:3030 \
  --db /absolute/path/to/runtime/workout.sqlite \
  --overwrite
```

Use the existing authentication mechanism described in [Grafana setup](setup-grafana.md). Credentials are never embedded in the dashboard.

**After importing a different workout, rerun the installer with `--overwrite`.** It updates the saved historical time range, regenerates per-segment Geomap queries/layers, and validates the current data. A browser refresh alone cannot update those saved settings. If a SQLite data-source connection still holds the old replaced database file, reconnect it first so the installer sees the new workout.

The installer checks the existing source type/path and read-only settings, retains the core animated queries, rejects duplicate references or unexpected data sources, checks every query is a single `SELECT`, validates schema/core results, and executes all native queries before saving the dashboard. The existing source remains `mode=ro` with `attachLimit=0`.

## Verification

```sh
python3 scripts/build_dashboard.py --check
python3 -B -m unittest discover -s tests -v
```

`tests/test_native_panels.py` uses only generated demo data. It executes all ten native queries and verifies their units, timestamp scale, null gap breaks and split values. It also verifies that installation derives absolute workout bounds without changing the committed export, separate Geomap layers cannot query across segments, empty routes remain safe, and invalid/duplicate queries are rejected.

The native panel structures and route-filter behavior were checked against the installed Grafana 12.1.1 bundle; the SQLite query mode and timestamp conversion were checked against the installed SQLite data-source 4.0.6 implementation. Actual Grafana rendering and data-source responses are checked during local dashboard validation, independently of these unit tests.
