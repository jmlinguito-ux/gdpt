import unittest

from dataverse import DataverseClient


class PublishCheckerMappingTests(unittest.TestCase):
    def test_records_publish_checker_to_duplicate_checker(self):
        client = self._client()
        ctx = client._publish_context('cr63f_batangasnegorecord', ['CHECKER'])
        self.assertEqual(client._row_to_payload({'CHECKER': 'FIRST CONTACT'}, ctx),
                         {'cr63f_duplicatechecker': 'FIRST CONTACT'})

    def test_productivity_keeps_its_own_checker_field(self):
        client = self._client()
        ctx = client._publish_context('cr63f_batangasnegoproductivity', ['CHECKER'])
        self.assertEqual(client._row_to_payload({'CHECKER': 'FIRST CONTACT'}, ctx),
                         {'cr63f_checker': 'FIRST CONTACT'})

    @staticmethod
    def _client():
        client = DataverseClient.__new__(DataverseClient)
        client.get_entity_info = lambda _: {
            'entitySetName': 'records', 'primaryIdAttribute': 'recordid', 'attributes': {},
        }
        client._attribute_map = lambda _: {
            'checker': {'logical': 'cr63f_checker', 'label': 'Checker', 'type': 'String',
                        'isValidForCreate': True},
            'duplicatechecker': {'logical': 'cr63f_duplicatechecker', 'label': 'DUPLICATE CHECKER',
                                 'type': 'String', 'isValidForCreate': True},
        }
        return client


if __name__ == '__main__':
    unittest.main()
