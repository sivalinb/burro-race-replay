# Validation record

The supplied Apple Health archive was validated locally. Exactly the latest Running activity was selected, with its linked GPX, matching-source heart-rate samples and exported device metadata. Other activities were not imported. The single-workout SQLite database passes its integrity check with no import warnings. Private data remains outside source control.

## Current layout

Ten native panels appear first: four Stat panels, three Time series panels, a split Bar chart, a split Table and a native Geomap. A compact course replay is last. Duplicate totals, HR/pace/elevation readings, custom charts, split table, large companion portrait and energy card have been removed from the animation. It retains the moving Siva-and-Miles marker, recorded route, playback controls, data-status badge and compact device provenance.

## Automated validation

- Fourteen Python tests pass: ten importer tests and four native-panel tests. They cover selected-workout scoping, safe archive paths, units/timezones, missing values, GPS gaps/pauses, split calculations, device provenance, SQLite replacement, SQL values, timestamp units, workout time bounds, per-segment map layers and invalid-query rejection.
- Eleven Node replay tests pass. The harness reads IDs from the actual panel HTML, so references to removed elements fail. It checks the simplified layout, both array and vector dataframes, timestamped movement, GPS bounds, pauses/gaps, synthetic tile suppression, real tile bounds/reuse, zoom/Fit, compact device provenance, warnings, errors, reduced motion, background suspension and cleanup.
- JavaScript syntax and dashboard build consistency checks pass.
- Fixtures are fictional; automated tests do not read private workout files or make map requests.

## Local Grafana verification

Installation validates all four SQLite tables and all replay/native queries before saving. The local source is read-only. The installer finds the replay by its panel type, independently of its dashboard position.

Browser checks confirm the native panels are first and contain recorded values with the correct historical time range. The native map renders and fits the recorded course. The final panel contains only the compact animation, controls and provenance, with no duplicate statistics or analysis cards. Playback, pause and scrub remain functional, and the map preserves attribution.

README screenshots use an isolated fictional database and temporary preview dashboard/source. These do not replace the real workout or its Grafana resources. Actual workout screenshots, GPS, the Health ZIP and SQLite database remain local.
