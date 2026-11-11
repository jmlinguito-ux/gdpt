import build_tag_recheck as btr

NAMES = ['cr63f_batangasnegoproductivities', 'cr63f_batangassourcingproductivities', 'cr63f_batangasnegorecords',
         'cr63f_batangasbuild', 'cr63f_quezonnegorecords', 'cr63f_bulacannegorecords',
         'cr63f_nuevaecijanegoproductivities', 'cr63f_nuevaecijabuild', 'cr63f_teamcomposition']


def test_batangas_tables_only():
    assert btr.stored_tables_for_build('cr63f_batangasbuild', NAMES) == [
        'cr63f_batangasnegoproductivities', 'cr63f_batangasnegorecords', 'cr63f_batangassourcingproductivities']


def test_nuevaecija_build_also_serves_bulacan():
    assert btr.stored_tables_for_build('cr63f_nuevaecijabuild', NAMES) == [
        'cr63f_bulacannegorecords', 'cr63f_nuevaecijanegoproductivities']


def test_non_build_table_name_gives_nothing():
    assert btr.stored_tables_for_build('cr63f_teamcomposition', NAMES) == []
    assert btr.stored_tables_for_build('', NAMES) == []


def test_required_weeks_and_range():
    recs = [{'workWeek': '2026-10'}, {'workWeek': '2026-01'}, {'workWeek': 'bad'}]
    assert btr.required_weeks(recs) == [(2025, 52), (2025, 53), (2026, 9)]
    assert btr.in_range('2026-40', (2026, 38), (2026, 44))
    assert not btr.in_range('2026-45', (2026, 38), (2026, 44))
    assert btr.parse_ww('2026-7') == (2026, 7) and btr.parse_ww('WW7') is None


def test_build_for_stored_table_and_offset():
    assert btr.build_for_stored_table('cr63f_batangasnegorecords') == 'cr63f_batangasbuild'
    assert btr.build_for_stored_table('cr63f_nuevaecijasourcingproductivities') == 'cr63f_nuevaecijabuild'
    assert btr.build_for_stored_table('cr63f_bulacannegorecords') == 'cr63f_nuevaecijabuild'
    assert btr.build_for_stored_table('cr63f_teamcomposition') is None
    assert btr.required_weeks([{'workWeek': '2026-07'}], offset=0) == [(2026, 7)]
    assert btr.required_weeks([{'workWeek': '2026-07'}], offset=1) == [(2026, 6)]


def test_same_week_match_uses_the_same_folder_week():
    import productivity_tool as pt
    build = [{'areaIndex': 'A-1', 'build': 'CONSO', 'projectTag': 'QUEZON-1', 'workWeek': 7}]
    rec = [{'id': '1', 'areaIndex': 'A-1', 'workWeek': '2026-07', 'build': '', 'projectTag': ''},
           {'id': '2', 'areaIndex': 'B-2', 'workWeek': '2026-07', 'build': '', 'projectTag': ''}]
    same = btr.calculate(rec, build, offset=0)
    got = {u['id']: (u['build'], u['projectTag']) for u in same['updates']}
    assert got == {'1': ('CONSO', 'QUEZON-1'), '2': ('BALANCE', 'BALANCE')}
    before = btr.calculate(rec, build, offset=1)     # needs WW06, which was not uploaded
    assert not before['updates'] and len(before['pending']) == 2
