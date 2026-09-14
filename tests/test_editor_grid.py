import unittest
from datetime import datetime, timedelta

from app import Api


def _future(days=5):
    return (datetime.now().date() + timedelta(days=days)).strftime('%m/%d/%Y')


def _make_api():
    """A bare Api with only what the editable-grid methods touch.

    Api.__init__ builds the whole app (settings, updater, webview), so the grid
    logic is exercised against a hand-seeded instance instead.
    """
    api = Api.__new__(Api)
    api.editors = {}
    api.ui_settings = {}
    api.mode = 'negotiation'
    api.dv_client = None
    api.state = lambda: {}
    return api


def _seed(api, scope='nego-records'):
    ed = api._editor(scope)
    ed.update({
        'table': 'cr63f_batangasnegorecord',
        'columns': ['cr63f_areaindex', 'cr63f_stage', 'cr63f_negotiationdate'],
        'labels': ['AREA-INDEX', 'STAGE', 'NEGO DATE'],
        'types': {'cr63f_negotiationdate': 'DateTime'},
        'rows': [
            {'cr63f_areaindex': 'A-1', 'cr63f_stage': 'Initial',
             'cr63f_negotiationdate': '01/10/2026', '_record_id': 'guid-1'},
            {'cr63f_areaindex': 'A-2', 'cr63f_stage': 'Closed',
             'cr63f_negotiationdate': '02/11/2026', '_record_id': 'guid-2'},
        ],
        'dirty': {}, 'loaded': True,
    })
    return ed


