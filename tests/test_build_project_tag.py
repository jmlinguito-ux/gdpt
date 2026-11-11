import unittest

import productivity_tool as pt
import build_tag_recheck as rc


def row(area='A-1', date='08/26/2026'):  # Wed in WW-35 (Tue-Mon weeks) -> needs Build at WW-34
    return {'AREA INDEX': area, 'AREA-INDEX': area, 'NEGO DATE': date}


def builds(*items):
    return [{'areaIndex': a, 'build': b, 'workWeek': w, 'projectTag': t} for a, b, w, t in items]


class BuildAndTagTests(unittest.TestCase):
    def setUp(self):
        self.ww = pt.iso_work_week_num('08/26/2026')
        self.prev = self.ww - 1

    def lookup(self, rows, area='A-1'):
        return pt.get_derived_build_and_tag(row(area), rows)

    def test_found_with_value_returns_value_and_folder(self):
        b = builds(('A-1', 'CONSO', f'2026-{self.prev:02d}', 'QUEZON-1'))
        self.assertEqual(self.lookup(b), ('CONSO', 'QUEZON-1'))

    def test_not_found_is_balance_for_both(self):
        b = builds(('B-9', 'CONSO', f'2026-{self.prev:02d}', 'QUEZON-1'))
        self.assertEqual(self.lookup(b), ('BALANCE', 'BALANCE'))

    def test_found_but_blank_build_is_no_data_with_folder(self):
        b = builds(('A-1', '', f'2026-{self.prev:02d}', 'BATANGAS-3'))
        self.assertEqual(self.lookup(b), ('NO DATA', 'BATANGAS-3'))

    def test_real_value_beats_blank_duplicate(self):
        b = builds(('A-1', '', f'2026-{self.prev:02d}', 'QUEZON-1'), ('A-1', 'NON-CONSO', f'2026-{self.prev:02d}', 'QUEZON-1'))
        self.assertEqual(self.lookup(b), ('NON-CONSO', 'QUEZON-1'))

    def test_not_in_current_build_sentinel_is_balance(self):
        b = builds(('A-1', 'NOT IN THE CURRENT BUILD', f'2026-{self.prev:02d}', 'QUEZON-1'))
        self.assertEqual(self.lookup(b), ('BALANCE', 'BALANCE'))

    def test_label_spelling_is_normalised(self):
        for raw in ('OUTSIDE CONSO', 'NON CONSO', 'NONCONSO', 'non-conso'):
            b = builds(('A-1', raw, f'2026-{self.prev:02d}', 'TARLAC-2'))
            self.assertEqual(self.lookup(b)[0].upper(), 'NON-CONSO', raw)

    def test_build_for_the_required_week_not_loaded_is_left_empty(self):
        # Build table only has older weeks -> do not write BALANCE yet
        b = builds(('A-1', 'CONSO', f'2026-{self.prev - 3:02d}', 'QUEZON-1'))
        self.assertEqual(self.lookup(b), ('', ''))
        self.assertEqual(self.lookup([]), ('', ''))

    def test_review_rows_carry_project_tag(self):
        b = builds(('A-1', 'CONSO', f'2026-{self.prev:02d}', 'QUEZON-1'))
        out = pt.calculate_review_rows([row()], [], [], b, [])
        self.assertEqual((out[0]['BUILD'], out[0]['PROJECT TAG']), ('CONSO', 'QUEZON-1'))
        self.assertIn('PROJECT TAG', pt.get_output_columns('negotiation'))

    def test_recheck_updates_only_what_changed_and_skips_pending(self):
        b = builds(('A-1', 'CONSO', '2026-34', 'QUEZON-1'), ('A-2', '', '2026-34', 'QUEZON-1'))
        recs = [
            {'id': 1, 'areaIndex': 'A-1', 'workWeek': '2026-35', 'build': 'BALANCE', 'projectTag': 'BALANCE'},
            {'id': 2, 'areaIndex': 'A-1', 'workWeek': '2026-35', 'build': 'CONSO', 'projectTag': 'QUEZON-1'},
            {'id': 3, 'areaIndex': 'A-2', 'workWeek': '2026-35', 'build': 'BALANCE', 'projectTag': ''},
            {'id': 4, 'areaIndex': 'A-3', 'workWeek': '2026-35', 'build': 'CONSO', 'projectTag': 'QUEZON-1'},
            {'id': 5, 'areaIndex': 'A-1', 'workWeek': '2026-44', 'build': '', 'projectTag': ''},
        ]
        res = rc.calculate(recs, b)
        got = {u['id']: (u['build'], u['projectTag']) for u in res['updates']}
        self.assertEqual(got, {1: ('CONSO', 'QUEZON-1'), 3: ('NO DATA', 'QUEZON-1'), 4: ('BALANCE', 'BALANCE')})
        self.assertEqual(res['unchanged'], 1)
        self.assertEqual([p['id'] for p in res['pending']], [5])


if __name__ == '__main__':
    unittest.main()
