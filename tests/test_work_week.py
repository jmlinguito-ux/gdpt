import unittest
from datetime import date, timedelta

import productivity_tool as pt


def _ww(d):
    return pt.get_work_week(d.strftime('%m/%d/%Y'))


class TuesdayToMondayWorkWeekTests(unittest.TestCase):
    def test_week_runs_tuesday_to_monday(self):
        self.assertEqual(pt.get_work_week('10/05/2026'), '2026-40')  # Mon closes the week
        self.assertEqual(pt.get_work_week('10/06/2026'), '2026-41')  # Tue opens the next
        self.assertEqual(pt.get_work_week('10/12/2026'), '2026-41')
        self.assertEqual(pt.get_work_week('10/13/2026'), '2026-42')

    def test_week_containing_jan_1_is_week_one_of_the_monday_year(self):
        self.assertEqual(pt.get_work_week('12/29/2025'), '2025-52')
        self.assertEqual(pt.get_work_week('12/30/2025'), '2026-01')
        self.assertEqual(pt.get_work_week('01/01/2026'), '2026-01')
        self.assertEqual(pt.get_work_week('01/05/2026'), '2026-01')
        self.assertEqual(pt.get_work_week('01/06/2026'), '2026-02')
        self.assertEqual(pt.get_work_week('12/31/2024'), '2025-01')
        self.assertEqual(pt.get_work_week('12/29/2026'), '2027-01')

    def test_every_day_shares_its_tuesday_start(self):
        d = date(2024, 1, 1)
        while d <= date(2028, 12, 31):
            label, start, end = pt.get_work_week_info(
                pt.datetime(d.year, d.month, d.day))
            self.assertEqual(start.weekday(), 1)          # Tuesday
            self.assertEqual((end - start).days, 6)        # ...through Monday
            self.assertTrue(start.date() <= d <= end.date())
            self.assertEqual(label[:4], str(end.year))
            d += timedelta(days=1)

    def test_week_numbers_are_consecutive(self):
        d, prev = date(2024, 12, 31), None
        while d <= date(2027, 12, 31):
            label = _ww(d)
            if prev and prev != label:
                py, pn = int(prev[:4]), int(prev[5:])
                y, n = int(label[:4]), int(label[5:])
                self.assertTrue((y == py and n == pn + 1) or (y == py + 1 and n == 1),
                                f'{prev} -> {label} at {d}')
            prev = label
            d += timedelta(days=1)

    def test_normalize_reads_new_and_legacy_formats(self):
        for value in ('2026-35', 'WW-35', 'WW35', '35', 35):
            self.assertEqual(pt.normalize_work_week(value), 35)

    def test_legacy_stored_week_matches_date_derived_week(self):
        self.assertEqual(pt.canonical_work_week('WW-35', '09/01/2026'), '2026-36')
        self.assertEqual(pt.canonical_work_week('2025-10', ''), '2025-10')

    def test_range_lists_newest_first(self):
        self.assertEqual(pt.work_weeks_in_range('2025-12-20', '2026-01-10'),
                         ['2026-02', '2026-01', '2025-52', '2025-51'])


if __name__ == '__main__':
    unittest.main()
