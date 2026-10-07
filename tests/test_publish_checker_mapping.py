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

    def test_productivity_publish_maps_build_to_text_column(self):
        client = self._client()
        ctx = client._publish_context('cr63f_batangasnegoproductivity', ['BUILD'])
        self.assertEqual(client._row_to_payload({'BUILD': 'BUILD-2'}, ctx),
                         {'cr63f_build': 'BUILD-2'})

    def test_productivity_publish_rejects_missing_build_column(self):
        client = self._client(include_build=False)
        with self.assertRaisesRegex(ValueError, 'missing a writable BUILD text column'):
            client._publish_context('cr63f_batangasnegoproductivity', ['BUILD'])

    def test_productivity_publish_rejects_non_text_build_column(self):
        client = self._client(build_type='Float')
        with self.assertRaisesRegex(ValueError, 'must be single-line text'):
            client._publish_context('cr63f_batangasnegoproductivity', ['BUILD'])

    @staticmethod
    def _client(include_build=True, build_type='String'):
        client = DataverseClient.__new__(DataverseClient)
        client.get_entity_info = lambda _: {
            'entitySetName': 'records', 'primaryIdAttribute': 'recordid', 'attributes': {},
        }
        attributes = {
            'checker': {'logical': 'cr63f_checker', 'label': 'Checker', 'type': 'String',
                        'isValidForCreate': True},
            'duplicatechecker': {'logical': 'cr63f_duplicatechecker', 'label': 'DUPLICATE CHECKER',
                                 'type': 'String', 'isValidForCreate': True},
        }
        if include_build:
            attributes['build'] = {'logical': 'cr63f_build', 'label': 'BUILD', 'type': build_type,
                                   'isValidForCreate': True}
        client._attribute_map = lambda _: attributes
        return client


if __name__ == '__main__':
    unittest.main()
