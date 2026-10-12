import unittest

from dataverse import DataverseClient


def _attr(logical, label, kind='String'):
    return {'logical': logical, 'label': label, 'type': kind,
            'isValidForCreate': True, 'isValidForUpdate': True}


NEGO_RECORD = {
    'negotiationdate': _attr('cr63f_negotiationdate', 'NEGO DATE', 'DateTime'),
    'negodate': _attr('cr63f_negotiationdate', 'NEGO DATE', 'DateTime'),
    'areaindex': _attr('cr63f_areaindex', 'AREA-INDEX'),
}
SOURCING_RECORD = {
    'reportdate': _attr('cr63f_reportdate', 'REPORT DATE', 'DateTime'),
    'areaindex': _attr('cr63f_areaindex', 'AREA-INDEX'),
}
SOURCING_PRODUCTIVITY = {
    'sourcingdate': _attr('cr63f_sourcingdate', 'SOURCING DATE', 'DateTime'),
    'areaindex': _attr('cr63f_areaindex', 'AREA INDEX'),
}
WORK_WEEK = {'workweek': _attr('cr63f_workweek', 'Work Week')}


class PublishWorkWeekTests(unittest.TestCase):
    def test_nego_record_publish_fills_work_week_from_nego_date(self):
        payload = self._payload({**NEGO_RECORD, **WORK_WEEK},
                                {'NEGO DATE': '09/02/2025', 'AREA-INDEX': 'A1'})
        self.assertEqual(payload['cr63f_workweek'], '2025-36')

    def test_sourcing_record_publish_fills_work_week_from_report_date(self):
        payload = self._payload({**SOURCING_RECORD, **WORK_WEEK},
                                {'REPORT DATE': '12/30/2025', 'AREA-INDEX': 'A1'})
        self.assertEqual(payload['cr63f_workweek'], '2026-01')

    def test_supplied_work_week_is_kept(self):
        payload = self._payload({**SOURCING_PRODUCTIVITY, **WORK_WEEK},
                                {'SOURCING DATE': '10/12/2026', 'WORK WEEK': '2026-41'})
        self.assertEqual(payload['cr63f_workweek'], '2026-41')

    def test_table_without_work_week_column_is_unchanged(self):
        payload = self._payload(dict(NEGO_RECORD), {'NEGO DATE': '09/02/2025', 'AREA-INDEX': 'A1'})
        self.assertNotIn('cr63f_workweek', payload)

    def test_grid_date_edit_moves_record_to_new_work_week(self):
        client = self._client({**NEGO_RECORD, **WORK_WEEK})
        fields = {'cr63f_negotiationdate': '2026-10-13'}
        client._fill_work_week(fields, client._work_week_columns('cr63f_tarlacnegorecord'))
        self.assertEqual(fields['cr63f_workweek'], '2026-42')

    def test_grid_edit_without_date_does_not_touch_work_week(self):
        client = self._client({**NEGO_RECORD, **WORK_WEEK})
        fields = {'cr63f_areaindex': 'A2'}
        client._fill_work_week(fields, client._work_week_columns('cr63f_tarlacnegorecord'))
        self.assertNotIn('cr63f_workweek', fields)

    def _payload(self, attributes, row):
        client = self._client(attributes)
        ctx = client._publish_context('cr63f_tarlacnegorecord', list(row))
        return client._row_to_payload(row, ctx)

    @staticmethod
    def _client(attributes):
        client = DataverseClient.__new__(DataverseClient)
        client.get_entity_info = lambda _: {
            'entitySetName': 'records', 'primaryIdAttribute': 'recordid', 'attributes': {},
        }
        client._attribute_map = lambda _: attributes
        return client


if __name__ == '__main__':
    unittest.main()