class EditorGridTests(unittest.TestCase):
    def test_edit_is_staged_locally_and_marked_dirty(self):
        api = _make_api()
        ed = _seed(api)
        res = api.update_editor_cell('nego-records', 0, 'cr63f_stage', 'Follow Up')
        self.assertTrue(res['ok'])
        self.assertEqual(ed['rows'][0]['cr63f_stage'], 'Follow Up')
        self.assertEqual(ed['dirty'], {0: {'cr63f_stage': 'Follow Up'}})
        # the pre-edit value is kept so discard can restore it
        self.assertEqual(ed['rows'][0]['_orig_cr63f_stage'], 'Initial')

    def test_editing_back_to_original_clears_dirty(self):
        api = _make_api()
        ed = _seed(api)
        api.update_editor_cell('nego-records', 0, 'cr63f_stage', 'Follow Up')
        api.update_editor_cell('nego-records', 0, 'cr63f_stage', 'Initial')
        self.assertEqual(ed['dirty'], {}, 'no net change should leave nothing to save')

    def test_discard_restores_original_values(self):
        api = _make_api()
        ed = _seed(api)
        api.update_editor_cell('nego-records', 0, 'cr63f_stage', 'Follow Up')
        api.update_editor_cell('nego-records', 1, 'cr63f_areaindex', 'CHANGED')
        api.discard_editor_edits('nego-records')
        self.assertEqual(ed['rows'][0]['cr63f_stage'], 'Initial')
        self.assertEqual(ed['rows'][1]['cr63f_areaindex'], 'A-2')
        self.assertEqual(ed['dirty'], {})

    def test_future_date_is_rejected_on_edit(self):
        # Same rule the CSV and productivity workspaces enforce.
        api = _make_api()
        ed = _seed(api)
        res = api.update_editor_cell('nego-records', 0, 'cr63f_negotiationdate', _future())
        self.assertFalse(res['ok'])
        self.assertIn('future', res['error'])
        self.assertEqual(ed['rows'][0]['cr63f_negotiationdate'], '01/10/2026', 'row must be untouched')
        self.assertEqual(ed['dirty'], {})

    def test_past_date_edit_is_accepted(self):
        api = _make_api()
        ed = _seed(api)
        res = api.update_editor_cell('nego-records', 0, 'cr63f_negotiationdate', '03/01/2020')
        self.assertTrue(res['ok'])
        self.assertEqual(ed['dirty'], {0: {'cr63f_negotiationdate': '03/01/2020'}})

    def test_unknown_column_and_row_are_rejected(self):
        api = _make_api()
        _seed(api)
        self.assertFalse(api.update_editor_cell('nego-records', 0, 'nope', 'x')['ok'])
        self.assertFalse(api.update_editor_cell('nego-records', 99, 'cr63f_stage', 'x')['ok'])

    def test_editor_table_exposes_dirty_cells_by_position(self):
        api = _make_api()
        _seed(api)
        api.update_editor_cell('nego-records', 1, 'cr63f_stage', 'Revisited')
        table = api._editor_table('nego-records')
        self.assertEqual(table['dirtyCount'], 1)
        self.assertIn('1:1', table['dirty'], 'row 1, column index 1 should be flagged')
        self.assertEqual(table['rows'][1][1], 'Revisited')
        self.assertEqual(table['scope'], 'nego-records')

    def test_save_requires_pending_edits(self):
        api = _make_api()
        _seed(api)
        self.assertFalse(api.save_editor_edits('nego-records')['ok'])

    def test_table_is_remembered_per_scope(self):
        api = _make_api()
        self.assertEqual(api._editor_table_name('nego-records'), '')
        api.ui_settings['editor_table_nego_records'] = 'cr63f_quezonnegorecord'
        api.ui_settings['editor_table_sourcing_records'] = 'cr63f_batangassourcingrecord'
        self.assertEqual(api._editor_table_name('nego-records'), 'cr63f_quezonnegorecord')
        self.assertEqual(api._editor_table_name('sourcing-records'), 'cr63f_batangassourcingrecord')
        # an explicit choice still wins over the remembered one
        self.assertEqual(api._editor_table_name('nego-records', 'cr63f_other'), 'cr63f_other')

    def test_editor_keys_never_collide_with_pipeline_keys(self):
        # The pipeline's publish/load targets must not move when the editor does.
        api = _make_api()
        api.ui_settings['nego_record_table'] = 'cr63f_quezonnegorecord'
        api.ui_settings['nego_productivity_table'] = 'cr63f_quezonnegoproductivity'
        for scope in Api._EDITOR_SCOPES:
            self.assertNotIn(Api._editor_pref_key(scope),
                             ('nego_record_table', 'sourcing_record_table',
                              'nego_productivity_table', 'sourcing_productivity_table'))
        # an editor pick leaves the pipeline keys untouched
        api.ui_settings[Api._editor_pref_key('nego-records')] = 'cr63f_tarlacnegorecord'
        self.assertEqual(api.ui_settings['nego_record_table'], 'cr63f_quezonnegorecord')

    def test_scope_tokens_bucket_real_table_names(self):
        # Guards the match rule against the actual naming in this environment.
        names = [
            'cr63f_quezonnegorecord', 'cr63f_batangassourcingrecord',
            'cr63f_quezonnegoproductivity', 'cr63f_batangassourcingproductivity',
            'cr63f_tarlacsourcingproductivity', 'cr63f_batangasbuild',
            'cr63f_teamcomposition',
        ]

        def bucket(scope):
            kind, kind_type = Api._EDITOR_SCOPES[scope]
            return [n for n in names if kind in n and kind_type in n]

        self.assertEqual(bucket('nego-records'), ['cr63f_quezonnegorecord'])
        self.assertEqual(bucket('sourcing-records'), ['cr63f_batangassourcingrecord'])
        self.assertEqual(bucket('nego-productivity'), ['cr63f_quezonnegoproductivity'])
        self.assertEqual(bucket('sourcing-productivity'),
                         ['cr63f_batangassourcingproductivity', 'cr63f_tarlacsourcingproductivity'])
        # reference tables must not leak into any scope
        for scope in Api._EDITOR_SCOPES:
            self.assertNotIn('cr63f_batangasbuild', bucket(scope))
            self.assertNotIn('cr63f_teamcomposition', bucket(scope))


if __name__ == '__main__':
    unittest.main()
