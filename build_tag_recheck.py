"""Maintenance re-check of BUILD and PROJECT TAG on rows that are already stored.

Build and Project Tag are written once, when a row is created, so a Build table
that is completed or corrected later leaves old rows stale. This module compares
each stored row with what the CURRENT Build table says and returns only the rows
that differ. It does no I/O: the caller fetches the rows / Build table, shows the
plan, and writes the updates.

Rules (same as the live derivation, see productivity_tool.get_derived_build_and_tag):
  * not in the Build table for the required week -> BALANCE / BALANCE
  * found with a build value                     -> value / folder (Project Tag)
  * found, blank build                           -> NO DATA / folder
  * the Build for that week is not loaded yet    -> left alone (reported as pending)
The required week is the week BEFORE the stored row's own work week (WW N-1).
"""
import re

import productivity_tool as pt


# Provinces whose rows are looked up in ANOTHER province's Build table.
_EXTRA_PREFIXES = {'cr63f_nuevaecijabuild': ('cr63f_bulacan',)}
RECHECK_YEAR = '2026'   # the Build / Project Tag rules apply to 2026 data only


def stored_tables_for_build(build_logical, logical_names):
    """Stored (productivity / records) tables whose rows use this Build table.

    A Build table cr63f_<province>build serves the cr63f_<province>... tables;
    cr63f_nuevaecijabuild also serves Bulacan.
    """
    build = str(build_logical or '').strip().lower()
    if not build.endswith('build'):
        return []
    prefixes = (build[:-len('build')],) + tuple(_EXTRA_PREFIXES.get(build, ()))
    out = []
    for name in logical_names:
        low = str(name or '').lower()
        if low.endswith('build') or not low.startswith(prefixes):
            continue
        if 'productivit' in low or low.endswith('record') or low.endswith('records'):
            out.append(name)
    return sorted(out)


def build_for_stored_table(logical):
    """The Build table (and so the BUILD PROJECT SITE folders) a stored table uses:
    cr63f_<province>negorecords -> cr63f_<province>build; Bulacan -> Nueva Ecija."""
    m = re.match(r'^cr63f_(.+?)(nego|sourcing)', str(logical or '').lower())
    if not m:
        return None
    for build, extras in _EXTRA_PREFIXES.items():
        if 'cr63f_' + m.group(1) in extras:
            return build
    return f'cr63f_{m.group(1)}build'


def parse_ww(text):
    """'2026-35' -> (2026, 35); anything else -> None."""
    m = re.match(r'^\s*(\d{4})-(\d{1,2})\s*$', str(text or ''))
    return (int(m.group(1)), int(m.group(2))) if m else None


def in_range(ww_text, start, end):
    """Is a stored 'YYYY-WW' inside [start, end] (both (year, week) tuples)?"""
    k = parse_ww(ww_text)
    return bool(k) and start <= k <= end


def required_weeks(records, offset=1):
    """The Build weeks the stored rows are compared with.

    offset=0: the SAME work week as the row. offset=1: the week BEFORE it (WW-01 then
    looks at the previous year's WW-52 and WW-53, whichever exists).
    Returns a sorted list of (year, week).
    """
    need = set()
    for r in records:
        k = parse_ww(r.get('workWeek'))
        if not k:
            continue
        y, w = k
        if offset == 0:
            need.add((y, w))
        elif w == 1:
            need.update({(y - 1, 52), (y - 1, 53)})
        else:
            need.add((y, w - 1))
    return sorted(need)


def _clean(v):
    return ' '.join(str(v or '').split())


def calculate(records, build_rows, only_year=None, offset=1):
    """records: dicts with id, areaIndex, workWeek (e.g. '2026-35'), build, projectTag.

    Returns {'updates': [...], 'pending': [...], 'unchanged': int}. An update is
    {id, areaIndex, workWeek, build, projectTag, oldBuild, oldProjectTag}.
    """
    index = pt.index_build_rows(build_rows)
    updates, pending, unchanged = [], [], 0
    for r in records:
        ww_text = _clean(r.get('workWeek'))
        if only_year and not ww_text.startswith(f'{only_year}-'):
            continue
        ww = pt.normalize_work_week(ww_text)
        area = _clean(r.get('areaIndex')).lower()
        if ww is None or not area:
            pending.append({'id': r.get('id'), 'areaIndex': r.get('areaIndex'), 'workWeek': ww_text,
                            'reason': 'no usable area index / work week'})
            continue
        accepted = (ww,) if offset == 0 else ((52, 53) if ww == 1 else (ww - 1,))
        if not (set(accepted) & index['weeks']):
            pending.append({'id': r.get('id'), 'areaIndex': r.get('areaIndex'), 'workWeek': ww_text,
                            'reason': 'Build not loaded yet for the required week'})
            continue
        build, tag = 'BALANCE', 'BALANCE'
        for acc in accepted:
            hit = index['by_ww'].get((area, acc))
            if hit:
                build, tag = hit
                break
        old_b, old_t = _clean(r.get('build')), _clean(r.get('projectTag'))
        if (old_b, old_t) == (build, tag):
            unchanged += 1
        else:
            updates.append({'id': r.get('id'), 'areaIndex': r.get('areaIndex'), 'workWeek': ww_text,
                            'build': build, 'projectTag': tag, 'oldBuild': old_b, 'oldProjectTag': old_t})
    return {'updates': updates, 'pending': pending, 'unchanged': unchanged}
