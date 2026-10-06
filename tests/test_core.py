"""Analytical fixtures and invariants for GIS-independent calculations."""
import random
import unittest
from dataclasses import FrozenInstanceError
from math import hypot
from transect_navigator.geometry.planar import Point2D, project_to_segment
from transect_navigator.navigation.bearings import grid_bearing


class GeometryTests(unittest.TestCase):
    def test_interior_projection(self):
        result = project_to_segment(Point2D(3, 4), Point2D(0, 0), Point2D(10, 0))
        self.assertEqual(result.point, Point2D(3, 0))
        self.assertAlmostEqual(result.fraction, 0.3)
        self.assertEqual(result.distance, 4)

    def test_endpoint_clamping(self):
        for point, expected, fraction in [(Point2D(-3, 4), Point2D(0, 0), 0),
                                          (Point2D(13, 4), Point2D(10, 0), 1)]:
            with self.subTest(point=point):
                result = project_to_segment(point, Point2D(0, 0), Point2D(10, 0))
                self.assertEqual(result.point, expected)
                self.assertEqual(result.fraction, fraction)
                self.assertEqual(result.distance, 5)

    def test_degenerate_segment(self):
        result = project_to_segment(Point2D(4, 6), Point2D(1, 2), Point2D(1, 2))
        self.assertEqual((result.fraction, result.distance), (0, 5))

    def test_diagonal_and_vertical(self):
        result = project_to_segment(Point2D(0, 2), Point2D(0, 0), Point2D(2, 2))
        self.assertAlmostEqual(result.point.x, 1)
        self.assertAlmostEqual(result.point.y, 1)
        self.assertAlmostEqual(result.distance, 2 ** 0.5)
        self.assertEqual(project_to_segment(Point2D(3, 4), Point2D(0, 0), Point2D(0, 8)).point,
                         Point2D(0, 4))

    def test_nonfinite_inputs(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            for coords in ((value, 0), (0, value)):
                with self.subTest(coords=coords), self.assertRaises(ValueError):
                    Point2D(*coords)

    def test_immutable_inputs(self):
        point = Point2D(1, 2)
        with self.assertRaises(FrozenInstanceError):
            point.x = 5

    def test_overflow_rejected(self):
        with self.assertRaises(ValueError):
            project_to_segment(Point2D(0, 0), Point2D(-1e308, 0), Point2D(1e308, 0))

    def test_small_nonzero_segment(self):
        result = project_to_segment(Point2D(5e-201, 1e-200), Point2D(0, 0), Point2D(1e-200, 0))
        self.assertAlmostEqual(result.fraction, 0.5)
        self.assertAlmostEqual(result.distance / 1e-200, 1)

    def test_seeded_reversal_translation_and_nearest_invariants(self):
        rng = random.Random(419)
        for _ in range(100):
            p, a, b = [Point2D(rng.uniform(-100, 100), rng.uniform(-100, 100)) for _ in range(3)]
            r = project_to_segment(p, a, b)
            reverse = project_to_segment(p, b, a)
            self.assertAlmostEqual(r.distance, reverse.distance)
            self.assertAlmostEqual(r.fraction, 1 - reverse.fraction)
            shifted = [Point2D(v.x + 500000, v.y + 4000000) for v in (p, a, b)]
            self.assertAlmostEqual(project_to_segment(*shifted).distance, r.distance, places=8)
            for fraction in (0, .25, .5, .75, 1):
                distance = hypot(p.x - (a.x + fraction * (b.x-a.x)),
                                 p.y - (a.y + fraction * (b.y-a.y)))
                self.assertLessEqual(r.distance, distance + 1e-10)


class BearingTests(unittest.TestCase):
    def test_cardinal_and_diagonal_bearings(self):
        for x, y, bearing in [(0, 1, 0), (1, 1, 45), (1, 0, 90), (1, -1, 135),
                              (0, -1, 180), (-1, -1, 225), (-1, 0, 270), (-1, 1, 315)]:
            with self.subTest(bearing=bearing):
                self.assertAlmostEqual(grid_bearing(Point2D(0, 0), Point2D(x, y)), bearing)

    def test_coincident_points(self):
        with self.assertRaises(ValueError):
            grid_bearing(Point2D(1, 1), Point2D(1, 1))

    def test_overflow_rejected(self):
        with self.assertRaises(ValueError):
            grid_bearing(Point2D(-1e308, 0), Point2D(1e308, 0))
