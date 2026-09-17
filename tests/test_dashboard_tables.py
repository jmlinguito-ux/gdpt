import unittest

from app import Api
from dataverse import DataverseClient


def _make_api(mode='negotiation'):
    """A bare Api with only what the dashboard record-table methods touch.

    Api.__init__ builds the whole app (settings, updater, webview), so the
    picker logic is exercised against a hand-seeded instance instead.
    """
    api = Api.__new__(Api)
    api.ui_settings = {}
    api.mode = mode
    api.rows = []
    api.calculated = []
    api.has_calculated = False
    api.municipality_codes = []
    api.dv_client = None
    api.state = lambda: {}
    api._write_settings = lambda: None
    return api


class _FakeDv:
    """Just the Dataverse surface the dashboard uses."""

    def __init__(self, tables=None):
        self.tables = tables or []
        self.config = {}
        self.calls = []
        self.errors = {'nego': '', 'sourcing': ''}

    def signed_in(self):
        return True

    def get_solution_tables(self, solution_id=None):
        return list(self.tables)

    def get_dashboard_records(self, date_from=None, date_to=None, work_week=None,
                              mode='all', nego_table='', sourcing_table=''):
        self.calls.append({'date_from': date_from, 'date_to': date_to, 'work_week': work_week,
                           'mode': mode, 'nego_table': nego_table,
                           'sourcing_table': sourcing_table})
        return {'nego': [], 'sourcing': [],
                'tables': {'nego': nego_table, 'sourcing': sourcing_table},
                'errors': dict(self.errors)}


SOLUTION_TABLES = [
    {'logicalName': 'cr63f_batangasnegorecord', 'displayName': 'Batangas Negotiation Record'},
    {'logicalName': 'cr63f_quezonnegorecord', 'displayName': 'Quezon Negotiation Record'},
    {'logicalName': 'cr63f_batangassourcingrecord', 'displayName': 'Batangas Land Sourcing Record'},
    {'logicalName': 'cr63f_quezonnegoproductivity', 'displayName': 'Quezon Negotiation Productivity'},
    {'logicalName': 'cr63f_batangasbuild', 'displayName': 'Batangas Build'},
]


