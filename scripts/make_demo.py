#!/usr/bin/env python3
"""Create an explicitly fictional 10 km race for dashboard development; no user data."""
import argparse
import math
from pathlib import Path

from import_workout import ImportError, assemble_race, haversine, timestamp, write_database


def demo_data():
    start = timestamp('2026-01-01T16:00:00Z')
    sample_count, interval, pause_length, pause_index = 600, 8, 120, 270
    # An invented loop anchored at an arbitrary coordinate, not a real race course.
    origin_lat, origin_lon = 39.65, -106.30
    coordinates = []
    for i in range(sample_count + 1):
        phase = 2 * math.pi * i / sample_count
        x = math.cos(phase) + 0.18 * math.sin(3 * phase)
        y = 0.74 * math.sin(phase) + 0.13 * math.sin(2 * phase)
        coordinates.append((x, y))
    radius = 1700.0
    for _ in range(4):
        raw = []
        for i, (x, y) in enumerate(coordinates):
            elapsed = i * interval + (pause_length if i > pause_index else 0)
            phase = 2 * math.pi * i / sample_count
            raw.append(dict(t=start+elapsed, lat=origin_lat+y*radius/111195,
                            lon=origin_lon+x*radius/(111195*math.cos(math.radians(origin_lat))),
                            elevation_m=2410 + 38*math.sin(phase) + 13*math.sin(3*phase), raw_segment=0))
        included_distance = sum(haversine(a,b) for i,(a,b) in enumerate(zip(raw,raw[1:])) if i != pause_index)
        radius *= 10000/included_distance
    end = raw[-1]['t']
    hr = []
    for elapsed in range(0, int(end-start)+1, 16):
        progress = elapsed/(end-start)
        bpm = round(141 + 19*(1-math.exp(-progress*6)) + 7*math.sin(progress*5*math.pi) + 2*math.sin(progress*31), 1)
        hr.append(dict(elapsed_s=elapsed, bpm=bpm, source='Fictional demo generator'))
    pauses = [(start+pause_index*interval, start+pause_index*interval+pause_length)]
    warnings = ['FICTIONAL PREVIEW: invented coordinates, timing, elevation, heart rate and calories; this is not the user’s recorded race.',
                'A fictional two-minute pause demonstrates elapsed-time replay and a route discontinuity.']
    return assemble_race('Burro Dash · fictional 10K preview', 'HKWorkoutActivityTypeRunning', start, end,
                         raw, hr, pauses, warnings, 'Fictional demo generator',
                         active_s=sample_count*interval, recorded_distance_m=10000, energy_kcal=840, synthetic=1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='runtime/workout.sqlite')
    parser.add_argument('--replace', action='store_true', help='Explicitly replace an existing workout database')
    args = parser.parse_args(argv)
    try:
        data = demo_data()
        path = write_database(Path(args.db), data, args.replace)
    except (ImportError, OSError) as exc:
        parser.exit(2, f'Demo failed: {exc}\n')
    print(f'FICTIONAL PREVIEW ONLY: {data[0]["point_count"]} route points, {data[0]["gps_distance_m"]:.1f} m, {data[0]["elapsed_s"]/60:.0f} minutes.')
    print(f'Database: {path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
