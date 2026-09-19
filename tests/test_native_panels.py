"""Verify native Grafana queries against synthetic SQLite data, never personal files."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_dashboard as builder
import install_grafana as installer
from import_workout import write_database
from make_demo import demo_data


def api_response(ref, records):
    names = list(records[0]) if records else []
    return {'results': {ref: {'status': 200, 'frames': [
        {'schema': {'fields': [{'name': name} for name in names]},
         'data': {'values': [[row[name] for row in records] for name in names]}}
    ]}}}


class NativePanelsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'sample.sqlite'
        self.data = demo_data()
        write_database(self.db, self.data)
        self.con = sqlite3.connect(self.db)
        self.addCleanup(self.con.close)
        self.dashboard = json.loads(builder.build())

    def test_native_queries_preserve_units_and_absolute_seconds(self):
        panels = builder.native_panels()
        targets = {p['targets'][0]['refId']: p['targets'][0] for p in panels}
        rows = {ref: self.con.execute(q['queryText']).fetchall() for ref, q in targets.items()}
        self.assertEqual(len(panels), 10)
        self.assertEqual({p['type'] for p in panels}, {'stat','timeseries','barchart','table','geomap'})
        self.assertEqual(sum(p['type']=='stat' for p in panels),4)
        self.assertEqual(sum(p['type']=='timeseries' for p in panels),3)
        self.assertAlmostEqual(rows['N1'][0][0], 10)
        self.assertAlmostEqual(rows['N1'][0][1], 10)
        self.assertEqual(rows['N2'][0], (4920,4800))
        self.assertEqual(rows['N4'][0][0],840)
        start = self.data[0]['started_at_epoch_s']
        self.assertEqual(rows['N5'][0][0],start)
        self.assertEqual(rows['N6'][0][0],start)
        self.assertIsNone(rows['N6'][0][1])
        self.assertAlmostEqual(rows['N6'][1][1],self.data[1][1]['pace_s_km']/60)
        self.assertTrue(any(value is None for _,value in rows['N7']))  # Break at the demo pause.
        self.assertEqual(len(rows['N8']),10)
        self.assertAlmostEqual(rows['N8'][0][1],self.data[2][0]['pace_s_km']/60)
        self.assertEqual(rows['N9'][0][0:2],(1,'Full km'))
        self.assertEqual(len(rows['N10']),601)
        for ref in ('N5','N6','N7'):
            self.assertEqual(targets[ref]['queryType'],'time series')
            self.assertEqual(targets[ref]['timeColumns'],['time'])
            self.assertTrue(all(start <= row[0] <= start+4920 for row in rows[ref]))
        self.con.execute('DELETE FROM heart_rate WHERE elapsed_s > 96 AND elapsed_s < 208')
        hr = self.con.execute(targets['N5']['queryText']).fetchall()
        self.assertTrue(any(abs(time-(start+96.001))<0.0001 and value is None for time,value in hr))

    def test_installed_range_uses_workout_dates_without_changing_export(self):
        response = api_response('A',[{'start_time':'2026-01-01T10:00:00-06:00','end_time':'2026-01-01T11:00:00-06:00'}])
        before = builder.build()
        start,end = installer.apply_workout_time(self.dashboard,response)
        self.assertEqual(self.dashboard['time'],{'from':'2026-01-01T16:00:00.000Z','to':'2026-01-01T17:00:00.000Z'})
        self.assertEqual(end-start,3600000)
        self.assertGreater(start,1000000000000)  # Query API range is milliseconds.
        self.assertEqual(builder.build(),before)  # No selected-workout dates written to source/export.
        with self.assertRaises(RuntimeError):
            installer.apply_workout_time(self.dashboard,api_response('A',[{'start_time':'2026-01-01T10:00:00','end_time':'2026-01-01T11:00:00'}]))
        with self.assertRaises(RuntimeError):
            installer.apply_workout_time(self.dashboard,api_response('A',[{'start_time':'2026-01-01T12:00:00Z','end_time':'2026-01-01T11:00:00Z'}]))

    def test_geomap_queries_and_layers_cannot_bridge_segments(self):
        records = [{'segment': row['segment']} for row in self.data[1]]
        installer.configure_geomap_segments(self.dashboard,api_response('B',records))
        panel = next(p for p in self.dashboard['panels'] if p['type']=='geomap')
        self.assertEqual(len(panel['targets']),2)
        self.assertEqual(len(panel['options']['layers']),2)
        for index, (target,layer) in enumerate(zip(panel['targets'],panel['options']['layers'])):
            rows = self.con.execute(target['queryText']).fetchall()
            self.assertTrue(rows)
            self.assertEqual({r[-1] for r in rows},{index})
            self.assertEqual(layer['type'],'route')
            self.assertEqual(layer['filterData'],{'id':'byRefId','options':target['refId']})
            self.assertEqual(layer['location'],{'mode':'coords','latitude':'Latitude','longitude':'Longitude'})
        self.assertEqual(len(installer.dashboard_targets(self.dashboard,allow_segment_queries=True)),15)
        empty = json.loads(builder.build())
        installer.configure_geomap_segments(empty,api_response('B',[]))
        empty_map = next(p for p in empty['panels'] if p['type']=='geomap')
        self.assertEqual(empty_map['options']['layers'][0]['type'],'markers')

    def test_installer_rejects_bad_or_duplicate_native_queries(self):
        self.assertEqual(len(installer.dashboard_targets(self.dashboard)),14)
        for change in ('duplicate','wrong_source','wrong_type','mutation'):
            dashboard = deepcopy(self.dashboard)
            target = dashboard['panels'][1]['targets'][0]
            if change=='duplicate': target['refId']='A'
            if change=='wrong_source': target['datasource']['uid']='other'
            if change=='wrong_type': target['queryType']='time_series'
            if change=='mutation': target['rawQueryText']=target['queryText']='DELETE FROM race'
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                installer.dashboard_targets(dashboard)


if __name__ == '__main__':
    unittest.main()