class DashboardTablePickTests(unittest.TestCase):
    def test_mode_tokens_normalise(self):
        self.assertEqual(Api._dashboard_mode(''), 'nego')
        self.assertEqual(Api._dashboard_mode('negotiation'), 'nego')
        self.assertEqual(Api._dashboard_mode('land-sourcing'), 'sourcing')
        self.assertEqual(Api._dashboard_mode('SOURCING'), 'sourcing')

    def test_prefs_are_dashboard_only_keys(self):
        self.assertEqual(Api._dashboard_table_pref_key('nego'), 'dashboard_table_nego')
        self.assertEqual(Api._dashboard_table_pref_key('land-sourcing'), 'dashboard_table_sourcing')
        # Never collide with the pipeline's publish targets.
        for mode in ('nego', 'sourcing'):
            self.assertNotIn('_record_table', Api._dashboard_table_pref_key(mode))

    def test_table_name_is_blank_until_picked(self):
        api = _make_api()
        # Nothing picked yet: blank, so nothing is read (no hidden default table).
        self.assertEqual(api._dashboard_table_name('nego'), '')
        self.assertEqual(api._dashboard_table_name('sourcing'), '')
        # a remembered pick is restored
        api.ui_settings['dashboard_table_nego'] = 'cr63f_quezonnegorecord'
        self.assertEqual(api._dashboard_table_name('nego'), 'cr63f_quezonnegorecord')
        self.assertEqual(api._dashboard_table_name('sourcing'), '')
        # an explicit pick beats the remembered one
        self.assertEqual(api._dashboard_table_name('nego', 'cr63f_tarlacnegorecord'),
                         'cr63f_tarlacnegorecord')

    def test_listing_offers_only_record_tables(self):
        api = _make_api()
        api.dv_client = _FakeDv(SOLUTION_TABLES)

        nego = api.get_dashboard_tables('nego')
        self.assertTrue(nego['ok'])
        self.assertEqual([t['logicalName'] for t in nego['tables']],
                         ['cr63f_batangasnegorecord', 'cr63f_quezonnegorecord'])
        # blank first time — nothing is preselected for the user
        self.assertEqual(nego['saved'], '')

        src = api.get_dashboard_tables('land-sourcing')
        self.assertEqual([t['logicalName'] for t in src['tables']],
                         ['cr63f_batangassourcingrecord'])

    def test_listing_works_while_disconnected(self):
        api = _make_api()
        res = api.get_dashboard_tables('nego')
        self.assertTrue(res['ok'])
        self.assertFalse(res['connected'])
        self.assertEqual(res['tables'], [])
        self.assertEqual(res['saved'], '')

    def test_listing_reports_the_remembered_pick(self):
        api = _make_api()
        api.dv_client = _FakeDv(SOLUTION_TABLES)
        api.ui_settings['dashboard_table_nego'] = 'cr63f_quezonnegorecord'
        self.assertEqual(api.get_dashboard_tables('nego')['saved'], 'cr63f_quezonnegorecord')
        # the other workstream keeps its own (empty) pick
        self.assertEqual(api.get_dashboard_tables('sourcing')['saved'], '')

    def test_save_dashboard_table_leaves_the_publish_target_alone(self):
        api = _make_api()
        api.ui_settings['nego_record_table'] = 'cr63f_batangasnegorecord'
        res = api.save_dashboard_table('nego', 'cr63f_quezonnegorecord')
        self.assertTrue(res['ok'])
        self.assertEqual(api.ui_settings['dashboard_table_nego'], 'cr63f_quezonnegorecord')
        self.assertEqual(api.ui_settings['nego_record_table'], 'cr63f_batangasnegorecord')
        # it is remembered, so the picker restores it next time
        self.assertEqual(api.get_dashboard_tables('nego')['saved'], 'cr63f_quezonnegorecord')
        # clearing the pick puts the workstream back to blank
        api.save_dashboard_table('nego', '')
        self.assertEqual(api._dashboard_table_name('nego'), '')

    def test_dashboard_data_forwards_the_picked_tables(self):
        api = _make_api()
        fake = _FakeDv(SOLUTION_TABLES)
        api.dv_client = fake
        res = api.get_dashboard_data({'mode': 'all', 'nego_table': 'cr63f_quezonnegorecord',
                                      'sourcing_table': 'cr63f_batangassourcingrecord'})
        self.assertTrue(res['ok'])
        self.assertEqual(res['tables'], {'nego': 'cr63f_quezonnegorecord',
                                         'sourcing': 'cr63f_batangassourcingrecord'})
        self.assertEqual(fake.calls[0]['nego_table'], 'cr63f_quezonnegorecord')
        self.assertEqual(fake.calls[0]['sourcing_table'], 'cr63f_batangassourcingrecord')

    def test_dashboard_data_uses_the_remembered_pick(self):
        api = _make_api()
        fake = _FakeDv(SOLUTION_TABLES)
        api.dv_client = fake
        api.ui_settings['dashboard_table_nego'] = 'cr63f_quezonnegorecord'
        res = api.get_dashboard_data({})
        self.assertTrue(res['ok'])
        self.assertEqual(res['tables']['nego'], 'cr63f_quezonnegorecord')
        self.assertEqual(fake.calls[0]['nego_table'], 'cr63f_quezonnegorecord')
        # the untouched workstream stays blank and is not read from Dataverse
        self.assertEqual(res['tables']['sourcing'], '')
        self.assertEqual(fake.calls[0]['sourcing_table'], '')

    def test_nothing_chosen_reads_nothing_from_dataverse(self):
        api = _make_api()
        fake = _FakeDv(SOLUTION_TABLES)
        api.dv_client = fake
        res = api.get_dashboard_data({})
        self.assertTrue(res['ok'])
        self.assertEqual(fake.calls, [], 'no table chosen means no Dataverse read at all')
        self.assertEqual(res['tables'], {'nego': '', 'sourcing': ''})
        self.assertEqual(res['summary']['total_records'], 0)

    def test_workspace_rows_survive_while_no_table_is_chosen(self):
        api = _make_api()
        fake = _FakeDv(SOLUTION_TABLES)
        api.dv_client = fake
        api.rows = [{'NEGO DATE': '01/05/2026', 'MUNICIPALITY': 'Batangas City',
                     'MUNICODE': '4101'}]
        res = api.get_dashboard_data({'mode': 'all'})
        self.assertEqual(fake.calls, [])
        self.assertEqual(res['summary']['total_nego'], 1)
        self.assertEqual(res['source'], 'Active Workspace')

    def test_choosing_one_workstream_leaves_the_other_on_the_workspace(self):
        api = _make_api()
        fake = _FakeDv(SOLUTION_TABLES)
        api.dv_client = fake
        api.rows = [{'NEGO DATE': '01/05/2026', 'MUNICIPALITY': 'Batangas City',
                     'MUNICODE': '4101'}]
        api.ui_settings['dashboard_table_sourcing'] = 'cr63f_batangassourcingrecord'
        res = api.get_dashboard_data({'mode': 'all'})
        self.assertEqual(res['tables']['nego'], '')
        self.assertEqual(res['tables']['sourcing'], 'cr63f_batangassourcingrecord')
        self.assertEqual(res['summary']['total_nego'], 1)      # from the workspace
        self.assertEqual(res['summary']['total_sourcing'], 0)  # Dataverse returned none


