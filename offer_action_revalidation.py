"""Record-backed actions for existing negotiation Productivity rows."""
from collections import defaultdict
import offer_letter as ol
import productivity_tool as pt


def calculate(productivity, records, offers):
    actions = ol.calculate_actions(records, offers, records)
    by_visit = defaultdict(list)
    for source, (action, issue) in zip(records, actions):
        area = ol.clean_key(ol.pick(source, 'AREA-INDEX', 'AREA INDEX'))
        day = ol.date_key(source.get('NEGO DATE'))
        names = {ol.clean_key(n) for n in pt.split_negotiators(source.get('NEGOTIATOR NAME') or '')}
        if area and day:
            by_visit[(area, day)].append((source, names, action, issue))
    wanted, errors, provenance = {}, [], {}
    for row in productivity:
        rid = row['id']
        area, day = ol.clean_key(row.get('area')), ol.date_key(row.get('date'))
        names = {ol.clean_key(row.get(k)) for k in ('name', 'matched')} - {''}
        candidates = [entry for entry in by_visit[(area, day)] if names & entry[1]]
        linked = [entry for entry in candidates if ol.clean_key(entry[0].get('NEGO ID')) == ol.clean_key(row.get('link')) and row.get('link')]
        if len(linked) == 1:
            candidates = linked
        results = {entry[2] for entry in candidates}
        if not candidates:
            errors.append({'id': rid, 'reason': 'No verified source visit for area/date/employee'})
        elif any(entry[3] for entry in candidates):
            errors.append({'id': rid, 'reason': '; '.join(sorted({entry[3] for entry in candidates if entry[3]}))})
        elif len(results) != 1:
            errors.append({'id': rid, 'reason': 'Conflicting source visit actions'})
        else:
            wanted[rid] = next(iter(results))
            provenance[rid] = [entry[0]['_record_id'] for entry in candidates]
    return wanted, errors, provenance
