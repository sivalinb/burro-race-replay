"""Behavioral tests with generated, non-personal Health/GPX fixtures."""
from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import import_workout as importer
from make_demo import demo_data


def gpx(points, second_segment_at=None):
    result = ['<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>']
    for i, p in enumerate(points):
        if i == second_segment_at:
            result.append('</trkseg><trkseg>')
        t, lat, lon, elevation = p
        ele = f'<ele>{elevation}</ele>' if elevation is not None else ''
        time = f'<time>{t}</time>' if t is not None else ''
        result.append(f'<trkpt lat="{lat}" lon="{lon}">{ele}{time}</trkpt>')
    return ''.join(result) + '</trkseg></trk></gpx>'


def stamp(seconds):
    return importer.iso(importer.timestamp('2026-01-01T16:00:00Z') + seconds)


def health_xml(extra_workout='', route='/workout-routes/run.gpx'):
    return f'''<?xml version="1.0"?><HealthData locale="en_US">
    <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Watch" unit="count/min" startDate="2026-01-01 09:59:59 -0600" endDate="2026-01-01 09:59:59 -0600" value="99"/>
    <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Watch" unit="count/min" startDate="2026-01-01 10:00:00 -0600" endDate="2026-01-01 10:00:00 -0600" value="150"/>
    <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Other" unit="count/min" startDate="2026-01-01 10:00:30 -0600" endDate="2026-01-01 10:00:30 -0600" value="210"/>
    <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Watch" unit="count/min" startDate="2026-01-01 10:01:00 -0600" endDate="2026-01-01 10:01:00 -0600" value="160"/>
    <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Watch" unit="count/min" startDate="2026-01-01 10:02:01 -0600" endDate="2026-01-01 10:02:01 -0600" value="190"/>
    <Record type="HKQuantityTypeIdentifierStepCount" value="999999" sourceName="Watch"/>
    <Workout workoutActivityType="HKWorkoutActivityTypeRunning" sourceName="Watch" startDate="2026-01-01 10:00:00 -0600" endDate="2026-01-01 10:02:00 -0600" duration="1.5" durationUnit="min" totalDistance="0.1" totalDistanceUnit="mi">
      <WorkoutStatistics type="HKQuantityTypeIdentifierActiveEnergyBurned" sum="41.84" unit="kJ"/>
      <WorkoutEvent type="HKWorkoutEventTypePause" date="2026-01-01 10:00:30 -0600"/>
      <WorkoutEvent type="HKWorkoutEventTypeResume" date="2026-01-01 10:01:00 -0600"/>
      <WorkoutRoute sourceName="Watch"><FileReference path="{route}"/></WorkoutRoute>
    </Workout>{extra_workout}</HealthData>'''


class ImportWorkoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make_health(self, zipped=False, extra_workout='', route='/workout-routes/run.gpx'):
        xml = health_xml(extra_workout, route)
        route_data = gpx([(stamp(n), 40, -105 + n / 100000, 1600 + n/10) for n in (0, 15, 30, 60, 90, 120)])
        if zipped:
            path = self.root / 'export.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('apple_health_export/export.xml', xml)
                archive.writestr('apple_health_export/workout-routes/run.gpx', route_data)
            return path
        path = self.root / 'export.xml'
        path.write_text(xml)
        routes = self.root / 'workout-routes'
        routes.mkdir(exist_ok=True)
        (routes / 'run.gpx').write_text(route_data)
        return path

    def test_health_zip_keeps_workout_children_and_limits_heart_rate(self):
        source = importer.HealthInput(self.make_health(zipped=True))
        self.addCleanup(source.close)
        workouts = importer.list_workouts(source)
        selected = importer.select_workout(workouts, index=1)
        self.assertEqual(selected.routes, ['/workout-routes/run.gpx'])
        self.assertEqual(len(selected.events), 2)
        race, points, splits, hr = importer.import_health(source, selected)
        self.assertEqual(race['point_count'], 6)
        self.assertEqual([h['bpm'] for h in hr], [150, 160])
        self.assertEqual(race['avg_hr_bpm'], 155)
        self.assertEqual(race['max_hr_bpm'], 160)
        self.assertEqual(race['start_time'], '2026-01-01T16:00:00.000Z')
        self.assertEqual(race['elapsed_s'], 120)
        self.assertEqual(race['active_s'], 90)
        self.assertAlmostEqual(race['recorded_distance_m'], 160.9344)
        self.assertAlmostEqual(race['energy_kcal'], 10)
        self.assertEqual(json.loads(race['pauses_json']), [{'start_s': 30, 'end_s': 60}])
        self.assertEqual(points[2]['distance_m'], points[3]['distance_m'])
        self.assertNotEqual(points[2]['segment'], points[3]['segment'])
        self.assertIsNone(points[-1]['hr_bpm'])  # Do not hold 160 bpm forever.
        self.assertNotAlmostEqual(race['recorded_distance_m'], race['gps_distance_m'])
        self.assertFalse((self.root / 'apple_health_export').exists())  # No ZIP extraction.

    def test_ambiguous_workout_is_never_auto_selected(self):
        second = '<Workout workoutActivityType="HKWorkoutActivityTypeRunning" sourceName="Watch" startDate="2026-01-02 10:00:00 -0600" endDate="2026-01-02 11:00:00 -0600"/>'
        source = importer.HealthInput(self.make_health(extra_workout=second))
        self.addCleanup(source.close)
        workouts = importer.list_workouts(source)
        with self.assertRaisesRegex(importer.ImportError, 'No workout was selected'):
            importer.select_workout(workouts)
        self.assertEqual(importer.select_workout(workouts, date='2026-01-02').index, 2)
        self.assertEqual(importer.select_workout(workouts, index=1, date='2026-01-01').index, 1)
        with self.assertRaises(importer.ImportError):
            importer.select_workout(workouts, index=1, date='2026-01-02')

    def test_latest_running_uses_absolute_time_and_excludes_other_activities(self):
        def workout(index, kind, start, end):
            return importer.Workout(index, {'workoutActivityType':kind, 'startDate':start, 'endDate':end}, [], [], [])
        running = 'HKWorkoutActivityTypeRunning'
        first = workout(1,running,'2026-01-01 10:00:00 -0600','2026-01-01 11:00:00 -0600')
        latest = workout(2,running,'2026-01-01 09:30:00 -0800','2026-01-01 10:30:00 -0800')
        other = workout(3,'HKWorkoutActivityTypeWalking','2026-01-02 10:00:00 -0600','2026-01-02 11:00:00 -0600')
        self.assertEqual(importer.select_workout([first,other,latest],latest_running=True).index,2)
        with self.assertRaises(importer.ImportError):
            importer.select_workout([other],latest_running=True)
        with self.assertRaisesRegex(importer.ImportError,'No workout was selected'):
            importer.select_workout([latest,latest],latest_running=True)

    def test_user_device_label_is_distinct_and_unique_identifiers_are_excluded(self):
        source = importer.HealthInput(self.make_health())
        self.addCleanup(source.close)
        workout = importer.list_workouts(source)[0]
        workout.attributes['sourceVersion'] = '27.0'
        workout.attributes['device'] = '<HKDevice: 0xPRIVATE>, name:Personal Watch, manufacturer:Apple Inc., model:Watch, hardware:Watch99,7, software:27.0, creation date:2026-01-01 00:00:00 +0000, localIdentifier:PRIVATE-ID, UDIDeviceIdentifier:SECRET>'
        race, _, _, _ = importer.import_health(source,workout,device_label='Apple Watch Ultra 4')
        self.assertEqual(race['device_label'],'Apple Watch Ultra 4')
        self.assertEqual(race['source_version'],'27.0')
        self.assertEqual(race['recorded_start_time'],'2026-01-01 10:00:00 -0600')
        metadata = json.loads(race['device_metadata_json'])
        self.assertEqual(metadata['label_provenance'],'user-provided')
        self.assertEqual(metadata['exported_device'],{'manufacturer':'Apple Inc.','model':'Watch','hardware':'Watch99,7','software':'27.0'})
        self.assertNotIn('PRIVATE',race['device_metadata_json'])
        self.assertNotIn('SECRET',race['device_metadata_json'])
        self.assertNotIn('Personal Watch',race['device_metadata_json'])

    def test_gpx_only_preserves_missing_metrics_and_rejects_bad_points(self):
        path = self.root / 'route.gpx'
        path.write_text(gpx([(stamp(0), 40, -105, None), (None, 40, -105.001, None),
                             (stamp(15), 'nan', -105, None), (stamp(30), 40, -105.001, None)]))
        race, points, splits, hr = importer.import_gpx(path)
        self.assertEqual(len(points), 2)
        for key in ('active_s', 'recorded_distance_m', 'elevation_gain_m', 'avg_hr_bpm', 'max_hr_bpm', 'energy_kcal'):
            self.assertIsNone(race[key], key)
        self.assertEqual(hr, [])
        self.assertIsNone(points[0]['pace_s_km'])
        self.assertIsNone(points[1]['hr_bpm'])
        self.assertIsNone(splits[0]['elevation_gain_m'])
        self.assertTrue(any('2 GPX points' in w for w in json.loads(race['warnings_json'])))
        self.assertEqual(race['synthetic'], 0)

    def test_route_gaps_explicit_segments_and_jumps_do_not_add_distance(self):
        start = importer.timestamp(stamp(0))
        def p(t, lon, segment=0):
            return dict(t=start+t, lat=0, lon=lon, elevation_m=0, raw_segment=segment)
        raw = [p(0,0), p(10,0.001), p(100,1), p(110,1.001), p(120,1.002,1), p(130,10,1)]
        warnings = []
        route = importer.prepare_route(raw, start, start+130, [], [], warnings)
        expected = 2 * 111.1950802335
        self.assertAlmostEqual(route[-1]['distance_m'], expected, places=5)
        self.assertEqual([p['segment'] for p in route], [0,0,1,1,2,3])
        self.assertIsNone(route[2]['pace_s_km'])
        self.assertTrue(any('3 route connections' in w for w in warnings))

    def test_splits_interpolate_kilometer_crossings_and_partial(self):
        points = []
        for i, distance in enumerate((0,600,1200,1800,2400,2500)):
            points.append(dict(seq=i, distance_m=distance, elapsed_s=distance*0.5,
                               elevation_m=100+distance/100, segment=0))
        hr = [dict(elapsed_s=100, bpm=140), dict(elapsed_s=600,bpm=160), dict(elapsed_s=1100,bpm=170)]
        splits = importer.make_splits(points, hr)
        self.assertEqual([s['distance_m'] for s in splits], [1000,1000,500])
        self.assertEqual([s['elapsed_s'] for s in splits], [500,500,250])
        self.assertEqual([s['pace_s_km'] for s in splits], [500,500,500])
        self.assertEqual([s['is_partial'] for s in splits], [0,0,1])
        self.assertEqual([s['avg_hr_bpm'] for s in splits], [140,160,170])
        self.assertAlmostEqual(sum(s['elevation_gain_m'] for s in splits), 25)

    def test_archive_and_route_paths_reject_traversal_or_network(self):
        for unsafe in ('../secret', '/etc/passwd', 'C:/secret', 'folder\\secret', '//example.com/x'):
            with self.subTest(unsafe=unsafe):
                path = self.root / 'unsafe.zip'
                with zipfile.ZipFile(path, 'w') as archive:
                    archive.writestr('export.xml', '<HealthData/>')
                    archive.writestr(unsafe, 'bad')
                with self.assertRaises(importer.ImportError):
                    importer.HealthInput(path)
        for unsafe in ('../secret.gpx', 'https://example.com/route.gpx', '/etc/route.gpx', '//example.com/route.gpx'):
            with self.subTest(reference=unsafe):
                source = importer.HealthInput(self.make_health(route=unsafe))
                try:
                    workout = importer.list_workouts(source)[0]
                    with self.assertRaises(importer.ImportError):
                        importer.import_health(source, workout)
                finally:
                    source.close()

    def test_symlink_route_cannot_escape_export_root(self):
        source = importer.HealthInput(self.make_health())
        self.addCleanup(source.close)
        route = self.root / 'workout-routes' / 'run.gpx'
        route.unlink()
        with tempfile.TemporaryDirectory() as elsewhere:
            target = Path(elsewhere) / 'other.gpx'
            target.write_text(gpx([(stamp(0),40,-105,0),(stamp(10),40,-105.001,0)]))
            route.symlink_to(target)
            with self.assertRaisesRegex(importer.ImportError, 'escapes'):
                importer.import_health(source, importer.list_workouts(source)[0])

    def test_timezones_finite_units_and_database_replacement(self):
        self.assertEqual(importer.timestamp('2026-01-01 10:00:00 -0600'), importer.timestamp('2026-01-01T16:00:00Z'))
        with self.assertRaises(importer.ImportError):
            importer.timestamp('2026-01-01T16:00:00')
        self.assertIsNone(importer.convert('nan','km','distance'))
        self.assertIsNone(importer.convert('1','furlong','distance'))
        self.assertIsNone(importer.convert('-1','m','distance'))
        self.assertAlmostEqual(importer.convert('1','mi','distance'),1609.344)
        data = demo_data()
        path = self.root / 'workout.sqlite'
        importer.write_database(path, data)
        before = path.read_bytes()
        with self.assertRaises(importer.ImportError):
            importer.write_database(path, data)
        self.assertEqual(before, path.read_bytes())
        importer.write_database(path, data, replace=True)
        with sqlite3.connect(path) as con:
            race = con.execute('SELECT synthetic,point_count,hr_count,gps_distance_m,elapsed_s FROM race').fetchone()
            self.assertEqual(race[0:3], (1,601,308))
            self.assertAlmostEqual(race[3],10000,places=4)
            self.assertEqual(race[4],4920)
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM route_points').fetchone()[0],601)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM splits').fetchone()[0],10)
            self.assertEqual(con.execute('SELECT SUM(is_partial) FROM splits').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM race').fetchone()[0],1)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
