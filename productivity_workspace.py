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
    return issues
