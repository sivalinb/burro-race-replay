"""Build an importable Grafana dashboard with the local portrait embedded once."""
import argparse
import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = {'type': 'frser-sqlite-datasource', 'uid': 'burro-race-sqlite'}


def query(ref, sql, timeseries=False, time_column=False):
    return {'refId': ref, 'datasource': dict(SOURCE), 'queryType': 'time series' if timeseries else 'table',
            'rawQueryText': sql, 'queryText': sql, 'timeColumns': ['time'] if timeseries or time_column else []}


def native_panels():
    """Built-in Grafana panels; all measurements remain in the local SQLite source."""
    panels = []

    def panel(ref, kind, title, sql, x, y, width, height, unit='none', decimals=1, color='#BFE1C1', description='', timeseries=False):
        item = {'id': 100 + int(ref[1:]), 'title': title, 'type': kind, 'pluginVersion': '12.1.1',
                'description': description, 'gridPos': {'x': x, 'y': y, 'w': width, 'h': height},
                'datasource': dict(SOURCE), 'targets': [query(ref, sql, timeseries=timeseries)],
                'fieldConfig': {'defaults': {'unit': unit, 'decimals': decimals, 'noValue': '—',
                                             'color': {'mode': 'fixed', 'fixedColor': color}}, 'overrides': []},
                'options': {}}
        panels.append(item)
        return item

    stat_specs = [
        ('N1', 'Distance · Watch & GPS', 'SELECT recorded_distance_m / 1000.0 AS "Watch distance", gps_distance_m / 1000.0 AS "GPS distance" FROM race', 'suffix:km', 2, '#BFE1C1',
         'Apple-reported distance and independently calculated GPS distance. They can differ because the watch and importer use different methods.'),
        ('N2', 'Time · elapsed & active', 'SELECT elapsed_s AS "Elapsed", active_s AS "Active" FROM race', 'dthms', 0, '#FFC77C',
         'Elapsed includes pauses. Active is the exported workout duration, or a duration derived from explicit pause events. Fractional duration can differ slightly from whole-second start/end timestamps.'),
        ('N3', 'Heart rate · mean & peak', 'SELECT avg_hr_bpm AS "Mean HR", max_hr_bpm AS "Peak HR" FROM race', 'suffix:bpm', 0, '#F299A5',
         'Mean of accepted selected-workout samples and observed maximum; no inferred maximum-heart-rate zones.'),
        ('N4', 'Recorded energy', 'SELECT energy_kcal AS "Workout energy" FROM race', 'suffix:kcal', 0, '#B3A2FF',
         'Energy reported by the workout or its active-energy statistics. Missing calories remain unavailable; nothing is estimated.'),
    ]
    for i, (ref, title, sql, unit, decimals, color, description) in enumerate(stat_specs):
        item = panel(ref, 'stat', title, sql, i*6, 34, 6, 5, unit, decimals, color, description)
        item['options'] = {'reduceOptions': {'values': False, 'calcs': ['lastNotNull'], 'fields': ''},
                           'orientation': 'auto', 'textMode': 'value_and_name', 'colorMode': 'value',
                           'graphMode': 'none', 'justifyMode': 'auto', 'wideLayout': True}

    hr_sql = '''SELECT r.started_at_epoch_s + h.elapsed_s AS time, h.bpm AS "Heart rate"
FROM heart_rate h CROSS JOIN race r
UNION ALL
SELECT r.started_at_epoch_s + h.previous_s + 0.001 AS time, NULL AS "Heart rate"
FROM (SELECT elapsed_s, LAG(elapsed_s) OVER (ORDER BY elapsed_s) AS previous_s FROM heart_rate) h CROSS JOIN race r
WHERE h.elapsed_s - h.previous_s > 60
ORDER BY time'''
    pace_sql = '''SELECT r.started_at_epoch_s + p.elapsed_s AS time, p.pace_s_km / 60.0 AS "Rolling pace"
FROM route_points p CROSS JOIN race r ORDER BY time'''
    elevation_sql = '''SELECT r.started_at_epoch_s + p.elapsed_s AS time, p.elevation_m AS "Elevation"
FROM route_points p CROSS JOIN race r
UNION ALL
SELECT r.started_at_epoch_s + p.previous_s + 0.001 AS time, NULL AS "Elevation"
FROM (SELECT elapsed_s, segment, LAG(elapsed_s) OVER (ORDER BY seq) AS previous_s,
LAG(segment) OVER (ORDER BY seq) AS previous_segment FROM route_points) p CROSS JOIN race r
WHERE p.segment != p.previous_segment
ORDER BY time'''
    for i, (ref, title, sql, unit, color, description) in enumerate([
        ('N5', 'Heart rate · recorded timeline', hr_sql, 'suffix:bpm', '#F299A5', 'Actual sample timestamps. Gaps longer than 60 seconds are disconnected; null means unavailable.'),
        ('N6', 'Pace · rolling GPS', pace_sql, 'suffix:min/km', '#BFE1C1', 'Approximately trailing 30-second pace derived by the importer, shown in decimal minutes per kilometer. Segment starts and unavailable pace remain null.'),
        ('N7', 'Elevation · GPS profile', elevation_sql, 'suffix:m', '#FFC77C', 'Recorded GPS elevations. The trace breaks at route segment changes; altitude noise is not corrected.'),
    ]):
        item = panel(ref, 'timeseries', title, sql, i*8, 39, 8, 9, unit, 1 if ref != 'N6' else 2, color, description, timeseries=True)
        item['fieldConfig']['defaults']['custom'] = {'drawStyle': 'line', 'lineInterpolation': 'linear', 'lineWidth': 2,
            'fillOpacity': 10, 'gradientMode': 'none', 'showPoints': 'never', 'pointSize': 3, 'spanNulls': False,
            'insertNulls': False, 'axisPlacement': 'auto', 'axisColorMode': 'text', 'axisBorderShow': False,
            'scaleDistribution': {'type': 'linear'}, 'hideFrom': {'legend': False, 'tooltip': False, 'viz': False},
            'stacking': {'mode': 'none', 'group': 'A'}, 'thresholdsStyle': {'mode': 'off'}}
        if ref == 'N6':
            item['fieldConfig']['defaults']['custom']['axisWidth'] = 110
        item['options'] = {'tooltip': {'mode': 'multi', 'sort': 'none', 'hideZeros': False},
                           'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom', 'calcs': []}}

    split_sql = '''SELECT 'Km ' || km || CASE WHEN is_partial = 1 THEN ' *' ELSE '' END AS "Split",
pace_s_km / 60.0 AS "Pace" FROM splits ORDER BY km'''
    item = panel('N8', 'barchart', 'Kilometer split pace', split_sql, 0, 48, 12, 10, 'suffix:min/km', 2, '#BFE1C1',
                 'Elapsed pace for each GPS-derived kilometer; lower is faster. * marks a partial final kilometer, normalized to min/km.')
    item['options'] = {'xField': 'Split', 'orientation': 'vertical', 'xTickLabelRotation': 0, 'xTickLabelSpacing': 0,
                       'showValue': 'auto', 'stacking': 'none', 'groupWidth': 0.7, 'barWidth': 0.85, 'barRadius': 0.1,
                       'fullHighlight': False, 'tooltip': {'mode': 'single', 'sort': 'none'},
                       'legend': {'showLegend': False, 'displayMode': 'list', 'placement': 'bottom', 'calcs': []}}
    item['fieldConfig']['defaults']['min'] = 0
    item['fieldConfig']['defaults']['custom'] = {'lineWidth': 0, 'fillOpacity': 90, 'gradientMode': 'none',
                                                'axisPlacement': 'auto', 'axisColorMode': 'text', 'axisBorderShow': False,
                                                'axisWidth': 110}

    table_sql = '''SELECT km AS "Km", CASE WHEN is_partial = 1 THEN 'Partial' ELSE 'Full km' END AS "Coverage",
distance_m / 1000.0 AS "Distance", elapsed_s AS "Elapsed", pace_s_km / 60.0 AS "Pace",
avg_hr_bpm AS "Mean HR", elevation_gain_m AS "Climb" FROM splits ORDER BY km'''
    item = panel('N9', 'table', 'Kilometer splits · detail', table_sql, 12, 48, 12, 10, description='One row per split. Distance and partial status are explicit; missing HR or elevation is shown as —.')
    item['fieldConfig']['defaults']['custom'] = {'align': 'auto', 'cellOptions': {'type': 'auto'}, 'inspect': False}
    item['fieldConfig']['overrides'] = [
        {'matcher': {'id': 'byName', 'options': name}, 'properties': [{'id': 'unit', 'value': unit}, {'id': 'decimals', 'value': decimals}]}
        for name, unit, decimals in [('Km', 'none', 0), ('Distance', 'suffix:km', 3), ('Elapsed', 'dthms', 0),
                                     ('Pace', 'suffix:min/km', 2), ('Mean HR', 'suffix:bpm', 0), ('Climb', 'suffix:m', 1)]
    ]
    item['options'] = {'showHeader': True, 'cellHeight': 'sm', 'sortBy': [{'displayName': 'Km', 'desc': False}],
                       'footer': {'show': False, 'reducer': ['sum'], 'countRows': False, 'fields': ''}}

    map_sql = '''SELECT r.started_at_epoch_s + p.elapsed_s AS time, p.lat AS "Latitude", p.lon AS "Longitude",
p.elevation_m AS "Elevation", p.distance_m / 1000.0 AS "Distance", p.segment AS "Segment"
FROM route_points p CROSS JOIN race r ORDER BY p.seq'''
    item = panel('N10', 'geomap', 'Recorded course · native Geomap', map_sql, 0, 58, 24, 16,
                 description='Recorded GPS coordinates over OpenStreetMap. The installer creates one route layer per connected segment so gaps are never bridged. A directly imported export safely shows point markers. Basemap tiles require internet access.')
    item['targets'][0]['timeColumns'] = ['time']
    # Grafana 12.1 defaults view.zoom to 1 and uses zoom as the fit cap; set it explicitly.
    item['options'] = {'view': {'id': 'fit', 'allLayers': True, 'padding': 15, 'zoom': 18, 'maxZoom': 18},
        'basemap': {'type': 'osm-standard', 'name': 'OpenStreetMap', 'config': {}},
        'controls': {'showZoom': True, 'mouseWheelZoom': True, 'showAttribution': True, 'showScale': True, 'scaleUnits': 'metric', 'showMeasure': True},
        'tooltip': {'mode': 'details'},
        'layers': [{'type': 'markers', 'name': 'Recorded GPS points', 'filterData': {'id': 'byRefId', 'options': 'N10'},
                    'location': {'mode': 'coords', 'latitude': 'Latitude', 'longitude': 'Longitude'},
                    'config': {'showLegend': False, 'style': {'color': {'fixed': '#A35220'}, 'size': {'fixed': 3, 'min': 3, 'max': 3},
                               'opacity': 0.85, 'symbol': {'mode': 'fixed', 'fixed': 'img/icons/marker/circle.svg'}}}, 'tooltip': True}]}
    return panels


