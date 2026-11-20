import unittest

import productivity_tool as pt


class PublishDuplicateKeyTests(unittest.TestCase):
    """The duplicate key must only use values that stay the same for one report."""

    def key(self, area='LUB-3115', date='11/15/2026', lo='FRANCISCO VILLAFLORES',
            usability='VALID', checker='1ST DUPLICATE', name='MIGUEL MANALO'):
        return pt.publish_duplicate_key(area, date, lo, usability, checker, name)

    def test_changed_checker_is_still_a_duplicate(self):
        # Real case (Occidental Mindoro, WW 2026-46): published as FIRST CONTACT,
        # re-generated as 1ST DUPLICATE. It is the same report, not a new one.
        self.assertEqual(self.key(checker='FIRST CONTACT'), self.key(checker='1ST DUPLICATE'))
        self.assertEqual(self.key(checker='2ND DUPLICATE'), self.key(checker='1ST DUPLICATE'))

    def test_changed_data_usability_is_still_a_duplicate(self):
        self.assertEqual(self.key(usability='VALID'), self.key(usability='INVALID'))

    def test_different_lo_same_day_is_a_new_report(self):
        self.assertNotEqual(self.key(lo='FRANCISCO VILLAFLORES'), self.key(lo='BONIFACIO VILLAFLORES'))

    def test_different_person_or_date_is_new(self):
        self.assertNotEqual(self.key(name='MIGUEL MANALO'), self.key(name='CARLOS MARCELLANA'))
        self.assertNotEqual(self.key(date='11/15/2026'), self.key(date='11/16/2026'))

    def test_date_formats_and_spacing_match(self):
        self.assertEqual(self.key(date='2026-11-15', lo=' francisco  villaflores '), self.key())

    def test_missing_area_or_date_has_no_key(self):
        self.assertEqual(self.key(area=''), '')
        self.assertEqual(self.key(date=''), '')


if __name__ == '__main__':
    unittest.main()
