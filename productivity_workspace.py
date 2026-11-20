"""App workspace revalidation and Productivity-only metric recalculation."""
from collections import defaultdict
import productivity_tool as pt
from productivity_revalidation import calculate as revalidate
from productivity_metric_recalculation import calculate_productivity_only


def refresh(rows, sources=(), history=(), teams=(), mode='negotiation', history_ready=True, historical_name_reference=None):
    """Mutate staged workspace rows only; return review issues, never write live data."""
    source_rows = []
    for prefix, records in (('source', sources), ('history', history)):
        for index, row in enumerate(records):
            source_rows.append({'id': f'{prefix}:{index}', 'area': pt.get_area_index(row),
                'date': row.get('NEGO DATE') or row.get('REPORT DATE') or row.get('date'),
                'name': row.get('NEGOTIATOR NAME') or row.get('LSA NAME') or row.get('negotiatorName'),
                'classification': row.get('CLASSIFICATION') or row.get('classification'),
                'usability': row.get('DATA USABILITY') or row.get('dataUsability'),
                'number': row.get('ID') or row.get('iD1') or index + 1,
                'link': row.get('NEGO ID') or row.get('SOURCING ID')})
            if historical_name_reference is not None and mode == 'land-sourcing':
                import historical_names as hn
                source = source_rows[-1]
                source['resolved_names'] = [hn.resolve(n, historical_name_reference).get('name')
                    for n in pt.split_negotiators(source['name'] or '')
                    if pt.historical_sourcer_name(n, source['date'], mode)]
    canonical = []
    for index, row in enumerate(rows):
        canonical.append({'id': str(index), 'area': row.get('AREA INDEX'), 'date': row.get('NEGO DATE'),
            'matched': row.get('MATCHED NEGOTIATOR NAME'), 'checker': row.get('CHECKER'),
            'lo': row.get('LO'), 'usability': row.get('DATA USABILITY'),
            'source_id': f"source:{row['_source_row_index']}" if '_source_row_index' in row else None})
    issues = []
    checker_before = [str(row.get('CHECKER') or '') for row in rows]
    if sources and history_ready:
        flags, problems, _ = revalidate(canonical, source_rows, pt.team_name_set(teams), mode)
        issues.extend(dict(p, scope='SOURCE') for p in problems)
        for rid, values in flags.items():
            row = rows[int(rid)]
            row['CHECKER'] = values['checker']
            row['_base_data_usability'] = values['usability']
            pt.apply_team_name_usability(row, pt.team_name_set(teams))
            canonical[int(rid)].update(checker=row['CHECKER'], usability=row['DATA USABILITY'])
    elif sources:
        for index, row in enumerate(rows):
            row['CHECKER'] = canonical[index]['checker'] = 'HISTORY REQUIRED'
            issues.append({'id': str(index), 'scope': 'SOURCE', 'reason': 'Load complete property history before calculation'})
    desired, problems = calculate_productivity_only(canonical)
    issues.extend(problems)
    by_row = defaultdict(list)
    for issue in issues:
        by_row[issue['id']].append(f"{issue['scope']}: {issue['reason']}")
    for index, row in enumerate(rows):
        row.update(desired.get(str(index), {}))
        row['_calculation_issues'] = by_row[str(index)]
        row['UNIQUE ID'] = f"{row.get('AREA INDEX', '')}{row.get('NEGO DATE', '')}{row.get('CHECKER', '')}"
        row.pop('_base_lo_day', None)
        row.pop('_base_lo_ww', None)
    refresh_distinctions(rows, checker_before, history, mode)
    return issues


def _is_duplicate(checker):
    return 'DUPLICATE' in str(checker or '').upper()


def refresh_distinctions(rows, checker_before, history=(), mode='negotiation'):
    """NEGO DISTINCTION reads DUPLICATE straight from CHECKER. When revalidation
    moves a row's CHECKER into or out of a duplicate, work its distinction out
    again so the two never disagree (e.g. FIRST CONTACT next to DUPLICATE)."""
    changed = [i for i, row in enumerate(rows)
               if 'NEGO DISTINCTION' in row and _is_duplicate(checker_before[i]) != _is_duplicate(row.get('CHECKER'))]
    if not changed:
        return
    def key(area, ww):
        return str(area or '').strip().lower(), str(ww or '')
    peers = defaultdict(list)
    for row in rows:
        peers[key(row.get('AREA INDEX'), row.get('WORK WEEK'))].append(row)
    existing = defaultdict(list)
    for rec in history or ():
        area = rec.get('aREAINDEX') or rec.get('AREA-INDEX') or rec.get('AREA INDEX') or ''
        day = pt.get_normalized_record_date(rec)
        existing[key(area, pt.canonical_work_week(rec.get('WORK WEEK'), day))].append(rec)
    for i in changed:
        row = rows[i]
        k = key(row.get('AREA INDEX'), row.get('WORK WEEK'))
        row['NEGO DISTINCTION'] = pt.get_nego_distinction(row, peers[k], existing.get(k, []), mode)