def build():
    panel = ROOT / 'panels/race'
    dashboard = json.loads((panel / 'dashboard.template.json').read_text())
    options = dashboard['panels'][0]['options']
    options['html'] = (panel / 'panel.html').read_text().replace('__SPRITE_DATA__', '')
    options['css'] = (panel / 'panel.css').read_text()
    options['onInit'] = (panel / 'on-init.js').read_text()
    options['codeData'] = json.dumps({'tileUrl': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', 'sprite': 'data:image/png;base64,' + base64.b64encode((ROOT / 'assets/runner-and-miles.png').read_bytes()).decode()})
    dashboard['panels'].extend(native_panels())
    dashboard['graphTooltip'] = 1
    dashboard['timepicker']['hidden'] = False
    return json.dumps(dashboard, ensure_ascii=False, indent=2) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    out = ROOT / 'dashboards/burro-race-replay.json'
    expected = build()
    if args.check:
        if not out.exists() or out.read_text() != expected:
            raise SystemExit('Dashboard export is outdated. Run python3 scripts/build_dashboard.py')
    else:
        out.parent.mkdir(exist_ok=True)
        out.write_text(expected)
    print(('Verified ' if args.check else 'Built ') + str(out.relative_to(ROOT)))


if __name__ == '__main__':
    main()
