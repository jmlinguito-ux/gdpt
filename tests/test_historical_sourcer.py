import unittest
import productivity_tool as pt


class HistoricalSourcerTests(unittest.TestCase):
    def test_cutoff_and_normalization(self):
        self.assertEqual(pt.historical_sourcer_name('  Juan\u00a0  Dela Cruz ', '08/31/2026', 'land-sourcing'), 'JUAN DELA CRUZ')
        self.assertEqual(pt.historical_sourcer_name('Juan', '09/01/2026', 'land-sourcing'), '')
        self.assertEqual(pt.historical_sourcer_name('Juan', '08/31/2026', 'negotiation'), '')
        self.assertEqual(pt.historical_sourcer_name('Juan', 'bad', 'land-sourcing'), '')

    def test_historical_name_is_not_replaced_by_a_similar_current_employee(self):
        teams = [{'employeeName': 'JUAN DELA CRUZ', 'team': 'TEAM 1', 'group': 'GROUP 1'}]
        source = [{'ID': 1, 'LSA NAME': '  Juan Dela Crux ', 'AREA-INDEX': 'A', 'REPORT DATE': '08/31/2026',
                   'CLASSIFICATION': 'SOURCED', 'DATA USABILITY': 'VALID', 'REGISTERED OWNER': 'Owner'}]
        built = pt.build_productivity_rows(source, teams, [], [], [], mode='land-sourcing')
        rows = pt.enrich_workspace_rows(built, teams)
        self.assertEqual(rows[0]['MATCHED NEGOTIATOR NAME'], 'JUAN DELA CRUX')
        self.assertEqual(rows[0]['DATA USABILITY'], 'INVALID')
        self.assertEqual(rows[0]['POINTS'], 0)

    def test_current_visit_still_matches_the_reference_employee(self):
        teams = [{'employeeName': 'JUAN DELA CRUZ', 'team': 'TEAM 1', 'group': 'GROUP 1'}]
        source = [{'ID': 1, 'LSA NAME': 'Juan Dela Crux', 'AREA-INDEX': 'A', 'REPORT DATE': '09/01/2026',
                   'CLASSIFICATION': 'SOURCED', 'DATA USABILITY': 'VALID', 'REGISTERED OWNER': 'Owner'}]
        rows = pt.enrich_workspace_rows(pt.build_productivity_rows(source, teams, [], [], [], mode='land-sourcing'), teams)
        self.assertEqual(rows[0]['MATCHED NEGOTIATOR NAME'], 'JUAN DELA CRUZ')
        self.assertEqual(rows[0]['POINTS'], 1)


if __name__ == '__main__':
    unittest.main()
