import unittest

import productivity_tool as pt


class BuildChoiceTests(unittest.TestCase):
    def choices(self, build_rows):
        return pt.get_column_type_and_choices('BUILD', 'negotiation', [], [], [], build_rows)['choices']

    def test_tarlac_outside_conso_is_offered_as_non_conso(self):
        got = self.choices([{'build': 'CONSO'}, {'build': 'OUTSIDE CONSO'}])
        self.assertIn('NON-CONSO', got)
        self.assertNotIn('OUTSIDE CONSO', got)
        self.assertEqual(len([c for c in got if c.upper() == 'NON-CONSO']), 1)

    def test_conso_labels_always_offered_and_no_duplicates(self):
        got = self.choices([])
        for label in ('CONSO', 'NON-CONSO', 'BALANCE', 'NO DATA'):
            self.assertIn(label, got)
        got = self.choices([{'build': 'conso'}, {'build': 'CONSO'}, {'build': 'Non Conso'}])
        self.assertEqual(sorted(c.upper() for c in got if 'CONSO' in c.upper()), ['CONSO', 'NON-CONSO'])

    def test_batangas_labels_come_from_the_build_table(self):
        got = self.choices([{'build': 'TERRA BLOCK'}, {'build': 'NON TERRA BLOCK'}])
        self.assertIn('TERRA BLOCK', got)
        self.assertIn('NON-TERRA BLOCK', got)


if __name__ == '__main__':
    unittest.main()
