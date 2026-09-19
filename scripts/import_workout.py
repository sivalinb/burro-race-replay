#!/usr/bin/env python3
"""Import one Apple Health workout (or timestamped GPX) into a local SQLite database."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
import zipfile

UTC = timezone.utc
GAP_SECONDS = 60.0
MAX_ROUTE_SPEED_M_S = 25.0
HR_NEARBY_SECONDS = 30.0
PACE_WINDOW_SECONDS = 30.0
SCHEMA = """
CREATE TABLE race (
 id INTEGER PRIMARY KEY CHECK(id=1), title TEXT NOT NULL, activity_type TEXT,
 start_time TEXT NOT NULL, end_time TEXT NOT NULL, elapsed_s REAL NOT NULL,
 active_s REAL, recorded_distance_m REAL, gps_distance_m REAL, elevation_gain_m REAL,
 avg_hr_bpm REAL, max_hr_bpm REAL, energy_kcal REAL, source TEXT,
 synthetic INTEGER NOT NULL CHECK(synthetic IN (0,1)), point_count INTEGER NOT NULL,
 hr_count INTEGER NOT NULL, warnings_json TEXT NOT NULL, started_at_epoch_s REAL NOT NULL,
 pauses_json TEXT NOT NULL, device_label TEXT, device_metadata_json TEXT NOT NULL,
 source_version TEXT, recorded_start_time TEXT
);
CREATE TABLE route_points (
 seq INTEGER PRIMARY KEY, elapsed_s REAL NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
 elevation_m REAL, distance_m REAL NOT NULL, hr_bpm REAL, pace_s_km REAL,
 segment INTEGER NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE splits (
 km INTEGER PRIMARY KEY, distance_m REAL NOT NULL, elapsed_s REAL NOT NULL,
 pace_s_km REAL, avg_hr_bpm REAL, elevation_gain_m REAL,
 is_partial INTEGER NOT NULL CHECK(is_partial IN (0,1))
);
CREATE TABLE heart_rate (elapsed_s REAL NOT NULL, bpm REAL NOT NULL, source TEXT);
CREATE INDEX heart_rate_time ON heart_rate(elapsed_s);
PRAGMA user_version=2;
"""


class ImportError(ValueError):
    """A useful input error that the CLI can show without a traceback."""


def local_name(tag):
    return tag.rsplit('}', 1)[-1]


def finite(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def timestamp(value):
    if not value:
        raise ImportError('Missing timestamp.')
    try:
        normalized = value.strip().replace('Z', '+00:00')
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            # Apple Health uses a space before its compact numeric UTC offset.
            # Python 3.9's fromisoformat does not accept that form.
            fmt = '%Y-%m-%d %H:%M:%S.%f %z' if '.' in normalized else '%Y-%m-%d %H:%M:%S %z'
            parsed = datetime.strptime(normalized, fmt)
        if parsed.tzinfo is None:
            raise ValueError('timezone required')
        epoch = parsed.timestamp()
        if not math.isfinite(epoch):
            raise ValueError('non-finite timestamp')
        return epoch
    except (ValueError, OverflowError, OSError) as exc:
        raise ImportError(f'Invalid timestamp with explicit timezone required: {value!r}') from exc


def iso(epoch):
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def convert(value, unit, kind):
    number = finite(value)
    if number is None or number < 0:
        return None
    scales = {
        'distance': {'m': 1, 'km': 1000, 'mi': 1609.344, 'ft': 0.3048, 'yd': 0.9144},
        'duration': {'s': 1, 'sec': 1, 'min': 60, 'hr': 3600, 'h': 3600},
        'energy': {'kcal': 1, 'Cal': 1, 'cal': 0.001, 'kJ': 1 / 4.184, 'J': 1 / 4184},
        'hr': {'count/min': 1, 'bpm': 1},
    }
    scale = scales[kind].get(unit)
    return number * scale if scale is not None else None


def safe_member(value, *, reference=False):
    """Reject traversal and URLs; allow Apple's /workout-routes/... reference convention."""
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise ImportError('Unsafe archive or route path.')
    if value.startswith('//') or ':' in value or '?' in value or '#' in value:
        raise ImportError(f'Network/unsafe route reference is not allowed: {value!r}')
    if value.startswith('/'):
        if reference and value.startswith('/workout-routes/'):
            value = value[1:]
        else:
            raise ImportError(f'Absolute archive/route path is not allowed: {value!r}')
    parts = PurePosixPath(value).parts
    if '..' in parts:
        raise ImportError(f'Path traversal is not allowed: {value!r}')
    return str(PurePosixPath(value))


class HealthInput:
    """Reopen streams for two streaming XML passes; never extract an archive."""
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.archive = None
        self.xml_member = None
        self.xml_path = None
        if self.path.is_file() and zipfile.is_zipfile(self.path):
            self.archive = zipfile.ZipFile(self.path)
            names = self.archive.namelist()
            try:
                for name in names:
                    safe_member(name)
            except ImportError:
                self.close()
                raise
            exports = [name for name in names if PurePosixPath(name).name == 'export.xml']
            if len(exports) != 1:
                self.close()
                raise ImportError('ZIP must contain exactly one export.xml.')
            self.xml_member = exports[0]
            self.root = PurePosixPath(self.xml_member).parent
        elif self.path.is_dir():
            candidates = [self.path / 'export.xml', self.path / 'apple_health_export' / 'export.xml']
            found = [p for p in candidates if p.is_file()]
            if len(found) != 1:
                raise ImportError('Directory must contain export.xml or apple_health_export/export.xml, uniquely.')
            self.xml_path = found[0]
            self.root = self.xml_path.parent.resolve()
        elif self.path.is_file() and self.path.suffix.lower() == '.xml':
            self.xml_path = self.path
            self.root = self.path.parent
        else:
            raise ImportError('Input must be a Health ZIP, export.xml, extracted export directory, or .gpx file.')

    def close(self):
        if self.archive:
            self.archive.close()

    @contextmanager
    def xml(self):
        stream = self.archive.open(self.xml_member) if self.archive else self.xml_path.open('rb')
        with stream:
            yield stream

    @contextmanager
    def route(self, reference):
        rel = safe_member(reference, reference=True)
        if self.archive:
            name = str(self.root / rel)
            if name not in self.archive.namelist():
                raise FileNotFoundError(f'Linked GPX not found: {rel}')
            with self.archive.open(name) as stream:
                yield stream
        else:
            file = (self.root / rel).resolve()
            if not file.is_relative_to(self.root):
                raise ImportError('Linked route escapes the export directory (including via symlink).')
            with file.open('rb') as stream:
                yield stream


def health_entities(stream):
    """Keep nested Workout children until its end tag; discard each top-level entity."""
    stack = []
    for event, element in ET.iterparse(stream, events=('start', 'end')):
        if event == 'start':
            stack.append(element)
            continue
        if len(stack) == 2:
            yield element
            stack[0].remove(element)
        stack.pop()


@dataclass
class Workout:
    index: int
    attributes: dict
    routes: list
    events: list
    statistics: list

    @property
    def start(self):
        return timestamp(self.attributes.get('startDate'))

    @property
    def end(self):
        return timestamp(self.attributes.get('endDate'))


def list_workouts(source):
    workouts = []
    with source.xml() as stream:
        for element in health_entities(stream):
            if local_name(element.tag) != 'Workout':
                continue
            routes, events, stats = [], [], []
            for child in element.iter():
                name = local_name(child.tag)
                if name == 'FileReference' and child.get('path'):
                    routes.append(child.get('path'))
                elif name == 'WorkoutEvent':
                    events.append(dict(child.attrib))
                elif name == 'WorkoutStatistics':
                    stats.append(dict(child.attrib))
            workouts.append(Workout(len(workouts) + 1, dict(element.attrib), list(dict.fromkeys(routes)), events, stats))
    return workouts


def select_workout(workouts, index=None, date=None, latest_running=False):
    matching = workouts
    if date:
        try:
            datetime.strptime(date, '%Y-%m-%d')
        except ValueError as exc:
            raise ImportError('--date must be YYYY-MM-DD.') from exc
        matching = [w for w in matching if w.attributes.get('startDate', '')[:10] == date]
    if index is not None:
        matching = [w for w in matching if w.index == index]
    if latest_running:
        if index is not None:
            raise ImportError('--latest-running cannot be combined with --workout.')
        matching = [w for w in matching if w.attributes.get('workoutActivityType') == 'HKWorkoutActivityTypeRunning']
        if matching:
            latest_start = max(w.start for w in matching)
            matching = [w for w in matching if w.start == latest_start]
    if not matching:
        raise ImportError('No workout matches the requested selection. Use --list.')
    if len(matching) != 1:
        raise ImportError(f'{len(matching)} workouts match; use --list then --workout N. No workout was selected.')
    selected = matching[0]
    if selected.end <= selected.start:
        raise ImportError('Workout end must be later than start.')
    return selected


def device_metadata(workout, device_label=None):
    """Keep exported hardware/software facts, never the HKDevice pointer or unique IDs."""
    raw = workout.attributes.get('device') or ''
    # A comma can be part of a hardware value, e.g. Watch7,12; split on named fields.
    aliases = {
        'manufacturer': 'manufacturer', 'model': 'model',
        'hardware': 'hardware', 'hardwareVersion': 'hardware', 'hardware version': 'hardware',
        'firmware': 'firmware', 'firmwareVersion': 'firmware', 'firmware version': 'firmware',
        'software': 'software', 'softwareVersion': 'software', 'software version': 'software',
    }
    exported = {}
    for match in re.finditer(r'(?:^|,\s*|>\s*,?\s*)([A-Za-z][A-Za-z0-9_ ]*?)\s*:\s*(.*?)(?=,\s*[A-Za-z][A-Za-z0-9_ ]*\s*:|$)', raw):
        key, value = match.groups()
        if key in aliases and value.strip():
            exported[aliases[key]] = value.strip().rstrip('>').strip()
    return {
        'label_provenance': 'user-provided' if device_label else None,
        'export_metadata_available': bool(raw),
        'exported_device': exported,
        'unique_identifiers_retained': False,
    }


def parse_pauses(workout, warnings):
    pairs = []
    events = []
    for e in workout.events:
        typ = e.get('type', '').lower()
        state = 'pause' if typ.endswith('pause') or typ.endswith('paused') else 'resume' if typ.endswith('resume') or typ.endswith('resumed') else None
        if state:
            try:
                events.append((timestamp(e.get('date') or e.get('startDate')), state))
            except ImportError:
                warnings.append('A pause/resume event had an invalid timestamp and was ignored.')
    opened = None
    for time, state in sorted(events):
        time = max(workout.start, min(workout.end, time))
        if state == 'pause' and opened is None:
            opened = time
        elif state == 'resume' and opened is not None:
            if time > opened:
                pairs.append((opened, time))
            opened = None
    if opened is not None:
        pairs.append((opened, workout.end))
        warnings.append('An unmatched pause extends to workout end.')
    return pairs


def parse_gpx(stream, warnings, segment_offset=0):
    points = []
    segment = segment_offset - 1
    skipped = 0
    for event, element in ET.iterparse(stream, events=('start', 'end')):
        name = local_name(element.tag)
        if event == 'start' and name in ('trkseg', 'rte'):
            segment += 1
        if event != 'end' or name not in ('trkpt', 'rtept'):
            continue
        latitude, longitude = finite(element.get('lat')), finite(element.get('lon'))
        fields = {local_name(child.tag): child.text for child in element}
        elevation = finite(fields.get('ele'))
        try:
            at = timestamp(fields.get('time'))
            if latitude is None or longitude is None or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ImportError('Invalid coordinate.')
        except ImportError:
            skipped += 1
        else:
            points.append({'t': at, 'lat': latitude, 'lon': longitude, 'elevation_m': elevation, 'raw_segment': max(segment_offset, segment)})
        element.clear()
    if skipped:
        warnings.append(f'{skipped} GPX points with missing/invalid timestamps or coordinates were skipped.')
    return points


def haversine(a, b):
    lat1, lat2 = math.radians(a['lat']), math.radians(b['lat'])
    dlat, dlon = lat2 - lat1, math.radians(b['lon'] - a['lon'])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


def read_heart_rate(source, workout, warnings):
    samples = []
    invalid = 0
    with source.xml() as stream:
        for e in health_entities(stream):
            if local_name(e.tag) != 'Record' or e.get('type') != 'HKQuantityTypeIdentifierHeartRate':
                continue
            try:
                start, end = timestamp(e.get('startDate')), timestamp(e.get('endDate') or e.get('startDate'))
            except ImportError:
                continue  # Do not disclose unrelated invalid health records.
            if end < workout.start or start > workout.end:
                continue
            bpm = convert(e.get('value'), e.get('unit'), 'hr')
            if end < start or bpm is None or bpm <= 0 or bpm > 300:
                invalid += 1
                continue
            samples.append({'elapsed_s': max(start, workout.start) - workout.start, 'bpm': bpm, 'source': e.get('sourceName', '')})
    preferred = [s for s in samples if s['source'] == workout.attributes.get('sourceName')]
    if preferred:
        samples = preferred
    elif samples:
        warnings.append('No heart-rate samples matched the workout source; overlapping samples from available sources were used.')
    if invalid:
        warnings.append(f'{invalid} invalid overlapping heart-rate samples were skipped.')
    samples.sort(key=lambda s: s['elapsed_s'])
    return samples


def prepare_route(raw, start, end, heart_rate, pauses, warnings):
    raw = sorted((p for p in raw if start <= p['t'] <= end), key=lambda p: p['t'])
    points, total, segment, hr_index, window_start = [], 0.0, 0, 0, 0
    duplicates, gaps = 0, 0
    for p in raw:
        elapsed = p['t'] - start
        previous = points[-1] if points else None
        connected = False
        if previous:
            dt = elapsed - previous['elapsed_s']
            if dt <= 0:
                duplicates += 1
                continue
            distance = haversine(previous, p)
            crosses_pause = any(previous['t'] < pause_end and p['t'] > pause_start for pause_start, pause_end in pauses)
            connected = p['raw_segment'] == previous['raw_segment'] and dt <= GAP_SECONDS and distance / dt <= MAX_ROUTE_SPEED_M_S and not crosses_pause
            if connected:
                total += distance
            else:
                segment += 1
                window_start = len(points)
                gaps += 1
        while hr_index + 1 < len(heart_rate) and heart_rate[hr_index + 1]['elapsed_s'] <= elapsed:
            hr_index += 1
        nearby = heart_rate[max(0, hr_index - 1):hr_index + 2]
        nearest = min(nearby, key=lambda h: abs(h['elapsed_s'] - elapsed)) if nearby else None
        bpm = nearest['bpm'] if nearest and abs(nearest['elapsed_s'] - elapsed) <= HR_NEARBY_SECONDS else None
        while window_start + 1 < len(points) and points[window_start + 1]['elapsed_s'] < elapsed - PACE_WINDOW_SECONDS:
            window_start += 1
        pace = None
        if connected and window_start < len(points):
            anchor = points[window_start]
            delta_distance = total - anchor['distance_m']
            if delta_distance >= 5:
                pace = (elapsed - anchor['elapsed_s']) / delta_distance * 1000
        point = dict(p, seq=len(points), elapsed_s=elapsed, distance_m=total, hr_bpm=bpm, pace_s_km=pace, segment=segment, recorded_at=iso(p['t']))
        points.append(point)
    if duplicates:
        warnings.append(f'{duplicates} duplicate GPX timestamps were dropped.')
    if gaps:
        warnings.append(f'{gaps} route connections were omitted across segments, pauses, gaps >60s, or jumps >25m/s.')
    return points


def elevation_gain(points):
    deltas = [max(0, b['elevation_m'] - a['elevation_m']) for a, b in zip(points, points[1:])
              if a['segment'] == b['segment'] and a['elevation_m'] is not None and b['elevation_m'] is not None]
    return sum(deltas) if deltas else None


def make_splits(points, hr):
    if not points or points[-1]['distance_m'] <= 0:
        return []
    total = points[-1]['distance_m']
    boundaries = [float(n * 1000) for n in range(1, int(total // 1000) + 1)]
    if not boundaries or total - boundaries[-1] > 0.000001:
        boundaries.append(total)
    results, previous_distance, previous_time, edge = [], 0.0, 0.0, 1
    for km, boundary in enumerate(boundaries, 1):
        while edge < len(points) and points[edge]['distance_m'] < boundary - 0.000001:
            edge += 1
        b = points[min(edge, len(points) - 1)]
        a = points[max(0, min(edge, len(points) - 1) - 1)]
        dd = b['distance_m'] - a['distance_m']
        time = a['elapsed_s'] + ((boundary - a['distance_m']) / dd) * (b['elapsed_s'] - a['elapsed_s']) if dd > 0 else b['elapsed_s']
        distance = boundary - previous_distance
        samples = [s['bpm'] for s in hr if previous_time <= s['elapsed_s'] < time or (km == len(boundaries) and s['elapsed_s'] == time)]
        gain, have_elevation = 0.0, False
        for left, right in zip(points, points[1:]):
            length = right['distance_m'] - left['distance_m']
            overlap = min(boundary, right['distance_m']) - max(previous_distance, left['distance_m'])
            if length > 0 and overlap > 0 and left['segment'] == right['segment'] and left['elevation_m'] is not None and right['elevation_m'] is not None:
                have_elevation = True
                gain += max(0, right['elevation_m'] - left['elevation_m']) * overlap / length
        duration = time - previous_time
        results.append(dict(km=km, distance_m=distance, elapsed_s=duration, pace_s_km=duration / distance * 1000,
                            avg_hr_bpm=sum(samples) / len(samples) if samples else None,
                            elevation_gain_m=gain if have_elevation else None, is_partial=int(distance < 999.999999)))
        previous_distance, previous_time = boundary, time
    return results


def stats_value(workout, kind, field='sum'):
    types = {
        'distance': {'HKQuantityTypeIdentifierDistanceWalkingRunning', 'HKQuantityTypeIdentifierDistanceCycling', 'HKQuantityTypeIdentifierDistanceSwimming'},
        'energy': {'HKQuantityTypeIdentifierActiveEnergyBurned'},
        'hr': {'HKQuantityTypeIdentifierHeartRate'},
    }
    for stat in workout.statistics:
        if stat.get('type') in types[kind]:
            value = convert(stat.get(field), stat.get('unit'), kind)
            if value is not None:
                return value
    return None


def assemble_race(title, activity_type, start, end, raw, hr, pauses, warnings, source,
                  active_s=None, recorded_distance_m=None, energy_kcal=None, synthetic=0,
                  fallback_avg_hr=None, fallback_max_hr=None, device_label=None,
                  device_metadata_json=None, source_version=None, recorded_start_time=None):
    points = prepare_route(raw, start, end, hr, pauses, warnings)
    if not points:
        warnings.append('No timestamped route points are available for this workout; route replay is unavailable.')
    if not hr:
        warnings.append('No overlapping heart-rate samples are available; route heart rate is null.')
    race = dict(id=1, title=title, activity_type=activity_type, start_time=iso(start), end_time=iso(end), elapsed_s=end-start,
                active_s=active_s, recorded_distance_m=recorded_distance_m,
                gps_distance_m=points[-1]['distance_m'] if len(points) > 1 else None, elevation_gain_m=elevation_gain(points),
                avg_hr_bpm=sum(h['bpm'] for h in hr) / len(hr) if hr else fallback_avg_hr,
                max_hr_bpm=max((h['bpm'] for h in hr), default=fallback_max_hr), energy_kcal=energy_kcal,
                source=source, synthetic=int(synthetic), point_count=len(points), hr_count=len(hr),
                warnings_json=json.dumps(warnings), started_at_epoch_s=start,
                pauses_json=json.dumps([{'start_s': a-start, 'end_s': b-start} for a, b in pauses]),
                device_label=device_label, device_metadata_json=device_metadata_json or '{}',
                source_version=source_version, recorded_start_time=recorded_start_time)
    return race, points, make_splits(points, hr), hr


def import_health(source, workout, title=None, device_label=None):
    warnings = []
    start, end = workout.start, workout.end
    pauses = parse_pauses(workout, warnings)
    raw = []
    for ref in workout.routes:
        offset = max((p['raw_segment'] for p in raw), default=-1) + 1
        try:
            with source.route(ref) as stream:
                raw.extend(parse_gpx(stream, warnings, offset))
        except FileNotFoundError:
            warnings.append('A GPX route linked by this workout was not found in the export.')
    hr = read_heart_rate(source, workout, warnings)
    attrs = workout.attributes
    active = convert(attrs.get('duration'), attrs.get('durationUnit'), 'duration')
    if active is None:
        active = end-start-sum(b-a for a,b in pauses) if pauses else None
    elif active > end-start + 1:
        warnings.append('Reported workout duration exceeds elapsed time; active duration is unavailable.')
        active = None
    recorded = convert(attrs.get('totalDistance'), attrs.get('totalDistanceUnit'), 'distance')
    if recorded is None:
        recorded = stats_value(workout, 'distance')
    energy = convert(attrs.get('totalEnergyBurned'), attrs.get('totalEnergyBurnedUnit'), 'energy')
    if energy is None:
        energy = stats_value(workout, 'energy')
    return assemble_race(title or 'Burro race · Apple Watch workout', attrs.get('workoutActivityType'), start, end,
                         raw, hr, pauses, warnings, attrs.get('sourceName'), active, recorded, energy,
                         fallback_avg_hr=stats_value(workout, 'hr', 'average'), fallback_max_hr=stats_value(workout, 'hr', 'maximum'),
                         device_label=device_label, device_metadata_json=json.dumps(device_metadata(workout, device_label)),
                         source_version=attrs.get('sourceVersion'), recorded_start_time=attrs.get('startDate'))


def import_gpx(path, title=None, device_label=None):
    warnings = ['GPX-only import: Apple-reported distance, active duration, calories, and heart-rate samples are unavailable.']
    with Path(path).open('rb') as stream:
        raw = parse_gpx(stream, warnings)
    if len(raw) < 2:
        raise ImportError('GPX requires at least two valid timestamped points.')
    start, end = min(p['t'] for p in raw), max(p['t'] for p in raw)
    if end <= start:
        raise ImportError('GPX timestamps must span a positive duration.')
    return assemble_race(title or 'Burro race · GPX workout', 'GPX route (activity unspecified)', start, end,
                         raw, [], [], warnings, 'GPX import', device_label=device_label,
                         device_metadata_json=json.dumps({'label_provenance': 'user-provided' if device_label else None,
                                                          'export_metadata_available': False, 'exported_device': {},
                                                          'unique_identifiers_retained': False}))


def write_database(path, data, replace=False):
    dest = Path(path).expanduser().resolve()
    if dest.exists() and not replace:
        raise ImportError(f'Database already exists: {dest}. Use --replace to replace this single-workout database.')
    if any(Path(str(dest) + suffix).exists() for suffix in ('-wal', '-shm')):
        raise ImportError('Database has SQLite WAL/SHM sidecars. Stop its writer and checkpoint before replacing it.')
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + dest.name + '.', suffix='.tmp', dir=dest.parent)
    os.close(fd)
    try:
        with sqlite3.connect(temporary) as con:
            con.executescript(SCHEMA)
            race, points, splits, hr = data
            for table, rows in (('race', [race]), ('route_points', points), ('splits', splits), ('heart_rate', hr)):
                allowed = [r[1] for r in con.execute(f'PRAGMA table_info({table})')]
                keys = allowed
                if rows:
                    con.executemany(f'INSERT INTO {table} ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})',
                                    [[row.get(k) for k in keys] for row in rows])
            con.commit()
            result = con.execute('PRAGMA integrity_check').fetchone()[0]
            if result != 'ok':
                raise ImportError(f'SQLite integrity check failed: {result}')
        os.chmod(temporary, 0o600)
        if replace:
            os.replace(temporary, dest)
        else:
            # Atomic no-clobber creation; a concurrent import cannot overwrite a new file.
            try:
                os.link(temporary, dest)
            except FileExistsError as exc:
                raise ImportError('Database appeared during import; refusing to overwrite it without --replace.') from exc
            os.unlink(temporary)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return dest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', help='Health export ZIP, export.xml, extracted directory, or timestamped GPX')
    parser.add_argument('--list', action='store_true', help='List workouts without importing health records')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--workout', type=int, help='1-based workout index shown by --list')
    selection.add_argument('--latest-running', action='store_true', help='Explicitly select the chronologically latest Running workout only')
    parser.add_argument('--date', help='Filter by workout start date in its recorded timezone (YYYY-MM-DD)')
    parser.add_argument('--db', default='runtime/workout.sqlite', help='Single-workout SQLite output')
    parser.add_argument('--replace', action='store_true', help='Explicitly replace an existing single-workout database')
    parser.add_argument('--title', help='Optional display title')
    parser.add_argument('--device-label', help='User-provided device name, kept distinct from exported hardware metadata')
    args = parser.parse_args(argv)
    source = None
    try:
        if args.list and args.latest_running:
            raise ImportError('--latest-running imports one selected run; use --list separately to inspect workout metadata.')
        path = Path(args.input).expanduser()
        if path.suffix.lower() == '.gpx':
            if args.list or args.workout is not None or args.date or args.latest_running:
                raise ImportError('GPX contains one route; --list, --workout, --latest-running and --date apply only to Health exports.')
            data = import_gpx(path, args.title, args.device_label)
        else:
            source = HealthInput(path)
            workouts = list_workouts(source)
            if args.list:
                for w in workouts:
                    if args.date and w.attributes.get('startDate', '')[:10] != args.date:
                        continue
                    a = w.attributes
                    print(f"{w.index}\t{a.get('startDate', '?')}\t{a.get('workoutActivityType', '?')}\t{a.get('duration', '?')} {a.get('durationUnit', '')}\t{len(w.routes)} linked route(s)\t{a.get('sourceName', '')}")
                return 0
            selected = select_workout(workouts, args.workout, args.date, args.latest_running)
            data = import_health(source, selected, args.title, args.device_label)
        destination = write_database(args.db, data, args.replace)
        race = data[0]
        print(f'Imported 1 workout: {race["point_count"]} route points, {race["hr_count"]} heart-rate samples.')
        print(f'Database: {destination}')
        for warning in json.loads(race['warnings_json']):
            print(f'Note: {warning}')
        return 0
    except (ImportError, ET.ParseError, OSError, zipfile.BadZipFile, sqlite3.Error) as exc:
        parser.exit(2, f'Import failed: {exc}\n')
    finally:
        if source:
            source.close()


if __name__ == '__main__':
    raise SystemExit(main())
