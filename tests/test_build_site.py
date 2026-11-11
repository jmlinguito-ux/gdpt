import os
import openpyxl
import build_site as bs


def _wb(path, header, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['junk'])
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _tree(tmp, folder, wk, files):
    d = os.path.join(tmp, 'BUILD PROJECT SITE', folder, '2026', wk)
    os.makedirs(d)
    for name, header, rows in files:
        if header is None:
            open(os.path.join(d, name), 'wb').write(b'x')
        else:
            _wb(os.path.join(d, name), header, rows)


def test_folders_for_build():
    names = ['BATANGAS-3', 'NUEVA ECIJA-1', 'NUEVA ECIJA-2', 'QUEZON-1', 'TARLAC-2']
    assert bs.folders_for_build('cr63f_nuevaecijabuild', names) == ['NUEVA ECIJA-1', 'NUEVA ECIJA-2']
    assert bs.folders_for_build('cr63f_quezonbuild', names) == ['QUEZON-1']
    assert bs.folders_for_build('cr63f_teamcomposition', names) == []


def test_week_folder_spellings():
    assert bs.week_folder(['WW06', 'WW 07'], 7) == 'WW 07'
    assert bs.week_folder(['WW44'], 4) is None


def test_pick_ignores_lsr_and_maps():
    e = [{'name': 'WW07 LSR SOURCING DATABASE 2026-02-19.xlsx'}, {'name': 'X INTERACTIVE MAP.kmz'},
         {'name': 'B-3 TERRA 2 DATABASE 2026-02-19.xlsx'}, {'name': 'B-3 TERRA 2 DATABASE 2026-02-11.xlsx'}]
    assert bs.pick_build_file(e)['name'] == 'B-3 TERRA 2 DATABASE 2026-02-19.xlsx'


def test_collect_rules(tmp_path):
    tmp = str(tmp_path)
    _tree(tmp, 'QUEZON-1', 'WW 07', [('QUEZON-1 BUILD DATABASE 2026-02-19.xlsx', ['AREA INDEX', 'CONSO'],
                                      [['Q-1', 'CONSO'], ['Q-2', None], ['Q-3', 'OUTSIDE CONSO']])])
    _tree(tmp, 'QUEZON-2', 'WW07', [('QUEZON-2 BUILD DATABASE 2026-02-19.xlsx', ['AREA-INDEX', 'STATUS'],
                                     [['Q-9', 'x'], ['Q-1', 'NON CONSO']])])
    res = bs.collect(bs.LocalSource(tmp), 'cr63f_quezonbuild', 2026, 7)
    got = {r['areaIndex']: (r['build'], r['projectTag']) for r in res['rows']}
    assert got['Q-1'] == ('CONSO', 'QUEZON-1')           # real value beats the other folder's
    assert got['Q-2'] == ('', 'QUEZON-1')                 # present, blank -> NO DATA later
    assert got['Q-3'] == ('NON-CONSO', 'QUEZON-1')
    assert got['Q-9'] == ('', 'QUEZON-2')                 # STATUS text that is not a build label
    assert {f['folder']: f['buildHeader'] for f in res['files']}['QUEZON-2'] == 'STATUS'


def test_batangas_reads_terrablock(tmp_path):
    tmp = str(tmp_path)
    _tree(tmp, 'BATANGAS-3', 'WW07', [('BATANGAS-3 TERRA 2 DATABASE 2026-02-19.xlsx',
                                       ['AREA INDEX', 'CONSO', 'TERRABLOCK'], [['B-1', 'CONSO', 'TERRA BLOCK']])])
    res = bs.collect(bs.LocalSource(tmp), 'cr63f_batangasbuild', 2026, 7)
    assert res['rows'][0]['build'] == 'TERRA BLOCK'


def test_plan_upsert():
    site = [{'areaIndex': 'A', 'build': 'CONSO', 'projectTag': 'Q1', 'workWeek': 7},
            {'areaIndex': 'B', 'build': '', 'projectTag': 'Q1', 'workWeek': 7},
            {'areaIndex': 'C', 'build': 'CONSO', 'projectTag': 'Q1', 'workWeek': 7}]
    cur = [{'areaIndex': 'a', 'build': 'CONSO', 'projectTag': 'Q1', 'workWeek': '2026-07', '_record_id': '1'},
           {'areaIndex': 'B', 'build': 'X', 'projectTag': '', 'workWeek': 'WW-07', '_record_id': '2'},
           {'areaIndex': 'C', 'build': 'CONSO', 'projectTag': 'Q1', 'workWeek': 'WW-06', '_record_id': '3'}]
    p = bs.plan_upsert(site, cur)
    assert p['unchanged'] == 1 and [u['areaIndex'] for u in p['updates']] == ['B']
    assert [i['areaIndex'] for i in p['inserts']] == ['C']     # other week -> new row


def test_cache_skips_reading_the_workbook_again(tmp_path):
    tmp = str(tmp_path / 'src')
    os.makedirs(tmp)
    _tree(tmp, 'QUEZON-1', 'WW07', [('QUEZON-1 BUILD DATABASE 2026-02-19.xlsx', ['AREA INDEX', 'CONSO'], [['Q-1', 'CONSO']])])
    cache = str(tmp_path / 'cache')
    src = bs.LocalSource(tmp)
    first = bs.collect(src, 'cr63f_quezonbuild', 2026, 7, cache_dir=cache)
    # break the workbook: a second read can only succeed from the cache
    for root, _d, fs in os.walk(tmp):
        for f in fs:
            open(os.path.join(root, f), 'wb').write(b'broken')
    second = bs.collect(src, 'cr63f_quezonbuild', 2026, 7, cache_dir=cache)
    assert first['rows'] == second['rows'] and second['rows'][0]['build'] == 'CONSO'


