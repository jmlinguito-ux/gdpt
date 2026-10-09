"""Negotiation offer reference fields and visit actions."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

FIELDS = [
    ('areaIndex', 'AREA INDEX'), ('province', 'PROVINCE'), ('municipality', 'MUNICIPALITY'), ('titleNo', 'TITLE NO'),
    ('registeredOwner', 'REGISTERED OWNER'), ('authorizedRepresentative', 'AUTHORIZED REPRESENTATIVE'),
    ('landArea', 'LAND AREA (SQM)'), ('offerDate', 'OFFER DATE'),
    ('offerType', 'OFFER TYPE'), ('offerLetterLink', 'OFFER LETTER LINK'),
]
LOGICAL_FIELDS = dict(zip((k for k, _ in FIELDS), (
    'cr63f_areaindex', 'cr63f_province', 'cr63f_municipality', 'cr63f_titleno', 'cr63f_registeredowner', 'cr63f_authorizedrepresentative',
    'cr63f_landareasqm', 'cr63f_offerdate', 'cr63f_offertype', 'cr63f_offerletterlink')))
OFFER_TYPES = ('OFFER LETTER', 'COUNTER OFFER')
ACTION_COLUMN = 'OFFER LETTER ACTION'
ACTIONS = ('INITIAL VISIT', 'OFFER SERVED', 'REVISITED - WITH OFFER', 'REVISITED - WITHOUT OFFER')

def clean_key(value):
    return ' '.join(str(value if value is not None else '').replace('\xa0', '').split()).upper()

def pick(row, *keys):
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return value
    return ''

def date_key(value):
    from productivity_tool import format_date_to_mm_dd_yyyy
    formatted = format_date_to_mm_dd_yyyy(value)
    try:
        return datetime.strptime(formatted, '%m/%d/%Y').date().isoformat()
    except (ValueError, TypeError):
        return ''

def index_key(row):
    value = clean_key(pick(row, 'INDEX NO', 'indexNo', 'INDEX-NO', 'cr63f_indexno'))
    try:
        number = Decimal(value)
        return format(number.normalize(), 'f') if number.is_finite() else value
    except InvalidOperation:
        return value

def validate_cell(key, value):
    value = str(value if value is not None else '').strip()
    if not value:
        return ''
    if key == 'landArea':
        try:
            number = Decimal(value.replace(',', ''))
            if not number.is_finite() or number < 0 or number > Decimal('100000000000'):
                raise ValueError()
            if number != number.quantize(Decimal('0.0001')):
                raise ValueError()
        except (InvalidOperation, ValueError):
            raise ValueError('LAND AREA (SQM) must be a nonnegative number with at most 4 decimal places.')
        return format(number, 'f')
    if key == 'offerDate':
        normalized = date_key(value)
        if not normalized:
            raise ValueError('OFFER DATE must be a valid date.')
        return datetime.strptime(normalized, '%Y-%m-%d').strftime('%m/%d/%Y')
    if key == 'offerType':
        value = clean_key(value)
        if value not in OFFER_TYPES:
            raise ValueError('OFFER TYPE must be OFFER LETTER or COUNTER OFFER.')
    if key == 'offerLetterLink':
        parsed = urlparse(value)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or len(value) > 2000:
            raise ValueError('OFFER LETTER LINK must be a valid http or https URL (maximum 2000 characters).')
    return value

def calculate_actions(rows, offers, history=(), reference_ready=True, history_ready=True):
    earliest, bad_history = {}, set()
    for row in [*rows, *history]:
        index = index_key(row)
        day = date_key(pick(row, 'NEGO DATE', 'date', 'negoDate'))
        if index and day:
            earliest[index] = min(earliest.get(index, day), day)
        elif index:
            bad_history.add(index)
    offer_dates, bad_offers = defaultdict(set), set()
    for offer in offers:
        day = date_key(pick(offer, 'offerDate', 'OFFER DATE'))
        keys = {clean_key(pick(offer, 'areaIndex', 'AREA INDEX', 'AREA-INDEX')),
                clean_key(pick(offer, 'titleNo', 'TITLE NO'))} - {''}
        for key in keys:
            if day:
                offer_dates[key].add(day)
            else:
                bad_offers.add(key)
    results = []
    for row in rows:
        area = clean_key(pick(row, 'AREA-INDEX', 'AREA INDEX', 'areaIndex'))
        key = clean_key(pick(row, 'TITLE NO', 'titleNo')) if area in ('', 'FOR PLOTTING') else area
        index = index_key(row)
        day = date_key(pick(row, 'NEGO DATE', 'date', 'negoDate'))
        issue = ''
        if not index or not key or not day:
            issue = 'Provide INDEX NO, a valid NEGO DATE, and AREA INDEX or TITLE NO.'
        elif not reference_ready:
            issue = 'Load the OFFER LETTER reference table and calculate again.'
        elif not history_ready:
            issue = 'Negotiation visit history is unavailable. Reconnect and calculate again.'
        elif index in bad_history:
            issue = 'A visit for this INDEX NO has an invalid or missing date.'
        elif key in bad_offers:
            issue = 'A matching OFFER LETTER entry has an invalid or missing OFFER DATE.'
        if issue:
            results.append(('', issue))
        elif day in offer_dates[key]:
            results.append(('OFFER SERVED', ''))
        elif day == earliest[index]:
            results.append(('INITIAL VISIT', ''))
        elif any(d < day for d in offer_dates[key]):
            results.append(('REVISITED - WITH OFFER', ''))
        else:
            results.append(('REVISITED - WITHOUT OFFER', ''))
    return results
