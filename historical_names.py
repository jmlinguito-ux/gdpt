"""Evidence-backed historical sourcer names and conservative spelling matching."""
from collections import defaultdict
from datetime import datetime
import re
import unicodedata


def normalize(value):
    text = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode().upper()
    if ',' in text:
        last, _, first = text.partition(',')
        text = first + ' ' + last
    return ' '.join(re.sub(r'[^A-Z ]', ' ', text).split())


def plausible(name):
    return len(name.split()) >= 2 and name not in {'NO NAME', 'NOT AVAILABLE', 'NO DATA', 'FOR UPDATE', 'FOR VERIFICATION', 'NOT APPLICABLE'}


def score(source, target):
    import productivity_tool as pt
    a, b = normalize(source), normalize(target)
    # Suffixes identify different people; never discard them during matching.
    suffixes = {'JR', 'SR', 'II', 'III', 'IV'}
    if set(a.split()) & suffixes != set(b.split()) & suffixes:
        return 0.0
    return pt._pair_score(a.split(), b.split())


def resolve(name, reference):
    normalized = normalize(name)
    options = reference['names']
    if not plausible(normalized):
        return {'name': '', 'method': 'unresolved', 'candidates': []}
    if normalized in options:
        return {'name': normalized, 'method': 'exact', 'candidates': [normalized]}
    scores = sorted(((score(normalized, n), n) for n in options), reverse=True)
    candidates = [n for s, n in scores[:3] if s >= .80]
    if scores and scores[0][0] >= .85 and (len(scores) == 1 or scores[0][0] - scores[1][0] >= .05):
        return {'name': scores[0][1], 'method': 'spelling', 'candidates': candidates}
    return {'name': '', 'method': 'ambiguous' if candidates else 'unresolved', 'candidates': candidates}


def build_reference(rows, teams):
    import productivity_tool as pt
    names = {normalize(t.get('employeeName')) for t in teams if plausible(normalize(t.get('employeeName')))}
    evidence = defaultdict(lambda: {'records': set(), 'dates': set(), 'areas': set(), 'groups': set()})
    for i, row in enumerate(rows):
        date = pt.format_date_to_mm_dd_yyyy(row.get('REPORT DATE') or row.get('NEGO DATE') or row.get('date'))
        try:
            day = datetime.strptime(date, '%m/%d/%Y')
        except (ValueError, TypeError):
            continue
        if day >= datetime(2026, 9, 1):
            continue
        area = str(row.get('AREA-INDEX') or row.get('AREA INDEX') or row.get('aREAINDEX') or '').strip().upper()
        if not area:
            continue
        for part in pt.split_negotiators(row.get('LSA NAME') or row.get('NEGOTIATOR NAME') or ''):
            name = normalize(part)
            if not plausible(name):
                continue
            e = evidence[name]
            e['records'].add(str(row.get('_record_id') or row.get('ID') or i))
            e['dates'].add(day.date().isoformat())
            e['areas'].add(area)
            group = str(row.get('GROUP') or '').strip().upper()
            if group:
                e['groups'].add(group)
    supported = {name for name, e in evidence.items() if len(e['records']) >= 5 and len(e['dates']) >= 3 and len(e['areas']) >= 3}
    # A supported historical spelling that resolves clearly to an official
    # roster name is an alias, not a competing canonical identity.
    anchors = set(names)
    for name in sorted(supported, key=lambda n: (-len(evidence[n]['records']), n)):
        match = resolve(name, {'names': sorted(anchors)})
        if not match['name']:
            anchors.add(name)
    return {'names': sorted(anchors), 'official_names': sorted(names),
            'evidence': {n: {k: len(v) for k, v in e.items()} for n, e in evidence.items()},
            'historical_names': sorted(anchors - names)}
