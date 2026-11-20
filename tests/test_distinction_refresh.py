import unittest

import productivity_tool as pt
import productivity_workspace as pw

SRC = {'AREA INDEX': 'LUB-121', 'NEGO DATE': '2026-11-11', 'CLASSIFICATION': 'FIRST CONTACT',
       'NEGOTIATOR NAME': 'MARK APPLE PANTOJA/JAYSON EDNACO', 'DATA USABILITY': 'VALID',
       'LO': 'HEIRS OF JUAN AGUILLAR', 'ID': '46337', 'TEAM': 'TEAM 1', 'GROUP': 'GROUP 1'}
TEAMS = [{'employeeName': 'MARK APPLE PANTOJA', 'team': 'TEAM 1', 'group': 'GROUP 1'},
         {'employeeName': 'JAYSON EDNACO', 'team': 'TEAM 1', 'group': 'GROUP 1'}]


def run(sources, history):
    rows = pt.build_productivity_rows([dict(s) for s in sources], TEAMS, [], history, [], 'negotiation', True)
    for r in rows:
        r['MATCHED NEGOTIATOR NAME'] = r['NEGOTIATOR NAME']
    pw.refresh(rows, [dict(s) for s in sources], history, TEAMS, 'negotiation', True)
    return [(r['CHECKER'], r['NEGO DISTINCTION']) for r in rows]


class DistinctionFollowsCheckerTests(unittest.TestCase):
    def test_visit_already_in_history_is_not_left_as_duplicate(self):
        # Generate sees the visit in history under another ID (1ST DUPLICATE ->
        # DUPLICATE); revalidation clears the checker, so the distinction follows.
        got = run([SRC], [dict(SRC, ID='46000')])
        self.assertEqual(got, [('FIRST CONTACT', 'DISTINCT MULTIPLE')] * 2)

    def test_real_duplicate_upload_gets_duplicate_distinction(self):
        got = run([SRC, SRC], [])
        for checker, distinction in got:
            self.assertEqual('DUPLICATE' in checker, distinction == 'DUPLICATE')
        self.assertEqual(sum(d == 'DUPLICATE' for _, d in got), 2)

    def test_unchanged_checker_keeps_its_distinction(self):
        self.assertEqual(run([SRC], []), [('FIRST CONTACT', 'DISTINCT MULTIPLE')] * 2)


if __name__ == '__main__':
    unittest.main()
