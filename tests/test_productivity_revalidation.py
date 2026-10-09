import unittest
from productivity_revalidation import calculate


def source(identifier, day='10/01/2026', name='JUAN / MARIA', classification='AGRICULTURAL', usability='VALID'):
    return dict(id=str(identifier), number=identifier, area='QZ-100', date=day,
                name=name, classification=classification, usability=usability, link=str(identifier))


def prod(identifier, record, employee='JUAN'):
    return dict(id=str(identifier), area=record['area'], date=record['date'],
                matched=employee, link=record['link'])


class RevalidationTests(unittest.TestCase):
    def test_confirmed_examples(self):
        records = [source(101), source(102, name='JUAN'), source(103, '10/03/2026'),
                   source(104, '10/05/2026', classification='RESIDENTIAL'),
                   source(105, '10/05/2026', classification='RESIDENTIAL'),
                   source(106, '10/07/2026', classification='RESIDENTIAL', usability='INVALID')]
        rows = [prod('a', records[0]), prod('b', records[0], 'MARIA'), prod('c', records[1]),
                prod('d', records[2]), prod('e', records[3]), prod('f', records[4]), prod('g', records[5], 'MARIA')]
        wanted, errors, _ = calculate(list(reversed(rows)), records)
        self.assertFalse(errors)
        self.assertEqual([(wanted[p['id']]['checker'], wanted[p['id']]['usability']) for p in rows],
                         [('FIRST CONTACT', 'VALID'), ('FIRST CONTACT', 'VALID'), ('1ST DUPLICATE', 'INVALID'),
                          ('FOLLOW UP', 'VALID'), ('REVISITED', 'VALID'), ('1ST DUPLICATE', 'INVALID'), ('FOLLOW UP', 'INVALID')])

    def test_fallback_conflict_and_wrong_formula_link(self):
        records = [source(1), source(2, classification='RESIDENTIAL')]
        row = prod('a', records[0]); row['link'] = 'unknown'
        wanted, errors, _ = calculate([row], records)
        self.assertFalse(wanted)
        self.assertEqual(errors[0]['reason'], 'Conflicting source classification/usability')
        row['link'] = '1'; row['area'] = 'QZ-200'
        self.assertFalse(calculate([row], records)[0])

    def test_different_classifications_not_duplicates(self):
        records = [source(1), source(2, classification='RESIDENTIAL')]
        wanted, errors, _ = calculate([prod('a', records[0]), prod('b', records[1])], records)
        self.assertFalse(errors)
        self.assertTrue(all(x['checker'] == 'FIRST CONTACT' for x in wanted.values()))

    def test_missing_employee_and_classification(self):
        r = source(1, classification='')
        self.assertFalse(calculate([prod('a', r)], [r])[0])
        r = source(1)
        self.assertFalse(calculate([prod('a', r, '')], [r])[0])

    def test_consistent_fallback_and_idempotence(self):
        r = source(1); rows = [prod('a', r), prod('b', r)]
        for row in rows: row['link'] = 'stale'
        first, errors, _ = calculate(rows, [r])
        self.assertFalse(errors)
        self.assertEqual(first['b']['usability'], 'INVALID')
        rows = [dict(p, checker=first[p['id']]['checker'], usability=first[p['id']]['usability']) for p in rows]
        self.assertEqual(calculate(rows, [r])[0], first)


if __name__ == '__main__':
    unittest.main()
