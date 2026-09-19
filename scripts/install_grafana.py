"""Install Burro Race Replay into an existing Grafana without changing server settings."""

import argparse
import base64
from copy import deepcopy
from datetime import datetime, timezone
import getpass
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_UID = "burro-race-replay"
SOURCE_UID = "burro-race-sqlite"
SOURCE_TYPE = "frser-sqlite-datasource"
PLUGIN_VERSIONS = {"gapit-htmlgraphics-panel": "2.2.3", SOURCE_TYPE: "4.0.6"}
REQUIRED_TABLES = {"A": "race", "B": "route_points", "C": "splits", "D": "heart_rate"}
NATIVE_REFS = {f"N{i}" for i in range(1, 11)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward credentials through a redirect, even to a login page."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_source(existing, db_path):
    """Only reuse the exact read-only source this installer would create."""
    data = existing.get("jsonData", {})
    expected = (
        existing.get("type") == SOURCE_TYPE
        and existing.get("access") == "proxy"
        and data.get("path") == db_path
        and data.get("pathOptions") == "mode=ro"
        and type(data.get("attachLimit")) is int
        and data["attachLimit"] == 0
    )
    if not expected:
        raise RuntimeError(
            f"Existing data source {SOURCE_UID} has a conflicting type, path, or security configuration. "
            "No conflicting data source was changed. Use its existing database path, or resolve the conflict in Grafana first."
        )


def check_query_results(response, refs, label, require_count=False):
    results = response.get("results", {})
    for ref in refs:
        entry = results.get(ref)
        if not isinstance(entry, dict) or entry.get("error"):
            raise RuntimeError(f"{label} query {ref} failed. Check the Grafana-visible database path and imported schema.")
        status = entry.get("status", 200)
        if not isinstance(status, (int, float)) or status >= 400:
            raise RuntimeError(f"{label} query {ref} failed with a non-success query status.")
        frames = entry.get("frames")
        if not isinstance(frames, list):
            raise RuntimeError(f"{label} query {ref} returned no frame result.")
        if require_count:
            values = [column for frame in frames for column in frame.get("data", {}).get("values", [])]
            if not values or not values[0] or not isinstance(values[0][0], (int, float)):
                raise RuntimeError(f"Schema query {ref} did not return a table count.")
        # Empty frames/columns are valid for unavailable heart rate and an empty-state dashboard.


def dashboard_targets(dashboard, allow_segment_queries=False):
    targets = []

    def visit(panels):
        for panel in panels:
            targets.extend(t for t in panel.get("targets", []) if not t.get("hide"))
            visit(panel.get("panels", []))

    visit(dashboard.get("panels", []))
    refs = [target.get("refId") for target in targets]
    expected = set(REQUIRED_TABLES) | NATIVE_REFS
    extras = set(refs) - expected
    if not expected.issubset(refs) or (extras and not (allow_segment_queries and all(isinstance(ref, str) and re.fullmatch(r'N10_S[1-9][0-9]*', ref) for ref in extras))):
        raise RuntimeError("The built dashboard must contain race queries A–D and native panel queries N1–N10. Rebuild it before installing.")
    if len(set(refs)) != len(refs):
        raise RuntimeError("The built dashboard has duplicate query references. Rebuild it before installing.")
    if not dashboard.get('panels') or {t.get('refId') for t in dashboard['panels'][0].get('targets', [])} != set(REQUIRED_TABLES):
        raise RuntimeError("The first panel must retain the animated replay's four queries A–D.")
    for target in targets:
        if target.get("datasource") != {"type": SOURCE_TYPE, "uid": SOURCE_UID}:
            raise RuntimeError("The built dashboard references an unexpected data source. Rebuild it before installing.")
        sql = target.get('rawQueryText', '').strip()
        if not re.match(r'^SELECT\b', sql, re.IGNORECASE) or ';' in sql.rstrip(';') or sql != target.get('queryText', '').strip():
            raise RuntimeError("Dashboard queries must be matching, single read-only SELECT statements.")
        if target.get('queryType') not in ('table', 'time series'):
            raise RuntimeError("SQLite queryType must be 'table' or 'time series'.")
    return targets


def query_rows(response, ref):
    """Read Grafana's columnar API frames without printing any workout values."""
    rows = []
    for frame in response.get('results', {}).get(ref, {}).get('frames', []):
        fields = frame.get('schema', {}).get('fields', [])
        columns = frame.get('data', {}).get('values', [])
        if len(fields) != len(columns) or any(not isinstance(c, list) for c in columns):
            raise RuntimeError(f'Query {ref} returned an invalid columnar frame.')
        if columns and any(len(c) != len(columns[0]) for c in columns):
            raise RuntimeError(f'Query {ref} returned unequal column lengths.')
        rows.extend({field['name']: columns[i][row] for i, field in enumerate(fields)} for row in range(len(columns[0]) if columns else 0))
    return rows


def apply_workout_time(dashboard, response):
    """Only the installed copy gets the selected historical workout's actual time range."""
    rows = query_rows(response, 'A')
    if len(rows) != 1:
        raise RuntimeError('The race table must contain exactly one imported workout.')
    endpoints = []
    for field in ('start_time', 'end_time'):
        value = rows[0].get(field)
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None or not math.isfinite(parsed.timestamp()):
                raise ValueError('Timezone required')
        except (AttributeError, ValueError, OverflowError):
            raise RuntimeError(f'The workout {field} must be an ISO timestamp with a timezone.') from None
        endpoints.append(parsed.astimezone(timezone.utc))
    if endpoints[1] <= endpoints[0]:
        raise RuntimeError('The workout end must be later than its start.')
    dashboard['time'] = dict(zip(('from', 'to'), [t.isoformat(timespec='milliseconds').replace('+00:00', 'Z') for t in endpoints]))
    return tuple(int(t.timestamp()*1000) for t in endpoints)


def configure_geomap_segments(dashboard, response):
    """Grafana 12.1 Route supports one frame, so use a filtered layer per segment."""
    panel = next((p for p in dashboard.get('panels', []) if p.get('type') == 'geomap'), None)
    if panel is None:
        raise RuntimeError('The native Geomap panel is missing; rebuild the dashboard.')
    segment_values = [row.get('segment') for row in query_rows(response, 'B')]
    if any(not isinstance(n, (int, float)) or not math.isfinite(n) or int(n) != n or n < 0 for n in segment_values):
        raise RuntimeError('Route segment identifiers must be nonnegative integers.')
    segments = sorted(set(int(n) for n in segment_values))
    if not segments:
        return  # The safe marker layer presents an empty map when no GPS exists.
    prototype = deepcopy(panel['targets'][0])
    panel['targets'], panel['options']['layers'] = [], []
    for i, segment in enumerate(segments):
        ref = 'N10' if i == 0 else f'N10_S{i}'
        target = deepcopy(prototype)
        sql = ('SELECT r.started_at_epoch_s + p.elapsed_s AS time, p.lat AS "Latitude", p.lon AS "Longitude", '
               'p.elevation_m AS "Elevation", p.distance_m / 1000.0 AS "Distance", p.segment AS "Segment" '
               f'FROM route_points p CROSS JOIN race r WHERE p.segment = {segment} ORDER BY p.seq')
        target.update(refId=ref, rawQueryText=sql, queryText=sql)
        panel['targets'].append(target)
        panel['options']['layers'].append({'type': 'route', 'name': f'Recorded segment {segment+1}',
            'filterData': {'id': 'byRefId', 'options': ref},
            'location': {'mode': 'coords', 'latitude': 'Latitude', 'longitude': 'Longitude'},
            'config': {'arrow': 0, 'style': {'color': {'fixed': '#A35220'}, 'size': {'fixed': 4, 'min': 4, 'max': 4},
                                          'opacity': 0.9, 'lineWidth': 4}}, 'tooltip': True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000"))
    parser.add_argument("--db", required=True, help="Absolute SQLite path visible to the Grafana server (container path for Docker)")
    parser.add_argument("--user", default=os.environ.get("GRAFANA_USER"))
    parser.add_argument("--install-plugins", action="store_true", help="Install missing pinned plugins; requires Grafana server-admin credentials")
    parser.add_argument("--overwrite", action="store_true", help="Update the matching dashboard UID; never changes a conflicting data source")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without authentication or network calls")
    args = parser.parse_args()
    if not Path(args.db).is_absolute():
        parser.error("--db must be an absolute path visible to the Grafana server")
    address = urllib.parse.urlsplit(args.url)
    if (address.scheme not in ("http", "https") or not address.hostname
            or address.username or address.password or address.query or address.fragment):
        parser.error("--url must be an HTTP(S) Grafana base URL without credentials, query, or fragment")
    base = args.url.rstrip("/")
    path = ROOT / "dashboards" / (DASHBOARD_UID + ".json")
    if not path.is_file():
        raise RuntimeError("Build the dashboard first: python3 scripts/build_dashboard.py")
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    if dashboard.get("uid") != DASHBOARD_UID:
        raise RuntimeError("The built dashboard has an unexpected UID. Rebuild it before installing.")
    targets = dashboard_targets(dashboard)
    source = {
        "uid": SOURCE_UID,
        "name": "Burro Race · Local Workout",
        "type": SOURCE_TYPE,
        "access": "proxy",
        "isDefault": False,
        "jsonData": {"path": args.db, "pathOptions": "mode=ro", "attachLimit": 0},
    }
    if args.dry_run:
        print(json.dumps({"grafana": base, "plugins": PLUGIN_VERSIONS, "data_source": source,
                          "dashboard": DASHBOARD_UID, "folder": "Burro Race", "overwrite": args.overwrite,
                          "native_panels": 10, "native_panel_types": ["stat", "timeseries", "barchart", "table", "geomap"],
                          "time_range": "Read selected workout start/end from query A before saving",
                          "geomap": "One filtered route layer/query per connected segment; no coordinates embedded in dashboard JSON"}, indent=2))
        return

    token = os.environ.get("GRAFANA_TOKEN")
    if token:
        authorization = "Bearer " + token
    else:
        user = args.user or input("Grafana username: ")
        password = os.environ.get("GRAFANA_PASSWORD") or getpass.getpass("Grafana password: ")
        authorization = "Basic " + base64.b64encode((user + ":" + password).encode()).decode()
    opener = urllib.request.build_opener(NoRedirect)

    def api(endpoint, body=None, missing_ok=False):
        request = urllib.request.Request(
            base + endpoint,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": authorization, "Content-Type": "application/json"},
            method="POST" if body is not None else "GET",
        )
        try:
            with opener.open(request, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            raise RuntimeError(f"Grafana API HTTP {error.code} for {endpoint}. Check URL, credentials, and permissions.") from None
        except urllib.error.URLError:
            raise RuntimeError("Could not reach Grafana. Check the base URL and whether the server is running.") from None
        except json.JSONDecodeError:
            raise RuntimeError(f"Grafana returned a non-JSON response for {endpoint}. Check the API base URL.") from None

    # Resolve conflicts before installing plugins or creating any resources.
    existing = api("/api/datasources/uid/" + SOURCE_UID, missing_ok=True)
    if existing:
        check_source(existing, args.db)
    current = api("/api/dashboards/uid/" + DASHBOARD_UID, missing_ok=True)
    if current and not args.overwrite:
        raise RuntimeError(f"Dashboard {DASHBOARD_UID} already exists. Pass --overwrite to update it intentionally.")

    plugins = {plugin["id"] for plugin in api("/api/plugins")}
    for plugin, version in PLUGIN_VERSIONS.items():
        if plugin not in plugins:
            if not args.install_plugins:
                raise RuntimeError(f"Missing {plugin}. Install it in Grafana or pass --install-plugins with server-admin credentials.")
            api(f"/api/plugins/{plugin}/install", {"version": version})
            print(f"Installed {plugin} {version}")
    if not existing:
        api("/api/datasources", source)

    now = int(time.time() * 1000)
    query_range = (now - 86400000, now)

    def query(items):
        return api("/api/ds/query", {"from": str(query_range[0]), "to": str(query_range[1]),
                                    "queries": [dict(item, intervalMs=1000, maxDataPoints=20000) for item in items]})

    schema_queries = []
    for ref, table in REQUIRED_TABLES.items():
        sql = f"SELECT COUNT(*) AS row_count FROM {table}"
        schema_queries.append({"refId": ref, "datasource": {"uid": SOURCE_UID, "type": SOURCE_TYPE},
                               "queryType": "table", "rawQueryText": sql, "queryText": sql, "timeColumns": []})
    check_query_results(query(schema_queries), REQUIRED_TABLES, "Schema", require_count=True)
    core_response = query([t for t in targets if t['refId'] in REQUIRED_TABLES])
    check_query_results(core_response, REQUIRED_TABLES, "Animated replay")
    query_range = apply_workout_time(dashboard, core_response)
    configure_geomap_segments(dashboard, core_response)
    targets = dashboard_targets(dashboard, allow_segment_queries=True)
    native_targets = [t for t in targets if t['refId'] not in REQUIRED_TABLES]
    check_query_results(query(native_targets), [t['refId'] for t in native_targets], "Native panel")

    folder = next((item for item in api("/api/folders?limit=1000") if item["title"] == "Burro Race"), None)
    if folder is None:
        folder = api("/api/folders", {"title": "Burro Race"})
    if current:
        dashboard["id"] = current["dashboard"]["id"]
        dashboard["version"] = current["dashboard"]["version"]
    saved = api("/api/dashboards/db", {"dashboard": dashboard, "folderUid": folder["uid"], "overwrite": False,
                                       "message": "Install Burro Race Replay dashboard."})
    print(base + saved["url"])


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
