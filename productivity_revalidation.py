"""Employee-level checker and record-backed usability revalidation.

Does not mutate inputs or perform I/O. Ambiguous source facts are left unchanged.
"""
from collections import defaultdict, Counter
from decimal import Decimal, InvalidOperation

import productivity_tool as pt
from offer_letter import date_key


def clean(value):
    return ' '.join(str(value or '').split()).upper()


def order(row):
    try:
        number = Decimal(str(row.get('number') or '').replace(',', ''))
        if not number.is_finite():
            raise InvalidOperation()
    except InvalidOperation:
        number = Decimal('Infinity')
    return number, str(row.get('created') or ''), str(row['id'])


def calculate(productivity, records, team_names=(), mode='negotiation'):
    """Canonical inputs: id, area, date, name, matched, classification,
    usability, checker, link, number, created. Return desired fields + exceptions.

    Formula IDs are hints, validated against property/date and employee identity.
    A fallback may use multiple sources only when their classification/usability
    agree; no unique source GUID is claimed for those groups.
    """
    visits, links, history = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in records:
        r = dict(r, area=clean(r.get('area')), day=date_key(r.get('date')))
        names = set()
        for n in pt.split_negotiators(r.get('name') or ''):
            historical = pt.historical_sourcer_name(n, r.get('date'), mode)
            resolved = historical or pt.get_fuzzy_negotiator_match(n, list(team_names)) or n
            names.update((clean(n), clean(resolved)))
        r['names'] = names
        names.update(clean(n) for n in r.get('resolved_names', ()) if n)
        if r['area'] and r['day']:
            visits[(r['area'], r['day'])].append(r)
            history[r['area']].append(r)
        if clean(r.get('link')):
            links[clean(r['link'])].append(r)

    groups, errors, methods = defaultdict(list), [], Counter()
    for p in productivity:
        area, day, employee = clean(p.get('area')), date_key(p.get('date')), clean(p.get('matched'))
        reason = ''
        if not area or not day or not employee or '/' in employee:
            reason = 'Missing property/date/single matched employee'
        candidates = []
        if not reason:
            exact = ([r for r in visits[(area, day)] if r['id'] == p['source_id']]
                     if p.get('source_id') else links.get(clean(p.get('link')), []))
            exact = [r for r in exact if (r['area'], r['day']) == (area, day) and employee in r['names']]
            if len(exact) == 1:
                candidates, method = exact, 'formula-id'
            else:
                candidates = [r for r in visits[(area, day)] if employee in r['names']]
                method = 'property-date-employee'
            if not candidates:
                reason = 'No verified source record'
            else:
                facts = {(clean(r.get('classification')), clean(r.get('usability'))) for r in candidates}
                if len(facts) != 1:
                    reason = 'Conflicting source classification/usability'
                elif not next(iter(facts))[0]:
                    reason = 'Missing source classification'
                elif next(iter(facts))[1] not in ('VALID', 'INVALID'):
                    reason = 'Missing or unsupported source usability'
        if reason:
            errors.append({'id': p['id'], 'reason': reason})
            continue
        source = min(candidates, key=order)
        classification, usability = next(iter(facts))
        entry = {'p': p, 'source': source, 'sources': [r['id'] for r in candidates],
                 'classification': classification, 'usability': usability, 'method': method}
        groups[(area, day, employee, classification)].append(entry)

    wanted = {}
    blocked_ids = {e['id'] for e in errors}
    blocked_keys = {(clean(p.get('area')), date_key(p.get('date')), clean(p.get('matched')))
                    for p in productivity if p['id'] in blocked_ids}
    for (area, day, employee, classification), group in groups.items():
        group.sort(key=lambda e: (order(e['source']), str(e['p'].get('created') or ''), e['p']['id']))
        # Existing unresolved rows in the same property/date/employee group could
        # precede the proposed keeper. Do not silently pick a keeper around them.
        if (area, day, employee) in blocked_keys:
            errors.extend({'id': e['p']['id'], 'reason': 'Unresolved peer in duplicate group'} for e in group)
            continue
        for rank, e in enumerate(group):
            if rank:
                checker, usability = pt.ordinal_duplicate(rank), 'INVALID'
            else:
                earlier = [r for r in history[area] if r['day'] < day]
                current = {'AREA-INDEX': area, 'NEGO DATE': day, 'CLASSIFICATION': classification,
                           'ID': e['source'].get('number'), '_record_id': e['source']['id']}
                prior = [{'AREA-INDEX': area, 'NEGO DATE': r['day'], 'CLASSIFICATION': r.get('classification'),
                          'ID': r.get('number'), '_record_id': r['id']} for r in earlier]
                checker = pt.get_checker(current, [current], prior, mode=mode)
                if checker == 'NEEDS REVIEW':
                    errors.append({'id': e['p']['id'], 'reason': 'Incomplete historical classification'})
                    continue
                usability = e['usability']
            wanted[e['p']['id']] = {'checker': checker, 'usability': usability,
                                    'source_ids': e['sources'], 'method': e['method']}
            methods[e['method']] += 1
    return wanted, errors, dict(methods)
