import unittest
from offer_action_revalidation import calculate


def record(rid, day, area='A', index='1'):
    return {'_record_id': rid, 'INDEX NO': index, 'AREA-INDEX': area, 'NEGO DATE': day,
            'NEGOTIATOR NAME': 'ALICE / BOB', 'NEGO ID': rid}


class OfferBackfillTests(unittest.TestCase):
    def test_offer_served_wins_first_day_and_both_employees_inherit(self):
        records = [record('r1', '10/01/2026'), record('r2', '10/02/2026')]
        prod = [dict(id='p1', area='A', date='10/01/2026', name='ALICE', link='r1'),
                dict(id='p2', area='A', date='10/01/2026', name='BOB', link='r1'),
                dict(id='p3', area='A', date='10/02/2026', name='ALICE', link='r2')]
        wanted, errors, provenance = calculate(prod, records, [{'areaIndex': 'A', 'offerDate': '10/01/2026'}])
        self.assertFalse(errors)
        self.assertEqual(wanted, {'p1': 'OFFER SERVED', 'p2': 'OFFER SERVED', 'p3': 'REVISITED - WITH OFFER'})
        self.assertEqual(provenance['p1'], ['r1'])

    def test_unverified_numeric_suffix_and_missing_index_preserved(self):
        records = [record('r1', '10/01/2026', area='JSE-2700'), record('r2', '10/02/2026', index='')]
        prod = [dict(id='p1', area='2700', date='10/01/2026', name='ALICE'),
                dict(id='p2', area='A', date='10/02/2026', name='BOB')]
        wanted, errors, _ = calculate(prod, records, [])
        self.assertFalse(wanted)
        self.assertEqual(len(errors), 2)


if __name__ == '__main__': unittest.main()
