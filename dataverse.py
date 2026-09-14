"""
Dataverse connector for Ground Data Processing Tool.

Live read access to the same tables the Power Apps app used, over the Dataverse
Web API, authenticated with MSAL device-code flow (interactive sign-in, no stored
secrets). Column logical names are discovered at runtime from entity metadata by
matching the display names the app defined, so the connector keeps working even if
schema names differ.

Config (dataverse_config.json in the app's LocalAppData folder when installed):
    {
      "environment_url": "https://YOUR_ORG.crm.dynamics.com",
      "tenant_id": "<your-tenant-guid or 'organizations'>",
      "client_id": "<your-Azure-AD-public-client-app-id>"
    }
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

import requests
from app_paths import data_file
from productivity_tool import (
    format_date_to_mm_dd_yyyy,
    is_valid_mm_dd_yyyy,
    is_date_column,
    is_excel_date_serial,
    excel_serial_to_date_str,
    date_to_excel_serial,
    normalize_work_week as pt_normalize_work_week,
)




import concurrent.futures as _cf

try:
    import msal
except Exception:  # pragma: no cover
    msal = None

API_VERSION = 'v9.2'
_TIMEOUT = 60
_BATCH_TIMEOUT = 180  # a $batch chunk covers many rows, so allow it longer than a single request


def config_path() -> str:
    return data_file('dataverse_config.json')


def _token_cache_path() -> str:
    return data_file('.dv_token_cache.bin')


def load_config() -> dict:
    path = config_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            'dataverse_config.json was not found in the app data folder. Create it with '
            'environment_url, tenant_id and client_id (see README).')
    with open(path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    for key in ('environment_url', 'tenant_id', 'client_id'):
        if not cfg.get(key):
            raise ValueError(f'dataverse_config.json is missing "{key}".')
    cfg['environment_url'] = cfg['environment_url'].rstrip('/')
    return cfg


def clean_table_title(name: str) -> str:

    """Strip cr63f_ or similar prefix and return clean, human-readable Title Case table name."""
    s = str(name or '').strip()
    s = re.sub(r'^[a-zA-Z0-9]+_', '', s)  # strip prefix e.g. cr63f_
    known = {
        'teamcomposition': 'Team Composition',
        'municipalitycode': 'Municipality Code',
        'mappingstatus': 'Mapping Status',
        'batangasbuild': 'Batangas Build',
        'batangasnegorecord': 'Batangas Negotiation Record',
        'batangassourcingrecord': 'Batangas Land Sourcing Record',
    }
    if s.lower() in known:
        return known[s.lower()]
    s = s.replace('_', ' ')
    return s.title()




def _norm(s: Any) -> str:

    return re.sub(r'[^a-z0-9]', '', str(s or '').lower())


def _s(val: Any) -> str:
    return '' if val is None else str(val).strip()


def _to_int(val: Any, default: int = 0) -> int:
    try:
        return int(float(str(val or 0).replace(',', '').strip()))
    except (ValueError, TypeError):
        return default


def _to_float(val: Any) -> float:
    try:
        return float(str(val or 0).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0.0


def _to_iso_date(val: Any) -> str | None:
    if not val:
        return None
    s = str(val).strip()
    if is_excel_date_serial(s):
        s = excel_serial_to_date_str(s)
    m = re.match(r'^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$', s)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m2 = re.match(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})', s)
    if m2:
        return f"{m2.group(1)}-{int(m2.group(2)):02d}-{int(m2.group(3)):02d}"
    return s



# Attribute types that never render as meaningful text. The primary-key column
# of a custom table is a Uniqueidentifier whose DisplayName is the TABLE name
# (e.g. "Mapping Status"), so a name-based lookup can collide with the real
# column and surface a raw record GUID instead of the value.
_IDENTIFIER_TYPES = {
    'Uniqueidentifier', 'Owner', 'Customer', 'PartyList',
    'Virtual', 'EntityName', 'ManagedProperty', 'CalendarRules',
}
# Lookups hold a GUID too, but OData exposes the related record's text through
# the FormattedValue annotation on _<logical>_value.
_LOOKUP_TYPES = {'Lookup'}


def _type_rank(attr_type: str | None) -> int:
    """0 = renders as text, 1 = lookup (readable via annotation), 2 = identifier."""
    if attr_type in _IDENTIFIER_TYPES:
        return 2
    if attr_type in _LOOKUP_TYPES:
        return 1
    return 0


def _select_name(meta: dict) -> str:
    """Column name to put in $select for this attribute."""
    if meta.get('type') in _LOOKUP_TYPES:
        return f"_{meta['logical']}_value"
    return meta['logical']


# Attribute types whose raw value is not human-readable and therefore need the
# FormattedValue annotation (which roughly doubles response size, so only ask
# for it when one of these is actually being selected).
_NEEDS_FORMATTED_VALUE = {
    'Picklist', 'State', 'Status', 'MultiSelectPicklist',
    'Lookup', 'Customer', 'Owner', 'Boolean', 'Money', 'DateTime',
}


def _needs_formatting(*metas) -> bool:
    return any(m and m.get('type') in _NEEDS_FORMATTED_VALUE for m in metas)


def _formatted(row: dict, logical: str) -> str:
    for key in (logical, f"_{logical}_value"):
        fmt_key = f"{key}@OData.Community.Display.V1.FormattedValue"
        if fmt_key in row and row[fmt_key] is not None:
            return str(row[fmt_key]).strip()
    if logical in row:
        return _s(row.get(logical))
    return _s(row.get(f"_{logical}_value"))


class DataverseClient:


    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.env = self.config['environment_url'].rstrip('/')
        self.api_root = f'{self.env}/api/data/{API_VERSION}/'
        self.scope = f'{self.env}/.default'
        self._token = None
        self._token_expiry = 0.0
        self._app = None
        self._cache = None
        self._meta_cache: dict[str, dict] = {}
        self._solution_tables_cache: dict[str, list[dict]] = {}
        self._entity_defs_cache: list[dict] | None = None
        self._entity_info_cache: dict[str, dict] = {}
        # One pooled session for the whole client. Without this every request
        # paid a fresh TCP + TLS handshake to the org host, which dominated
        # connect/fetch time (~19 round-trips for connect + fetch all tables).
        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=2)
        self._session.mount('https://', adapter)
        self._session.mount('http://', adapter)

    # -- auth --------------------------------------------------------------
    def _msal_app(self):
        if msal is None:
            raise RuntimeError('msal is not installed.')
        if self._app is None:
            self._cache = msal.SerializableTokenCache()
            cache_file = _token_cache_path()
            if os.path.exists(cache_file):
                try:
                    self._cache.deserialize(open(cache_file, 'r', encoding='utf-8').read())
                except Exception:
                    pass
            authority = f"https://login.microsoftonline.com/{self.config['tenant_id']}"
            self._app = msal.PublicClientApplication(self.config['client_id'], authority=authority, token_cache=self._cache)
        return self._app

    def _save_cache(self):
        try:
            if self._cache is not None and self._cache.has_state_changed:
                with open(_token_cache_path(), 'w', encoding='utf-8') as f:
                    f.write(self._cache.serialize())
        except Exception:
            pass

    def try_silent_token(self) -> bool:
        """Return True if a cached account produced a token without interaction."""
        app = self._msal_app()
        accounts = app.get_accounts()
        if not accounts:
            return False
        result = app.acquire_token_silent([self.scope], account=accounts[0])
        self._save_cache()
        if result and 'access_token' in result:
            self._store_token(result)
            return True
        return False

    def begin_device_flow(self) -> dict:
        """Start device-code flow. Returns {user_code, verification_uri, message}."""
        app = self._msal_app()
        flow = app.initiate_device_flow(scopes=[self.scope])
        if not flow or not isinstance(flow, dict):
            raise RuntimeError('Failed to initiate device flow with identity provider.')
        if 'user_code' not in flow:
            err = flow.get('error_description') or flow.get('error') or 'Failed to start device flow.'
            raise RuntimeError(err)
        self._pending_flow = flow
        return {
            'user_code': flow.get('user_code', ''),
            'verification_uri': flow.get('verification_uri', ''),
            'message': flow.get('message', '')
        }

    def complete_device_flow(self) -> None:
        """Block until the user finishes the device-code login."""
        app = self._msal_app()
        result = app.acquire_token_by_device_flow(self._pending_flow)
        self._save_cache()
        if not result or not isinstance(result, dict) or 'access_token' not in result:
            err = (result.get('error_description') or result.get('error')) if isinstance(result, dict) else 'Sign-in failed, expired, or timed out.'
            raise RuntimeError(err)
        self._store_token(result)

    def _store_token(self, result: dict):
        self._token = result['access_token']
        self._token_expiry = time.time() + int(result.get('expires_in', 3600)) - 120

    def _auth_header(self) -> dict:
        if not self._token or time.time() >= self._token_expiry:
            if not self.try_silent_token():
                raise RuntimeError('Not signed in to Dataverse. Connect first.')
        return {'Authorization': f'Bearer {self._token}'}

    def signed_in(self) -> bool:
        # Fast path: a still-valid access token.
        if self._token and time.time() < self._token_expiry:
            return True
        # Access token expired/absent — silently renew it via the cached MSAL
        # refresh token so the connection doesn't drop mid-session (the ~1h
        # access-token lifetime otherwise flips the UI to "disconnected" hourly).
        # Throttle repeated attempts so a genuinely-lost session doesn't spam the
        # identity provider on every status poll.
        now = time.time()
        if now - getattr(self, '_last_silent_attempt', 0.0) < 15:
            return bool(self._token) and now < self._token_expiry
        self._last_silent_attempt = now
        try:
            return self.try_silent_token()
        except Exception:  # noqa: BLE001
            return False

    # -- low level ---------------------------------------------------------
    def _get(self, url: str, headers: dict | None = None) -> dict:
        h = {'Accept': 'application/json', 'OData-MaxVersion': '4.0', 'OData-Version': '4.0'}
        h.update(self._auth_header())
        if headers:
            h.update(headers)
        resp = self._session.get(url, headers=h, timeout=_TIMEOUT)
        if resp.status_code >= 400:
            raise RuntimeError(f'Dataverse request failed ({resp.status_code}): {resp.text[:300]}')
        return resp.json()

    def _get_all(self, entity_set: str, select: list[str], formatted: bool = False, top: int = 5000,
                 filter_str: str | None = None, order_by: str | None = None,
                 max_rows: int | None = None) -> list[dict]:
        params = []
        if select:
            params.append('$select=' + ','.join(select))
        if filter_str:
            params.append('$filter=' + filter_str)
        if order_by:
            params.append('$orderby=' + order_by)
        url = f'{self.api_root}{entity_set}'
        if params:
            url += '?' + '&'.join(params)
        headers = {}
        prefer = [f'odata.maxpagesize={top}']
        if formatted:
            prefer.append('odata.include-annotations="OData.Community.Display.V1.FormattedValue"')
        headers['Prefer'] = ','.join(prefer)
        rows: list[dict] = []
        while url:
            data = self._get(url, headers=headers)
            rows.extend(data.get('value', []))
            url = data.get('@odata.nextLink')
            if max_rows is not None and len(rows) >= max_rows:
                return rows[:max_rows]
            if len(rows) > 200000:
                break
        return rows

    # -- metadata: display name -> logical name ----------------------------
    def _attribute_map(self, entity_logical: str) -> dict:
        if not hasattr(self, '_meta_cache'):
            self._meta_cache = {}
        if entity_logical in self._meta_cache:
            return self._meta_cache[entity_logical]
        url = (f"{self.api_root}EntityDefinitions(LogicalName='{entity_logical}')/Attributes"
               "?$select=LogicalName,DisplayName,AttributeType,IsValidForCreate,IsValidForUpdate")
        try:
            data = self._get(url)
        except Exception:
            data = {}
        mapping = {}
        for attr in data.get('value', []):
            logical = attr.get('LogicalName')
            disp = attr.get('DisplayName') or {}
            label = ((disp.get('UserLocalizedLabel') or {}).get('Label')
                     or (disp.get('LocalizedLabels') or [{}])[0].get('Label') if isinstance(disp, dict) else None)
            attr_type = attr.get('AttributeType')
            if logical:
                meta = {'logical': logical, 'type': attr_type, 'label': label or logical,
                        'isValidForCreate': attr.get('IsValidForCreate', True),
                        'isValidForUpdate': attr.get('IsValidForUpdate', True)}
                for key in filter(None, (_norm(logical), _norm(label) if label else None)):
                    prev = mapping.get(key)
                    # Several attributes can share a display name; keep the one
                    # that actually renders as text rather than whichever the
                    # API happened to return last.
                    if prev is None or _type_rank(attr_type) < _type_rank(prev.get('type')):
                        mapping[key] = meta
        self._meta_cache[entity_logical] = mapping
        return mapping

    def _resolve(self, entity_logical: str, *candidate_names: str) -> dict | None:
        """Find an attribute by display name or logical name.

        Identifier-typed columns (primary key, owner, ...) are skipped: matching
        one produces a raw GUID in the UI instead of the value the user expects.
        """
        amap = self._attribute_map(entity_logical)
        for name in candidate_names:
            hit = amap.get(_norm(name))
            if hit and _type_rank(hit.get('type')) < 2:
                return hit
        return None



    def get_entity_info(self, entity_logical: str) -> dict:
        if not hasattr(self, '_entity_info_cache'):
            self._entity_info_cache = {}
        if entity_logical in self._entity_info_cache:
            return self._entity_info_cache[entity_logical]
        url = f"{self.api_root}EntityDefinitions(LogicalName='{entity_logical}')?$select=LogicalName,EntitySetName,PrimaryIdAttribute,DisplayName"
        try:
            data = self._get(url)
            disp = ''
            if isinstance(data.get('DisplayName'), dict):
                localized = data['DisplayName'].get('UserLocalizedLabel') or {}
                disp = localized.get('Label') or ''
            info = {
                'logicalName': entity_logical,
                'entitySetName': data.get('EntitySetName') or f"{entity_logical}s",
                'primaryIdAttribute': data.get('PrimaryIdAttribute') or f"{entity_logical}id",
                'title': disp or clean_table_title(entity_logical),
            }
        except Exception:
            info = {
                'logicalName': entity_logical,
                'entitySetName': f"{entity_logical}s",
                'primaryIdAttribute': f"{entity_logical}id",
                'title': clean_table_title(entity_logical),
            }
        self._entity_info_cache[entity_logical] = info
        return info

    @property
    def token(self) -> str | None:
        if not self._token or time.time() >= self._token_expiry:
            self.try_silent_token()
        return self._token

    def get_headers(self, extra: dict | None = None) -> dict:
        h = {'Accept': 'application/json',
             'OData-MaxVersion': '4.0', 'OData-Version': '4.0', 'Content-Type': 'application/json; charset=utf-8'}
        h.update(self._auth_header())
        if extra:
            h.update(extra)
        return h


    def format_attribute_value(self, entity_logical: str, attr_logical_or_name: str, value: Any) -> Any:
        """Convert string input value to the appropriate Dataverse typed value."""
        if value is None:
            return None
        s_val = str(value).strip()
        if s_val == '':
            return None
            
        amap = self._attribute_map(entity_logical)
        attr_meta = amap.get(_norm(attr_logical_or_name))
        if not attr_meta:
            return s_val
            
        attr_type = attr_meta.get('type')
        attr_log = (attr_meta.get('logical', '') or '').lower()
        attr_label = (attr_meta.get('label', '') or '').lower()

        # Check if this attribute is a Date-Text / Serial column (e.g. 'cr63f_negodatetext', 'NEGO DATE TEXT', 'cr63f_reportdatetext')
        is_date_text = (
            ('text' in attr_log and any(k in attr_log for k in ('date', 'nego', 'report', 'sourced', 'extract')))
            or ('text' in attr_label and 'date' in attr_label)
            or ('serial' in attr_log or 'serial' in attr_label)
        )

        if is_date_text:
            serial_val = date_to_excel_serial(s_val)
            if attr_type in ('Integer', 'BigInt'):
                return _to_int(serial_val)
            return serial_val

        if attr_type in ('Integer', 'BigInt'):
            return _to_int(s_val)
        elif attr_type in ('Decimal', 'Double', 'Money'):
            return _to_float(s_val)
        elif attr_type in ('DateTime', 'Date'):
            return _to_iso_date(s_val)
        elif attr_type in ('String', 'Memo'):
            # If the string field is a date column (not date text), convert Excel serial numbers to MM/DD/YYYY
            if any(k in attr_log for k in ('date', 'nego', 'report', 'extracted')):
                if is_excel_date_serial(s_val):
                    return excel_serial_to_date_str(s_val)
                fmt_d = format_date_to_mm_dd_yyyy(s_val)
                if fmt_d:
                    return fmt_d
            return s_val
        elif attr_type == 'Boolean':
            return s_val.lower() in ('true', '1', 'yes', 'y')
            
        return s_val


    def get_existing_keys(self, entity_logical: str) -> set[str]:
        """Existing duplicate keys (AREA-INDEX + DATE + NEGOTIATOR composites) as a set."""
        return set(self._existing_key_index(entity_logical).keys())

    def get_existing_key_map(self, entity_logical: str) -> dict[str, str]:
        """Same keys as get_existing_keys, mapped to the owning record's id (GUID).

        Used by 'override on duplicate' publishing to know WHICH existing record a
        duplicate row should update. When several records share a key (e.g. the
        AREA+DATE+NEGOTIATOR composite), the highest-ID (most recent) record wins.
        """
        return self._existing_key_index(entity_logical)

    def _existing_key_index(self, entity_logical: str) -> dict[str, str]:
        """Build {duplicate-key -> record id} for the target table.

        get_existing_keys() exposes just the keys; the override publish path uses
        the id so it can PATCH the matched record instead of skipping it.
        """
        from productivity_tool import format_date_to_mm_dd_yyyy, is_excel_date_serial, excel_serial_to_date_str
        info = self.get_entity_info(entity_logical)
        entity_set = info.get('entitySetName')
        if not entity_set:
            return {}
        amap = self._attribute_map(entity_logical)
        pk = info.get('primaryIdAttribute')

        # Duplicate identity is ONLY AREA-INDEX + DATE + NEGOTIATOR. ID / UNIQUE ID
        # are deliberately not keyed on. id_attr is resolved solely to order records
        # so the most-recent one wins a shared composite key.
        id_attr = amap.get(_norm('ID')) or amap.get(_norm('cr63f_id')) or amap.get(_norm('ITEM #')) or amap.get(_norm('cr63f_itemno'))
        area_attr = amap.get(_norm('AREA-INDEX')) or amap.get(_norm('AREA_INDEX')) or amap.get(_norm('cr63f_areaindex')) or amap.get(_norm('cr63f_area_index'))
        date_attr = (amap.get(_norm('cr63f_negotiationdate')) or amap.get(_norm('NEGO DATE'))
                     or amap.get(_norm('cr63f_negodate')) or amap.get(_norm('Negotiation Date'))
                     or amap.get(_norm('cr63f_reportdate')) or amap.get(_norm('REPORT DATE'))
                     or amap.get(_norm('SOURCING DATE')) or amap.get(_norm('cr63f_sourcingdate'))
                     or amap.get(_norm('DATE')) or amap.get(_norm('cr63f_date')))
        # Matched negotiator (with raw-negotiator fallback) scopes the AREA+DATE
        # duplicate key so the same property/day by DIFFERENT negotiators is not a
        # duplicate. Kept symmetric with app.py _row_keys on the client side.
        mneg_attr = (amap.get(_norm('MATCHED NEGOTIATOR NAME')) or amap.get(_norm('cr63f_matchednegotiatorname'))
                     or amap.get(_norm('MATCHED SOURCER')) or amap.get(_norm('cr63f_matchedsourcer'))
                     or amap.get(_norm('MATCHED SOURCER NAME')) or amap.get(_norm('cr63f_matchedsourcername'))
                     or amap.get(_norm('MATCHED NEGOTIATOR')))
        neg_attr = (amap.get(_norm('NEGOTIATOR NAME')) or amap.get(_norm('cr63f_negotiatorname'))
                    or amap.get(_norm('SOURCER NAME')) or amap.get(_norm('cr63f_sourcername'))
                    or amap.get(_norm('cr63f_sourcer')) or amap.get(_norm('LSA NAME')) or amap.get(_norm('cr63f_lsaname')))

        if not (area_attr and date_attr):
            return {}

        fetch_attrs = []
        for a in (area_attr, date_attr, mneg_attr, neg_attr, id_attr):
            if a and a.get('logical') and a.get('logical') not in fetch_attrs:
                fetch_attrs.append(a.get('logical'))
        if pk and pk not in fetch_attrs:
            fetch_attrs.append(pk)

        try:
            records = self._get_all(entity_set, select=fetch_attrs, top=5000)
        except Exception:
            return {}

        # Most-recent record wins a shared key: sort by numeric ID ascending so a
        # later (higher-ID) record overwrites an earlier one in the map.
        def _idnum(r):
            try:
                return int(str(r.get(id_attr.get('logical'), 0) if id_attr else 0).strip() or 0)
            except (TypeError, ValueError):
                return 0
        records = sorted(records, key=_idnum)

        key_map: dict[str, str] = {}
        for r in records:
            rid = str(r.get(pk, '') or '').strip('{}') if pk else ''
            if area_attr and date_attr:
                av = str(r.get(area_attr.get('logical'), '') or '').strip().upper()
                raw_d = str(r.get(date_attr.get('logical'), '') or '').strip()
                mn = str((r.get(mneg_attr.get('logical'), '') if mneg_attr else '') or '').strip().upper()
                if not mn:
                    mn = str((r.get(neg_attr.get('logical'), '') if neg_attr else '') or '').strip().upper()
                if av and raw_d:
                    key_map[f"{av}___{raw_d.upper()}___{mn}"] = rid
                    if is_excel_date_serial(raw_d):
                        raw_d = excel_serial_to_date_str(raw_d)
                    norm_d = format_date_to_mm_dd_yyyy(raw_d)
                    if norm_d:
                        key_map[f"{av}___{norm_d.upper()}___{mn}"] = rid
                        if len(norm_d) == 10 and norm_d[2] == '/' and norm_d[5] == '/':
                            iso_d = f"{norm_d[6:10]}-{norm_d[0:2]}-{norm_d[3:5]}"
                            key_map[f"{av}___{iso_d.upper()}___{mn}"] = rid
        return key_map


    def update_record(self, entity_logical: str, record_id: str, payload: dict) -> bool:
        if not record_id:
            raise ValueError('Cannot update record without record_id.')
        info = self.get_entity_info(entity_logical)
        clean_id = record_id.strip('{}')
        url = f"{self.api_root}{info['entitySetName']}({clean_id})"
        
        formatted_payload = {}
        for k, v in payload.items():
            formatted_payload[k] = self.format_attribute_value(entity_logical, k, v) if isinstance(v, str) else v
            
        headers = self.get_headers({'If-Match': '*'})
        resp = self._session.patch(url, headers=headers, json=formatted_payload, timeout=_TIMEOUT)
        if resp.status_code not in (200, 204):
            err_msg = resp.text
            try:
                err_data = resp.json()
                if 'error' in err_data and 'message' in err_data['error']:
                    err_msg = err_data['error']['message']
            except Exception:
                pass
            raise RuntimeError(f"Dataverse update failed ({resp.status_code}): {err_msg}")
        return True

    def create_record(self, entity_logical: str, payload: dict) -> str:
        info = self.get_entity_info(entity_logical)
        url = f"{self.api_root}{info['entitySetName']}"
        
        formatted_payload = {}
        for k, v in payload.items():
            formatted_payload[k] = self.format_attribute_value(entity_logical, k, v) if isinstance(v, str) else v
            
        headers = self.get_headers({'Prefer': 'return=representation'})
        resp = self._session.post(url, headers=headers, json=formatted_payload, timeout=_TIMEOUT)
        if resp.status_code not in (200, 201, 204):
            err_msg = resp.text
            try:
                err_data = resp.json()
                if 'error' in err_data and 'message' in err_data['error']:
                    err_msg = err_data['error']['message']
            except Exception:
                pass
            raise RuntimeError(f"Dataverse insert failed ({resp.status_code}): {err_msg}")
        # Extract new GUID
        entity_id_hdr = resp.headers.get('OData-EntityId', '')
        if entity_id_hdr and '(' in entity_id_hdr and ')' in entity_id_hdr:
            return entity_id_hdr.split('(')[-1].split(')')[0].strip('{}')
        if resp.status_code == 201 and resp.text:
            try:
                data = resp.json()
                return str(data.get(info['primaryIdAttribute'], ''))
            except Exception:
                pass
        return ''

    def delete_record(self, entity_logical: str, record_id: str) -> bool:
        if not record_id:
            raise ValueError('Cannot delete record without record_id.')
        info = self.get_entity_info(entity_logical)
        clean_id = record_id.strip('{}')
        url = f"{self.api_root}{info['entitySetName']}({clean_id})"
        headers = self.get_headers()
        resp = self._session.delete(url, headers=headers, timeout=_TIMEOUT)
        if resp.status_code not in (200, 204):
            err_msg = resp.text
            try:
                err_data = resp.json()
                if 'error' in err_data and 'message' in err_data['error']:
                    err_msg = err_data['error']['message']
            except Exception:
                pass
            raise RuntimeError(f"Dataverse delete failed ({resp.status_code}): {err_msg}")
        return True

    def _publish_context(self, entity_logical: str, sample_keys: list[str]) -> dict:
        """Resolve the column map + date-text companions once for a publish batch."""
        info = self.get_entity_info(entity_logical)
        entity_set = info.get('entitySetName')
        pk = info.get('primaryIdAttribute')
        if not entity_set:
            raise ValueError(f"Could not find entity set for table '{entity_logical}'.")
        attributes = info.get('attributes', {})
        amap = self._attribute_map(entity_logical)

        _KEY_ALIASES = {
            'WITH ANNOTATION - THIRD-PARTY ENCUMBRANCES/MORTGAGE': [
                'WITH ANNOTATION - THIRD-PARTY ENCUMBRANCES/MORTGAGE',
                'WITH ANNOTATION - THIRD-PARTY ENCUMBRANCES/MORTGAG',
                'cr63f_withannotationthirdpartyencumbran',
            ],
            'WITH ANNOTATION - THIRD PARTY ENCUMBRANCES/MORTGAGE': [
                'WITH ANNOTATION - THIRD-PARTY ENCUMBRANCES/MORTGAGE',
                'WITH ANNOTATION - THIRD-PARTY ENCUMBRANCES/MORTGAG',
                'cr63f_withannotationthirdpartyencumbran',
            ],
            'PRICE VARIANCE (SALE)': [
                'PRICE VARIANCE (SALE)',
                'PRICE VARIANCE SALE',
                'cr63f_pricevariancesale',
            ],
            'NEGO DATE': ['NEGO DATE', 'SOURCING DATE', 'REPORT DATE', 'DATE', 'cr63f_sourcingdate', 'cr63f_negodate', 'cr63f_reportdate', 'cr63f_date'],
            'SOURCING DATE': ['SOURCING DATE', 'NEGO DATE', 'REPORT DATE', 'DATE', 'cr63f_sourcingdate', 'cr63f_negodate', 'cr63f_reportdate', 'cr63f_date'],
            'REPORT DATE': ['REPORT DATE', 'SOURCING DATE', 'NEGO DATE', 'DATE', 'cr63f_reportdate', 'cr63f_sourcingdate', 'cr63f_negodate', 'cr63f_date'],
            'DATE': ['DATE', 'SOURCING DATE', 'NEGO DATE', 'REPORT DATE', 'cr63f_sourcingdate', 'cr63f_negodate', 'cr63f_reportdate', 'cr63f_date'],
            'NEGOTIATOR NAME': ['NEGOTIATOR NAME', 'SOURCER NAME', 'SOURCER', 'LSA NAME', 'cr63f_sourcername', 'cr63f_negotiatorname', 'cr63f_sourcer', 'cr63f_lsaname', 'cr63f_lsa'],
            'SOURCER NAME': ['SOURCER NAME', 'NEGOTIATOR NAME', 'SOURCER', 'LSA NAME', 'cr63f_sourcername', 'cr63f_negotiatorname', 'cr63f_sourcer', 'cr63f_lsaname', 'cr63f_lsa'],
            'MATCHED NEGOTIATOR NAME': ['MATCHED NEGOTIATOR NAME', 'MATCHED NEGOTIATOR', 'MATCHED SOURCER', 'MATCHED SOURCER NAME', 'cr63f_matchednegotiator', 'cr63f_matchedsourcer', 'cr63f_matchednegotiatorname', 'cr63f_matchedsourcername'],
            'MATCHED SOURCER': ['MATCHED SOURCER', 'MATCHED SOURCER NAME', 'MATCHED NEGOTIATOR', 'MATCHED NEGOTIATOR NAME', 'cr63f_matchedsourcer', 'cr63f_matchednegotiator', 'cr63f_matchedsourcername', 'cr63f_matchednegotiatorname'],
            'MATCHED SOURCER NAME': ['MATCHED SOURCER NAME', 'MATCHED SOURCER', 'MATCHED NEGOTIATOR', 'MATCHED NEGOTIATOR NAME', 'cr63f_matchedsourcer', 'cr63f_matchednegotiator', 'cr63f_matchedsourcername', 'cr63f_matchednegotiatorname'],
            'NEGO DISTINCTION': ['NEGO DISTINCTION', 'SOURCING DISTINCTION', 'DISTINCTION', 'cr63f_sourcingdistinction', 'cr63f_negodistinction', 'cr63f_distinction'],
            'SOURCING DISTINCTION': ['SOURCING DISTINCTION', 'NEGO DISTINCTION', 'DISTINCTION', 'cr63f_sourcingdistinction', 'cr63f_negodistinction', 'cr63f_distinction'],
            'NEGO CODE': ['NEGO CODE', 'SOURCER CODE', 'SOURCING CODE', 'CODE', 'cr63f_sourcercode', 'cr63f_negocode', 'cr63f_code'],
            'SOURCER CODE': ['SOURCER CODE', 'NEGO CODE', 'SOURCING CODE', 'CODE', 'cr63f_sourcercode', 'cr63f_negocode', 'cr63f_code'],
            'NEGOTIATOR MATCH': ['NEGOTIATOR MATCH', 'SOURCER MATCH', 'SOURCING MATCH', 'cr63f_sourcermatch', 'cr63f_negotiatormatch'],
            'SOURCER MATCH': ['SOURCER MATCH', 'NEGOTIATOR MATCH', 'SOURCING MATCH', 'cr63f_sourcermatch', 'cr63f_negotiatormatch'],
        }

        col_map = {}
        for key in sample_keys:
            candidates = _KEY_ALIASES.get(key.upper(), [key])
            res = self._resolve(entity_logical, *candidates)
            if res and res.get('isValidForCreate') is not False:
                col_map[key] = res['logical']
            else:
                n_key = _norm(key)
                for attr_name, attr_meta in attributes.items():
                    if attr_meta.get('isValidForCreate') is False:
                        continue
                    if _norm(attr_name) == n_key or _norm(attr_meta.get('label', '')) == n_key:
                        col_map[key] = attr_name
                        break

        # Find Date columns (DateTime / Date fields)
        date_attrs = []
        # Find Date-Text / Serial companion columns
        date_text_attrs = []

        for norm_key, meta in amap.items():
            logical_name = meta.get('logical', '')
            label = (meta.get('label', '') or '').lower()
            attr_type = meta.get('type')
            if (
                ('text' in logical_name.lower() and any(k in logical_name.lower() for k in ('date', 'nego', 'report', 'sourced', 'extract')))
                or ('text' in label and 'date' in label)
                or ('serial' in logical_name.lower() or 'serial' in label)
            ):
                if logical_name not in date_text_attrs and logical_name != pk:
                    date_text_attrs.append(logical_name)
            elif (
                attr_type in ('DateTime', 'Date')
                or any(k in logical_name.lower() for k in ('sourcingdate', 'negodate', 'reportdate'))
                or (('date' in logical_name.lower() or 'date' in label) and 'text' not in logical_name.lower() and 'text' not in label and 'serial' not in logical_name.lower())
            ):
                if logical_name not in date_attrs and logical_name != pk:
                    date_attrs.append(logical_name)

        return {'info': info, 'entity_set': entity_set, 'pk': pk,
                'col_map': col_map, 'date_attrs': date_attrs, 'date_text_attrs': date_text_attrs}

    def _row_to_payload(self, row: dict, ctx: dict) -> dict:
        """Build the raw (unformatted) create payload for one row."""
        col_map, pk, date_attrs, date_text_attrs = ctx['col_map'], ctx['pk'], ctx['date_attrs'], ctx['date_text_attrs']
        payload = {}
        for k, v in row.items():
            if k in col_map and v is not None and str(v).strip() != '':
                dv_attr = col_map[k]
                if dv_attr == pk:
                    continue  # Let Dataverse generate the primary GUID
                payload[dv_attr] = str(v).strip()

        row_date = (row.get('SOURCING DATE') or row.get('NEGO DATE') or row.get('REPORT DATE')
                    or row.get('DATE') or row.get('DATE EXTRACTED') or '')
        row_date_str = str(row_date).strip()

        if row_date_str:
            # Ensure actual Date column is always populated
            if date_attrs:
                for da in date_attrs:
                    if da not in payload:
                        payload[da] = row_date_str
            # Ensure Date-Text / Serial companion column is populated
            if date_text_attrs:
                serialized = date_to_excel_serial(row_date_str)
                for dta in date_text_attrs:
                    if dta not in payload:
                        payload[dta] = serialized
        return payload

    def _format_payload(self, entity_logical: str, payload: dict) -> dict:
        out = {}
        for k, v in payload.items():
            out[k] = self.format_attribute_value(entity_logical, k, v) if isinstance(v, str) else v
        return out

    def publish_records_batch(self, entity_logical: str, rows: list[dict], mode: str = 'negotiation',
                              chunk_size: int = 100, row_labels: list | None = None) -> dict:
        """Create records via $batch (one changeset per row, so failures are per-row).

        Returns {ok, created, results:[{row, status:'created'|'failed'|'empty', error, id}]}.
        `row_labels` (optional) supplies a caller-facing label per row for the receipt.
        """
        if not rows:
            return {'ok': False, 'error': 'No records to publish.', 'created': 0, 'results': []}
        ctx = self._publish_context(entity_logical, list(rows[0].keys()))
        entity_set = ctx['entity_set']
        labels = row_labels if row_labels is not None else list(range(len(rows)))

        results = [None] * len(rows)
        payloads = []
        for i, row in enumerate(rows):
            payload = self._row_to_payload(row, ctx)
            if not payload:
                results[i] = {'row': i + 1, 'label': labels[i], 'status': 'empty', 'error': 'No mappable columns'}
                payloads.append(None)
            else:
                payloads.append(self._format_payload(entity_logical, payload))

        created = 0
        pending = [i for i, pl in enumerate(payloads) if pl is not None]
        for start in range(0, len(pending), chunk_size):
            idx_chunk = pending[start:start + chunk_size]
            batch_results = self._post_batch_creates(entity_set, [(i, payloads[i]) for i in idx_chunk])
            for i, (status, err, rec_id) in batch_results.items():
                results[i] = {'row': i + 1, 'label': labels[i], 'status': status, 'error': err, 'id': rec_id}
                if status == 'created':
                    created += 1
        return {'ok': True, 'created': created, 'results': [r for r in results if r is not None]}

    def update_records_batch(self, entity_logical: str, updates: list, mode: str = 'negotiation',
                             chunk_size: int = 100) -> dict:
        """PATCH existing records via $batch. Used by 'override on duplicate' publishing.

        `updates`: list of {'_record_id': guid, 'row': {...}, 'label': ...}.
        Returns {ok, updated, results:[{row, status:'updated'|'failed'|'empty', error}]}.
        """
        if not updates:
            return {'ok': False, 'error': 'No records to update.', 'updated': 0, 'results': []}
        ctx = self._publish_context(entity_logical, list(updates[0]['row'].keys()))
        entity_set = ctx['entity_set']

        results = [None] * len(updates)
        prepared = []  # (record_id, formatted_payload) or None
        for i, u in enumerate(updates):
            rid = str(u.get('_record_id') or '').strip().strip('{}')
            payload = self._row_to_payload(u.get('row') or {}, ctx)
            if not payload or not rid:
                results[i] = {'row': i + 1, 'label': u.get('label'), 'status': 'empty',
                              'error': 'No mappable columns or missing record id'}
                prepared.append(None)
            else:
                prepared.append((rid, self._format_payload(entity_logical, payload)))

        updated = 0
        pending = [i for i, p in enumerate(prepared) if p is not None]
        for start in range(0, len(pending), chunk_size):
            idx_chunk = pending[start:start + chunk_size]
            batch_results = self._post_batch_updates(entity_set, [(i, prepared[i][0], prepared[i][1]) for i in idx_chunk])
            for i, (status, err, _id) in batch_results.items():
                results[i] = {'row': i + 1, 'label': updates[i].get('label'), 'status': status, 'error': err}
                if status == 'updated':
                    updated += 1
        return {'ok': True, 'updated': updated, 'results': [r for r in results if r is not None]}

    def update_records_fields_batch(self, logical_name: str, updates: list, chunk_size: int = 100) -> dict:
        """PATCH chosen fields on existing records, addressed by logical column name.

        update_records_batch() translates canonical CSV headers onto fields for
        publishing. The editable grids already hold Dataverse logical names, so
        they go straight through without that translation.

        `updates`: [{'_record_id': guid, 'fields': {logical: value}, 'label': str}]
        """
        if not updates:
            return {'ok': False, 'error': 'No records to update.', 'updated': 0, 'results': []}
        info = self.get_entity_info(logical_name)
        entity_set = info['entitySetName']

        results = [None] * len(updates)
        prepared = []
        for i, u in enumerate(updates):
            rid = str(u.get('_record_id') or '').strip().strip('{}')
            fields = {k: v for k, v in (u.get('fields') or {}).items() if k and not k.startswith('_')}
            if not rid or not fields:
                results[i] = {'row': i + 1, 'label': u.get('label'), 'status': 'empty',
                              'error': 'No changed columns or missing record id'}
                prepared.append(None)
            else:
                prepared.append((rid, self._format_payload(logical_name, fields)))

        updated = 0
        pending = [i for i, p in enumerate(prepared) if p is not None]
        for start in range(0, len(pending), chunk_size):
            idx_chunk = pending[start:start + chunk_size]
            batch_results = self._post_batch_updates(entity_set, [(i, prepared[i][0], prepared[i][1]) for i in idx_chunk])
            for i, (status, err, _id) in batch_results.items():
                results[i] = {'row': i + 1, 'label': updates[i].get('label'), 'status': status, 'error': err}
                if status == 'updated':
                    updated += 1
        return {'ok': True, 'updated': updated, 'results': [r for r in results if r is not None]}

    def _post_batch_updates(self, entity_set: str, indexed: list) -> dict:
        """PATCH a chunk of records as one $batch, each in its own changeset.

        `indexed`: list of (index, record_id, payload). `If-Match: *` makes each an
        update-only (never an upsert-create). Returns {index: (status, error, id)}.
        """
        import uuid as _uuid
        batch_id = 'batch_' + _uuid.uuid4().hex
        lines = []
        order = []
        for n, (i, rid, payload) in enumerate(indexed, start=1):
            cs_id = 'changeset_' + _uuid.uuid4().hex
            order.append(i)
            lines.append('--' + batch_id)
            lines.append('Content-Type: multipart/mixed; boundary=' + cs_id)
            lines.append('')
            lines.append('--' + cs_id)
            lines.append('Content-Type: application/http')
            lines.append('Content-Transfer-Encoding: binary')
            lines.append('Content-ID: ' + str(n))
            lines.append('')
            lines.append('PATCH ' + self.api_root + entity_set + '(' + rid + ') HTTP/1.1')
            lines.append('Content-Type: application/json;type=entry')
            lines.append('If-Match: *')
            lines.append('')
            lines.append(json.dumps(payload))
            lines.append('--' + cs_id + '--')
        lines.append('--' + batch_id + '--')
        body = '\r\n'.join(lines)
        out = {}
        try:
            resp = self._post_batch_with_retry(body, batch_id)
        except Exception as exc:
            reason = f'Batch update network error: {exc}'
            for i in order:
                out[i] = ('failed', reason, None)
            return out

        if resp.status_code >= 400:
            reason = 'Batch update failed (%d): %s' % (resp.status_code, self._extract_batch_error(resp.text))
            for i in order:
                out[i] = ('failed', reason, None)
            return out
        statuses = self._parse_batch_response(resp.text)
        for pos, i in enumerate(order):
            st = statuses[pos] if pos < len(statuses) else ('failed', 'No response for row', None)
            # _parse_batch_response labels any 2xx (incl. PATCH's 204) as 'created'.
            out[i] = ('updated', st[1], None) if st[0] == 'created' else st
        return out

    def _post_batch_with_retry(self, body: str, batch_id: str, max_retries: int = 3) -> requests.Response:
        """Send a $batch request with automatic retry on 429 throttling and transient 5xx errors."""
        last_resp = None
        last_exc = None
        for attempt in range(max_retries):
            headers = self._auth_header()
            headers['Content-Type'] = 'multipart/mixed; boundary=' + batch_id
            headers['Accept'] = 'application/json'
            try:
                resp = self._session.post(self.api_root + '$batch', headers=headers,
                                          data=body.encode('utf-8'), timeout=_BATCH_TIMEOUT)
                if resp.status_code == 429:
                    retry_after = 2.0
                    try:
                        retry_after = float(resp.headers.get('Retry-After', 2.0))
                    except (ValueError, TypeError):
                        pass
                    time.sleep(max(1.0, min(retry_after, 10.0)))
                    continue
                if resp.status_code in (502, 503, 504):
                    time.sleep(1.5 ** attempt)
                    continue
                return resp
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                last_exc = exc
                time.sleep(1.5 ** attempt)
        if last_exc:
            raise last_exc
        return resp

    def _post_batch_creates(self, entity_set: str, indexed_payloads: list) -> dict:
        """POST a chunk of creates as one $batch, each in its own changeset.

        Per-row isolation: one bad row fails only its own changeset, the rest commit.
        Returns {index: (status, error, id)}.
        """
        import uuid as _uuid
        batch_id = 'batch_' + _uuid.uuid4().hex
        lines = []
        order = []
        for n, (i, payload) in enumerate(indexed_payloads, start=1):
            cs_id = 'changeset_' + _uuid.uuid4().hex
            order.append(i)
            lines.append('--' + batch_id)
            lines.append('Content-Type: multipart/mixed; boundary=' + cs_id)
            lines.append('')
            lines.append('--' + cs_id)
            lines.append('Content-Type: application/http')
            lines.append('Content-Transfer-Encoding: binary')
            lines.append('Content-ID: ' + str(n))
            lines.append('')
            lines.append('POST ' + self.api_root + entity_set + ' HTTP/1.1')
            lines.append('Content-Type: application/json;type=entry')
            lines.append('')
            lines.append(json.dumps(payload))
            lines.append('--' + cs_id + '--')
        lines.append('--' + batch_id + '--')
        body = '\r\n'.join(lines)

        out = {}
        try:
            resp = self._post_batch_with_retry(body, batch_id)
        except Exception as exc:
            reason = f'Batch create network error: {exc}'
            for i in order:
                out[i] = ('failed', reason, None)
            return out

        if resp.status_code >= 400:
            reason = 'Batch request failed (%d): %s' % (resp.status_code, self._extract_batch_error(resp.text))
            for i in order:
                out[i] = ('failed', reason, None)
            return out
        statuses = self._parse_batch_response(resp.text)
        for pos, i in enumerate(order):
            st = statuses[pos] if pos < len(statuses) else ('failed', 'No response for row', None)
            out[i] = st
        return out

    @staticmethod
    def _extract_batch_error(text: str) -> str:
        """Pull the real error message out of a failed $batch response.

        On a top-level 400 the useful detail is a JSON {"error":{"message":...}}
        blob buried past the MIME boundary headers, so a naive text[:200] slice
        only ever shows meaningless multipart noise. This digs out the message.
        """
        import re as _re
        if not text:
            return 'no error detail returned by Dataverse'
        for jm in _re.finditer(r'\{.*?"error".*?\}\s*\}', text, _re.DOTALL) or []:
            try:
                err = json.loads(jm.group(0)).get('error', {})
                msg = err.get('message')
                if msg:
                    code = err.get('code')
                    return ('[%s] %s' % (code, msg)) if code else msg
            except Exception:
                continue
        # Fallback: strip MIME/HTTP boundary noise and return the first real text.
        cleaned = _re.sub(r'(?im)^(--(?:batch|changeset)response[^\r\n]*|'
                          r'Content-[^\r\n]*|HTTP/1\.1[^\r\n]*|OData-[^\r\n]*|'
                          r'Date:[^\r\n]*|boundary=[^\r\n]*)$', '', text)
        cleaned = cleaned.strip()
        return cleaned[:500] if cleaned else 'no error detail returned by Dataverse'

    @staticmethod
    def _parse_batch_response(text: str):
        """Parse a $batch multipart response into an ordered list of (status, error, id)."""
        import re as _re
        results = []
        matches = list(_re.finditer(r'HTTP/1\.1\s+(\d{3})([^\r\n]*)', text))
        for idx, m in enumerate(matches):
            code = int(m.group(1))
            end_pos = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            part = text[m.end(): end_pos]
            if 200 <= code < 300:
                rec_id = None
                em = _re.search(r'OData-EntityId:[^(]*\(([0-9a-fA-F-]+)\)', part)
                if em:
                    rec_id = em.group(1)
                results.append(('created', None, rec_id))
            else:
                err = None
                jm = _re.search(r'\{.*?"error".*?\}\s*\}', part, _re.DOTALL)
                if jm:
                    try:
                        err_obj = json.loads(jm.group(0)).get('error', {})
                        err = err_obj.get('message')
                    except Exception:
                        pass
                if not err:
                    fallback = _re.search(r'"message"\s*:\s*"([^"]+)"', part)
                    if fallback:
                        err = fallback.group(1)
                results.append(('failed', err or ('HTTP %d' % code), None))
        return results

    def publish_records(self, entity_logical: str, rows: list[dict], mode: str = 'negotiation') -> dict:
        """Legacy sequential publish (kept for compatibility). Prefer publish_records_batch."""
        if not rows:
            return {'ok': False, 'error': 'No records to publish.'}
        ctx = self._publish_context(entity_logical, list(rows[0].keys()))
        info = ctx['info']
        created_count = 0
        errors = []
        for idx, row in enumerate(rows):
            payload = self._row_to_payload(row, ctx)
            if not payload:
                continue
            try:
                self.create_record(entity_logical, payload)
                created_count += 1
            except Exception as exc:
                errors.append(f"Row #{idx + 1}: {exc}")
                if len(errors) > 5:
                    break

        table_display = info.get('title') or clean_table_title(entity_logical)

        if errors and created_count == 0:
            err_details = "\n- " + "\n- ".join(errors)
            return {'ok': False, 'error': f"Publish failed ({len(errors)} error(s)):{err_details}", 'created': 0, 'errors': errors, 'has_errors': True}

        if errors:
            err_details = "\n- " + "\n- ".join(errors)
            msg = f"Published {created_count} of {len(rows)} record(s) to '{table_display}'. Failed on {len(errors)} record(s):{err_details}"
            return {'ok': True, 'created': created_count, 'message': msg, 'errors': errors, 'has_errors': True}


        msg = f"Successfully published all {created_count} record(s) to '{table_display}'."
        return {'ok': True, 'created': created_count, 'message': msg, 'errors': [], 'has_errors': False}




    _CANONICAL_FIELD_NAMES = {
        'team': {
            'employeeName': ('Employee Name', 'Name', 'Negotiator', 'Negotiator Name', 'LSA Name'),
            'team': ('Team', 'Team Name'),
            'group': ('Group', 'Group Name'),
            'dept': ('Department', 'Dept', 'Department Name'),
        },
        'municipality': {
            'municipality': ('Municipality', 'City', 'Town'),
            'muniCode': ('MuniCode', 'Muni Code', 'Code'),
            'province': ('Province', 'Prov'),
        },
        'mapping': {
            'description': ('Description', 'Desc', 'Mapping Status'),
            'mappingLabel': ('Mapping Status', 'Status'),
            'dataUsability': ('Data Usability', 'Usability'),
        },
        'build': {
            'areaIndex': ('Area Index', 'Area-Index', 'AreaIndex'),
            'build': ('Build', 'Build Name'),
        },
        'existing': {
            'aREAINDEX': ('AREA-INDEX', 'Area Index', 'AreaIndex'),
            'iD1': ('ID', 'Id', 'Record ID'),
            'date': ('NEGO DATE', 'Nego Date', 'REPORT DATE', 'Report Date', 'SOURCING DATE', 'Sourcing Date', 'DATE', 'Date'),
            'classification': ('CLASSIFICATION', 'Classification', 'Class', 'CLASS'),
        },
    }

    def resolve_field_name(self, entity_logical: str, kind: str, canonical_key: str) -> str:
        candidates = self._CANONICAL_FIELD_NAMES.get(kind, {}).get(canonical_key, (canonical_key,))
        res = self._resolve(entity_logical, *candidates)
        if res and res.get('logical'):
            return res['logical']
        amap = self._attribute_map(entity_logical)
        hit = amap.get(_norm(canonical_key))
        if hit and hit.get('logical'):
            return hit['logical']
        return canonical_key

    # -- typed table readers ----------------------------------------------
    def get_team_composition(self) -> list[dict]:

        ent = 'cr63f_teamcomposition'
        info = self.get_entity_info(ent)
        pk = info['primaryIdAttribute']
        name = self._resolve(ent, 'Employee Name')
        team = self._resolve(ent, 'Team')
        group = self._resolve(ent, 'Group')
        dept = self._resolve(ent, 'Department', 'Dept')
        select = [_select_name(c) for c in (name, team, group, dept) if c]
        if pk and pk not in select:
            select.append(pk)
        rows = self._get_all(info['entitySetName'], select, formatted=_needs_formatting(name, team, group, dept))
        out = []
        for r in rows:
            out.append({
                'employeeName': _formatted(r, name['logical']) if name else '',
                'team': _formatted(r, team['logical']) if team else '',
                'group': _formatted(r, group['logical']) if group else '',
                'dept': _formatted(r, dept['logical']) if dept else '',
                '_record_id': _s(r.get(pk)),
                '_entity_logical': ent,
            })
        return [r for r in out if r['employeeName']]

    def get_municipality_codes(self) -> list[dict]:
        ent = 'cr63f_municipalitycode'
        info = self.get_entity_info(ent)
        pk = info['primaryIdAttribute']
        muni = self._resolve(ent, 'Municipality')
        code = self._resolve(ent, 'MuniCode', 'Muni Code')
        prov = self._resolve(ent, 'Province')
        select = [_select_name(c) for c in (muni, code, prov) if c]
        if pk and pk not in select:
            select.append(pk)
        rows = self._get_all(info['entitySetName'], select, formatted=_needs_formatting(muni, code, prov))
        out = []
        for r in rows:
            out.append({
                'municipality': _formatted(r, muni['logical']) if muni else '',
                'muniCode': _formatted(r, code['logical']) if code else '',
                'province': _formatted(r, prov['logical']) if prov else '',
                '_record_id': _s(r.get(pk)),
                '_entity_logical': ent,
            })
        return [r for r in out if r['municipality']]

    def get_mapping_statuses(self) -> list[dict]:
        ent = 'cr63f_mappingstatus'
        info = self.get_entity_info(ent)
        pk = info['primaryIdAttribute']
        desc = self._resolve(ent, 'Description')
        status = self._resolve(ent, 'Mapping Status')
        usab = self._resolve(ent, 'Data Usability')
        select = [_select_name(c) for c in (desc, status, usab) if c]
        if pk and pk not in select:
            select.append(pk)
        rows = self._get_all(info['entitySetName'], select, formatted=True)
        out = []
        for r in rows:
            out.append({
                'description': _formatted(r, desc['logical']) if desc else '',
                'mappingLabel': _formatted(r, status['logical']) if status else '',
                'dataUsability': _formatted(r, usab['logical']) if usab else '',
                '_record_id': _s(r.get(pk)),
                '_entity_logical': ent,
            })
        return [r for r in out if r['description'] or r['mappingLabel']]

    def get_build_rows(self, logical_name: str = 'cr63f_batangasbuild') -> list[dict]:
        ent = (logical_name or 'cr63f_batangasbuild').strip()
        info = self.get_entity_info(ent)
        pk = info['primaryIdAttribute']
        area = self._resolve(ent, 'Area Index', 'Area-Index', 'AreaIndex')
        build = self._resolve(ent, 'Build', 'Build Name')
        ww = self._resolve(ent, 'Work Week', 'WorkWeek', 'WW', 'Work-Week')
        select = [_select_name(c) for c in (area, build, ww) if c]
        if pk and pk not in select:
            select.append(pk)
        rows = self._get_all(info['entitySetName'], select, formatted=_needs_formatting(area, build, ww))
        out = []
        for r in rows:
            out.append({
                'areaIndex': _formatted(r, area['logical']) if area else '',
                'build': _formatted(r, build['logical']) if build else '',
                'workWeek': _formatted(r, ww['logical']) if ww else '',
                '_record_id': _s(r.get(pk)),
                '_entity_logical': ent,
            })
        return [r for r in out if r['areaIndex']]

    def sync_build_records(self, logical_name: str, inserts: list[dict], updates: list[dict],
                           progress_cb=None, max_retries: int = 2) -> dict:
        """Apply a Build upsert via OData $batch (one changeset per row, per-row
        isolation), retrying transiently-failed rows up to `max_retries` times.

        Each insert  = {'areaIndex', 'build', 'workWeek'}.
        Each update  = {'_record_id', 'build', 'workWeek'}  (area-index is the match key, unchanged).
        Never deletes. `progress_cb(done, total)` (optional) fires per batch chunk.
        Returns {'created', 'updated', 'errors':[...]}.
        """
        ent = (logical_name or 'cr63f_batangasbuild').strip()
        area = self._resolve(ent, 'Area Index', 'Area-Index', 'AreaIndex')
        build = self._resolve(ent, 'Build', 'Build Name')
        ww = self._resolve(ent, 'Work Week', 'WorkWeek', 'WW', 'Work-Week')
        if not area or not build:
            raise RuntimeError(f"Could not resolve Area Index / Build columns on '{clean_table_title(ent)}'.")

        def _payload(rec: dict, with_area: bool) -> dict:
            p = {build['logical']: _s(rec.get('build'))}
            if with_area:
                p[area['logical']] = _s(rec.get('areaIndex'))
            if ww is not None:
                wk = pt_normalize_work_week(rec.get('workWeek'))
                if wk is not None:
                    # Pass as str so create/update coerce to the WW column's real
                    # type (whole-number -> int, text -> string).
                    p[ww['logical']] = str(wk)
            return p

        info = self.get_entity_info(ent)
        entity_set = info['entitySetName']
        area_col = area['logical']

        # Build the operation list (POST for inserts, PATCH for updates).
        ops: list[dict] = []
        errors: list[dict] = []
        for idx, rec in enumerate(inserts):
            ops.append({'key': f'i{idx}', 'kind': 'insert', 'method': 'POST',
                        'url': self.api_root + entity_set, 'payload': _payload(rec, True),
                        'area': _s(rec.get('areaIndex'))})
        for idx, rec in enumerate(updates):
            rid = _s(rec.get('_record_id')).strip('{}')
            if not rid:
                errors.append({'area': _s(rec.get('areaIndex')), 'op': 'update', 'error': 'Missing record id'})
                continue
            ops.append({'key': f'u{idx}', 'kind': 'update', 'method': 'PATCH',
                        'url': f"{self.api_root}{entity_set}({rid})", 'payload': _payload(rec, False),
                        'area': _s(rec.get('areaIndex'))})

        total = len(ops)
        results: dict = {}  # key -> (status, error, id)
        done = 0
        chunk_size = 100

        pending = ops
        round_no = 0
        while pending and round_no <= max_retries:
            if round_no > 0:
                # A read-timeout can leave an insert committed server-side even though
                # the client saw it fail. Before retrying inserts, drop any whose area
                # index now exists, so a retry never creates a duplicate row.
                ins = [o for o in pending if o['kind'] == 'insert']
                if ins:
                    present = self._area_indexes_present(entity_set, area_col, [o['area'] for o in ins])
                    kept = []
                    for o in pending:
                        if o['kind'] == 'insert' and o['area'].strip().lower() in present:
                            results[o['key']] = ('created', None, None)
                        else:
                            kept.append(o)
                    pending = kept
                time.sleep(1.5)  # brief backoff before retrying
            failed = []
            for start in range(0, len(pending), chunk_size):
                chunk = pending[start:start + chunk_size]
                res = self._post_batch_ops(chunk)
                for o in chunk:
                    st = res.get(o['key'], ('failed', 'No response for row', None))
                    results[o['key']] = st
                    if st[0] != 'created':
                        failed.append(o)
                if round_no == 0:
                    done += len(chunk)
                    if progress_cb:
                        try:
                            progress_cb(min(done, total), total)
                        except Exception:  # noqa: BLE001
                            pass
            pending = failed
            round_no += 1

        created = updated = 0
        by_key = {o['key']: o for o in ops}
        for key, (status, err, _id) in results.items():
            o = by_key.get(key)
            if not o:
                continue
            if status == 'created':
                if o['kind'] == 'insert':
                    created += 1
                else:
                    updated += 1
            else:
                errors.append({'area': o['area'], 'op': o['kind'], 'error': err or 'Write failed'})
        return {'created': created, 'updated': updated, 'errors': errors}

    def _post_batch_ops(self, ops: list) -> dict:
        """Send a chunk of create/update operations as one $batch, each in its own
        changeset (per-row isolation). ops: [{'key','method','url','payload'}].
        A batch-level failure/timeout marks every row in the chunk failed (to be
        retried by the caller). Returns {key: (status, error, id)}."""
        import uuid as _uuid
        batch_id = 'batch_' + _uuid.uuid4().hex
        lines, order = [], []
        for n, op in enumerate(ops, start=1):
            cs_id = 'changeset_' + _uuid.uuid4().hex
            order.append(op['key'])
            lines.append('--' + batch_id)
            lines.append('Content-Type: multipart/mixed; boundary=' + cs_id)
            lines.append('')
            lines.append('--' + cs_id)
            lines.append('Content-Type: application/http')
            lines.append('Content-Transfer-Encoding: binary')
            lines.append('Content-ID: ' + str(n))
            lines.append('')
            lines.append(op['method'] + ' ' + op['url'] + ' HTTP/1.1')
            lines.append('Content-Type: application/json;type=entry')
            if op['method'] == 'PATCH':
                lines.append('If-Match: *')
            lines.append('')
            lines.append(json.dumps(op['payload']))
            lines.append('--' + cs_id + '--')
        lines.append('--' + batch_id + '--')
        body = '\r\n'.join(lines)

        headers = self._auth_header()
        headers['Content-Type'] = 'multipart/mixed; boundary=' + batch_id
        headers['Accept'] = 'application/json'
        try:
            resp = self._session.post(self.api_root + '$batch', headers=headers,
                                      data=body.encode('utf-8'), timeout=_BATCH_TIMEOUT)
        except Exception as exc:  # noqa: BLE001  (timeout / connection error -> whole chunk retried)
            return {k: ('failed', f'Batch request failed: {exc}', None) for k in order}
        if resp.status_code >= 400:
            reason = 'Batch request failed (%d): %s' % (resp.status_code, self._extract_batch_error(resp.text))
            return {k: ('failed', reason, None) for k in order}
        statuses = self._parse_batch_response(resp.text)
        out = {}
        for pos, k in enumerate(order):
            out[k] = statuses[pos] if pos < len(statuses) else ('failed', 'No response for row', None)
        return out

    def _area_indexes_present(self, entity_set: str, area_col: str, area_values: list) -> set:
        """Return the lowercased set of the given area indexes that already exist in
        the table (used to avoid re-inserting a row a timed-out attempt committed)."""
        cleaned = [str(a).strip() for a in area_values if a and str(a).strip()]
        if not cleaned:
            return set()
        found: set = set()
        chunk = 40
        for start in range(0, len(cleaned), chunk):
            part = cleaned[start:start + chunk]
            or_parts = [f"{area_col} eq '{_escape_odata(a)}'" for a in part]
            url = f"{self.api_root}{entity_set}?$select={area_col}&$filter=({' or '.join(or_parts)})"
            try:
                for r in self._get(url).get('value', []):
                    v = _s(r.get(area_col))
                    if v:
                        found.add(v.strip().lower())
            except Exception:  # noqa: BLE001
                pass
        return found

    def sync_reference_records(self, logical_name: str, kind: str, inserts: list[dict],
                               updates: list[dict], progress_cb=None) -> dict:
        """Non-destructive upsert of a small reference table (team/municipality/mapping):
        create `inserts`, overwrite `updates` (by `_record_id`). Never deletes. Each row
        is app-side keys (e.g. employeeName/team/group/dept) resolved to DV columns via
        resolve_field_name(kind). `progress_cb(done, total)` fires after each write."""
        ent = (logical_name or '').strip()

        def _payload(rec: dict) -> dict:
            p = {}
            for k, v in rec.items():
                if k.startswith('_') or v is None or str(v).strip() == '':
                    continue
                p[self.resolve_field_name(ent, kind, k)] = v
            return p

        def _first_val(rec: dict) -> str:
            for k, v in rec.items():
                if not k.startswith('_') and str(v or '').strip():
                    return str(v)
            return ''

        created = updated = 0
        errors: list[dict] = []
        total = len(inserts) + len(updates)
        done = 0

        def _tick():
            nonlocal done
            done += 1
            if progress_cb:
                try:
                    progress_cb(done, total)
                except Exception:  # noqa: BLE001
                    pass

        for rec in inserts:
            try:
                self.create_record(ent, _payload(rec))
                created += 1
            except Exception as exc:  # noqa: BLE001
                errors.append({'key': _first_val(rec), 'op': 'insert', 'error': str(exc)})
            _tick()
        for rec in updates:
            rid = _s(rec.get('_record_id'))
            if not rid:
                errors.append({'key': _first_val(rec), 'op': 'update', 'error': 'Missing record id'})
                _tick()
                continue
            try:
                self.update_record(ent, rid, _payload(rec))
                updated += 1
            except Exception as exc:  # noqa: BLE001
                errors.append({'key': _first_val(rec), 'op': 'update', 'error': str(exc)})
            _tick()
        return {'created': created, 'updated': updated, 'errors': errors}

    def get_latest_id(self, mode: str) -> int:
        """Fetch highest ID value with an orderby/top=1 query (1 record fetch)."""
        if mode == 'land-sourcing':
            ent, entity_set = 'cr63f_batangassourcingrecord', 'cr63f_batangassourcingrecords'
        else:
            ent, entity_set = 'cr63f_batangasnegorecord', 'cr63f_batangasnegorecords'
        rec_id = self._resolve(ent, 'cr63f_id', 'ID', 'Id', 'Record ID', 'ITEM #', 'ITEM NO', 'cr63f_itemno')
        if not rec_id:
            return 0
        id_col = rec_id['logical']
        url = f"{self.api_root}{entity_set}?$select={id_col}&$filter={id_col} ne null&$orderby={id_col} desc&$top=1"
        try:
            data = self._get(url)
            rows = data.get('value', [])
            if rows:
                return _to_int(rows[0].get(id_col), 0)
        except Exception:
            # Fallback without filter if filter syntax differs
            try:
                url_simple = f"{self.api_root}{entity_set}?$select={id_col}&$orderby={id_col} desc&$top=1"
                data = self._get(url_simple)
                rows = data.get('value', [])
                if rows:
                    return _to_int(rows[0].get(id_col), 0)
            except Exception:
                pass
        return 0

    def check_existing_area_indexes(self, mode: str, area_indexes: set[str] | list[str]) -> set[str]:
        if not area_indexes:
            return set()
        if mode == 'land-sourcing':
            ent, entity_set = 'cr63f_batangassourcingrecord', 'cr63f_batangassourcingrecords'
        else:
            ent, entity_set = 'cr63f_batangasnegorecord', 'cr63f_batangasnegorecords'
        area = self._resolve(ent, 'AREA-INDEX', 'Area Index')

        if not area:
            return set()
        area_col = area['logical']
        cleaned = [idx.strip() for idx in area_indexes if idx and idx.strip()]
        if not cleaned:
            return set()
        found: set[str] = set()
        chunk_size = 40
        chunks = [cleaned[i:i + chunk_size] for i in range(0, len(cleaned), chunk_size)]

        def probe(chunk):
            or_parts = [f"{area_col} eq '{_escape_odata(idx)}'" for idx in chunk]
            url = f"{self.api_root}{entity_set}?$select={area_col}&$filter=({' or '.join(or_parts)})"
            try:
                return [_s(r.get(area_col)) for r in self._get(url).get('value', [])]
            except Exception:
                return []

        # A large upload produces dozens of chunks; running them in series was
        # one round-trip each. The pooled session makes concurrent probes cheap.
        if len(chunks) > 1:
            with _cf.ThreadPoolExecutor(max_workers=min(6, len(chunks))) as pool:
                for vals in pool.map(probe, chunks):
                    found.update(v.lower() for v in vals if v)
        elif chunks:
            found.update(v.lower() for v in probe(chunks[0]) if v)
        return found


    def get_records_for_area_indexes(self, mode: str, area_indexes: set[str] | list[str]) -> list[dict]:
        """Fetch all historical records for specific area indexes from Dataverse."""
        if not area_indexes:
            return []
        from productivity_tool import format_date_to_mm_dd_yyyy
        if mode == 'land-sourcing':
            ent, entity_set = 'cr63f_batangassourcingrecord', 'cr63f_batangassourcingrecords'
            date_attr = self._resolve(ent, 'cr63f_reportdate', 'REPORT DATE', 'Report Date', 'cr63f_reportdatetext', 'cr63f_sourcingdate', 'SOURCING DATE')
            classification = self._resolve(ent, 'cr63f_classification', 'CLASSIFICATION', 'Classification', 'Class')
        else:
            ent, entity_set = 'cr63f_batangasnegorecord', 'cr63f_batangasnegorecords'
            date_attr = self._resolve(ent, 'cr63f_negotiationdate', 'NEGO DATE', 'Nego Date', 'Negotiation Date', 'cr63f_negodate', 'NEGO DATE TEXT', 'cr63f_negodatetext')
            classification = self._resolve(ent, 'cr63f_classificationstatus', 'cr63f_classification', 'CLASSIFICATION', 'Classification', 'Classification Status', 'Class')
        area = self._resolve(ent, 'cr63f_areaindex', 'AREA-INDEX', 'Area Index')
        rec_id = self._resolve(ent, 'cr63f_id', 'ID', 'ITEM #')
        reg_owner = self._resolve(ent, 'cr63f_registeredowner', 'REGISTERED OWNER', 'Registered Owner', 'Owner')
        auth_rep = self._resolve(ent, 'cr63f_authorizedrepresentative', 'AUTHORIZED REPRESENTATIVE', 'Authorized Representative', 'Auth Rep', 'Representative')

        if not area:
            return []
        area_col = area['logical']
        select_cols = [a['logical'] for a in (area, rec_id, date_attr, classification, reg_owner, auth_rep) if a and a.get('logical')]
        info = self.get_entity_info(ent)
        pk = info.get('primaryIdAttribute')
        if pk and pk not in select_cols:
            select_cols.append(pk)

        cleaned = [idx.strip() for idx in area_indexes if idx and idx.strip()]
        if not cleaned:
            return []

        chunk_size = 40
        chunks = [cleaned[i:i + chunk_size] for i in range(0, len(cleaned), chunk_size)]

        def fetch_chunk(chunk):
            or_parts = [f"{area_col} eq '{_escape_odata(idx)}'" for idx in chunk]
            filter_str = ' or '.join(or_parts)
            return self._get_all(entity_set, select_cols, filter_str=filter_str)

        all_rows = []
        if len(chunks) > 1:
            with _cf.ThreadPoolExecutor(max_workers=min(6, len(chunks))) as pool:
                for chunk_rows in pool.map(fetch_chunk, chunks):
                    all_rows.extend(chunk_rows)
        elif chunks:
            all_rows.extend(fetch_chunk(chunks[0]))

        out = []
        for r in all_rows:
            raw_date = _formatted(r, date_attr['logical']) if date_attr else ''
            norm_date = format_date_to_mm_dd_yyyy(raw_date) or raw_date
            cls_val = _formatted(r, classification['logical']) if classification else ''
            ro_val = _formatted(r, reg_owner['logical']) if reg_owner else ''
            ar_val = _formatted(r, auth_rep['logical']) if auth_rep else ''
            lo_val = ro_val or ar_val
            out.append({
                'aREAINDEX': _formatted(r, area['logical']) if area else '',
                'iD1': _to_int(r.get(rec_id['logical'])) if rec_id else 0,
                'date': norm_date,
                'NEGO DATE': norm_date,
                'REPORT DATE': norm_date,
                'classification': cls_val,
                'CLASSIFICATION': cls_val,
                'registeredOwner': ro_val,
                'REGISTERED OWNER': ro_val,
                'authorizedRepresentative': ar_val,
                'AUTHORIZED REPRESENTATIVE': ar_val,
                'lo': lo_val,
                'LO': lo_val,
                '_record_id': _s(r.get(pk)) if pk else '',
                '_entity_logical': ent,
            })
        return out


    def get_records_in_date_range(self, mode: str, start_date: str, end_date: str) -> list[dict]:
        """Fetch records within a specific date/work-week range (start_date/end_date in YYYY-MM-DD)."""
        from productivity_tool import format_date_to_mm_dd_yyyy
        if mode == 'land-sourcing':
            ent, entity_set = 'cr63f_batangassourcingrecord', 'cr63f_batangassourcingrecords'
            date_attr = self._resolve(ent, 'cr63f_reportdate', 'REPORT DATE', 'Report Date', 'cr63f_reportdatetext', 'cr63f_sourcingdate', 'SOURCING DATE')
            classification = self._resolve(ent, 'cr63f_classification', 'CLASSIFICATION', 'Classification', 'Class')
        else:
            ent, entity_set = 'cr63f_batangasnegorecord', 'cr63f_batangasnegorecords'
            date_attr = self._resolve(ent, 'cr63f_negotiationdate', 'NEGO DATE', 'Nego Date', 'Negotiation Date', 'cr63f_negodate', 'NEGO DATE TEXT', 'cr63f_negodatetext')
            classification = self._resolve(ent, 'cr63f_classificationstatus', 'cr63f_classification', 'CLASSIFICATION', 'Classification', 'Classification Status', 'Class')
        area = self._resolve(ent, 'AREA-INDEX', 'Area Index')
        rec_id = self._resolve(ent, 'ID')
        reg_owner = self._resolve(ent, 'cr63f_registeredowner', 'REGISTERED OWNER', 'Registered Owner', 'Owner')
        auth_rep = self._resolve(ent, 'cr63f_authorizedrepresentative', 'AUTHORIZED REPRESENTATIVE', 'Authorized Representative', 'Auth Rep', 'Representative')
        select = [a['logical'] for a in (area, rec_id, date_attr, classification, reg_owner, auth_rep) if a and a.get('logical')]
        info = self.get_entity_info(ent)
        pk = info.get('primaryIdAttribute')
        if pk and pk not in select:
            select.append(pk)

        filter_str = None
        if date_attr:
            d_col = date_attr['logical']
            attr_type = date_attr.get('type')
            if attr_type == 'DateTime':
                filter_str = f"({d_col} ge {start_date}T00:00:00Z and {d_col} le {end_date}T23:59:59Z)"
            else:
                filter_str = f"({d_col} ge '{start_date}' and {d_col} le '{end_date}')"

        rows = self._get_all(entity_set, select, filter_str=filter_str)
        out = []
        for r in rows:
            raw_date = _formatted(r, date_attr['logical']) if date_attr else ''
            norm_date = format_date_to_mm_dd_yyyy(raw_date) or raw_date
            cls_val = _formatted(r, classification['logical']) if classification else ''
            ro_val = _formatted(r, reg_owner['logical']) if reg_owner else ''
            ar_val = _formatted(r, auth_rep['logical']) if auth_rep else ''
            lo_val = ro_val or ar_val
            out.append({
                'aREAINDEX': _formatted(r, area['logical']) if area else '',
                'iD1': _to_int(r.get(rec_id['logical'])) if rec_id else 0,
                'date': norm_date,
                'NEGO DATE': norm_date,
                'REPORT DATE': norm_date,
                'classification': cls_val,
                'CLASSIFICATION': cls_val,
                'registeredOwner': ro_val,
                'REGISTERED OWNER': ro_val,
                'authorizedRepresentative': ar_val,
                'AUTHORIZED REPRESENTATIVE': ar_val,
                'lo': lo_val,
                'LO': lo_val,
                '_record_id': _s(r.get(pk)) if pk else '',
                '_entity_logical': ent,
            })
        return out


    # -- dynamic solution & table explorer ---------------------------------
    def get_solutions(self) -> list[dict]:
        """Fetch all visible solutions from the environment."""
        url = (f"{self.api_root}solutions?$select=solutionid,uniquename,friendlyname,version,ismanaged,description"
               "&$filter=isvisible eq true&$orderby=friendlyname asc")
        try:
            data = self._get(url)
            return [{
                'id': s.get('solutionid'),
                'uniqueName': s.get('uniquename'),
                'friendlyName': s.get('friendlyname') or s.get('uniquename'),
                'version': s.get('version', ''),
                'isManaged': bool(s.get('ismanaged')),
                'description': s.get('description') or '',
            } for s in data.get('value', [])]
        except Exception as exc:
            raise RuntimeError(f'Could not fetch solutions: {exc}') from exc

    def _entity_definitions(self) -> list[dict]:
        """Environment-wide table metadata, fetched at most once per session.

        This is the single heaviest call in the API (every table in the org),
        and it used to be re-issued on every solution switch.
        """
        if getattr(self, '_entity_defs_cache', None) is not None:
            return self._entity_defs_cache
        url = (f"{self.api_root}EntityDefinitions?$select=MetadataId,LogicalName,DisplayName,EntitySetName,"
               "PrimaryNameAttribute,IsCustomEntity&$filter=IsPrivate eq false")
        self._entity_defs_cache = self._get(url).get('value', [])
        return self._entity_defs_cache

    def get_solution_tables(self, solution_id: str | None = None) -> list[dict]:
        """Fetch tables in a solution (or all non-private tables in the environment)."""
        cache_key = str(solution_id or '')
        cached = self._solution_tables_cache.get(cache_key)
        if cached is not None:
            return cached
        table_meta_ids = set()
        resolved_sol_id = solution_id
        if solution_id:
            # If solution_id is a unique name or friendly name, resolve its GUID
            if len(solution_id) != 36 or '-' not in solution_id:
                try:
                    sol_url = f"{self.api_root}solutions?$select=solutionid,uniquename&$filter=uniquename eq '{solution_id}' or friendlyname eq '{solution_id}'"
                    sols = self._get(sol_url).get('value', [])
                    if sols:
                        resolved_sol_id = sols[0].get('solutionid')
                except Exception:
                    pass

            try:
                comp_url = f"{self.api_root}solutions({resolved_sol_id})/solution_solutioncomponent?$filter=componenttype eq 1&$select=objectid"
                comp_data = self._get(comp_url)
                for comp in comp_data.get('value', []):
                    oid = comp.get('objectid')
                    if oid:
                        table_meta_ids.add(str(oid).lower())
            except Exception:
                pass

        entity_defs = self._entity_definitions()
        tables = []
        for ent in entity_defs:
            meta_id = str(ent.get('MetadataId', '')).lower()
            if table_meta_ids and meta_id not in table_meta_ids:
                continue
            logical = ent.get('LogicalName')
            disp = ent.get('DisplayName') or {}
            label = ((disp.get('UserLocalizedLabel') or {}).get('Label')
                     or (disp.get('LocalizedLabels') or [{}])[0].get('Label') if disp else None) or logical
            tables.append({
                'metadataId': meta_id,
                'logicalName': logical,
                'displayName': label,
                'entitySetName': ent.get('EntitySetName') or f"{logical}s",
                'isCustom': bool(ent.get('IsCustomEntity')),
                'primaryName': ent.get('PrimaryNameAttribute') or '',
            })
        # If solution filter returned nothing (e.g. solution component lookup restriction), return custom entities
        if table_meta_ids and not tables:
            for ent in entity_defs:
                if ent.get('IsCustomEntity') or (ent.get('LogicalName') or '').startswith('cr63f_'):
                    logical = ent.get('LogicalName')
                    disp = ent.get('DisplayName') or {}
                    label = ((disp.get('UserLocalizedLabel') or {}).get('Label')
                             or (disp.get('LocalizedLabels') or [{}])[0].get('Label') if disp else None) or logical
                    tables.append({
                        'metadataId': str(ent.get('MetadataId', '')).lower(),
                        'logicalName': logical,
                        'displayName': label,
                        'entitySetName': ent.get('EntitySetName') or f"{logical}s",
                        'isCustom': bool(ent.get('IsCustomEntity')),
                        'primaryName': ent.get('PrimaryNameAttribute') or '',
                    })
        # sort custom tables first, then alphabetically
        tables.sort(key=lambda t: (not t['isCustom'], t['displayName'].lower()))
        self._solution_tables_cache[cache_key] = tables
        return tables

    def clear_metadata_cache(self):
        """Drop cached metadata so the next call re-reads it from Dataverse."""
        self._entity_defs_cache = None
        self._solution_tables_cache = {}
        self._meta_cache = {}
        self._entity_info_cache = {}


    def get_table_columns(self, logical_name: str) -> list[dict]:
        """Readable, non-identifier columns for a table, ordered for display.

        Shared by the table preview and by the editable loaders' column picker,
        so the two always offer exactly the same column set.
        """
        attr_url = (f"{self.api_root}EntityDefinitions(LogicalName='{logical_name}')/Attributes"
                    "?$select=LogicalName,DisplayName,AttributeType,IsValidForRead&$filter=IsValidForRead eq true")
        attr_data = self._get(attr_url)
        col_list = []
        for a in attr_data.get('value', []):
            clogical = a.get('LogicalName')
            cdisp = a.get('DisplayName') or {}
            clabel = ((cdisp.get('UserLocalizedLabel') or {}).get('Label')
                      or (cdisp.get('LocalizedLabels') or [{}])[0].get('Label') if cdisp else None)
            ctype = a.get('AttributeType')
            # Identifier columns (primary key, owner, ...) only ever show a raw
            # GUID, so keep them out entirely.
            if clogical and not clogical.startswith('_') and clabel and _type_rank(ctype) < 2:
                col_list.append({'logical': clogical, 'label': clabel, 'type': ctype})
        # sort columns with non-system first
        col_list.sort(key=lambda c: (c['logical'].startswith('created') or c['logical'].startswith('modified') or c['logical'].startswith('version'), c['label'].lower()))
        if not col_list:
            col_list = [{'logical': 'id', 'label': 'ID', 'type': 'String'}]
        return col_list

    def get_table_load_options(self, logical_name: str) -> dict:
        """Column picker payload: every readable column plus the date columns
        that can scope a load."""
        cols = self.get_table_columns(logical_name)
        date_cols = [c for c in cols if c.get('type') in ('DateTime', 'Date')]
        if not date_cols:
            # Some tables store the date as text; offer those as a fallback so a
            # range can still be applied.
            date_cols = [c for c in cols if 'date' in c['logical'].lower()]
        return {
            'ok': True,
            'logicalName': logical_name,
            'columns': cols,
            'dateColumns': date_cols,
            'defaultDateColumn': date_cols[0]['logical'] if date_cols else '',
        }

    @staticmethod
    def _date_range_filter(date_meta: dict, date_from: str, date_to: str) -> str:
        """OData filter for a date column, quoted according to its type."""
        col = date_meta['logical']
        clauses = []
        if date_meta.get('type') == 'DateTime':
            if date_from:
                clauses.append(f"{col} ge {date_from}T00:00:00Z")
            if date_to:
                clauses.append(f"{col} le {date_to}T23:59:59Z")
        else:
            if date_from:
                clauses.append(f"{col} ge '{date_from}'")
            if date_to:
                clauses.append(f"{col} le '{date_to}'")
        return f"({' and '.join(clauses)})" if clauses else ''

    def get_table_choices(self, logical_name: str, wanted: set[str] | None = None) -> dict:
        """Option-set labels for a table's picklist columns, as {logical: [labels]}.

        Fetched in one metadata call for the whole table rather than one per
        column, so opening a grid with several choice columns stays a single
        round-trip.
        """
        out: dict[str, list[str]] = {}
        base = f"{self.api_root}EntityDefinitions(LogicalName='{logical_name}')/Attributes"
        for cast in ('Microsoft.Dynamics.CRM.PicklistAttributeMetadata',
                     'Microsoft.Dynamics.CRM.StateAttributeMetadata',
                     'Microsoft.Dynamics.CRM.StatusAttributeMetadata'):
            try:
                data = self._get(f"{base}/{cast}?$select=LogicalName&$expand=OptionSet($select=Options)")
            except Exception:  # noqa: BLE001
                continue  # a table with no picklists of this kind is not an error
            for a in data.get('value', []) or []:
                logical = a.get('LogicalName')
                if not logical or (wanted and logical not in wanted):
                    continue
                labels = []
                for opt in ((a.get('OptionSet') or {}).get('Options') or []):
                    lab = opt.get('Label') or {}
                    text = ((lab.get('UserLocalizedLabel') or {}).get('Label')
                            or (lab.get('LocalizedLabels') or [{}])[0].get('Label') if lab else None)
                    if text:
                        labels.append(str(text))
                if labels:
                    out[logical] = labels
        return out

    def load_table_records(self, logical_name: str, columns: list[str] | None = None,
                           date_column: str = '', date_from: str = '', date_to: str = '',
                           limit: int | None = None) -> dict:
        """Load editable rows from any table, reading only the chosen columns.

        Narrowing $select and filtering server-side is the whole point: an
        unscoped read pages the entire table before the grid can render.
        """
        info = self.get_entity_info(logical_name)
        entity_set = info['entitySetName']
        pk = info.get('primaryIdAttribute')
        all_cols = self.get_table_columns(logical_name)
        by_logical = {c['logical']: c for c in all_cols}
        chosen = [by_logical[c] for c in (columns or []) if c in by_logical]
        if not chosen:
            chosen = all_cols[:25]

        select = [_select_name(c) for c in chosen]
        if pk and pk not in select:
            select.append(pk)

        filter_str = ''
        if date_column and (date_from or date_to) and date_column in by_logical:
            filter_str = self._date_range_filter(by_logical[date_column], date_from, date_to)

        rows_raw = self._get_all(entity_set, select, formatted=_needs_formatting(*chosen),
                                 top=min(limit, 5000) if limit else 5000,
                                 filter_str=filter_str or None, max_rows=limit)
        rows = []
        for r in rows_raw:
            row = {c['logical']: _formatted(r, c['logical']) for c in chosen}
            row['_record_id'] = _s(r.get(pk)) if pk else ''
            row['_entity_logical'] = logical_name
            rows.append(row)
        choice_cols = {c['logical'] for c in chosen
                       if c.get('type') in ('Picklist', 'State', 'Status')}
        return {
            'ok': True,
            'logicalName': logical_name,
            'columns': [c['logical'] for c in chosen],
            'labels': [c['label'] for c in chosen],
            'types': {c['logical']: c.get('type') for c in chosen},
            'choices': self.get_table_choices(logical_name, choice_cols) if choice_cols else {},
            'rows': rows,
            'count': len(rows),
            'filtered': bool(filter_str),
        }

    def get_any_table_data(self, logical_name: str, top: int = 500) -> dict:
        """Fetch columns and live rows from ANY table in Dataverse."""
        # 1. get entity definition (for entity set name and display name)
        ent_url = f"{self.api_root}EntityDefinitions(LogicalName='{logical_name}')?$select=LogicalName,DisplayName,EntitySetName"
        ent_data = self._get(ent_url)
        entity_set = ent_data.get('EntitySetName') or f"{logical_name}s"
        disp = ent_data.get('DisplayName') or {}
        table_title = ((disp.get('UserLocalizedLabel') or {}).get('Label')
                       or (disp.get('LocalizedLabels') or [{}])[0].get('Label') if disp else None) or logical_name

        col_list = self.get_table_columns(logical_name)

        select_cols = [_select_name(c) for c in col_list[:60]]  # limit select to 60 columns for safe URL size
        rows_raw = self._get_all(entity_set, select_cols, formatted=True, top=top)
        rows_out = []
        for r in rows_raw:
            row_cells = []
            for c in col_list[:60]:
                fmt_val = _formatted(r, c['logical'])
                row_cells.append(fmt_val)
            rows_out.append(row_cells)

        return {
            'ok': True,
            'logicalName': logical_name,
            'title': table_title,
            'labels': [c['label'] for c in col_list[:60]],
            'columns': [c['logical'] for c in col_list[:60]],
            'rows': rows_out,
            'count': len(rows_out),
            'source': f"Dataverse: {logical_name}",
        }

    def load_arbitrary_reference_table(self, logical_name: str, kind: str) -> list[dict]:
        """Load ANY table into one of the app's reference roles (team, municipality, mapping, build, existing)."""
        info = self.get_entity_info(logical_name)
        entity_set = info['entitySetName']
        pk = info['primaryIdAttribute']

        if kind == 'team':
            name = self._resolve(logical_name, 'Employee Name', 'Name', 'Negotiator', 'Negotiator Name', 'LSA Name')
            team = self._resolve(logical_name, 'Team', 'Team Name')
            group = self._resolve(logical_name, 'Group', 'Group Name')
            dept = self._resolve(logical_name, 'Department', 'Dept')
            select = [_select_name(c) for c in (name, team, group, dept) if c]
            if pk and pk not in select:
                select.append(pk)
            rows = self._get_all(entity_set, select, formatted=_needs_formatting(name, team, group, dept))
            out = [{'employeeName': _formatted(r, name['logical']) if name else '',
                    'team': _formatted(r, team['logical']) if team else '',
                    'group': _formatted(r, group['logical']) if group else '',
                    'dept': _formatted(r, dept['logical']) if dept else '',
                    '_record_id': _s(r.get(pk)),
                    '_entity_logical': logical_name} for r in rows]
            return [r for r in out if r['employeeName']]

        if kind == 'municipality':
            muni = self._resolve(logical_name, 'Municipality', 'City', 'Town')
            code = self._resolve(logical_name, 'MuniCode', 'Muni Code', 'Code')
            prov = self._resolve(logical_name, 'Province', 'Prov')
            select = [_select_name(c) for c in (muni, code, prov) if c]
            if pk and pk not in select:
                select.append(pk)
            rows = self._get_all(entity_set, select, formatted=_needs_formatting(muni, code, prov))
            out = [{'municipality': _formatted(r, muni['logical']) if muni else '',
                    'muniCode': _formatted(r, code['logical']) if code else '',
                    'province': _formatted(r, prov['logical']) if prov else '',
                    '_record_id': _s(r.get(pk)),
                    '_entity_logical': logical_name} for r in rows]
            return [r for r in out if r['municipality']]

        if kind == 'mapping':
            desc = self._resolve(logical_name, 'Description', 'Desc', 'Mapping Status')
            status = self._resolve(logical_name, 'Mapping Status', 'Status')
            usab = self._resolve(logical_name, 'Data Usability', 'Usability')
            select = [_select_name(c) for c in (desc, status, usab) if c]
            if pk and pk not in select:
                select.append(pk)
            rows = self._get_all(entity_set, select, formatted=True)
            out = [{'description': _formatted(r, desc['logical']) if desc else '',
                    'mappingLabel': _formatted(r, status['logical']) if status else '',
                    'dataUsability': _formatted(r, usab['logical']) if usab else '',
                    '_record_id': _s(r.get(pk)),
                    '_entity_logical': logical_name} for r in rows]
            return [r for r in out if r['description'] or r['mappingLabel']]

        if kind == 'build':
            area = self._resolve(logical_name, 'Area Index', 'Area-Index', 'AreaIndex')
            build = self._resolve(logical_name, 'Build', 'Build Name')
            ww = self._resolve(logical_name, 'Work Week', 'WorkWeek', 'WW', 'Work-Week')
            select = [_select_name(c) for c in (area, build, ww) if c]
            if pk and pk not in select:
                select.append(pk)
            rows = self._get_all(entity_set, select, formatted=_needs_formatting(area, build, ww))
            out = [{'areaIndex': _formatted(r, area['logical']) if area else '',
                    'build': _formatted(r, build['logical']) if build else '',
                    'workWeek': _formatted(r, ww['logical']) if ww else '',
                    '_record_id': _s(r.get(pk)),
                    '_entity_logical': logical_name} for r in rows]
            return [r for r in out if r['areaIndex']]

        if kind == 'existing':
            from productivity_tool import format_date_to_mm_dd_yyyy
            area = self._resolve(logical_name, 'cr63f_areaindex', 'AREA-INDEX', 'Area Index', 'AreaIndex')
            rec_id = self._resolve(logical_name, 'cr63f_id', 'ID', 'Id', 'Record ID', 'ITEM #', 'ITEM NO')
            date_attr = self._resolve(
                logical_name,
                'cr63f_negotiationdate', 'NEGO DATE', 'Nego Date', 'Negotiation Date', 'cr63f_negodate', 'NEGO DATE TEXT', 'cr63f_negodatetext',
                'cr63f_reportdate', 'REPORT DATE', 'Report Date', 'cr63f_reportdatetext', 'cr63f_sourcingdate', 'SOURCING DATE', 'DATE', 'Date'
            )
            classification = self._resolve(
                logical_name,
                'cr63f_classificationstatus', 'cr63f_classification', 'CLASSIFICATION', 'Classification', 'Classification Status', 'Class', 'CLASS'
            )
            reg_owner = self._resolve(logical_name, 'cr63f_registeredowner', 'REGISTERED OWNER', 'Registered Owner', 'Owner')
            auth_rep = self._resolve(logical_name, 'cr63f_authorizedrepresentative', 'AUTHORIZED REPRESENTATIVE', 'Authorized Representative', 'Auth Rep', 'Representative')
            select = [_select_name(c) for c in (area, rec_id, date_attr, classification, reg_owner, auth_rep) if c]
            if pk and pk not in select:
                select.append(pk)
            rows = self._get_all(entity_set, select, formatted=True)
            out = []
            for r in rows:
                raw_date = _formatted(r, date_attr['logical']) if date_attr else ''
                norm_date = format_date_to_mm_dd_yyyy(raw_date) or raw_date
                cls_val = _formatted(r, classification['logical']) if classification else ''
                ro_val = _formatted(r, reg_owner['logical']) if reg_owner else ''
                ar_val = _formatted(r, auth_rep['logical']) if auth_rep else ''
                lo_val = ro_val or ar_val
                out.append({
                    'aREAINDEX': _formatted(r, area['logical']) if area else '',
                    'iD1': _to_int(r.get(rec_id['logical'])) if rec_id else 0,
                    'date': norm_date,
                    'NEGO DATE': norm_date,
                    'REPORT DATE': norm_date,
                    'classification': cls_val,
                    'CLASSIFICATION': cls_val,
                    'registeredOwner': ro_val,
                    'REGISTERED OWNER': ro_val,
                    'authorizedRepresentative': ar_val,
                    'AUTHORIZED REPRESENTATIVE': ar_val,
                    'lo': lo_val,
                    'LO': lo_val,
                    '_record_id': _s(r.get(pk)),
                    '_entity_logical': logical_name,
                })
            return [r for r in out if r['aREAINDEX']]

        return []

    def get_existing_records(self, mode: str, date_range: tuple[str, str] | None = None,
                             limit: int | None = None) -> list[dict]:
        """Existing nego/sourcing records.

        `limit` caps the read to the most recent N records. The app's existence
        checks do NOT read this list - they use the bounded
        check_existing_area_indexes() query - so an unbounded read here only
        ever fed the reference viewer, at the cost of paging the entire table.
        """
        if date_range:
            return self.get_records_in_date_range(mode, date_range[0], date_range[1])
        from productivity_tool import format_date_to_mm_dd_yyyy
        if mode == 'land-sourcing':
            ent, entity_set = 'cr63f_batangassourcingrecord', 'cr63f_batangassourcingrecords'
            date_attr = self._resolve(ent, 'cr63f_reportdate', 'REPORT DATE', 'Report Date', 'cr63f_reportdatetext', 'cr63f_sourcingdate', 'SOURCING DATE')
            classification = self._resolve(ent, 'cr63f_classification', 'CLASSIFICATION', 'Classification', 'Class')
        else:
            ent, entity_set = 'cr63f_batangasnegorecord', 'cr63f_batangasnegorecords'
            date_attr = self._resolve(ent, 'cr63f_negotiationdate', 'NEGO DATE', 'Nego Date', 'Negotiation Date', 'cr63f_negodate', 'NEGO DATE TEXT', 'cr63f_negodatetext')
            classification = self._resolve(ent, 'cr63f_classificationstatus', 'cr63f_classification', 'CLASSIFICATION', 'Classification', 'Classification Status', 'Class')
        info = self.get_entity_info(ent)
        pk = info.get('primaryIdAttribute')
        area = self._resolve(ent, 'AREA-INDEX', 'Area Index')
        rec_id = self._resolve(ent, 'ID')
        reg_owner = self._resolve(ent, 'cr63f_registeredowner', 'REGISTERED OWNER', 'Registered Owner', 'Owner')
        auth_rep = self._resolve(ent, 'cr63f_authorizedrepresentative', 'AUTHORIZED REPRESENTATIVE', 'Authorized Representative', 'Auth Rep', 'Representative')
        select = [_select_name(c) for c in (area, rec_id, date_attr, classification, reg_owner, auth_rep) if c]
        if pk and pk not in select:
            select.append(pk)
        order_by = f"{rec_id['logical']} desc" if (limit and rec_id) else None
        rows = self._get_all(entity_set, select, formatted=_needs_formatting(area, rec_id, date_attr, classification, reg_owner, auth_rep),
                             top=min(limit, 5000) if limit else 5000,
                             order_by=order_by, max_rows=limit)
        out = []
        for r in rows:
            raw_date = _formatted(r, date_attr['logical']) if date_attr else ''
            norm_date = format_date_to_mm_dd_yyyy(raw_date) or raw_date
            cls_val = _formatted(r, classification['logical']) if classification else ''
            ro_val = _formatted(r, reg_owner['logical']) if reg_owner else ''
            ar_val = _formatted(r, auth_rep['logical']) if auth_rep else ''
            lo_val = ro_val or ar_val
            out.append({
                'aREAINDEX': _formatted(r, area['logical']) if area else '',
                'iD1': _to_int(r.get(rec_id['logical'])) if rec_id else 0,
                'date': norm_date,
                'NEGO DATE': norm_date,
                'REPORT DATE': norm_date,
                'classification': cls_val,
                'CLASSIFICATION': cls_val,
                'registeredOwner': ro_val,
                'REGISTERED OWNER': ro_val,
                'authorizedRepresentative': ar_val,
                'AUTHORIZED REPRESENTATIVE': ar_val,
                'lo': lo_val,
                'LO': lo_val,
                '_record_id': _s(r.get(pk)) if pk else '',
                '_entity_logical': ent,
            })
        return [r for r in out if r['aREAINDEX']]

    def get_dashboard_records(self, date_from: str | None = None, date_to: str | None = None, work_week: str | None = None, mode: str = 'all') -> dict:
        """Fetch summary records from negotiation and/or land sourcing tables based on query parameters."""
        out = {'nego': [], 'sourcing': []}
        from productivity_tool import get_work_week

        from_key = date_from.replace('-', '').replace('/', '') if date_from else '00000000'
        to_key = date_to.replace('-', '').replace('/', '') if date_to else '99999999'
        ww_filter = (work_week or '').strip().upper()

        def _match_record(date_str):
            if not date_str or not str(date_str).strip():
                pass_filter = (not date_from and not date_to and not ww_filter)
                return pass_filter, '', '00000000', ''
            s = str(date_str).strip()
            m = re.match(r'^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$', s)
            if m:
                mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
                fmt = f"{mo:02d}/{da:02d}/{yr:04d}"
                k = f"{yr:04d}{mo:02d}{da:02d}"
                ww = get_work_week(fmt)
            else:
                m2 = re.match(r'^(\d{4})[/-](\d{1,2})[/-](\d{1,2})', s)
                if m2:
                    yr, mo, da = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
                    fmt = f"{mo:02d}/{da:02d}/{yr:04d}"
                    k = f"{yr:04d}{mo:02d}{da:02d}"
                    ww = get_work_week(fmt)
                else:
                    ww = get_work_week(s)
                    pass_filter = (not date_from and not date_to and not ww_filter)
                    return pass_filter, s, '00000000', ww

            if (date_from or date_to) and (k < from_key or k > to_key):
                return False, fmt, k, ww
            if ww_filter and ww.upper() != ww_filter:
                return False, fmt, k, ww
            return True, fmt, k, ww

        include_nego = mode in ('all', 'nego', 'negotiation', '')
        include_sourcing = mode in ('all', 'sourcing', 'land-sourcing', '')

        if include_nego:
            try:
                nego_table = 'cr63f_batangasnegorecord'
                nego_info = self.get_entity_info(nego_table)
                nego_set = nego_info['entitySetName']
                date_col = self._resolve(nego_table, 'NEGO DATE', 'Nego Date', 'Date')
                muni_col = self._resolve(nego_table, 'MUNICIPALITY', 'Municipality', 'City')
                code_col = self._resolve(nego_table, 'MUNICODE', 'MuniCode', 'Muni Code')
                id_col = self._resolve(nego_table, 'ID', 'Id')
                select_nego = [c['logical'] for c in (date_col, muni_col, code_col, id_col) if c]
                if select_nego:
                    raw_nego = self._get_all(nego_set, select_nego, formatted=True, top=5000)
                    for r in raw_nego:
                        raw_d = _formatted(r, date_col['logical']) if date_col else ''
                        ok, fmt_d, k, ww = _match_record(raw_d)
                        if ok:
                            out['nego'].append({
                                'date': fmt_d,
                                'sort_k': k,
                                'ww': ww,
                                'municipality': _formatted(r, muni_col['logical']) if muni_col else '',
                                'municode': _formatted(r, code_col['logical']) if code_col else '',
                                'id': _to_int(r.get(id_col['logical'])) if id_col else 0,
                            })
            except Exception:
                pass

        if include_sourcing:
            try:
                src_table = 'cr63f_batangassourcingrecord'
                src_info = self.get_entity_info(src_table)
                src_set = src_info['entitySetName']
                date_col = self._resolve(src_table, 'REPORT DATE', 'Report Date', 'SOURCING DATE', 'Date')
                muni_col = self._resolve(src_table, 'MUNICIPALITY', 'Municipality', 'City')
                code_col = self._resolve(src_table, 'MUNICODE', 'MuniCode', 'Muni Code')
                id_col = self._resolve(src_table, 'ID', 'Id')
                select_src = [c['logical'] for c in (date_col, muni_col, code_col, id_col) if c]
                if select_src:
                    raw_src = self._get_all(src_set, select_src, formatted=True, top=5000)
                    for r in raw_src:
                        raw_d = _formatted(r, date_col['logical']) if date_col else ''
                        ok, fmt_d, k, ww = _match_record(raw_d)
                        if ok:
                            out['sourcing'].append({
                                'date': fmt_d,
                                'sort_k': k,
                                'ww': ww,
                                'municipality': _formatted(r, muni_col['logical']) if muni_col else '',
                                'municode': _formatted(r, code_col['logical']) if code_col else '',
                                'id': _to_int(r.get(id_col['logical'])) if id_col else 0,
                            })
            except Exception:
                pass

        return out


    def load_all(self, mode: str) -> dict:
        return {
            'teams': self.get_team_composition(),
            'municipality_codes': self.get_municipality_codes(),
            'mapping_statuses': self.get_mapping_statuses(),
            'build_rows': self.get_build_rows(),
            'existing_records': self.get_existing_records(mode),
        }




def _escape_odata(s: str) -> str:
    return str(s or '').replace("'", "''")


