"""Install Burro Race Replay into an existing Grafana without changing server settings."""

import argparse
import base64
import getpass
import json
import os
from pathlib import Path
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


def dashboard_targets(dashboard):
    targets = []

    def visit(panels):
        for panel in panels:
            targets.extend(t for t in panel.get("targets", []) if not t.get("hide"))
            visit(panel.get("panels", []))

    visit(dashboard.get("panels", []))
    if {target.get("refId") for target in targets} != set(REQUIRED_TABLES):
        raise RuntimeError("The built dashboard must contain the four race queries A, B, C, and D. Rebuild it before installing.")
    if len(targets) != len(REQUIRED_TABLES):
        raise RuntimeError("The built dashboard has duplicate race query references. Rebuild it before installing.")
    for target in targets:
        if target.get("datasource") != {"type": SOURCE_TYPE, "uid": SOURCE_UID}:
            raise RuntimeError("The built dashboard references an unexpected data source. Rebuild it before installing.")
    return targets


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
                          "dashboard": DASHBOARD_UID, "folder": "Burro Race", "overwrite": args.overwrite}, indent=2))
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

    def query(items):
        return api("/api/ds/query", {"from": str(now - 86400000), "to": str(now),
                                    "queries": [dict(item, intervalMs=1000, maxDataPoints=20000) for item in items]})

    schema_queries = []
    for ref, table in REQUIRED_TABLES.items():
        sql = f"SELECT COUNT(*) AS row_count FROM {table}"
        schema_queries.append({"refId": ref, "datasource": {"uid": SOURCE_UID, "type": SOURCE_TYPE},
                               "queryType": "table", "rawQueryText": sql, "queryText": sql, "timeColumns": []})
    check_query_results(query(schema_queries), REQUIRED_TABLES, "Schema", require_count=True)
    check_query_results(query(targets), REQUIRED_TABLES, "Dashboard")

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
