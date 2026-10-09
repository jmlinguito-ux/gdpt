import unittest

from app import Api
import productivity_tool as pt


class UnassignedTeamChoicesTests(unittest.TestCase):
    def test_placeholder_choices_available_without_roster_in_both_modes(self):
        for mode in ('negotiation', 'land-sourcing'):
            for column, placeholder, legacy in (
                    ('TEAM', 'NO TEAM', 'TEAM 0'), ('GROUP', 'NO GROUP', 'GROUP 0')):
                options = pt.get_column_type_and_choices(column, mode, [], [], [], [])['choices']
                self.assertIn(placeholder, options)
                self.assertIn(legacy, options)

    def test_department_filter_and_reference_choices_remain_intact(self):
        teams = [{'dept': 'COMDEV', 'team': 'TEAM 1', 'group': 'GROUP A'},
                 {'dept': 'LSR', 'team': 'TEAM 2', 'group': 'GROUP B'},
                 {'dept': 'LSR', 'team': 'NO TEAM', 'group': 'NO GROUP'}]
        options = pt.get_column_type_and_choices('TEAM', 'land-sourcing', teams, [], [], [])['choices']
        self.assertIn('TEAM 2', options)
        self.assertNotIn('TEAM 1', options)
        self.assertEqual(options.count('NO TEAM'), 1)
        self.assertEqual(pt.get_match_status('NO TEAM', 'NO TEAM'), 'MATCH')
        self.assertEqual(pt.get_match_status('NO GROUP', 'NO GROUP'), 'MATCH')

    def test_editor_choices_preserve_loaded_custom_values_and_native_picklists(self):
        api = Api.__new__(Api)
        api.mode = 'negotiation'
        api.teams = [{'dept': 'LSR', 'team': 'TEAM 2', 'group': 'GROUP B'}]
        data = {'columns': ['team', 'group', 'native', 'owner'],
                'labels': ['Team', 'Group Name', 'Team Name', 'Owner'],
                'types': {'team': 'String', 'group': 'String', 'native': 'Picklist', 'owner': 'String'},
                'choices': {'native': ['NATIVE ONLY']},
                'rows': [{'team': 'HISTORICAL TEAM', 'group': 'HISTORICAL GROUP'}]}
        api._add_editor_team_group_choices(data, 'cr63f_tarlacsourcingproductivity')
        self.assertIn('NO TEAM', data['choices']['team'])
        self.assertIn('TEAM 2', data['choices']['team'])
        self.assertIn('HISTORICAL TEAM', data['choices']['team'])
        self.assertIn('NO GROUP', data['choices']['group'])
        self.assertIn('HISTORICAL GROUP', data['choices']['group'])
        self.assertEqual(data['choices']['native'], ['NATIVE ONLY'])
        self.assertNotIn('owner', data['choices'])

    def test_editor_choices_do_not_require_existing_rows(self):
        api = Api.__new__(Api)
        api.mode = 'negotiation'
        data = {'columns': ['team', 'group'], 'labels': ['TEAM', 'GROUP'],
                'types': {'team': 'String', 'group': 'String'}, 'rows': []}
        api._add_editor_team_group_choices(data, 'cr63f_tarlacnegoproductivity')
        self.assertIn('NO TEAM', data['choices']['team'])
        self.assertIn('NO GROUP', data['choices']['group'])


if __name__ == '__main__':
    unittest.main()