LINK = ('https://solarph.sharepoint.com/sites/MDM/Shared%20Documents/Forms/AllItems.aspx?id=%2Fsites%2FMDM%2F'
        'Shared%20Documents%2FBUILD%20PROJECT%20SITE&viewid=56439582%2Def23%2D4a10%2D95a0%2D2d195c0384bd')


def test_parse_sharepoint_link():
    p = bs.parse_sharepoint_link(LINK)
    assert p == {'host': 'solarph.sharepoint.com', 'site': '/sites/MDM', 'library': 'Shared Documents',
                 'root': ['BUILD PROJECT SITE']}
    q = bs.parse_sharepoint_link('https://solarph.sharepoint.com/sites/MDM/Shared Documents/BUILD PROJECT SITE/QUEZON-1')
    assert q['root'] == ['BUILD PROJECT SITE', 'QUEZON-1']
    assert bs.parse_sharepoint_link('C:\\Users\\x\\folder') is None
    assert bs.parse_sharepoint_link('https://example.com/nothing') is None


def test_graph_source_builds_urls_from_the_link():
    calls = []

    class R:
        status_code = 200
        def __init__(s, j): s._j = j
        def json(s): return s._j

    class S:
        def get(s, url, headers=None, timeout=0, **kw):
            calls.append(url)
            if url.endswith(':/drive'):
                return R({'id': 'D1'})
            return R({'value': [{'name': 'QUEZON-1', 'folder': {}}]})

    g = bs.GraphSource(lambda: 'tok', session=S(), link=bs.parse_sharepoint_link(LINK))
    assert [e['name'] for e in g.list_dir([])] == ['QUEZON-1']
    assert any('sites/solarph.sharepoint.com:/sites/MDM:/drive' in c for c in calls)
    assert any('/drives/D1/root:/BUILD%20PROJECT%20SITE:/children' in c for c in calls)


def test_guess_project_tag():
    assert bs.guess_project_tag('TAR-2 BUILD DATABASE 2026-11-04.xlsx') == 'TARLAC-2'
    assert bs.guess_project_tag('C:\\x\\QUEZON-1 BUILD DATABASE 2026-11-04.xlsx') == 'QUEZON-1'
    assert bs.guess_project_tag('BATANGAS-3 TERRA 2 DATABASE 2026-02-19.xlsx') == 'BATANGAS-3'
    assert bs.guess_project_tag('NUEVA ECIJA-2 BUILD DATABASE.xlsx') == 'NUEVA ECIJA-2'
    assert bs.guess_project_tag('WW07 LSR SOURCING DATABASE 2026-02-19.xlsx') == ''


def test_collect_files(tmp_path):
    p1, p2 = str(tmp_path / 'a.xlsx'), str(tmp_path / 'b.xlsx')
    _wb(p1, ['AREA INDEX', 'CONSO'], [['Q-1', 'CONSO'], ['Q-2', None]])
    _wb(p2, ['AREA INDEX', 'TERRABLOCK'], [['B-1', 'NON TERRA BLOCK']])
    res = bs.collect_files([{'path': p1, 'projectTag': 'quezon-1'}, {'path': p2, 'projectTag': 'BATANGAS-3'},
                            {'path': p1, 'projectTag': ''}], 44)
    got = {r['areaIndex']: (r['build'], r['projectTag'], r['workWeek']) for r in res['rows']}
    assert got == {'Q-1': ('CONSO', 'QUEZON-1', 44), 'Q-2': ('', 'QUEZON-1', 44),
                   'B-1': ('NON-TERRA BLOCK', 'BATANGAS-3', 44)}
    assert any('no project tag' in n for n in res['notes'])


def test_status_column_used_when_no_conso_column(tmp_path):
    p = str(tmp_path / 'ne.xlsx')
    _wb(p, ['AREA INDEX', 'STATUS'], [['N-1', 'CONSO'], ['N-2', 'NON CONSO'], ['N-3', None]])
    res = bs.parse_build_workbook(p)
    assert res['buildHeader'] == 'STATUS'
    assert {r['areaIndex']: r['build'] for r in res['rows']} == {'N-1': 'CONSO', 'N-2': 'NON-CONSO', 'N-3': ''}


def test_status_column_ignored_when_it_holds_something_else(tmp_path):
    p = str(tmp_path / 'x.xlsx')
    _wb(p, ['AREA INDEX', 'STATUS'], [['N-1', 'FOR PAYMENT'], ['N-2', 'SIGNED']])
    res = bs.parse_build_workbook(p)
    assert res['buildHeader'] is None and all(r['build'] == '' for r in res['rows'])


def test_conso_column_wins_over_status(tmp_path):
    p = str(tmp_path / 'y.xlsx')
    _wb(p, ['AREA INDEX', 'STATUS', 'CONSO'], [['N-1', 'NON-CONSO', 'CONSO']])
    assert bs.parse_build_workbook(p)['rows'][0]['build'] == 'CONSO'
