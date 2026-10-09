import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import offer_letter as ol
import productivity_tool as pt
from app import Api
from dataverse import DataverseClient


def visit(day, index='7', area='A-7', **extra):
    return {'INDEX NO': index, 'AREA-INDEX': area, 'NEGO DATE': day,
            'NEGOTIATOR NAME': 'Alice / Bob', 'CLASSIFICATION': 'OPEN', **extra}


class OfferLetterTests(unittest.TestCase):
    def test_four_actions_and_future_offer_ignored(self):
        rows = [visit('01/01/2026'), visit('01/02/2026'), visit('01/03/2026'),
                visit('01/02/2026', index='8', area='B-8')]
        offers = [{'areaIndex': 'A-7', 'offerDate': '01/02/2026', 'offerType': 'COUNTER OFFER'},
                  {'areaIndex': 'B-8', 'offerDate': '01/05/2026'}]
        history = [visit('01/01/2026', index='8', area='B-8')]
        self.assertEqual([a for a, _ in ol.calculate_actions(rows, offers, history)], list(ol.ACTIONS))

    def test_offer_served_wins_on_initial_visit_date(self):
        self.assertEqual(ol.calculate_actions([visit('01/01/2026')],
                         [{'areaIndex': 'A-7', 'offerDate': '01/01/2026'}])[0], ('OFFER SERVED', ''))

    def test_title_fallback_nonbreaking_spaces_and_cross_identifier_match(self):
        for area in ('', 'FOR PLOTTING'):
            row = visit('01/02/2026', area=area, **{'TITLE NO': '  T\xa0-1  '})
            offers = [{'areaIndex': 'T-1', 'offerDate': '2026-01-02T00:00:00Z'}]
            self.assertEqual(ol.calculate_actions([row], offers, [visit('01/01/2026')])[0], ('OFFER SERVED', ''))

    def test_first_date_is_by_index_not_area_or_contributor(self):
        rows = [visit('01/02/2026'), visit('01/02/2026')]
        history = [visit('01/01/2026', area='OTHER-AREA', index='7.0')]
        self.assertEqual([a for a, _ in ol.calculate_actions(rows, [], history)],
                         ['REVISITED - WITHOUT OFFER'] * 2)
        self.assertEqual([a for a, _ in ol.calculate_actions(rows, [])], ['INITIAL VISIT'] * 2)

    def test_missing_history_reference_identifiers_and_bad_dates_are_issues(self):
        current = visit('01/02/2026')
        cases = [ol.calculate_actions([current], [], reference_ready=False),
                 ol.calculate_actions([current], [], history_ready=False),
                 ol.calculate_actions([visit('bad')], []),
                 ol.calculate_actions([visit('01/02/2026', index='')], []),
                 ol.calculate_actions([current], [{'areaIndex': 'A-7', 'offerDate': ''}]),
                 ol.calculate_actions([current], [], [visit('bad')])]
        for result in cases:
            self.assertEqual(result[0][0], '')
            self.assertTrue(result[0][1])

    def test_unrelated_invalid_offer_does_not_block_visit(self):
        self.assertEqual(ol.calculate_actions([visit('01/02/2026')], [{'areaIndex': 'OTHER', 'offerDate': ''}])[0],
                         ('INITIAL VISIT', ''))

    def test_negotiation_calculation_propagates_before_name_expansion(self):
        rows = [visit('01/02/2026')]
        calculated = pt.calculate_review_rows(rows, [], [], [], [],
            offer_rows=[{'titleNo': 'A-7', 'offerDate': '01/02/2026'}],
            offer_history=[visit('01/01/2026')])
        self.assertEqual(calculated[0][ol.ACTION_COLUMN], 'OFFER SERVED')
        generated = pt.build_productivity_rows(calculated, [], [], [], [])
        self.assertEqual([r[ol.ACTION_COLUMN] for r in generated], ['OFFER SERVED', 'OFFER SERVED'])

    def test_land_sourcing_excludes_reference_column_and_calculation(self):
        api = Api.__new__(Api)
        api.mode = 'land-sourcing'
        self.assertNotIn('offer', api.reference_kinds())
        self.assertNotIn(ol.ACTION_COLUMN, pt.get_review_headers(api.mode))
        self.assertNotIn(ol.ACTION_COLUMN, pt.get_output_columns(api.mode))
        self.assertFalse(api.get_reference_rows('offer')['ok'])
        self.assertFalse(api.upload_reference_file('offer')['ok'])
        row = {'REPORT DATE': '01/02/2026', 'AREA-INDEX': 'A-7', ol.ACTION_COLUMN: 'STALE'}
        calculated = pt.calculate_review_rows([row], [], [], [], [], mode=api.mode)
        self.assertNotIn(ol.ACTION_COLUMN, calculated[0])
        self.assertEqual(api._offer_calculation_context(), {})

    def test_reference_validation(self):
        self.assertEqual(ol.validate_cell('landArea', '1,234.5678'), '1234.5678')
        self.assertEqual(ol.validate_cell('landArea', '0'), '0')
        self.assertEqual(ol.validate_cell('offerType', 'counter offer'), 'COUNTER OFFER')
        for value in ('1/2/26', '1-2-2026', '2026-01-02', '2026-01-02T00:00:00Z', 'January 2, 2026'):
            self.assertEqual(ol.validate_cell('offerDate', value), '01/02/2026')
        self.assertEqual(ol.validate_cell('offerDate', ''), '')
        for key, value in [('landArea', 'text'), ('landArea', '-1'), ('landArea', 'NaN'),
                           ('offerType', 'OTHER'), ('offerDate', '02/30/2026'), ('offerLetterLink', 'not-a-url')]:
            with self.assertRaises(ValueError):
                ol.validate_cell(key, value)

    def test_csv_import_preserves_all_ten_fields_and_title_only_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'offer.csv'
            with path.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.writer(handle)
                writer.writerow([label for _, label in ol.FIELDS])
                writer.writerow(['', 'BATANGAS', 'LIAN', 'T-1', 'Owner', 'Rep', '123.45', '01/02/2026', 'COUNTER OFFER', 'https://example.com/offer'])
            loaded = pt.load_single_reference(str(path), 'offer')
            self.assertEqual(set(loaded[0]), {key for key, _ in ol.FIELDS})
            self.assertEqual(loaded[0]['titleNo'], 'T-1')
            self.assertEqual(loaded[0]['province'], 'BATANGAS')
            self.assertEqual(loaded[0]['municipality'], 'LIAN')

    def test_action_read_only_at_both_workspaces(self):
        api = Api.__new__(Api)
        self.assertFalse(api.update_review_cell(0, ol.ACTION_COLUMN, 'MANUAL')['ok'])
        self.assertFalse(api.update_productivity_cell(0, ol.ACTION_COLUMN, 'MANUAL')['ok'])

    def test_file_history_preserves_index_for_title_only_visits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.csv'
            path.write_text('ID,AREA-INDEX,INDEX NO,NEGO DATE\n1,,7,01/01/2026\n', encoding='utf-8')
            for records in (pt.load_single_reference(str(path), 'existing'), pt.load_existing_records(str(path))):
                self.assertEqual(records[0]['indexNo'], '7')
                self.assertEqual(ol.calculate_actions([visit('01/02/2026')], [], records)[0],
                                 ('REVISITED - WITHOUT OFFER', ''))

    def test_offer_loader_formats_raw_dates_without_using_regional_display_values(self):
        client = DataverseClient.__new__(DataverseClient)
        client.get_entity_info = lambda _: {'entitySetName': 'offers', 'primaryIdAttribute': 'rowid'}
        client._resolve = lambda _, label, logical: {'logical': logical}
        client._get_all = Mock(return_value=[{'rowid': '123', 'cr63f_offerdate': '2026-01-02T00:00:00Z',
            'cr63f_province': 'BATANGAS', 'cr63f_municipality': 'LIAN',
            'cr63f_offerdate@OData.Community.Display.V1.FormattedValue': '02/01/2026',
            'cr63f_landareasqm': 1234.5,
            'cr63f_landareasqm@OData.Community.Display.V1.FormattedValue': '1.234,5',
            'cr63f_offertype': 100000001,
            'cr63f_offertype@OData.Community.Display.V1.FormattedValue': 'COUNTER OFFER'}])
        row = client.load_arbitrary_reference_table('cr63f_offerletter', 'offer')[0]
        self.assertEqual(row['offerDate'], '01/02/2026')
        self.assertEqual(row['landArea'], 1234.5)
        self.assertEqual(row['offerType'], 'COUNTER OFFER')
        self.assertEqual(row['province'], 'BATANGAS')
        self.assertEqual(row['municipality'], 'LIAN')

    def test_reference_view_formats_already_loaded_iso_date_and_preserves_blank(self):
        api = Api.__new__(Api)
        api.mode = 'negotiation'
        api.ref_sources = {}
        api.offer_rows = [{'offerDate': '2026-01-02T00:00:00Z'}, {'offerDate': ''}]
        table = api.get_reference_rows('offer')
        date_index = table['keys'].index('offerDate')
        self.assertEqual([row[date_index] for row in table['rows']], ['01/02/2026', ''])

    def test_index_history_queries_only_relevant_values_and_columns(self):
        client = DataverseClient.__new__(DataverseClient)
        client.config = {}
        client.get_entity_info = lambda _: {'entitySetName': 'visits'}
        client._resolve = lambda _, *names: {'logical': 'idx' if names[0] == 'INDEX NO' else 'date', 'type': 'String'}
        client._get_all = Mock(return_value=[{'idx': '7', 'date': '2026-01-01'}])
        self.assertEqual(client.get_negotiation_visit_history(['7', '8']),
                         [{'INDEX NO': '7', 'NEGO DATE': '2026-01-01'}])
        self.assertEqual(client._get_all.call_args.args, ('visits', ['idx', 'date']))
        self.assertEqual(client._get_all.call_args.kwargs['filter_str'], "idx eq '7' or idx eq '8'")

    def test_offer_choice_is_resolved_to_numeric_dataverse_value(self):
        client = DataverseClient.__new__(DataverseClient)
        client.api_root = 'https://example/api/'
        client._attribute_map = lambda _: {'offertype': {'logical': 'cr63f_offertype', 'label': 'OFFER TYPE', 'type': 'Picklist'}}
        client._get = lambda _: {'OptionSet': {'Options': [{'Value': 100000001, 'Label': {'LocalizedLabels': [{'Label': 'COUNTER OFFER'}]}}]}}
        self.assertEqual(client.format_attribute_value('cr63f_offerletter', 'OFFER TYPE', 'COUNTER OFFER'), 100000001)

    def test_reference_edit_rejects_invalid_numeric_before_local_or_remote_write(self):
        api = Api.__new__(Api)
        api.mode = 'negotiation'
        api.offer_rows = [{'landArea': '12'}]
        self.assertFalse(api.update_reference_cell('offer', 0, 'landArea', 'TEXT')['ok'])
        self.assertEqual(api.offer_rows[0]['landArea'], '12')

    def test_history_failure_does_not_reuse_stale_results(self):
        api = Api.__new__(Api)
        api.mode = 'negotiation'
        api.rows = [visit('01/02/2026')]
        api.ref_sources = {'offer': 'Dataverse: cr63f_offerletter', 'existing': 'Dataverse: cr63f_batangasnegorecord'}
        api.offer_rows = []
        api.dv_client = Mock()
        api.dv_client.signed_in.return_value = True
        api.dv_client.get_negotiation_visit_history.side_effect = RuntimeError('unavailable')
        self.assertFalse(api._offer_calculation_context(refresh=True)['offer_history_ready'])

    def test_successfully_loaded_empty_reference_is_valid(self):
        api = Api.__new__(Api)
        api.mode = 'negotiation'
        api.rows = [visit('01/02/2026')]
        api.ref_sources = {'offer': 'Dataverse: cr63f_offerletter'}
        api.offer_rows = []
        api.dv_client = Mock()
        api.dv_client.signed_in.return_value = True
        api.dv_client.get_negotiation_visit_history.return_value = []
        context = api._offer_calculation_context(refresh=True)
        calculated = pt.calculate_review_rows(api.rows, [], [], [], [], **context)
        self.assertEqual(calculated[0][ol.ACTION_COLUMN], 'INITIAL VISIT')

    def test_publish_rejects_unresolved_action(self):
        api = Api.__new__(Api)
        api.mode = 'negotiation'
        _, _, _, errors, _ = api._partition_rows([{**visit('01/02/2026'), ol.ACTION_COLUMN: '',
            '_offer_letter_issue': 'Load reference'}], set())
        self.assertEqual(errors[0]['issues'], ['Load reference'])
        _, _, _, errors, _ = api._partition_rows([visit('01/02/2026')], set())
        self.assertTrue(errors)

    def test_publish_requires_calculation_even_if_file_contains_action(self):
        api = Api.__new__(Api)
        api.mode = 'negotiation'
        api.has_calculated = False
        api.dv_client = Mock()
        api.rows = [{**visit('01/02/2026'), ol.ACTION_COLUMN: 'INITIAL VISIT'}]
        self.assertFalse(api.validate_publish_records('review', 'cr63f_batangasnegorecord')['ok'])
        self.assertFalse(api._publish_stage('review', 'cr63f_batangasnegorecord')['ok'])
        api.dv_client.publish_records_batch.assert_not_called()

    def test_action_publish_mapping_and_missing_column_guard(self):
        client = DataverseClient.__new__(DataverseClient)
        client.get_entity_info = lambda _: {'entitySetName': 'rows', 'primaryIdAttribute': 'rowid'}
        client._attribute_map = lambda _: {'offerletteraction': {'logical': 'cr63f_offerletteraction',
            'label': ol.ACTION_COLUMN, 'type': 'String', 'isValidForCreate': True}}
        for table in ('cr63f_batangasnegorecord', 'cr63f_batangasnegoproductivity'):
            context = client._publish_context(table, [ol.ACTION_COLUMN])
            self.assertEqual(client._row_to_payload({ol.ACTION_COLUMN: 'OFFER SERVED'}, context),
                             {'cr63f_offerletteraction': 'OFFER SERVED'})
        client._attribute_map = lambda _: {}
        with self.assertRaisesRegex(ValueError, 'OFFER LETTER ACTION'):
            client._publish_context('cr63f_batangasnegorecord', [ol.ACTION_COLUMN])


if __name__ == '__main__':
    unittest.main()
