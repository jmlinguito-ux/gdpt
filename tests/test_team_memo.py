import os
import tempfile
import unittest

from dataverse import DataverseClient
from app import Api
import productivity_tool as pt


class _FakeTeamClient(DataverseClient):
    """Prove the field is detected from its display name, whatever its logical name."""

    def __init__(self):
        self.config = {}
        self.last_select = []

    def get_entity_info(self, logical_name):
        return {
            'entitySetName': f'{logical_name}s',
            'primaryIdAttribute': 'cr63f_teamcompositionid',
        }

    def _attribute_map(self, entity_logical):
        fields = {
            'Employee Name': 'cr63f_name',
            'Team': 'cr63f_team',
            'Group': 'cr63f_group',
            'Department': 'cr63f_department',
            'Memo No.': 'cr63f_custom_memofield',
        }
        mapping = {}
        for label, logical in fields.items():
            meta = {'logical': logical, 'type': 'String', 'label': label}
            mapping[''.join(ch for ch in label.lower() if ch.isalnum())] = meta
            mapping[''.join(ch for ch in logical.lower() if ch.isalnum())] = meta
        return mapping

    def _get_all(self, entity_set, select, formatted=False, top=5000, **kwargs):
        self.last_select = select
        return [{
            'cr63f_name': 'SANTOS',
            'cr63f_team': 'TEAM 1',
            'cr63f_group': 'GROUP A',
            'cr63f_department': 'COMDEV',
            'cr63f_custom_memofield': 'MEMO-2026-014',
            'cr63f_teamcompositionid': 'record-guid',
        }]


class _FakeProductivityClient(DataverseClient):
    def __init__(self, writable=True):
        self.writable = writable

    def get_entity_info(self, logical_name):
        return {
            'entitySetName': f'{logical_name}s',
            'primaryIdAttribute': f'{logical_name}id',
            'attributes': {
                'cr63f_memoref': {
                    'label': 'Memo Ref', 'type': 'String',
                    'isValidForCreate': self.writable,
                    'isValidForUpdate': self.writable,
                },
            },
        }

    def _attribute_map(self, entity_logical):
        meta = {
            'logical': 'cr63f_memoref', 'label': 'Memo Ref', 'type': 'String',
            'isValidForCreate': self.writable,
            'isValidForUpdate': self.writable,
        }
        return {'memoref': meta, 'cr63fmemoref': meta}


