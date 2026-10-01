import os
import tempfile
import unittest

import productivity_tool as pt
from dataverse import DataverseClient


class _FakeTeamClient(DataverseClient):
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
            'Memo Code': 'cr63f_memono',
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
            'cr63f_memono': 'MEMO-2026-014',
            'cr63f_teamcompositionid': 'record-guid',
        }]


class TeamMemoReferenceTests(unittest.TestCase):
    def test_live_team_reader_loads_memo_code_from_field_metadata(self):
        client = _FakeTeamClient()
        rows = client.get_team_composition()
        self.assertEqual(rows[0]['memoNo'], 'MEMO-2026-014')
        self.assertIn('cr63f_memono', client.last_select)

    def test_arbitrary_team_reader_loads_memo_code(self):
        client = _FakeTeamClient()
        rows = client.load_arbitrary_reference_table('cr63f_otherteam', 'team')
        self.assertEqual(rows[0]['memoNo'], 'MEMO-2026-014')
        self.assertIn('cr63f_memono', client.last_select)

    def test_team_reference_view_and_template_include_memo_number(self):
        self.assertIn(('memoNo', 'Memo No.'), pt.REFERENCE_VIEW_COLUMNS['team'])
        self.assertIn('Memo No.', pt.REFERENCE_TEMPLATE_HEADERS['team'])

    def test_team_file_import_accepts_memo_code_header(self):
        handle, path = tempfile.mkstemp(suffix='.csv')
        os.close(handle)
        try:
            with open(path, 'w', encoding='utf-8', newline='') as stream:
                stream.write('Employee Name,Team,Group,Department,Memo Code\n')
                stream.write('SANTOS,TEAM 1,GROUP A,COMDEV,MEMO-2026-014\n')
            rows = pt.load_single_reference(path, 'team')
            self.assertEqual(rows[0]['memoNo'], 'MEMO-2026-014')
        finally:
            os.unlink(path)

    def test_reference_sync_resolves_memo_code_to_stable_logical_name(self):
        client = _FakeTeamClient()
        self.assertEqual(
            client.resolve_field_name('cr63f_teamcomposition', 'team', 'memoNo'),
            'cr63f_memono',
        )


if __name__ == '__main__':
    unittest.main()
