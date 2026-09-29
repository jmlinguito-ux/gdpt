import unittest

from dataverse import DataverseClient, parse_numeric_value


class NumericValueTests(unittest.TestCase):
    def test_invalid_double_text_is_rejected_instead_of_becoming_zero(self):
        client = self._client('Double')

        with self.assertRaisesRegex(ValueError, 'numeric'):
            client.format_attribute_value('example', 'cr63f_amount', 'not-a-number')

    def test_valid_double_and_grouped_number_are_numeric(self):
        client = self._client('Double')

        self.assertEqual(client.format_attribute_value('example', 'cr63f_amount', '0.75'), 0.75)
        self.assertEqual(client.format_attribute_value('example', 'cr63f_amount', '1,234.5'), 1234.5)

    def test_integer_attributes_reject_fractional_or_text_values(self):
        client = self._client('Integer')

        with self.assertRaisesRegex(ValueError, 'whole number'):
            client.format_attribute_value('example', 'cr63f_amount', '1.5')
        with self.assertRaisesRegex(ValueError, 'whole number'):
            client.format_attribute_value('example', 'cr63f_amount', 'text')

    def test_numeric_reader_uses_raw_money_value(self):
        client = DataverseClient.__new__(DataverseClient)
        client.get_entity_info = lambda _: {'entitySetName': 'examples', 'primaryIdAttribute': 'exampleid'}
        client.get_table_columns = lambda _: [
            {'logical': 'cr63f_amount', 'label': 'AMOUNT', 'type': 'Money'},
        ]
        client._get_all = lambda *args, **kwargs: [{
            'cr63f_amount': 125.5,
            'cr63f_amount@OData.Community.Display.V1.FormattedValue': '$125.50',
            'exampleid': 'guid-1',
        }]

        result = client.load_table_records('example', ['cr63f_amount'])

        self.assertEqual(result['rows'][0]['cr63f_amount'], '125.5')

    @staticmethod
    def _client(attr_type):
        client = DataverseClient.__new__(DataverseClient)
        meta = {'logical': 'cr63f_amount', 'label': 'AMOUNT', 'type': attr_type}
        client._attribute_map = lambda _: {'cr63famount': meta}
        return client


if __name__ == '__main__':
    unittest.main()
