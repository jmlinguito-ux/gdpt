import unittest

import productivity_tool as pt
from app import Api


def row(identifier, date='01/02/2026', classification='OPEN', names='Alice / Bob'):
    return {'ID': identifier, 'AREA-INDEX': 'A-1', 'NEGO DATE': date,
            'CLASSIFICATION': classification, 'NEGOTIATOR NAME': names}


class CheckerTests(unittest.TestCase):
    def test_original_rows_rank_by_id_not_display_order(self):
        rows = [row(3), row(1), row(2)]
        self.assertEqual([pt.get_checker(r, rows, []) for r in rows],
                         ['2ND DUPLICATE', 'FIRST CONTACT', '1ST DUPLICATE'])

    def test_prior_visit_classification(self):
        history = [row(1, '01/01/2026')]
        for classification, expected in [('OPEN', 'FOLLOW UP'), ('CLOSED', 'REVISITED'), ('', 'NEEDS REVIEW')]:
            current = row(2, classification=classification)
            self.assertEqual(pt.get_checker(current, [current], history), expected)
        current = row(2)
        self.assertEqual(pt.get_checker(current, [current], [row(1, '01/01/2026', '')]), 'NEEDS REVIEW')

    def test_latest_visit_and_current_batch_history(self):
        rows = [row(1, '01/01/2026', 'OPEN'), row(2, '01/02/2026', 'CLOSED'), row(3, '01/03/2026', 'CLOSED')]
        self.assertEqual([pt.get_checker(r, rows, []) for r in rows], ['FIRST CONTACT', 'REVISITED', 'FOLLOW UP'])

    def test_same_day_history_counted_once(self):
        old = {'iD1': 1, 'aREAINDEX': 'A-1', 'date': '01/02/2026', 'classification': 'OPEN'}
        current = row(2)
        self.assertEqual(pt.get_checker(current, [current], [old, dict(old)]), '1ST DUPLICATE')
        self.assertEqual(pt.get_checker(current, [current], [old, {**old, '_record_id': 'guid-1'}]), '1ST DUPLICATE')

    def test_history_unavailable_and_empty_success(self):
        current = row(1)
        self.assertEqual(pt.get_checker(current, [current], [], False), 'HISTORY REQUIRED')
        self.assertEqual(pt.get_checker(current, [current], [], True), 'FIRST CONTACT')

    def test_csv_and_productivity_agree_before_name_expansion(self):
        rows = [row(1), row(2)]
        calculated = pt.calculate_review_rows(rows, [], [], [], [], history_ready=True)
        self.assertEqual([r['CHECKER'] for r in calculated], ['FIRST CONTACT', '1ST DUPLICATE'])
        expanded = pt.build_productivity_rows(calculated, [], [], [], [], history_ready=True)
        self.assertEqual([r['CHECKER'] for r in expanded],
                         ['FIRST CONTACT', 'FIRST CONTACT', '1ST DUPLICATE', '1ST DUPLICATE'])
        pending = pt.build_productivity_rows(calculated, [], [], [], [], history_ready=False)
        self.assertTrue(all(r['CHECKER'] == 'HISTORY REQUIRED' for r in pending))

    def test_build_lookup_flows_into_read_only_productivity_column(self):
        source = [row(1, names='Alice / Bob')]
        build_rows = [{'areaIndex': 'A-1', 'build': 'BUILD-2', 'workWeek': 53}]
        calculated = pt.calculate_review_rows(source, [], [], build_rows, [])
        productivity = pt.build_productivity_rows(calculated, [], [], [], [])

        self.assertEqual(calculated[0]['BUILD'], 'BUILD-2')
        self.assertEqual([r['BUILD'] for r in productivity], ['BUILD-2', 'BUILD-2'])
        self.assertIn('BUILD', pt.get_output_columns('negotiation'))
        self.assertIn('BUILD', pt.get_output_columns('land-sourcing'))
        self.assertEqual(pt.get_column_type_and_choices('BUILD', 'negotiation', [], [], [], build_rows)['type'], 'choice')

        api = Api.__new__(Api)
        api.productivity = productivity
        for mode in ('negotiation', 'land-sourcing'):
            api.mode = mode
            self.assertEqual(api._productivity_table()['meta']['BUILD']['type'], 'text')
            self.assertEqual([r['BUILD'] for r in api._rows_for_stage('prod')], ['BUILD-2', 'BUILD-2'])
        self.assertFalse(api.update_productivity_cell(0, 'BUILD', 'BUILD-1')['ok'])
        self.assertEqual(api.productivity[0]['BUILD'], 'BUILD-2')

    def test_both_modes_expose_checker_as_text(self):
        for mode in ['negotiation', 'land-sourcing']:
            self.assertEqual(pt.get_review_headers(mode).count('CHECKER'), 1)
            self.assertEqual(pt.get_column_type_and_choices('CHECKER', mode, [], [], [], [])['type'], 'text')

    def test_checker_cannot_be_manually_edited(self):
        api = Api.__new__(Api)
        self.assertFalse(api.update_review_cell(0, 'CHECKER', 'FIRST CONTACT')['ok'])
        self.assertFalse(api.update_productivity_cell(0, 'CHECKER', 'FIRST CONTACT')['ok'])

    def test_history_scope_tracks_area_changes_and_empty_success(self):
        api = Api.__new__(Api)
        api.mode = 'negotiation'
        api.rows = [row(1)]
        api.municipality_codes = []
        api.ref_sources = {}
        self.assertFalse(api._checker_history_ready())
        api._checker_history_scope = ('negotiation', {'a-1'})
        self.assertTrue(api._checker_history_ready())
        api.rows[0]['AREA-INDEX'] = 'A-2'
        self.assertFalse(api._checker_history_ready())
        api.rows[0]['AREA-INDEX'] = 'A-1'
        api.mode = 'land-sourcing'
        self.assertFalse(api._checker_history_ready())

    def test_review_table_displays_checker_before_calculate(self):
        api = Api.__new__(Api)
        api.mode = 'negotiation'
        api.rows = [row(1), row(2)]
        api.municipality_codes = api.mapping_statuses = api.build_rows = api.teams = []
        api.existing_records = api._dedup_records = []
        api.existing_area_set = api._dedup_area_set = set()
        api.ref_sources = {}
        api.has_calculated = api.use_current_build = False
        table = api._review_table(api.rows)
        index = table['headers'].index('CHECKER')
        self.assertEqual([r[index] for r in table['rows']], ['HISTORY REQUIRED'] * 2)
        api._checker_history_scope = ('negotiation', {'a-1'})
        table = api._review_table(api.rows)
        self.assertEqual([r[index] for r in table['rows']], ['FIRST CONTACT', '1ST DUPLICATE'])

    def test_land_sourcing_uses_report_date(self):
        rows = [row(1), row(2)]
        for item in rows:
            item['REPORT DATE'] = item.pop('NEGO DATE')
            item['LSA NAME'] = item.pop('NEGOTIATOR NAME')
        calculated = pt.calculate_review_rows(rows, [], [], [], [], mode='land-sourcing')
        expanded = pt.build_productivity_rows(calculated, [], [], [], [], mode='land-sourcing')
        self.assertEqual([r['CHECKER'] for r in calculated], ['FIRST CONTACT', '1ST DUPLICATE'])
        self.assertEqual([r['CHECKER'] for r in expanded], ['FIRST CONTACT'] * 2 + ['1ST DUPLICATE'] * 2)

    def test_ordinal_suffixes(self):
        for number, prefix in [(1, '1ST'), (2, '2ND'), (3, '3RD'), (11, '11TH'), (12, '12TH'), (13, '13TH'), (21, '21ST'), (111, '111TH')]:
            self.assertEqual(pt.ordinal_duplicate(number), prefix + ' DUPLICATE')


if __name__ == '__main__':
    unittest.main()