class TeamMemoTests(unittest.TestCase):
    def test_live_team_reader_detects_memo_by_display_name(self):
        client = _FakeTeamClient()
        rows = client.get_team_composition()
        self.assertEqual(rows[0]['memoNo'], 'MEMO-2026-014')
        self.assertIn('cr63f_custom_memofield', client.last_select)

    def test_arbitrary_team_reader_detects_memo_by_display_name(self):
        client = _FakeTeamClient()
        rows = client.load_arbitrary_reference_table('cr63f_otherteam', 'team')
        self.assertEqual(rows[0]['memoNo'], 'MEMO-2026-014')
        self.assertIn('cr63f_custom_memofield', client.last_select)

    def test_team_view_and_file_import_include_memo_number(self):
        self.assertIn(('memoNo', 'Memo No.'), pt.REFERENCE_VIEW_COLUMNS['team'])
        handle, path = tempfile.mkstemp(suffix='.csv')
        os.close(handle)
        try:
            with open(path, 'w', encoding='utf-8', newline='') as stream:
                stream.write('Employee Name,Team,Group,Department,Memo No.\n')
                stream.write('SANTOS,TEAM 1,GROUP A,COMDEV,MEMO-2026-014\n')
            rows = pt.load_single_reference(path, 'team')
            self.assertEqual(rows[0]['memoNo'], 'MEMO-2026-014')
        finally:
            os.unlink(path)

    def test_one_nonblank_duplicate_memo_wins_over_blanks(self):
        lookup = pt.build_team_lookup([
            {'employeeName': 'SANTOS', 'team': 'TEAM 1', 'group': 'GROUP A', 'memoNo': ''},
            {'employeeName': 'SANTOS', 'team': 'TEAM 1', 'group': 'GROUP A', 'memoNo': ' Memo-014 '},
        ])
        self.assertEqual(lookup['santos']['memoRef'], 'Memo-014')
        self.assertFalse(lookup['santos']['memoAmbiguous'])

    def test_identical_duplicate_memos_are_not_ambiguous(self):
        lookup = pt.build_team_lookup([
            {'employeeName': 'SANTOS', 'memoNo': 'Memo-014'},
            {'employeeName': 'santos', 'memoNo': 'MEMO-014'},
        ])
        self.assertEqual(lookup['santos']['memoRef'], 'Memo-014')
        self.assertFalse(lookup['santos']['memoAmbiguous'])

    def test_conflicting_duplicate_memos_are_blank_and_blocking(self):
        lookup = pt.build_team_lookup([
            {'employeeName': 'SANTOS', 'memoNo': 'MEMO-014'},
            {'employeeName': 'Santos', 'memoNo': 'MEMO-099'},
        ])
        self.assertEqual(lookup['santos']['memoRef'], '')
        self.assertTrue(lookup['santos']['memoAmbiguous'])
        self.assertIn('MEMO-014', lookup['santos']['memoError'])
        self.assertIn('MEMO-099', lookup['santos']['memoError'])

    def test_workspace_derives_memo_ref_from_matched_name(self):
        rows = [{'NEGOTIATOR NAME': 'J. SANTOS', 'MATCHED NEGOTIATOR NAME': 'SANTOS',
                 'TEAM': 'TEAM 1', 'GROUP': 'GROUP A'}]
        teams = [{'employeeName': 'SANTOS', 'team': 'TEAM 1', 'group': 'GROUP A',
                  'memoNo': 'MEMO-014'}]
        enriched = pt.enrich_workspace_rows(rows, teams)
        self.assertEqual(enriched[0]['MEMO REF'], 'MEMO-014')
        self.assertEqual(enriched[0]['_MEMO_REF_ERROR'], '')

    def test_workspace_tracks_conflict_separately_from_value(self):
        row = {'MATCHED NEGOTIATOR NAME': 'SANTOS', 'TEAM': 'TEAM 1', 'GROUP': 'GROUP A'}
        teams = [
            {'employeeName': 'SANTOS', 'memoNo': 'MEMO-014'},
            {'employeeName': 'SANTOS', 'memoNo': 'MEMO-099'},
        ]
        enriched = pt.enrich_workspace_rows([row], teams)[0]
        self.assertEqual(enriched['MEMO REF'], '')
        self.assertIn('Conflicting Memo No.', enriched['_MEMO_REF_ERROR'])

    def test_memo_ref_is_exported_after_matched_negotiator(self):
        columns = pt.get_output_columns('negotiation')
        matched = columns.index('MATCHED NEGOTIATOR NAME')
        self.assertEqual(columns[matched + 1], 'MEMO REF')

    def test_csv_export_contains_memo_ref_value(self):
        handle, path = tempfile.mkstemp(suffix='.csv')
        os.close(handle)
        try:
            pt.write_output([{'MATCHED NEGOTIATOR NAME': 'SANTOS',
                              'MEMO REF': 'MEMO-014'}], path, 'negotiation')
            with open(path, 'r', encoding='utf-8-sig') as stream:
                content = stream.read()
            self.assertIn('MEMO REF', content.splitlines()[0])
            self.assertIn('MEMO-014', content)
        finally:
            os.unlink(path)

    def test_team_refresh_recalculates_existing_productivity_rows(self):
        api = Api.__new__(Api)
        api.teams = [{'employeeName': 'SANTOS', 'team': 'TEAM 2',
                      'group': 'GROUP B', 'memoNo': 'MEMO-NEW'}]
        api.productivity = [{'MATCHED NEGOTIATOR NAME': 'SANTOS',
                             'TEAM': 'TEAM 1', 'GROUP': 'GROUP A',
                             'MEMO REF': 'MEMO-OLD'}]
        api._refresh_productivity_team_derivations()
        row = api.productivity[0]
        self.assertEqual(row['MEMO REF'], 'MEMO-NEW')
        self.assertEqual(row['CORRECT TEAM'], 'TEAM 2')
        self.assertEqual(row['CORRECT GROUP'], 'GROUP B')

    def test_refresh_clears_ambiguity_after_team_data_is_corrected(self):
        api = Api.__new__(Api)
        api.productivity = [{'MATCHED NEGOTIATOR NAME': 'SANTOS'}]
        api.teams = [
            {'employeeName': 'SANTOS', 'memoNo': 'MEMO-A'},
            {'employeeName': 'SANTOS', 'memoNo': 'MEMO-B'},
        ]
        api._refresh_productivity_team_derivations()
        self.assertTrue(api.productivity[0]['_MEMO_REF_ERROR'])
        api.teams[1]['memoNo'] = 'MEMO-A'
        api._refresh_productivity_team_derivations()
        self.assertEqual(api.productivity[0]['MEMO REF'], 'MEMO-A')
        self.assertEqual(api.productivity[0]['_MEMO_REF_ERROR'], '')

    def test_backend_partition_blocks_memo_ambiguity(self):
        api = Api.__new__(Api)
        row = {'AREA INDEX': 'BAT-001', 'NEGO DATE': '09/20/2026',
               'MATCHED NEGOTIATOR NAME': 'SANTOS', 'MEMO REF': '',
               '_MEMO_REF_ERROR': 'Conflicting Memo No. values for SANTOS: A, B.'}
        writable, duplicates, updatable, errors, _ = api._partition_rows([row], set())
        self.assertEqual(writable, [])
        self.assertEqual(duplicates, [])
        self.assertEqual(updatable, [])
        self.assertEqual(len(errors), 1)
        self.assertIn('MEMO REF', errors[0]['issues'][0])

    def test_backend_partition_allows_blank_nonambiguous_memo(self):
        api = Api.__new__(Api)
        row = {'AREA INDEX': 'BAT-001', 'NEGO DATE': '09/20/2026',
               'MATCHED NEGOTIATOR NAME': 'SANTOS', 'MEMO REF': '',
               '_MEMO_REF_ERROR': ''}
        writable, _, _, errors, _ = api._partition_rows([row], set())
        self.assertEqual(len(writable), 1)
        self.assertEqual(errors, [])

    def test_publish_context_maps_memo_ref_to_logical_column(self):
        client = _FakeProductivityClient()
        ctx = client._publish_context('cr63f_testproductivity', ['MEMO REF'])
        self.assertEqual(ctx['col_map']['MEMO REF'], 'cr63f_memoref')
        payload = client._row_to_payload({'MEMO REF': ' MEMO-014 '}, ctx)
        self.assertEqual(payload['cr63f_memoref'], 'MEMO-014')

    def test_target_validation_rejects_missing_or_readonly_memo_ref(self):
        api = Api.__new__(Api)
        api.dv_client = _FakeProductivityClient(writable=False)
        self.assertIn('does not contain a writable Memo Ref',
                      api._productivity_memo_target_error('cr63f_testproductivity'))

    def test_target_validation_accepts_writable_memo_ref(self):
        api = Api.__new__(Api)
        api.dv_client = _FakeProductivityClient(writable=True)
        self.assertEqual(api._productivity_memo_target_error('cr63f_testproductivity'), '')


if __name__ == '__main__':
    unittest.main()
