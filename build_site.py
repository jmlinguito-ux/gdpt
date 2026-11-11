"""Read weekly Build values from the BUILD PROJECT SITE folders.

Layout on SharePoint (library "Shared Documents"):
    BUILD PROJECT SITE/<PROJECT FOLDER>/<YEAR>/WW<nn>/<files>
e.g. BUILD PROJECT SITE/QUEZON-1/2026/WW 44/QUEZON-1 BUILD DATABASE 2026-11-04.xlsx

The folder name is the PROJECT TAG. The build value comes from the CONSO column
(Batangas: TERRABLOCK) of the week's DATABASE workbook. A week folder can also hold
maps and an extra "LSR SOURCING" workbook; only the project's own DATABASE file is used.

Two interchangeable sources: a locally synced copy of the library (no extra
permission) or Microsoft Graph (needs Sites.Read.All consent for the app).
This module does no Dataverse work; it returns plain rows.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from urllib.parse import quote

import productivity_tool as pt

ROOT_NAME = 'BUILD PROJECT SITE'
SITE_HOST, SITE_PATH = 'solarph.sharepoint.com', '/sites/MDM'
GRAPH_SCOPES = ['https://graph.microsoft.com/Sites.Read.All']

_AREA_HEADERS = {'AREAINDEX'}
PARSER_VERSION = 2   # bump when parsing changes so cached reads are redone
_SKIP_WORDS = ('LSR', 'SOURCING', 'INTERACTIVE', 'KMZ')


def _norm(v) -> str:
    return re.sub(r'[^A-Z0-9]', '', str(v or '').upper())


def folders_for_build(build_logical: str, folder_names) -> list[str]:
    """Project folders served by a Build table (cr63f_<province>build). NUEVA ECIJA
    folders also serve Bulacan, so they are listed under the Nueva Ecija table."""
    low = str(build_logical or '').lower()
    if not low.endswith('build'):
        return []
    prefix = _norm(low[:-5].replace('cr63f_', ''))
    out = [n for n in folder_names if _norm(n).startswith(prefix) and prefix]
    return sorted(out, key=str.upper)


def week_folder(names, week: int) -> str | None:
    """The WW folder for a week: 'WW07', 'WW 7', 'ww-07' ... all mean week 7."""
    for n in names:
        m = re.match(r'^\s*WW[\s\-_]*0*(\d{1,2})\s*$', str(n), re.I)
        if m and int(m.group(1)) == week:
            return n
    return None


def pick_build_file(entries) -> dict | None:
    """The project's own weekly DATABASE workbook out of a week folder's files.

    entries: [{'name', 'mtime'(optional)}]. Maps and the extra LSR SOURCING workbook
    are ignored; if several candidates remain the one dated latest in its name wins.
    """
    best, best_key = None, None
    for e in entries:
        name = str(e.get('name') or '')
        up = name.upper()
        if not up.endswith(('.XLSX', '.XLSM')) or name.startswith('~$'):
            continue
        if any(w in up for w in _SKIP_WORDS):
            continue
        d = re.search(r'(\d{4}-\d{2}-\d{2})', name)
        key = (1 if 'DATABASE' in up else 0, d.group(1) if d else '', str(e.get('mtime') or ''))
        if best_key is None or key > best_key:
            best, best_key = e, key
    return best


def parse_build_workbook(path: str, terra: bool = False) -> dict:
    """Read AREA INDEX and the build column from the workbook's first sheet.

    Returns {'rows': [{'areaIndex','build'}], 'buildHeader': str|None}. A workbook
    with the area column but no build column yields blank builds (reported as NO DATA).
    terra=True reads TERRABLOCK (Batangas); otherwise CONSO. BUILD, then STATUS, are used
    when that column is missing (some files keep CONSO / NON-CONSO under STATUS). A
    STATUS column is only trusted if its values are build labels (contain CONSO / TERRA).
    """
    import openpyxl
    wanted = ['TERRABLOCK', 'BUILD', 'STATUS'] if terra else ['CONSO', 'BUILD', 'STATUS']
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        area_i = build_i = None
        build_header = None
        rows: dict[str, str] = {}
        for n, row in enumerate(ws.iter_rows(values_only=True)):
            if area_i is None:
                if n > 25:
                    break
                heads = [_norm(c) for c in row]
                hits = [i for i, h in enumerate(heads) if h in _AREA_HEADERS]
                if not hits:
                    continue
                area_i = hits[0]
                for w in wanted:
                    if w in heads:
                        build_i, build_header = heads.index(w), w
                        break
                continue
            if area_i >= len(row):
                continue
            area = str(row[area_i] if row[area_i] is not None else '').strip()
            if not area:
                continue
            raw = row[build_i] if (build_i is not None and build_i < len(row)) else None
            val = pt._normalize_build_label(str(raw).strip()) if raw is not None else ''
            key = area.lower()
            if key not in rows or (not rows[key][1] and val):
                rows[key] = (area, val)
        if area_i is None:
            raise ValueError('no AREA INDEX column in the first sheet')
        if build_header == 'STATUS':
            marker = 'TERRA' if terra else 'CONSO'
            if not any(marker in b.upper() for _a, b in rows.values()):
                # A STATUS column that holds something else: treat as no build column.
                rows = {k: (a, '') for k, (a, _b) in rows.items()}
                build_header = None
            else:
                # Keep only build labels; any other status text counts as blank (NO DATA).
                rows = {k: (a, b if marker in b.upper() else '') for k, (a, b) in rows.items()}
        return {'rows': [{'areaIndex': a, 'build': b} for a, b in rows.values()], 'buildHeader': build_header}
    finally:
        wb.close()


# -- sources ---------------------------------------------------------------
class LocalSource:
    """A locally synced copy of the library (or its BUILD PROJECT SITE folder)."""

    def __init__(self, root: str):
        root = os.path.normpath(root)
        inner = os.path.join(root, ROOT_NAME)
        self.root = inner if os.path.isdir(inner) else root

    def list_dir(self, rel: list[str]) -> list[dict]:
        p = os.path.join(self.root, *rel)
        if not os.path.isdir(p):
            return []
        out = []
        for n in os.listdir(p):
            fp = os.path.join(p, n)
            out.append({'name': n, 'isDir': os.path.isdir(fp),
                        'mtime': str(int(os.path.getmtime(fp))) if os.path.exists(fp) else ''})
        return out

    def fetch(self, rel: list[str], dest_dir: str) -> str:
        return os.path.join(self.root, *rel)   # already on disk, nothing to copy


def parse_sharepoint_link(url: str) -> dict | None:
    """Split a SharePoint folder link into host, site, library and folder segments.

    Accepts a library view link (.../Forms/AllItems.aspx?id=%2Fsites%2FMDM%2FShared%20Documents%2F...)
    or a plain folder URL (.../sites/MDM/Shared Documents/BUILD PROJECT SITE).
    """
    from urllib.parse import urlparse, parse_qs, unquote
    u = urlparse((url or '').strip())
    if u.scheme not in ('http', 'https') or not u.netloc:
        return None
    ident = (parse_qs(u.query).get('id') or [''])[0]
    path = unquote(ident) if ident else unquote(u.path)
    parts = [p for p in path.split('/') if p]
    if len(parts) < 3 or parts[0].lower() not in ('sites', 'teams'):
        return None
    return {'host': u.netloc, 'site': '/' + '/'.join(parts[:2]), 'library': parts[2], 'root': parts[3:]}


class GraphSource:
    """Microsoft Graph. `token_fn()` returns a bearer token for Sites.Read.All.

    `link` (see parse_sharepoint_link) selects the site, library and folder; without
    it the MDM site's BUILD PROJECT SITE folder is used.
    """

    def __init__(self, token_fn, session=None, link: dict | None = None):
        import requests
        self._tok = token_fn
        self._s = session or requests.Session()
        self._link = link or {'host': SITE_HOST, 'site': SITE_PATH, 'library': 'Shared Documents', 'root': [ROOT_NAME]}
        self._drive = None
        self._root = list(self._link['root'])
        self._root_checked = False

    def _get(self, url, **kw):
        r = self._s.get(url, headers={'Authorization': f'Bearer {self._tok()}'}, timeout=120, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f'SharePoint request failed ({r.status_code}): {r.text[:200]}')
        return r

    def _drive_id(self):
        if not self._drive:
            site = f"https://graph.microsoft.com/v1.0/sites/{self._link['host']}:{self._link['site']}"
            lib = self._link['library']
            if lib.lower() in ('shared documents', 'documents'):
                self._drive = self._get(site + ':/drive').json()['id']
            else:
                drives = self._get(site + ':/drives').json().get('value', [])
                hit = next((d for d in drives if d.get('name', '').lower() == lib.lower()
                            or str(d.get('webUrl', '')).rstrip('/').lower().endswith('/' + lib.lower())), None)
                if not hit:
                    raise RuntimeError(f'Library "{lib}" was not found on that SharePoint site.')
                self._drive = hit['id']
        return self._drive

    def _url(self, rel, suffix):
        segs = [*self._root, *rel]
        base = f'https://graph.microsoft.com/v1.0/drives/{self._drive_id()}/root'
        if not segs:
            return base + suffix
        return base + ':/' + '/'.join(quote(s, safe='') for s in segs) + ':' + suffix

    def _children(self, rel):
        url, out = self._url(rel, '/children?$top=200&$select=name,folder,lastModifiedDateTime'), []
        while url:
            try:
                j = self._get(url).json()
            except RuntimeError as exc:
                if '(404)' in str(exc):
                    return []
                raise
            out += [{'name': i['name'], 'isDir': 'folder' in i, 'mtime': i.get('lastModifiedDateTime', '')}
                    for i in j.get('value', [])]
            url = j.get('@odata.nextLink')
        return out

    def list_dir(self, rel):
        # A link to the library (or a parent folder) is fine: step into BUILD PROJECT SITE.
        if not self._root_checked:
            self._root_checked = True
            if not self._root or self._root[-1].lower() != ROOT_NAME.lower():
                if any(c['isDir'] and c['name'].lower() == ROOT_NAME.lower() for c in self._children([])):
                    self._root.append(ROOT_NAME)
        return self._children(rel)

    def fetch(self, rel, dest_dir):
        dest = os.path.join(dest_dir, rel[-1])
        with self._get(self._url(rel, '/content'), stream=True) as r, open(dest, 'wb') as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        return dest


def _cache_path(cache_dir, key):
    return os.path.join(cache_dir, hashlib.sha1(key.encode('utf-8')).hexdigest() + '.json')


def collect_weeks(source, build_logical: str, weeks, progress=None, cache_dir=None) -> dict:
    """collect() for several (year, week) pairs; rows keep their own week."""
    rows, files, notes = [], [], []
    for i, (y, w) in enumerate(weeks):
        if progress:
            progress(f'Week {y}-{w:02d} ({i + 1}/{len(weeks)})…')
        try:
            res = collect(source, build_logical, y, w, progress=progress, cache_dir=cache_dir)
        except RuntimeError as exc:
            notes.append(f'{y}-{w:02d}: {exc}')
            continue
        rows += res['rows']
        files += [{**f, 'week': f'{y}-{w:02d}'} for f in res['files']]
        notes += [f'{y}-{w:02d} {n}' for n in res['notes']]
    return {'rows': rows, 'files': files, 'notes': notes}


def collect(source, build_logical: str, year: int, week: int, progress=None, cache_dir=None) -> dict:
    """Weekly Build rows for every project folder of a Build table.

    Returns {'rows': [{'areaIndex','build','projectTag','workWeek'}], 'files': [...],
             'notes': [str]}. The same area in two folders keeps the one with a build value.
    """
    notes, files = [], []
    folders = folders_for_build(build_logical, [e['name'] for e in source.list_dir([]) if e['isDir']])
    if not folders:
        raise RuntimeError('No project folders match this Build table in BUILD PROJECT SITE.')
    merged: dict[str, dict] = {}
    tmp = tempfile.mkdtemp(prefix='gdpt-build-')
    try:
        for i, folder in enumerate(folders):
            if progress:
                progress(f'Reading {folder} ({i + 1}/{len(folders)})…')
            years = [e['name'] for e in source.list_dir([folder]) if e['isDir']]
            if str(year) not in years:
                notes.append(f'{folder}: no {year} folder')
                continue
            wk = week_folder([e['name'] for e in source.list_dir([folder, str(year)]) if e['isDir']], week)
            if not wk:
                notes.append(f'{folder}: no WW{week:02d} folder')
                continue
            pick = pick_build_file(source.list_dir([folder, str(year), wk]))
            if not pick:
                notes.append(f'{folder}: no DATABASE workbook in {wk}')
                continue
            # A workbook never changes once read, so a parsed copy is reused until the
            # file's name or modified time changes.
            ckey = f"v{PARSER_VERSION}|{folder}|{year}|{wk}|{pick['name']}|{pick.get('mtime')}"
            cpath = _cache_path(cache_dir, ckey) if cache_dir else None
            parsed = None
            if cpath and os.path.exists(cpath):
                try:
                    parsed = json.load(open(cpath, encoding='utf-8'))
                except (OSError, ValueError):
                    parsed = None
            if parsed is None:
                local = source.fetch([folder, str(year), wk, pick['name']], tmp)
                try:
                    parsed = parse_build_workbook(local, terra=_norm(folder).startswith('BATANGAS'))
                except Exception as exc:  # noqa: BLE001
                    notes.append(f'{folder}: could not read {pick["name"]} ({exc})')
                    continue
                finally:
                    if local.startswith(tmp):
                        try:
                            os.remove(local)
                        except OSError:
                            pass
                if cpath:
                    try:
                        os.makedirs(cache_dir, exist_ok=True)
                        json.dump(parsed, open(cpath, 'w', encoding='utf-8'))
                    except OSError:
                        pass
            files.append({'folder': folder, 'file': pick['name'], 'rows': len(parsed['rows']),
                          'buildHeader': parsed['buildHeader'],
                          'path': '/'.join([ROOT_NAME, folder, str(year), wk, pick['name']])})
            if not parsed['buildHeader']:
                notes.append(f'{folder}: {pick["name"]} has no build column - rows tagged NO DATA')
            for r in parsed['rows']:
                k = r['areaIndex'].lower()
                cur = merged.get(k)
                if cur is None or (not cur['build'] and r['build']):
                    merged[k] = {'areaIndex': r['areaIndex'], 'build': r['build'],
                                 'projectTag': folder, 'workWeek': week}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {'rows': list(merged.values()), 'files': files, 'notes': notes}


_ABBR = {'TAR': 'TARLAC', 'QUE': 'QUEZON', 'BAT': 'BATANGAS', 'NE': 'NUEVA ECIJA',
         'ZAM': 'ZAMBALES', 'PAN': 'PANGASINAN'}


def guess_project_tag(filename: str) -> str:
    """'TAR-2 BUILD DATABASE 2026-11-04.xlsx' -> 'TARLAC-2'; '' when it can't be told."""
    up = re.split(r'[\\/]', str(filename or ''))[-1].upper()
    m = re.match(r'^\s*([A-Z][A-Z ]*?)\s*-\s*(\d+)\b', up)
    if not m:
        return ''
    word, num = m.group(1).strip(), m.group(2)
    if word in _ABBR:
        word = _ABBR[word]
    elif len(word) >= 3:
        word = next((full for full in _ABBR.values() if full.startswith(word)), word)
    return f'{word}-{num}'


