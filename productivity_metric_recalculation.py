"""Recalculate points independently of LO, preserving ambiguous LO history."""
from collections import defaultdict

import productivity_tool as pt
from offer_letter import date_key
from lo_metric_calculation import calculate as calculate_lo, key, week, METRICS


def calculate_points(productivity):
    desired, errors = defaultdict(dict), []
    groups = defaultdict(list)
    for row in productivity:
        use = key(row.get('usability'))
        if use == 'invalid':
            desired[row['id']]['POINTS'] = 0.0
        day, area, checker = date_key(row.get('date')), key(row.get('area')), key(row.get('checker'))
        if not day or not area or not checker or checker in ('needs review', 'history required'):
            if use != 'invalid': errors.append({'id': row['id'], 'scope': 'POINTS', 'reason': 'Incomplete visit identity/checker'})
            continue
        groups[(area, day, checker)].append(row)
    work = []
    for visit, rows in groups.items():
        if any(key(r.get('usability')) not in ('valid', 'invalid') for r in rows):
            errors.extend({'id': r['id'], 'scope': 'POINTS', 'reason': 'Unresolved usability in visit'} for r in rows if key(r.get('usability')) != 'invalid')
            continue
        for r in rows:
            work.append({'_id': r['id'], 'UNIQUE ID': visit, 'NEGO DATE': visit[1],
                         'WORK WEEK': week(visit[1]), 'DATA USABILITY': r['usability']})
    pt.recompute_valid_points(work)
    for r in work: desired[r['_id']]['POINTS'] = float(r['POINTS'])
    return dict(desired), errors


def calculate(productivity, records):
    point_values, errors = calculate_points(productivity)
    desired = defaultdict(dict, point_values)

    source_visits, source_days = set(), set()
    property_owners = defaultdict(set)
    bad_weeks = set()
    for r in records:
        day, lo, area = date_key(r.get('date')), key(r.get('lo')), key(r.get('area'))
        if not day or not lo: continue
        source_days.add((day, lo))
        if area:
            source_visits.add((area, day, lo))
            property_owners[(area, day)].add(lo)
        else: bad_weeks.add((week(day), lo))
        if key(r.get('usability')) not in ('valid', 'invalid'):
            bad_weeks.add((week(day), lo))
    for r in productivity:
        day, lo, area = date_key(r.get('date')), key(r.get('lo')), key(r.get('area'))
        if day and lo and records and (area, day, lo) not in source_visits:
            bad_weeks.add((week(day), lo))
        if day and lo and key(r.get('usability')) not in ('valid', 'invalid'):
            bad_weeks.add((week(day), lo))
        owners = property_owners.get((area, day), set())
        if day and lo and owners and lo not in owners:
            bad_weeks.update((week(day), owner) for owner in owners | {lo})
    eligible = [r for r in productivity if date_key(r.get('date')) and key(r.get('lo'))
                and (week(date_key(r['date'])), key(r['lo'])) not in bad_weeks]
    # Source-only invalid visits must not inflate valid LO denominators. Invalid
    # productivity visits are still removed by the app's valid-row adjustment.
    counted_records = [r for r in records if key(r.get('usability')) != 'invalid']
    lo_values, lo_errors, diagnostics = calculate_lo(eligible, counted_records)
    for rid, values in lo_values.items(): desired[rid].update(values)
    errors.extend({'id': e['record_id'], 'scope': 'LO', 'reason': e['reason']} for e in lo_errors)
    eligible_ids = {r['id'] for r in eligible}
    for r in productivity:
        if r['id'] not in eligible_ids:
            errors.append({'id': r['id'], 'scope': 'LO', 'reason': 'Missing LO/date or unreconciled source property in LO work week'})
        # These four zeros are determinate even when history/identity is missing.
        if key(r.get('usability')) == 'invalid':
            desired[r['id']].update({h: 0.0 for h in ('LO POINTS BY DAY', 'LO POINTS BY WW', 'LO COUNT BY DAY', 'LO COUNT BY WW')})
    diagnostics['unreconciled_lo_weeks'] = len(bad_weeks)
    return dict(desired), errors, diagnostics


def calculate_productivity_only(productivity):
    """Points and LO credit based solely on represented Productivity visits."""
    values, errors = calculate_points(productivity)
    desired = defaultdict(dict, values)
    bad_weeks = set()
    for row in productivity:
        day, lo = date_key(row.get('date')), key(row.get('lo'))
        usable = key(row.get('usability'))
        checker = key(row.get('checker'))
        if day and lo and usable != 'invalid' and (
            usable != 'valid' or not key(row.get('area')) or not checker
            or checker in ('needs review', 'history required')):
            bad_weeks.add((week(day), lo))
    eligible = []
    for row in productivity:
        day, lo = date_key(row.get('date')), key(row.get('lo'))
        checker = key(row.get('checker'))
        complete = day and lo and key(row.get('area')) and checker and checker not in ('needs review', 'history required')
        if complete and key(row.get('usability')) in ('valid', 'invalid') and (week(day), lo) not in bad_weeks:
            eligible.append(row)
        else:
            errors.append({'id': row['id'], 'scope': 'LO', 'reason': 'Incomplete visit identity/LO or unresolved contributor in LO work week'})
        if key(row.get('usability')) == 'invalid':
            desired[row['id']].update({h: 0.0 for h in ('LO POINTS BY DAY', 'LO POINTS BY WW', 'LO COUNT BY DAY', 'LO COUNT BY WW')})
    # Empty Records means only distinct Productivity visits contribute totals.
    lo_values, lo_errors, _ = calculate_lo(eligible, [])
    for rid, metrics in lo_values.items():
        desired[rid].update(metrics)
    errors.extend({'id': e['record_id'], 'scope': 'LO', 'reason': e['reason']} for e in lo_errors)
    return dict(desired), errors
