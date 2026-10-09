import unittest
from productivity_metric_recalculation import calculate_productivity_only


def row(rid, area='A', day='2026-10-01', lo='Owner', use='VALID', checker='FIRST CONTACT'):
    return dict(id=rid, area=area, date=day, lo=lo, usability=use, checker=checker)


class ProductivityOnlyTests(unittest.TestCase):
    def test_employees_share_points_but_visit_count_once(self):
        values, errors = calculate_productivity_only([row('a'), row('b'), row('c', area='B'), row('d', area='C', day='2026-10-02')])
        self.assertFalse(errors)
        self.assertEqual(values['a']['POINTS'], .5)
        self.assertEqual(values['a']['LO OCCURRENCE BY DAY'], 2)
        self.assertEqual(values['a']['LO OCCURRENCE BY WW'], 3)
        self.assertEqual(values['a']['LO POINTS BY DAY'], .5)
        self.assertEqual(values['a']['LO POINTS BY WW'], .33333)
        self.assertEqual(values['a']['LO COUNT BY DAY'], 1)

    def test_invalid_visit_excluded_and_invalid_credit_zero(self):
        values, errors = calculate_productivity_only([row('a'), row('b', use='INVALID'), row('c', area='C', use='INVALID')])
        self.assertFalse(errors)
        self.assertEqual(values['a']['LO OCCURRENCE BY DAY'], 1)
        self.assertEqual(values['a']['LO POINTS BY DAY'], 1)
        self.assertEqual(values['c']['POINTS'], 0)
        self.assertEqual(values['c']['LO COUNT BY WW'], 0)

    def test_unresolved_visit_preserves_lo_week_but_points_independent(self):
        values, errors = calculate_productivity_only([row('a'), row('b', area='')])
        self.assertEqual(values['a'], {'POINTS': 1.0})
        self.assertTrue(errors)

    def test_same_property_different_day_counts_two_weekly_visits(self):
        values, errors = calculate_productivity_only([row('a'), row('b', day='2026-10-02', checker='FOLLOW UP')])
        self.assertFalse(errors)
        self.assertEqual(values['a']['LO OCCURRENCE BY WW'], 2)
        self.assertEqual(values['a']['LO POINTS BY WW'], .5)


if __name__ == '__main__': unittest.main()