def collect_files(files, week: int, progress=None) -> dict:
    """Weekly Build rows from workbooks the user picked.

    files: [{'path', 'projectTag'}]. All files are taken as that week's uploads. The
    same area in two files keeps the one with a build value.
    """
    merged, used, notes = {}, [], []
    for i, f in enumerate(files):
        tag = str(f.get('projectTag') or '').strip().upper()
        name = os.path.basename(f.get('path') or '')
        if not tag:
            notes.append(f'{name}: no project tag given - skipped')
            continue
        if progress:
            progress(f'Reading {name} ({i + 1}/{len(files)})…')
        try:
            parsed = parse_build_workbook(f['path'], terra=_norm(tag).startswith('BATANGAS'))
        except Exception as exc:  # noqa: BLE001
            notes.append(f'{name}: could not read ({exc})')
            continue
        used.append({'folder': tag, 'file': name, 'rows': len(parsed['rows']), 'buildHeader': parsed['buildHeader'],
                     'path': f.get('path') or name})
        if not parsed['buildHeader']:
            notes.append(f'{name}: no build column - rows tagged NO DATA')
        for r in parsed['rows']:
            k = r['areaIndex'].lower()
            cur = merged.get(k)
            if cur is None or (not cur['build'] and r['build']):
                merged[k] = {'areaIndex': r['areaIndex'], 'build': r['build'], 'projectTag': tag, 'workWeek': week}
    return {'rows': list(merged.values()), 'files': used, 'notes': notes}


def plan_upsert(site_rows: list[dict], current: list[dict]) -> dict:
    """Compare weekly site rows with the Build table, keyed by (area index, work week)."""
    cur = {}
    for c in current:
        k = (str(c.get('areaIndex', '')).strip().lower(), pt.normalize_work_week(c.get('workWeek')))
        cur.setdefault(k, c)
    inserts, updates, unchanged = [], [], 0
    for r in site_rows:
        k = (r['areaIndex'].strip().lower(), r['workWeek'])
        c = cur.get(k)
        if c is None:
            inserts.append(dict(r))
        elif ((c.get('build') or '').strip(), (c.get('projectTag') or '').strip()) != (r['build'], r['projectTag']):
            updates.append({**r, '_record_id': c.get('_record_id'),
                            'fromWeek': c.get('workWeek') or '', 'toWeek': f"WW-{r['workWeek']:02d}"})
        else:
            unchanged += 1
    return {'inserts': inserts, 'updates': updates, 'unchanged': unchanged}
