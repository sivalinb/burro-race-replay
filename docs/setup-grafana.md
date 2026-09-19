# Install the race replay in Grafana

This project uses Grafana's **HTML Graphics** panel for the animated course and **SQLite** data source for the imported workout. Ten built-in Stat, Time series, Bar chart, Table and Geomap panels provide additional analysis. Everything runs inside a normal Grafana dashboard. The panel's HTML, CSS, and JavaScript are included in the dashboard export; no separate web app is required.

The configured plugin versions are:

| Plugin | ID | Version |
| --- | --- | --- |
| [HTML Graphics](https://grafana.com/grafana/plugins/gapit-htmlgraphics-panel/) | `gapit-htmlgraphics-panel` | `2.2.3` |
| [SQLite](https://grafana.com/grafana/plugins/frser-sqlite-datasource/) | `frser-sqlite-datasource` | `4.0.6` |

The implementation targets Grafana 12.1.1 and these plugin versions. The installer installs only missing plugins when requested; it does not replace an already installed version.

## Install into your existing local Grafana

1. Complete [the workout export and import](export-workout.md), producing `runtime/workout.sqlite`, or use the labeled demonstration described in the README.
2. Build the dashboard from its source files:

   ```sh
   python3 scripts/build_dashboard.py
   ```

3. Preview the install plan. Use the absolute database path **as seen by the Grafana server**:

   ```sh
   python3 scripts/install_grafana.py \
     --url http://127.0.0.1:3030 \
     --db "/absolute/path/to/burro-race-replay/runtime/workout.sqlite" \
     --dry-run
   ```

4. Install using an account that can manage data sources, folders, and dashboards:

   ```sh
   python3 scripts/install_grafana.py \
     --url http://127.0.0.1:3030 \
     --db "/absolute/path/to/burro-race-replay/runtime/workout.sqlite" \
     --user YOUR_GRAFANA_USERNAME
   ```

The script prompts for the password without displaying it. It prints the saved dashboard URL on success. Omit `--url` for the default `http://127.0.0.1:3000`, or set `GRAFANA_URL`.

If a plugin is missing, install it from Grafana's plugin administration page or add `--install-plugins` to the command using **Grafana server-admin credentials**. Plugin installation may require a Grafana restart before the backend becomes available; restart through your normal service/container manager, then rerun the installer. Dashboard and data-source permissions alone may not grant plugin-install permissions.

For automation, the script supports `GRAFANA_TOKEN` for a service-account token. Otherwise it uses `GRAFANA_USER` / `--user` and `GRAFANA_PASSWORD` or the password prompt. Supply secrets through your usual secret manager or shell environment; there are no password/token command-line flags, and the script does not save or print credentials. API redirects are refused so credentials are not forwarded to another location.

## What the installer creates and verifies

- Folder: **Burro Race**.
- Data source: **Burro Race · Local Workout**, UID `burro-race-sqlite`.
- Dashboard UID: `burro-race-replay`, loaded from `dashboards/burro-race-replay.json`.
- SQLite options: `pathOptions=mode=ro`, `attachLimit=0`, server-side access.
- Schema checks: `race`, `route_points`, `splits`, and `heart_rate` must all be queryable.
- Dashboard checks: executes replay queries A–D, native queries N1–N10 and any additional Geomap segment queries before saving. Empty successful heart-rate results are valid when the workout contains no heart-rate samples.
- Historical time range: reads the selected workout start/end from query A and saves absolute dashboard bounds.
- Geomap: creates one filtered route layer per connected GPS segment, avoiding lines across missing coverage.

An existing dashboard is left intact unless you provide `--overwrite`. The update uses its current version to avoid silently overwriting a concurrent edit. A conflicting data-source type, path, access mode, or read-only configuration is always refused, including with `--overwrite`. The installer never edits a conflicting source to point at a different file.

Use the same database path for later imports. After importing another workout with the importer's `--replace` option, rerun `install_grafana.py` with the same `--url` and `--db` plus `--overwrite`. This updates the saved time range and the Geomap layers for that workout's GPS segments. Then reopen the dashboard without old `from`/`to` URL parameters, which override its saved range. If SQLite still holds a connection to the old file after atomic replacement, reconnect the data source or restart Grafana before rerunning the installer. Rebuild first when the dashboard source has changed.

Keep normal Grafana authentication enabled. This panel uses the HTML Graphics plugin's JavaScript capability; it does **not** require global `disable_sanitize_html`, anonymous access, or disabling plugin signature checks.

## If Grafana runs in Docker

The database path must exist **inside the Grafana container**. A path in your Mac's Downloads folder is not automatically available there. The SQLite plugin reads from Grafana's filesystem, as described in its [plugin documentation](https://grafana.com/grafana/plugins/frser-sqlite-datasource/).

For an existing container, add a read-only bind mount of the repository's `runtime` directory, for example:

```yaml
volumes:
  - /absolute/path/to/burro-race-replay/runtime:/var/lib/burro:ro
  - burro_grafana_storage:/var/lib/grafana
```

Then supply `--db /var/lib/burro/workout.sqlite` to the installer, even when running the Python script on the Mac. Mounting the containing directory also lets the container see database replacement at the same filename.

The importer creates the database with permissions `0600`, readable only by its owner. A default Grafana container can run under a different numeric user ID and therefore cannot automatically read that file. For the separate-container example below, Grafana runs under the importing host user's UID/GID, and its writable storage directory is created by that same user. The database can keep its `0600` permissions, including after later imports.

If you retain an existing container's different UID instead, explicitly grant its Grafana process read access to this database and traversal access to the mounted directory using your operating system's user/group or ACL mechanism. Keep access limited to that service account. Atomic `--replace` creates a new `0600` file, so any service-specific ACL/group permission on the old file must be reapplied after each import. Its Grafana persistent volume must also remain writable by the UID actually running the server.

For a separate local container, this is a starting configuration. Run it from the repository directory after generating `runtime/workout.sqlite`:

```sh
mkdir -p private/grafana-storage
chmod 700 private/grafana-storage
docker run -d --name burro-grafana \
  --user "$(id -u):$(id -g)" \
  -p 127.0.0.1:3031:3000 \
  --mount "type=bind,source=$PWD/private/grafana-storage,target=/var/lib/grafana" \
  --mount "type=bind,source=$PWD/runtime,target=/var/lib/burro,readonly" \
  -e "GF_PLUGINS_PREINSTALL=gapit-htmlgraphics-panel@2.2.3,frser-sqlite-datasource@4.0.6" \
  grafana/grafana:12.1.1
```

Complete Grafana's first login and password setup at `http://127.0.0.1:3031`, wait for the plugins to finish installing, and run the installer with that URL and the container database path. This example uses a different local port from the existing Grafana instance. It is an optional deployment example, not a claim that this container was started for you.

The example's `private/grafana-storage` bind mount preserves Grafana settings, dashboards, and installed plugins across container replacement. The read-only `runtime` bind mount preserves the separate workout database. Both directories are ignored by Git. Existing installations can keep their named Grafana volume instead when its ownership matches their configured process UID. Deleting either storage location removes the corresponding data. Grafana documents [Docker persistence, bind mounts, and versioned plugin installation](https://grafana.com/docs/grafana/latest/setup-grafana/installation/docker/).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Missing plugin | Install the two IDs above; restart Grafana if needed. |
| File not found / query error | Use the server or container path, not a browser-side path. Confirm the import succeeded and that Grafana can read the file. |
| Missing table | Run the importer to create the complete project schema, including an empty heart-rate table when necessary. |
| Source conflict | Reuse the exact path already configured, or resolve the conflict explicitly in Grafana before retrying. |
| Dashboard exists | Add `--overwrite` only when you intend to update this project's dashboard. |
| Redirect or unauthorized response | Check the base URL and account permissions; use the direct Grafana URL rather than a login-page URL. |
| No route / no heart rate | Check the export coverage. The dashboard cannot reconstruct measurements the Watch did not record or export. |

The Health archive and workout database stay on your machine. Keep `runtime/`, raw exports, local configuration, and credentials out of version control even when the source repository itself is private.
