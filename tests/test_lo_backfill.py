import unittest
from lo_metric_calculation import calculate


def row(identifier, area='A', day='01/02/2026', lo='Owner', checker='FIRST CONTACT', use='VALID'):
    return {'id': identifier, 'area': area, 'date': day, 'lo': lo, 'checker': checker, 'usability': use}


class LoBackfillTests(unittest.TestCase):
    def test_contributors_do_not_add_visits_and_record_history_is_included(self):
        prod = [row('p1'), row('p2')]
        records = [row('r1'), row('r2', area='B'), row('r3', area='C', day='01/01/2026')]
        values, errors, _ = calculate(prod, records)
        self.assertFalse(errors)
        for value in values.values():
            self.assertEqual(value['LO OCCURRENCE BY DAY'], 2)
            self.assertEqual(value['LO OCCURRENCE BY WW'], 3)
            self.assertEqual(value['LO POINTS BY DAY'], .5)
            self.assertEqual(value['LO POINTS BY WW'], .33333)
            self.assertEqual(value['LO COUNT BY DAY'], 1)
            self.assertEqual(value['LO COUNT BY WW'], 1)

    def test_all_invalid_visit_is_removed_from_valid_denominators(self):
        prod = [row('p1'), row('p2', area='B', use='INVALID')]
        values, errors, _ = calculate(prod, [row('r1'), row('r2', area='B')])
        self.assertFalse(errors)
        self.assertEqual(values['p1']['LO OCCURRENCE BY DAY'], 1)
        self.assertEqual(values['p1']['LO POINTS BY DAY'], 1)
        self.assertEqual(values['p1']['LO POINTS BY WW'], 1)
        for metric in ('LO POINTS BY DAY', 'LO POINTS BY WW', 'LO COUNT BY DAY', 'LO COUNT BY WW'):
            self.assertEqual(values['p2'][metric], 0)

    def test_partially_invalid_contributors_still_count_one_visit(self):
        values, errors, _ = calculate([row('p1'), row('p2', use='INVALID')], [row('r1')])
        self.assertFalse(errors)
        self.assertEqual(values['p1']['LO POINTS BY DAY'], 1)
        self.assertEqual(values['p2']['LO POINTS BY DAY'], 0)

    def test_different_years_are_not_combined_by_week_number(self):
        values, errors, _ = calculate([row('p1', day='01/02/2025'), row('p2')], [])
        self.assertFalse(errors)
        self.assertEqual([v['LO OCCURRENCE BY WW'] for v in values.values()], [1, 1])

    def test_missing_lo_or_date_is_reported(self):
        values, errors, _ = calculate([row('p1', lo=''), row('p2', day='bad')], [])
        self.assertFalse(values)
        self.assertEqual(len(errors), 2)

    def test_unlabeled_source_history_with_multiple_visits_is_not_guessed(self):
        prod = [row('p1'), row('p2', checker='1ST DUPLICATE')]
        values, errors, _ = calculate(prod, [row('r1', checker='')])
        self.assertFalse(values)
        self.assertEqual(len(errors), 2)

    def test_original_records_with_repeated_labels_still_count_each_original(self):
        values, errors, _ = calculate([row('p1'), row('p2')], [row('r1'), row('r2')])
        self.assertFalse(errors)
        self.assertEqual(values['p1']['LO OCCURRENCE BY DAY'], 2)

    def test_reclassified_checker_does_not_create_an_extra_visit(self):
        values, errors, _ = calculate([row('p1'), row('p2')], [row('r1', checker='FOLLOW UP')])
        self.assertFalse(errors)
        self.assertEqual(values['p1']['LO OCCURRENCE BY DAY'], 1)
        self.assertEqual(values['p1']['LO POINTS BY DAY'], 1)

    def test_duplicate_ordinal_still_counts_a_separate_visit(self):
        prod = [row('p1'), row('p2', checker='1ST DUPLICATE')]
        records = [row('r1', checker='REVISITED'), row('r2', checker='1ST DUPLICATE')]
        values, errors, _ = calculate(prod, records)
        self.assertFalse(errors)
        self.assertEqual(values['p1']['LO OCCURRENCE BY DAY'], 2)


if __name__ == '__main__':
    unittest.main()
