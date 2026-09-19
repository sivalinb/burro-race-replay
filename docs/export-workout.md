# Export your Apple Watch race

The best input is the Health export from the iPhone paired with your Apple Watch. It can include the recorded workout summary, heart-rate samples, and the GPS track needed to replay your actual course. A photograph or a screenshot of the workout summary cannot supply that track.

## 1. Check the recorded race

Open **Fitness** on your iPhone and find the race in your workout history. Note its date, approximate start time, activity type, and recorded distance. Open the route map if it is available. Apple documents the workout summary and route-tracking requirements in its [Apple Watch workout guide](https://support.apple.com/en-nz/guide/watch/apd95450de2a/26/watchos/26).

An event advertised as 10 km may have a different Watch-recorded distance. This project retains the recorded value and separately calculates distance from the GPS points; it does not force either to exactly 10 km.

If the route is missing, first let the Watch and iPhone finish syncing and check the workout again. Route tracking must have been enabled during the activity. Enabling it afterward cannot recreate GPS points that were never recorded. A manually drawn course can be a separate illustration, but it cannot be presented as your Watch recording.

## 2. Export using the built-in Health app

1. On the paired iPhone, open **Health**.
2. Select **Summary**, then your profile picture or initials.
3. Choose **Export All Health Data** and confirm the export if prompted.
4. Wait for the export to finish, then use the share sheet to save or transfer the resulting archive.

These are Apple's built-in export controls; no paid app is required. Apple describes the export as health and fitness data in XML format. The archive layout handled by this project is described below. See [Apple's Health export instructions](https://support.apple.com/guide/iphone/share-your-health-data-iph5ede58c3d/ios).

Choose **AirDrop** and your Mac in the share sheet, or save to Files and transfer the file to the Mac afterward. Keep both devices nearby with Wi-Fi and Bluetooth enabled. Transfers between devices signed into the same Apple Account can be accepted automatically; check Downloads or the destination you selected. See [Apple's AirDrop guide](https://support.apple.com/guide/iphone/use-airdrop-to-send-items-to-nearby-devices-iphcd8b9f0af/ios).

The result is commonly named `export.zip`. Keep it locally: **Export All Health Data includes health history beyond this race**. Give the importer its local path; do not commit the archive, extracted Health data, or generated workout database to GitHub.

## 3. Select and import just the race

Run these commands from the repository directory with Python 3. Replace the example path with your actual file path. Quoting the path handles spaces in filenames.

```sh
python3 scripts/import_workout.py "/path/to/export.zip" --list
```

Find the race by its date, start time, activity type, and distance. Select its **1-based index** from that output:

```sh
python3 scripts/import_workout.py "/path/to/export.zip" \
  --workout 42 --db runtime/workout.sqlite
```

`42` is an example, not a preset race. To narrow the list by the workout's original local start date:

```sh
python3 scripts/import_workout.py "/path/to/export.zip" \
  --date YYYY-MM-DD --list
```

Replace `YYYY-MM-DD` with the actual date. A date can also select a workout directly when exactly one matches; otherwise use its listed index. Indices refer to the original complete workout list, even when the date filter narrows the display.

An existing database is protected from accidental replacement. To intentionally replace a previous import or demo database, repeat the selected import command with `--replace`. The selected workout becomes the database's race; this is a single-race replay.

## What the importer reads

| Input | What it provides |
| --- | --- |
| Health ZIP | Reads `export.xml` and route files inside the archive without extracting the whole ZIP. |
| Extracted export folder | Accepts `export.xml` in that folder or under `apple_health_export/`. Preserve the accompanying route folders. |
| `export.xml` | Reads the selected workout and looks for its linked GPX file beside the export. XML alone cannot supply a missing route file. |
| Timestamped `.gpx` | Replays the track and derives GPS distance, pace, and splits. It does not import heart rate or calories, including GPX extensions. |

The Health importer supports the `Workout` / `WorkoutRoute` / `FileReference` structure and the associated `workout-routes/*.gpx` convention, including a leading `/workout-routes/` reference. These are **supported export conventions, not an Apple guarantee that the format will remain unchanged**. Apple's consumer export instructions specify XML but do not define a stable schema or promise that every workout has a route.

The importer selects the workout explicitly, follows that workout's route references, and filters heart-rate records to its time window. When available, it prefers heart-rate records whose source name matches the workout source. It does not guess which unrelated GPX belongs to the race. Missing measurements remain unavailable; they are not filled with simulated personal results.

## If you only want to provide race-specific files

You can keep the full Health archive on your Mac and provide its local path for local processing. This is the simplest way to include both route and workout measurements without uploading the entire archive.

For a smaller handoff, a **timestamped GPX for this race** is sufficient for course replay. To verify the totals, also provide the selected workout summary from Fitness. Heart rate and active calories require the relevant exported Health records or a separate supported data integration; a GPX alone does not guarantee either. Screenshots are useful for cross-checking, but this importer does not parse them or merge them into the database. No third-party export app is required for the native Health workflow above.

## What to provide for your personalized replay

- The local path to the exported ZIP or race GPX.
- The race date and approximate start time, so the correct workout can be selected.
- Optionally, the burro's name and your preferred display name. If the reference photo contains several people or burros, identify which pair should be animated.

Once imported, follow the [README](../README.md) for database setup, Grafana installation, playback controls, and the explanation of recorded versus derived statistics. Until a real workout is imported, any demonstration course and readings are illustrative and must remain labeled as demo data.
