import unittest
from datetime import datetime, timedelta

import productivity_tool as pt


def _fmt(d):
    return d.strftime('%m/%d/%Y')


TODAY = datetime.now().date()
TOMORROW = _fmt(TODAY + timedelta(days=1))
NEXT_YEAR = _fmt(TODAY + timedelta(days=365))
YESTERDAY = _fmt(TODAY - timedelta(days=1))
TODAY_STR = _fmt(TODAY)


class FutureDateRuleTests(unittest.TestCase):
    def test_future_work_dates_are_rejected(self):
        for column in ('NEGO DATE', 'SOURCING DATE', 'REPORT DATE'):
            for value in (TOMORROW, NEXT_YEAR):
                self.assertEqual(pt.date_cell_error(column, value), 'cannot be a future date',
                                 f'{column} {value} should be rejected')

    def test_today_and_past_are_accepted(self):
        # Today must pass: work recorded the same day is the normal case.
        for column in ('NEGO DATE', 'SOURCING DATE', 'REPORT DATE'):
            for value in (TODAY_STR, YESTERDAY, '01/15/2020'):
                self.assertEqual(pt.date_cell_error(column, value), '',
                                 f'{column} {value} should be accepted')

    def test_next_call_date_may_be_in_the_future(self):
        # This column exists to schedule ahead, so the rule must not apply.
        self.assertEqual(pt.date_cell_error('NEXT CALL DATE', NEXT_YEAR), '')

    def test_blank_is_allowed(self):
        for value in ('', None, '   '):
            self.assertEqual(pt.date_cell_error('NEGO DATE', value), '')

    def test_bad_format_still_reported_as_format_error(self):
        err = pt.date_cell_error('NEGO DATE', 'not-a-date')
        self.assertIn('MM/DD/YYYY', err)

    def test_column_matching_is_case_and_space_insensitive(self):
        self.assertEqual(pt.date_cell_error('  nego date  ', TOMORROW), 'cannot be a future date')

    def test_is_future_date_boundaries(self):
        self.assertFalse(pt.is_future_date(TODAY_STR))
        self.assertFalse(pt.is_future_date(YESTERDAY))
        self.assertTrue(pt.is_future_date(TOMORROW))
        self.assertFalse(pt.is_future_date('garbage'))

    def test_get_invalid_date_cells_flags_future_nego_date(self):
        rows = [
            {'AREA-INDEX': 'A1', 'NEGO DATE': YESTERDAY},
            {'AREA-INDEX': 'A2', 'NEGO DATE': TOMORROW},
        ]
        invalid = pt.get_invalid_date_cells(rows, ['AREA-INDEX', 'NEGO DATE'])
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0]['row'], 2)
        self.assertEqual(invalid[0]['column'], 'NEGO DATE')
        self.assertEqual(invalid[0]['reason'], 'cannot be a future date')

    def test_get_invalid_date_cells_reports_reason_for_bad_format(self):
        invalid = pt.get_invalid_date_cells([{'NEGO DATE': '13/45/2020'}], ['NEGO DATE'])
        self.assertEqual(len(invalid), 1)
        self.assertIn('MM/DD/YYYY', invalid[0]['reason'])


if __name__ == '__main__':
    unittest.main()
