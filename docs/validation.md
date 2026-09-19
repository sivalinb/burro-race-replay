# Validation record

The supplied Apple Health archive has been validated locally. Exactly the latest Running activity was selected, with its linked GPX, matching-source heart-rate samples and exported device metadata. Other activities were not imported. The resulting single-workout SQLite database passes its integrity check and has no import warnings. Private data remains outside source control.

- Ten Python unit tests pass for Health ZIP/XML + GPX parsing, nested workout records, source/time-scoped HR, timezone/unit handling, invalid-data filtering, route gaps/pauses, split interpolation, unsafe archive/reference/symlink rejection, SQLite replacement/integrity, latest-running selection across activity types/timezones, and device-label provenance with identifier exclusion.
- The builder generates the Grafana JSON from the committed panel and artwork sources; `--check` detects drift.
- The JavaScript passes Node syntax validation.
- Installation into the local Grafana succeeded using its existing HTML Graphics and SQLite plugins. The installer validated all four tables, replay queries A–D, and the native-panel queries.
- The browser shows the synthetic-data notice, 10.00 km course, 82-minute elapsed / 80-minute active duration, course animation, revised Siva/Miles portrait, HR/pace/elevation readings, chart and kilometer splits. Browser error log was empty during this check.

The README preview is captured from the running Grafana dashboard. It contains fictional measurements. The following UI control checks were also completed.

- Browser controls: scrubbed two times within the fictional watch-pause interval and verified an identical marker transform with `WATCH PAUSED`; scrubbed to finish and verified 100% / 10.00 km; restart returned to 0; replay-speed selection changed correctly.
- Actual-data browser verification: recorded-workout badge, GPS/Watch distance distinction, elapsed/active duration, live-at-replay HR/pace/elevation, ten splits with partial final kilometer, user-supplied watch label and separate exported hardware/software all appear.
- OpenStreetMap tiles and attribution are visible beneath the recorded course. External tile loading was enabled only after the user explicitly approved OpenStreetMap requests. The repository screenshot remains fictional; actual-route screenshots stay local.

- Nine replay behavior tests pass in the portable Node harness, covering array/vector frames, gaps/pauses, HR expiry, synthetic map isolation, real-mode tile bounds and reuse, zoom/Fit, device provenance, query errors, reduced motion, background suspension, tile failures and cleanup. They use fictional data and no network.
- Four native-panel Python tests pass for units, Unix-seconds timestamps, missing-data breaks, historical dashboard bounds, independent map segment layers and invalid-query rejection (14 Python tests total).

- Final native-panel browser verification: all four Stat panels show recorded values; HR, rolling pace and elevation charts use the workout's actual time bounds; the split bar chart has complete readable axis labels; the table marks the partial final kilometer; and native Geomap fits and renders the recorded route over OpenStreetMap. Scale, zoom and attribution controls work. No new browser console errors appeared after the final map corrections. Actual-data screenshots are stored outside the repository.
