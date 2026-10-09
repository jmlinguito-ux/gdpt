import unittest
from productivity_metric_recalculation import calculate


def row(identifier, area='A', lo='Owner', use='VALID', checker='FIRST CONTACT'):
    return dict(id=identifier, area=area, date='10/01/2026', lo=lo, usability=use, checker=checker)


class MetricsTests(unittest.TestCase):
    def test_valid_contributors_share_point_and_invalid_gets_zero(self):
        prod = [row('a'), row('b'), row('c', use='INVALID')]
        values, errors, _ = calculate(prod, [row('record')])
        self.assertFalse(errors)
        self.assertEqual([values[r['id']]['POINTS'] for r in prod], [.5, .5, 0])
        self.assertEqual(values['c']['LO COUNT BY WW'], 0)

    def test_points_do_not_depend_on_lo(self):
        values, _, _ = calculate([row('a', lo=''), row('b')], [])
        self.assertEqual(values['a']['POINTS'], .5)
        self.assertEqual(values['b']['POINTS'], .5)
        self.assertNotIn('LO OCCURRENCE BY DAY', values['a'])

    def test_unreconciled_source_area_blocks_entire_lo_week(self):
        values, errors, _ = calculate([row('a', area='2700'), row('b', area='B')],
                                     [row('r1', area='JSE-2700'), row('r2', area='B')])
        self.assertEqual(values['a'], {'POINTS': 1.0})
        self.assertEqual(values['b'], {'POINTS': 1.0})
        self.assertEqual(len(errors), 2)

    def test_invalid_missing_identity_still_zeroes_known_metrics(self):
        r = row('a', area='', lo='', use='INVALID'); r['date'] = ''
        values, _, _ = calculate([r], [])
        self.assertEqual(values['a']['POINTS'], 0)
        self.assertEqual(values['a']['LO POINTS BY DAY'], 0)
        self.assertNotIn('LO OCCURRENCE BY DAY', values['a'])

    def test_invalid_source_only_visit_does_not_inflate_denominator(self):
        values, errors, _ = calculate([row('a')], [row('r1'), row('r2', area='B', use='INVALID')])
        self.assertFalse(errors)
        self.assertEqual(values['a']['LO OCCURRENCE BY DAY'], 1)

    def test_conflicting_owner_blocks_lo_but_not_points(self):
        values, errors, _ = calculate([row('a')], [row('r1', lo='Different owner')])
        self.assertEqual(values['a'], {'POINTS': 1.0})
        self.assertTrue(errors)

    def test_unmatched_property_even_with_different_source_owner_is_not_guessed(self):
        values, errors, _ = calculate([row('a', area='2700')], [row('r1', area='JSE-2700', lo='Another owner')])
        self.assertEqual(values['a'], {'POINTS': 1.0})
        self.assertTrue(errors)


if __name__ == '__main__': unittest.main()
