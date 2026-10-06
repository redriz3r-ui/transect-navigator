"""Independent analytic expectations for directed polyline nearest-point QA."""
from math import cos, sin, radians, sqrt
import unittest
from unittest.mock import patch
from transect_navigator.geometry.planar import Point2D
from transect_navigator.qaqc.deviation import DirectedTransect


def line(*xy):
    return DirectedTransect([Point2D(*p) for p in xy])


class DeviationTests(unittest.TestCase):
    def test_on_transect(self):
        row = line((0,0),(100,0)).compare(Point2D(40,0))
        self.assertEqual(row.nearest, Point2D(40,0))
        self.assertEqual((row.absolute_m,row.signed_m,row.distance_along_m,row.progress_pct), (0,0,40,40))

    def test_one_metre_left(self):
        row = line((0,0),(100,0)).compare(Point2D(40,1))
        self.assertEqual((row.absolute_m,row.signed_m), (1,1))
        self.assertEqual(row.sign_status,'left')

    def test_one_metre_right(self):
        row = line((0,0),(100,0)).compare(Point2D(40,-1))
        self.assertEqual((row.absolute_m,row.signed_m), (1,-1))
        self.assertEqual(row.sign_status,'right')

    def test_five_metre_deviation_oblique_345_basis(self):
        # Unit tangent (3/5,4/5), left normal (-4/5,3/5).
        # Station 10 is (6,8); adding 5 left gives (2,11).
        row = line((0,0),(30,40)).compare(Point2D(2,11))
        self.assertAlmostEqual(row.absolute_m,5)
        self.assertAlmostEqual(row.signed_m,5)
        self.assertAlmostEqual(row.distance_along_m,10)
        self.assertAlmostEqual(row.progress_pct,20)
        self.assertAlmostEqual(row.nearest.x,6)
        self.assertAlmostEqual(row.nearest.y,8)

    def test_sign_independent_rotation_and_translation(self):
        for angle in (0,30,90,150,180,225,270,330):
            ux,uy = cos(radians(angle)),sin(radians(angle))
            path = line((101,-50),(101+100*ux,-50+100*uy))
            for d in (-5,-1,1,5):
                # Construct known station 20 and signed offset d with an orthonormal basis.
                point = Point2D(101+20*ux-d*uy,-50+20*uy+d*ux)
                row = path.compare(point)
                with self.subTest(angle=angle,d=d):
                    self.assertAlmostEqual(row.signed_m,d,places=10)
                    self.assertAlmostEqual(row.distance_along_m,20,places=10)

    def test_reversal_sign_and_station(self):
        path = [(0,0),(10,0),(10,10)]
        for point in (Point2D(4,1),Point2D(11,4),Point2D(11,-1),Point2D(10,0)):
            a,b = line(*path).compare(point), line(*path[::-1]).compare(point)
            self.assertAlmostEqual(a.absolute_m,b.absolute_m)
            self.assertAlmostEqual(a.distance_along_m+b.distance_along_m,20)
            self.assertAlmostEqual(a.progress_pct+b.progress_pct,100)
            self.assertAlmostEqual(a.nearest.x,b.nearest.x)
            self.assertAlmostEqual(a.nearest.y,b.nearest.y)
            self.assertAlmostEqual(a.signed_m,-b.signed_m)

    def test_multisegment_station(self):
        row = line((0,0),(10,0),(10,20)).compare(Point2D(15,12))
        self.assertEqual(row.nearest,Point2D(10,12))
        self.assertEqual((row.absolute_m,row.signed_m,row.distance_along_m), (5,-5,22))
        self.assertAlmostEqual(row.progress_pct,100*22/30)

    def test_vertex_on_path(self):
        row = line((0,0),(10,0),(10,10)).compare(Point2D(10,0))
        self.assertEqual((row.absolute_m,row.signed_m,row.distance_along_m),(0,0,10))

    def test_vertex_exterior_consistent_side(self):
        row = line((0,0),(10,0),(10,10)).compare(Point2D(11,-1))
        self.assertEqual(row.nearest,Point2D(10,0))
        self.assertAlmostEqual(row.absolute_m,sqrt(2))
        self.assertAlmostEqual(row.signed_m,-sqrt(2))
        self.assertEqual(row.distance_along_m,10)

    def test_vertex_tangent_boundary_has_no_sign(self):
        row = line((0,0),(10,0),(10,10)).compare(Point2D(11,0))
        self.assertEqual(row.absolute_m,1)
        self.assertIsNone(row.signed_m)
        self.assertEqual(row.sign_status,'vertex_side_ambiguous')

    def test_vertex_nearby_unique_projection(self):
        row = line((0,0),(10,0),(10,10)).compare(Point2D(9,-1))
        self.assertEqual((row.signed_m,row.distance_along_m),(-1,9))

    def test_equal_distance_distinct_nearest_locations_rejected(self):
        with self.assertRaisesRegex(ValueError,'ambiguous nearest'):
            line((0,0),(10,0),(10,10)).compare(Point2D(9,1))

    def test_before_and_after_endpoints_include_overshoot(self):
        for x,station in [(-3,0),(103,100)]:
            row = line((0,0),(100,0)).compare(Point2D(x,4))
            self.assertEqual(row.absolute_m,5)  # 3-4-5 distance, not just lateral 4.
            self.assertIsNone(row.signed_m)
            self.assertEqual(row.distance_along_m,station)
            self.assertEqual(row.progress_pct,station)
            self.assertEqual(row.sign_status,'endpoint_no_lateral_sign')

    def test_collinear_overshoot_is_not_zero_deviation(self):
        row = line((0,0),(100,0)).compare(Point2D(105,0))
        self.assertEqual(row.absolute_m,5)
        self.assertIsNone(row.signed_m)

    def test_exact_endpoints_are_zero(self):
        for x in (0,100):
            row = line((0,0),(100,0)).compare(Point2D(x,0))
            self.assertEqual((row.absolute_m,row.signed_m,row.progress_pct),(0,0,x))

    def test_multiple_observations_preserve_input_order(self):
        points = [Point2D(80,5),Point2D(20,-1),Point2D(20,-1)]
        rows = line((0,0),(100,0)).compare_many(points)
        self.assertEqual([r.signed_m for r in rows],[5,-1,-1])
        self.assertEqual([r.distance_along_m for r in rows],[80,20,20])
        self.assertEqual(points[0],Point2D(80,5))

    def test_collinear_subdivision_invariance(self):
        a = line((0,0),(100,0))
        b = line((0,0),(25,0),(50,0),(75,0),(100,0))
        for point in [Point2D(25,1),Point2D(50,-5),Point2D(49,3)]:
            self.assertEqual(a.compare(point),b.compare(point))

    def test_invalid_empty_zero_and_backtracking(self):
        for vertices in [[],[(0,0)],[(0,0),(0,0)],[(0,0),(10,0),(5,0)]]:
            with self.subTest(vertices=vertices), self.assertRaises(ValueError):
                line(*vertices)

    def test_crossings_closed_and_touches_rejected(self):
        for vertices in [[(0,0),(10,10),(0,10),(10,0)],[(0,0),(10,0),(10,10),(0,0)],
                         [(0,0),(10,0),(10,10),(5,0)],[(0,0),(10,0),(10,5),(5,5),(5,0),(8,0)]]:
            with self.subTest(vertices=vertices), self.assertRaises(ValueError):
                line(*vertices)

    def test_finite_coordinates_and_local_scope(self):
        with self.assertRaises(ValueError):
            Point2D(float('nan'),0)
        with self.assertRaises(ValueError):
            line((1e10,0),(1e10+100,0))

    def test_batch_limits_and_empty(self):
        path = line((0,0),(100,0))
        with self.assertRaises(ValueError):
            path.compare_many([])
        with patch('transect_navigator.qaqc.deviation.MAX_PROJECTIONS',1), self.assertRaises(ValueError):
            path.compare_many([Point2D(1,1),Point2D(2,1)])

    def test_ambiguous_batch_identifies_observation(self):
        with self.assertRaisesRegex(ValueError,'Observation 2'):
            line((0,0),(10,0),(10,10)).compare_many([Point2D(5,-1),Point2D(9,1)])
