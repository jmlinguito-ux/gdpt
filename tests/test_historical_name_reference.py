import unittest
import historical_names as hn
import productivity_tool as pt


def history(name):
    return [{'ID': i, 'LSA NAME': name, 'REPORT DATE': f'08/{i + 1:02d}/2026', 'AREA-INDEX': f'A-{i}'} for i in range(5)]


class HistoricalReferenceTests(unittest.TestCase):
    def test_repeat_visits_establish_a_historical_employee(self):
        ref = hn.build_reference(history('JOEL CASTRO'), [])
        self.assertEqual(hn.resolve('Joel Castro', ref)['name'], 'JOEL CASTRO')
        for typo in ('JOOEL CASTRO', 'JOL CASRO'):
            self.assertEqual(hn.resolve(typo, ref)['name'], 'JOEL CASTRO')

    def test_frequency_without_independent_dates_and_properties_is_insufficient(self):
        ref = hn.build_reference([{'ID': i, 'LSA NAME': 'JOEL CASTRO', 'REPORT DATE': '08/01/2026', 'AREA-INDEX': 'A'} for i in range(100)], [])
        self.assertFalse(hn.resolve('JOEL CASTRO', ref)['name'])

    def test_ambiguous_names_and_suffixes_are_not_merged(self):
        ref = {'names': ['JOEL CASTRO', 'JOEL CASTOR']}
        self.assertEqual(hn.resolve('JOEL CAST', ref)['method'], 'ambiguous')
        self.assertFalse(hn.resolve('JOEL CASTRO JR', {'names': ['JOEL CASTRO']})['name'])

    def test_placeholder_and_single_word_are_not_employee_evidence(self):
        for name in ('NO NAME', 'JOEL', ''):
            self.assertFalse(hn.build_reference(history(name), [])['names'])

    def test_resolved_history_restores_name_validity_but_preserves_other_invalidity(self):
        ref = hn.build_reference(history('JOEL CASTRO'), [])
        teams = [{'employeeName': 'OTHER EMPLOYEE', 'team': 'TEAM 1', 'group': 'GROUP A'}]
        source = [{'ID': i, 'LSA NAME': name, 'REPORT DATE': '08/31/2026', 'AREA-INDEX': f'A-{i}',
                   'REGISTERED OWNER': 'Owner', 'CLASSIFICATION': 'SOURCED', 'DATA USABILITY': usability}
                  for i, (name, usability) in enumerate([('JOL CASRO', 'VALID'), ('JOEL CASTRO', 'INVALID')])]
        output = pt.enrich_workspace_rows(pt.build_productivity_rows(source, teams, [], [], [], mode='land-sourcing'), teams, ref)
        self.assertEqual(output[0]['MATCHED NEGOTIATOR NAME'], 'JOEL CASTRO')
        self.assertEqual(output[0]['DATA USABILITY'], 'VALID')
        self.assertEqual(output[0]['POINTS'], 1)
        self.assertEqual(output[1]['DATA USABILITY'], 'INVALID')
        self.assertEqual(output[1]['POINTS'], 0)


if __name__ == '__main__':
    unittest.main()
