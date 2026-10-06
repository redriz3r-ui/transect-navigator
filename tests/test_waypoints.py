"""Exact metre-based fixtures exercise the production stationing engine."""
import unittest
from math import atan2, degrees, hypot
from transect_navigator.navigation.waypoints import Transect, generate_waypoints, station_distances


class CartesianMetres:
    """Analytical test metric, not a replacement or mock for QGIS geodesy."""
    def distance(self, a, b):
        return hypot(b[0]-a[0], b[1]-a[1])

    def interpolate(self, a, b, distance):
        fraction = distance / self.distance(a, b)
        return (a[0]+fraction*(b[0]-a[0]), a[1]+fraction*(b[1]-a[1]))

    def bearing(self, a, b):
        return degrees(atan2(b[0]-a[0], b[1]-a[1])) % 360


class WaypointTests(unittest.TestCase):
    def make(self, vertices=((0, 0), (100, 0)), spacing=10, reverse=False):
        return generate_waypoints([Transect('T04', vertices)], spacing, CartesianMetres(), reverse)

    def test_100_metres(self):
        rows = self.make()
        self.assertEqual(len(rows), 11)
        self.assertEqual([r.distance_along_m for r in rows], list(range(0, 101, 10)))
        self.assertEqual([r.coordinate for r in rows], [(x, 0) for x in range(0, 101, 10)])
        self.assertEqual([r.sequence for r in rows], list(range(1, 12)))
        self.assertEqual([r.waypoint_id for r in rows], [f'T04_{n:03d}' for n in range(1, 12)])
        self.assertTrue(all(r.total_length_m == 100 for r in rows))
        self.assertTrue(all(r.bearing_next_deg == 90 for r in rows[:-1]))
        self.assertIsNone(rows[-1].bearing_next_deg)

    def test_remainder_and_short_line(self):
        rows = self.make(((0, 0), (105, 0)))
        self.assertEqual([r.distance_along_m for r in rows], [*range(0, 101, 10), 105])
        self.assertEqual(rows[-1].coordinate, (105, 0))
        self.assertEqual(len(self.make(((0, 0), (3, 0)))), 2)

    def test_reverse_geometry_and_option(self):
        forward = self.make()
        reverse = self.make(((100, 0), (0, 0)))
        self.assertEqual(reverse, self.make(reverse=True))
        self.assertEqual([r.coordinate for r in reverse], list(reversed([r.coordinate for r in forward])))
        self.assertTrue(all(r.bearing_next_deg == 270 for r in reverse[:-1]))
        self.assertIsNone(reverse[-1].bearing_next_deg)
        self.assertEqual(self.make(((0, 0), (105, 0)), reverse=True)[1].coordinate, (95, 0))

    def test_multiple_transects_and_stable_order(self):
        transects = [Transect('B', ((0, 0), (0, 20))), Transect('A', ((0, 0), (20, 0)))]
        rows = generate_waypoints(transects, 10, CartesianMetres())
        self.assertEqual(rows, generate_waypoints(list(reversed(transects)), 10, CartesianMetres()))
        self.assertEqual([r.waypoint_id for r in rows], ['A_001','A_002','A_003','B_001','B_002','B_003'])
        self.assertEqual([r.sequence for r in rows], [1,2,3,1,2,3])
        self.assertEqual(rows[3].bearing_next_deg, 0)

    def test_bend_bearing_points_to_next_waypoint(self):
        rows = self.make(((0, 0), (10, 0), (10, 10)), spacing=15)
        self.assertEqual(rows[1].coordinate, (10, 5))
        self.assertAlmostEqual(rows[0].bearing_next_deg, degrees(atan2(10, 5)))
        self.assertEqual(rows[1].bearing_next_deg, 0)

    def test_exact_vertex_station(self):
        self.assertEqual(self.make(((0,0),(10,0),(10,10)))[1].coordinate, (10,0))

    def test_invalid_spacing(self):
        for spacing in (0, -1, float('nan'), float('inf'), -float('inf'), True):
            with self.subTest(spacing=spacing), self.assertRaises(ValueError):
                self.make(spacing=spacing)

    def test_empty_and_degenerate_geometry(self):
        for points in ((), ((1,1),), ((1,1),(1,1)), ((0,0),(0,0),(1,1)), ((0,0),(float('nan'),1))):
            with self.subTest(points=points), self.assertRaises(ValueError):
                self.make(points)
        with self.assertRaises(ValueError):
            generate_waypoints([], 10, CartesianMetres())

    def test_bad_identifiers(self):
        for transects in ([Transect(' ', ((0,0),(1,0)))],
                         [Transect('A', ((0,0),(1,0))), Transect('A', ((1,1),(2,1)))]):
            with self.assertRaises(ValueError):
                generate_waypoints(transects, 10, CartesianMetres())

    def test_endpoint_roundoff(self):
        for length in (100 - 1e-9, 100, 100 + 1e-9):
            stations = station_distances(length, 10)
            self.assertEqual(len(stations), 11)
            self.assertEqual(stations[-1], length)
            self.assertTrue(all(a < b for a,b in zip(stations, stations[1:])))
        self.assertEqual(len(station_distances(100.00001,10)), 12)

    def test_output_cap(self):
        with self.assertRaises(ValueError):
            self.make(spacing=1e-12)
        with self.assertRaises(ValueError):
            station_distances(10, 10, limit=1)

    def test_closed_loop_undefined_bearing(self):
        rows = self.make(((0,0),(10,0),(10,10),(0,0)), spacing=100)
        self.assertIsNone(rows[0].bearing_next_deg)
        self.assertIsNone(rows[1].bearing_next_deg)

    def test_fractional_spacing_and_input_preservation(self):
        points = ((0,0),(1,0))
        rows = self.make(points, spacing=.1)
        self.assertEqual(len(rows), 11)
        self.assertEqual(points, ((0,0),(1,0)))