class _FakeClient(DataverseClient):
    """DataverseClient without transport: records which entity set was read."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.sets = []
        self.config = {}

    def get_entity_info(self, logical_name):
        return {'entitySetName': f'{logical_name}s', 'primaryIdAttribute': 'cr63f_id'}

    def _resolve(self, entity, *names):
        return {'logical': 'cr63f_' + names[0].lower().replace(' ', '')}

    def _get_all(self, entity_set, select, formatted=False, top=5000, **kwargs):
        self.sets.append(entity_set)
        return list(self.rows)


    def test_dashboard_data_surfaces_a_table_it_could_not_read(self):
        api = _make_api()
        fake = _FakeDv(SOLUTION_TABLES)
        fake.errors['nego'] = 'table cr63f_ghost not found'
        api.dv_client = fake
        res = api.get_dashboard_data({'nego_table': 'cr63f_ghost'})
        self.assertTrue(res['ok'])
        self.assertEqual(res['errors']['nego'], 'table cr63f_ghost not found')
        # the failure is reported, not silently turned into "no records"
        self.assertEqual(res['tables']['nego'], 'cr63f_ghost')


class _BrokenClient(_FakeClient):
    def get_entity_info(self, logical_name):
        raise RuntimeError(f'table {logical_name} not found')


class DashboardQueryTests(unittest.TestCase):
    def test_query_reads_the_requested_nego_table(self):
        client = _FakeClient()
        out = client.get_dashboard_records('', '', '', 'all',
                                           nego_table='cr63f_quezonnegorecord')
        self.assertIn('cr63f_quezonnegorecords', client.sets)
        self.assertNotIn('cr63f_batangasnegorecords', client.sets)
        self.assertEqual(out['tables']['nego'], 'cr63f_quezonnegorecord')
        # no sourcing table was named, so nothing is read for that workstream
        self.assertEqual(out['tables']['sourcing'], '')
        self.assertNotIn('cr63f_batangassourcingrecords', client.sets)

    def test_query_reads_the_requested_sourcing_table(self):
        client = _FakeClient()
        out = client.get_dashboard_records('', '', '', 'all',
                                           sourcing_table='cr63f_tarlacsourcingrecord')
        self.assertIn('cr63f_tarlacsourcingrecords', client.sets)
        self.assertNotIn('cr63f_batangassourcingrecords', client.sets)
        self.assertEqual(out['tables']['sourcing'], 'cr63f_tarlacsourcingrecord')

    def test_query_reports_a_table_it_cannot_read(self):
        client = _BrokenClient()
        out = client.get_dashboard_records('', '', '', 'all',
                                           nego_table='cr63f_ghostnegorecord')
        self.assertEqual(out['nego'], [])
        self.assertEqual(out['tables']['nego'], 'cr63f_ghostnegorecord')
        self.assertIn('cr63f_ghostnegorecord', out['errors']['nego'])

    def test_query_without_tables_reads_nothing(self):
        client = _FakeClient()
        out = client.get_dashboard_records()
        self.assertEqual(client.sets, [], 'no table named means no hidden default is read')
        self.assertEqual(out['tables'], {'nego': '', 'sourcing': ''})
        self.assertEqual(out['nego'], [])
        self.assertEqual(out['sourcing'], [])


if __name__ == '__main__':
    unittest.main()
