"""Pure LO calculation. No Dataverse reads or writes."""
from collections import Counter, defaultdict
from datetime import datetime
import re
import productivity_tool as pt
from offer_letter import date_key

METRICS = ('LO OCCURRENCE BY DAY', 'LO OCCURRENCE BY WW', 'LO POINTS BY DAY',
           'LO POINTS BY WW', 'LO COUNT BY DAY', 'LO COUNT BY WW')

def key(value):
    return str(value or '').strip().lower()


def week(day):
    return pt.get_work_week_info(datetime.strptime(day, '%Y-%m-%d'))[1].date().isoformat()


def visit_label(value):
    """Transition labels can change; the same-day duplicate ordinal cannot."""
    label = key(value)
    if label in ('first contact', 'follow up', 'revisited', 'needs review'):
        return 'base'
    match = re.fullmatch(r'(\d+)(?:st|nd|rd|th) duplicate', label)
    if match:
        return 'duplicate-' + str(int(match[1]))
    return label


def calculate(productivity, records):
    """Return metrics by GUID plus exceptions, following original-visit counting."""
    groups = defaultdict(list)
    errors, missing_lo = [], []
    for row in productivity:
        day = date_key(row.get('date'))
        if not day or not key(row.get('area')):
            errors.append({'record_id': row['id'], 'reason': 'Missing AREA INDEX or valid visit date'})
            continue
        if not key(row.get('lo')):
            missing_lo.append({'record_id': row['id'], 'reason': 'Missing LO; no owner identity can be inferred safely'})
            continue
        visit = (key(row['area']), day, visit_label(row.get('checker')))
        groups[(visit, key(row['lo']))].append(row)
    productive_by_property = defaultdict(set)
    for (visit, lo) in groups:
        productive_by_property[(visit[0], visit[1], lo)].add(visit[2])
    record_groups = defaultdict(list)
    for row in records:
        day = date_key(row.get('date'))
        lo = key(row.get('lo'))
        if day and lo and key(row.get('area')):
            record_groups[(key(row['area']), day, lo)].append(row)
    day_counts, week_counts = Counter(), Counter()
    ambiguous = set()
    for property_day in set(productive_by_property) | set(record_groups):
        area, day, lo = property_day
        source = record_groups.get(property_day, [])
        prod_checkers = productive_by_property.get(property_day, set())
        source_checkers = Counter(visit_label(r.get('checker')) for r in source)
        if source_checkers.get('') and len(prod_checkers) > 1:
            # With missing checker labels, the overlap between source visits and
            # multiple productivity visits cannot be identified without guessing.
            ambiguous.add(property_day)
            continue
        if source_checkers.get(''):
            count = max(len(source), len(prod_checkers))
        else:
            count = len(source) + sum(c not in source_checkers for c in prod_checkers)
        day_counts[(day, lo)] += count
        week_counts[(week(day), lo)] += count
    bad_days = {(day, lo) for _, day, lo in ambiguous}
    bad_weeks = {(week(day), lo) for _, day, lo in ambiguous}
    work_rows = []
    for (visit, lo), contributors in groups.items():
        area, day, checker = visit
        if (day, lo) in bad_days or (week(day), lo) in bad_weeks:
            errors.extend({'record_id': r['id'], 'reason': 'History has blank checker labels for multiple visits; LO week total is ambiguous'} for r in contributors)
            continue
        for row in contributors:
            work_rows.append({'_id': row['id'], 'UNIQUE ID': (area, day, checker, lo),
                'NEGO DATE': day, 'WORK WEEK': week(day), 'LO': lo,
                'DATA USABILITY': row.get('usability', ''),
                'LO OCCURRENCE BY DAY': max(1, day_counts[(day, lo)]),
                'LO OCCURRENCE BY WW': max(1, week_counts[(week(day), lo)])})
    pt.recompute_valid_points(work_rows)
    values = {r['_id']: {metric: float(r[metric]) for metric in METRICS} for r in work_rows}
    return values, errors + missing_lo, {'visits': len(groups), 'invalid_rows': sum(key(r.get('usability')) == 'invalid' for r in productivity),
                                       'missing_lo': len(missing_lo), 'ambiguous_history_groups': len(ambiguous)}
