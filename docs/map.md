# Map backdrop and route replay

The map provides geographic context for the imported Watch course. The recorded latitude/longitude points form the route overlay; OpenStreetMap supplies the background streets, paths, land features, and labels. Those are separate sources. The backdrop does not determine the runner's position or alter the imported GPS points.

The animated Siva-and-Miles marker follows the workout's recorded timestamps and an elapsed-time replay clock controlled by Play, speed and the scrubber. The native metric panels above it show the full workout independently. Changing playback speed changes the rate of historical replay. It does not request a new route from OpenStreetMap, geocode the athlete's position, or communicate with the Watch.

## Tile requests and attribution

The browser requests standard raster tiles over HTTPS using `https://tile.openstreetmap.org/{z}/{x}/{y}.png`. Requests are limited to tiles needed for the currently displayed map area. Ordinary browser caching is used; the dashboard does not force refresh headers or add cache-busting query parameters. There is no bulk downloader, offline archive, or background prefetch of additional regions or zoom levels.

The map shows **© OpenStreetMap contributors** linked to [OpenStreetMap's copyright page](https://www.openstreetmap.org/copyright). Keep this attribution visible in the dashboard and in screenshots containing the map. Browser requests retain normal User-Agent and Referer behavior; do not add a restrictive referrer policy that suppresses the Referer header.

These choices follow the [OpenStreetMap Foundation tile usage policy](https://operations.osmfoundation.org/policies/tiles/). The community tile service has no availability guarantee. For an offline deployment or a large deployment, choose a provider or self-hosted tiles whose terms support that use; do not turn the standard endpoint into an offline download service.

## What stays local and what leaves the browser

SQLite supplies the selected workout to Grafana. Route coordinates, heart-rate samples, timing, workout totals, and the illustrated marker are rendered locally by the panel. The panel's external data requests are the background tile images; it does not send the full GPX track, Health archive, heart-rate values, calories, athlete name, or device metadata to OpenStreetMap.

A tile request still discloses the tile's zoom level and grid coordinates, which identify the **geographic area being viewed**. The tile server also receives ordinary network/browser request metadata, such as the connecting IP address, User-Agent, and Referer. This map is therefore not a network-isolated mode, even though the full workout is not uploaded. Do not put private workout details in the Grafana page URL or override the browser to send sensitive URL parameters as a full Referer.

If tile requests are unavailable, the local workout database remains intact. The recorded route and statistics do not depend on reconstructing the course from the map service. A displayed basemap can contain current map information that differs from conditions on the race date; it is context for the replay, not evidence of historical trail conditions.

See [the data contract](data-contract.md) for GPS gaps, pauses, distance calculations, and what the importer preserves.
