"""Build an importable Grafana dashboard with the local portrait embedded once."""
import argparse
import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    panel = ROOT / 'panels/race'
    dashboard = json.loads((panel / 'dashboard.template.json').read_text())
    options = dashboard['panels'][0]['options']
    options['html'] = (panel / 'panel.html').read_text().replace('__SPRITE_DATA__', '')
    options['css'] = (panel / 'panel.css').read_text()
    options['onInit'] = (panel / 'on-init.js').read_text()
    options['codeData'] = json.dumps({'tileUrl': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', 'sprite': 'data:image/png;base64,' + base64.b64encode((ROOT / 'assets/runner-and-miles.png').read_bytes()).decode()})
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
