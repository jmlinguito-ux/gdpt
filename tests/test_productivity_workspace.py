import unittest
import productivity_tool as pt
from productivity_workspace import refresh
from app import Api


def source(n, names, day='10/01/2026', classification='OPEN'):
    return {'ID': n, 'AREA-INDEX': 'A', 'NEGO DATE': day, 'CLASSIFICATION': classification,
            'REGISTERED OWNER': 'Owner', 'NEGOTIATOR NAME': names, 'INDEX NO': 1}


class WorkspaceTests(unittest.TestCase):
    def build(self, sources):
        teams = [{'employeeName': n, 'team': 'TEAM 1', 'group': 'GROUP 1'} for n in ('ALICE', 'BOB', 'CAROL')]
        calculated = pt.calculate_review_rows(sources, [], [], [], [])
        rows = pt.enrich_workspace_rows(pt.build_productivity_rows(calculated, teams, [], [], []), teams)
        issues = refresh(rows, calculated, [], teams)
        self.assertFalse(issues)
        return rows, calculated, teams

    def test_partial_repeat_invalidates_only_repeated_employee(self):
        rows, calculated, teams = self.build([source(1, 'ALICE / BOB'), source(2, 'BOB / CAROL')])
        self.assertEqual([r['DATA USABILITY'] for r in calculated], ['VALID', 'VALID'])
        self.assertEqual([r['DATA USABILITY'] for r in rows], ['VALID', 'VALID', 'INVALID', 'VALID'])
        self.assertEqual([r['CHECKER'] for r in rows], ['FIRST CONTACT', 'FIRST CONTACT', '1ST DUPLICATE', 'FIRST CONTACT'])
        self.assertEqual([r['POINTS'] for r in rows], [.33333, .33333, 0, .33333])
        self.assertEqual(rows[0]['LO OCCURRENCE BY DAY'], 1)
        self.assertFalse(refresh(rows, calculated, [], teams))
        self.assertEqual(rows[0]['POINTS'], .33333)

    def test_lowest_source_id_kept_and_different_classifications_eligible(self):
        rows, calculated, _ = self.build([source(2, 'ALICE'), source(1, 'ALICE'), source(3, 'ALICE', classification='CLOSED')])
        self.assertEqual([r['DATA USABILITY'] for r in calculated], ['INVALID', 'VALID', 'VALID'])
        self.assertEqual([r['DATA USABILITY'] for r in rows], ['INVALID', 'VALID', 'VALID'])

    def test_metrics_ignore_source_only_history(self):
        rows, calculated, teams = self.build([source(1, 'ALICE'), source(2, 'ALICE', day='10/02/2026')])
        old = source(0, 'ALICE', day='09/30/2026'); old['AREA-INDEX'] = 'B'
        self.assertFalse(refresh(rows, calculated, [old], teams))
        self.assertEqual(rows[0]['LO OCCURRENCE BY WW'], 2)
        self.assertEqual(rows[0]['LO POINTS BY WW'], .5)

    def test_source_invalid_is_not_restored_by_first_contact(self):
        r = source(1, 'ALICE'); r['DATA USABILITY'] = 'INVALID'
        teams = [{'employeeName': 'ALICE'}]
        rows = pt.enrich_workspace_rows(pt.build_productivity_rows([r], teams, [], [], []), teams)
        self.assertFalse(refresh(rows, [r]))
        self.assertEqual(rows[0]['CHECKER'], 'FIRST CONTACT')
        self.assertEqual(rows[0]['POINTS'], 0)

    def test_unresolved_metrics_are_visible_and_publish_is_blocked(self):
        rows, calculated, teams = self.build([source(1, 'ALICE')])
        rows[0]['LO'] = ''
        self.assertTrue(refresh(rows, calculated, [], teams))
        api = Api.__new__(Api); api.mode = 'negotiation'; api.productivity = rows
        self.assertTrue(api._productivity_table()['calculationErrors'])
        self.assertTrue(api._partition_rows(rows, set(), stage='prod')[3])
        self.assertFalse(api.update_productivity_cell(0, 'LO POINTS BY WW', '7')['ok'])


if __name__ == '__main__': unittest.main()
