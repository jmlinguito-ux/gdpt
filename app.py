"""
Ground Data Processing Tool - desktop UI.

A native windowed front-end (pywebview + WebView2) over the same offline engine
in productivity_tool.py. Mirrors the referenced Power Apps flow:
  choose mode -> load reference data -> upload CSV/Excel -> review -> calculate
  -> generate productivity -> edit matched negotiator -> export to CSV/Excel.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import sys
import threading
import time
import traceback
import multiprocessing

from updater import UpdateService, run_startup_hooks, app_version

# Velopack may need to handle an install/update command and exit before WebView2
# or the rest of the application is initialised.
if __name__ == '__main__':
    run_startup_hooks()

# Ensure the directory of this file and sys._MEIPASS (for PyInstaller) are in sys.path
_base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
if _base_dir and _base_dir not in sys.path:
    sys.path.insert(0, _base_dir)
_file_dir = os.path.dirname(os.path.abspath(__file__))
if _file_dir and _file_dir not in sys.path:
    sys.path.insert(0, _file_dir)

import webview

import productivity_tool as pt
from app_paths import ensure_user_data

try:
    import dataverse as dv
except Exception:  # noqa: BLE001
    dv = None


# Single source of truth: the single-instance check finds the running window by
# this exact title, so both uses must stay derived from the same string.
APP_TITLE = f'Ground Data Processing Tool v{app_version()}'


def _resource_dir() -> str:
    # PyInstaller unpacks bundled data to sys._MEIPASS
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'web')


def _app_base_dir() -> str:
    # Stable writable storage outside Velopack's replaceable `current` directory.
    return ensure_user_data()


def _settings_path() -> str:
    return os.path.join(_app_base_dir(), 'app_settings.json')


class Api:
    EXISTING_PREVIEW_LIMIT = 5000

    def __init__(self):
        self.mode = 'negotiation'
        self.auto_mode = True
        self.rows: list[dict] = []          # parsed review rows (with IDs)
        self.calculated: list[dict] = []     # after Calculate
        self.productivity: list[dict] = []   # enriched productivity rows
        self.has_calculated = False
        # stage 5 (Publish productivity) gate: true only after stage 4
        # (Publish records) wrote/confirmed every calculated row into the
        # record table with zero failures. Re-locked on any data change.
        self.records_published_clean = False
        self.teams: list[dict] = []
        self.municipality_codes: list[dict] = []
        self.mapping_statuses: list[dict] = []
        self.build_rows: list[dict] = []
        self.use_current_build: bool = False
        self._pending_build_sync: dict | None = None  # staged Build->Dataverse upsert plan
        self._pending_ref_sync: dict | None = None    # staged team/municipality/mapping upsert plan
        self._existing_keys_cache: dict | None = None  # {target, keys, ts} reused by publish right after validate
        self.existing_records: list[dict] = []
        # Editable grids over published Dataverse data, keyed by scope
        # ('existing' / 'productivity'). Edits stage here until an explicit Save.
        self.editors: dict[str, dict] = {}
        self.editor_active_scope: str = ''
        # cap for the viewer-only prefetch of existing records (see fetch_dataverse_tables)

        self.existing_area_set: set[str] = set()
        # Batch history for duplicate detection (fetched on input load) — kept
        # SEPARATE from the Existing Records reference display so uploading a
        # transaction file doesn't change that row's count/name.
        self._dedup_records: list[dict] = []
        self._dedup_area_set: set[str] = set()
        # Displayed Existing Records count (set on fetch, bumped after a records
        # publish so the row reflects the newly-written records).
        self.existing_count: int = 0
        self.input_name = ''
        self.input_path = ''  # full path of the uploaded transaction file (for Save to file)
        self.reference_name = ''
        self.existing_name = ''
        self.dv_client = None
        self.ref_sources: dict = {}

        # persisted reference file paths -> auto-load on startup
        self.settings = self._read_settings()
        self.remembered: dict = dict(self.settings.get('references', {}))
        # MRU history so the UI can offer a dropdown of previously loaded files
        self.history: dict = {k: list(v) for k, v in self.settings.get('references_history', {}).items()}
        # Build is Dataverse-only now (Upload removed) — never auto-load it from a
        # remembered file, and purge any stale remembered Build file/history.
        self.remembered.pop('build', None)
        self.history.pop('build', None)
        self.input_history: list = list(self.settings.get('input_history', []))
        self.ui_settings: dict = dict(self.settings.get('ui_settings', {}))
        self.boot_notices: list = []
        self.updater = UpdateService(self._emit_update_status)
        saved_mode = self.settings.get('mode')
        if saved_mode in ('negotiation', 'land-sourcing'):
            self.mode = saved_mode
        else:
            self.mode = 'negotiation'
        self.auto_mode = False
        self._is_busy = False
        self._busy_lock = threading.Lock()
        # remembered files are loaded by boot_load() AFTER the window paints,
        # so a slow/large file never delays the window from appearing.

    # -- helpers -----------------------------------------------------------
    def _window(self):
        return webview.windows[0]

    def _open_dialog(self, file_types):
        result = self._window().create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=file_types)
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    def _ok(self, **extra):
        return {'ok': True, 'state': self.state(), **extra}

    def _err(self, message):
        return {'ok': False, 'error': str(message)}

    # -- state -------------------------------------------------------------
    def state(self):
        return {
            'mode': self.mode,
            'autoMode': self.auto_mode,
            'isBusy': self._is_busy,
            'inputName': self.input_name,
            'canSaveToSource': bool(self.input_path and os.path.exists(self.input_path)),
            'referenceName': self.reference_name,
            'existingName': self.existing_name,
            'rowCount': len(self.rows),
            'productivityCount': len(self.productivity),
            'hasCalculated': self.has_calculated,
            'recordsPublishedClean': self.records_published_clean,
            'connected': bool(self.dv_client and self.dv_client.signed_in()),
            'refSource': self.reference_name,
            'refSources': dict(self.ref_sources),
            'remembered': dict(self.remembered),
            'history': {k: [{'path': p, 'name': os.path.basename(p)} for p in v] for k, v in self.history.items()},
            'inputHistory': [{'path': e['path'], 'name': os.path.basename(e['path']), 'mode': e['mode']} for e in self.input_history],
            'bootNotices': self._pop_boot_notices(),
            'uiSettings': dict(self.ui_settings),
            'update': self.updater.state(),
            'refCounts': {
                'teams': len(self.teams),
                'municipality': len(self.municipality_codes),
                'mapping': len(self.mapping_statuses),
                'build': len(self.build_rows),
                'existing': self.existing_count if self.existing_count else len(self.existing_records),
            },
            'refTablesLoading': getattr(self, 'ref_tables_loading', False),
            'negotiatorOptions': pt.negotiator_options(self.teams, self.mode),
        }

    def save_ui_settings(self, new_settings: dict):
        try:
            if isinstance(new_settings, dict):
                for k, v in new_settings.items():
                    if isinstance(v, dict) and isinstance(self.ui_settings.get(k), dict):
                        self.ui_settings[k].update(v)
                    else:
                        self.ui_settings[k] = v
                self._write_settings()
            return self._ok(uiSettings=self.ui_settings)
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))

    # -- application updates ----------------------------------------------
    def _emit_update_status(self, status: dict):
        try:
            payload = json.dumps(status)
            self._window().evaluate_js(f'window.onUpdateStatus && window.onUpdateStatus({payload})')
        except Exception:  # Window may not be ready during early startup.
            pass

    def get_update_state(self):
        return {'ok': True, 'update': self.updater.state()}

    def check_for_updates(self, manual=True):
        return {'ok': True, 'update': self.updater.check(bool(manual))}

    def download_update(self):
        return {'ok': True, 'update': self.updater.download()}

    def install_update(self):
        status = self.updater.apply_and_restart()
        if status.get('status') == 'installing':
            # Give the API response/event time to paint, then release all app files.
            threading.Timer(0.8, lambda: os._exit(0)).start()
        return {'ok': status.get('status') != 'error', 'update': status,
                'error': status.get('message') if status.get('status') == 'error' else None}

    def set_mode(self, mode):
        self.mode = 'land-sourcing' if mode == 'land-sourcing' else 'negotiation'
        self.auto_mode = False
        self._reset_data()
        self._clear_all_references()
        self._write_settings()
        return self._ok()

    def _clear_all_references(self):
        for attr in self._REF_ATTR.values():
            setattr(self, attr, [])
        self.ref_sources.clear()
        self.existing_name = ''
        self.existing_area_set = set()
        self._dedup_records = []
        self._dedup_area_set = set()
        self.existing_count = 0

    def _reset_data(self):
        self.rows, self.calculated, self.productivity = [], [], []
        self.has_calculated = False
        self.records_published_clean = False
        self.input_name = ''
        self.input_path = ''

    def reset_transaction_file(self):
        """Unload the active transaction file, reset calculations, and return fresh state."""
        self._reset_data()
        return {'ok': True, 'state': self.state()}

    def reflect_to_source_file(self):
        """Write the current CSV Workspace rows back to the uploaded transaction
        data file, overwriting it in place.

        Reflects in-app edits plus the calculated columns (once Calculate has
        run) into the same file the data was loaded from, preserving its format
        (.csv exports .csv, .xlsx exports .xlsx)."""
        if not self.rows:
            return self._err('Upload a CSV/Excel file first.')
        path = self.input_path
        if not path or not os.path.exists(path):
            return self._err('The uploaded file is no longer available on disk. Re-upload it to save changes.')
        rows = self.calculated if (self.has_calculated and self.calculated) else self.rows

        def worker():
            try:
                pt.write_review_grid(rows, path, self.mode)
                self._done(f'Reflected {len(rows)} rows into {os.path.basename(path)}.', refresh=False)
            except Exception as exc:  # noqa: BLE001
                self._done(f'Could not save to the source file: {exc}', error=True, refresh=False)

        return self._async(worker)

    def _pop_boot_notices(self):
        notices, self.boot_notices = self.boot_notices, []
        return notices

    def boot_load(self):
        """Load remembered reference files after the window is visible (off the UI thread)."""
        def worker():
            try:
                self._autoload_references()
            except Exception:  # noqa: BLE001
                pass
            if dv is not None and not (self.dv_client and self.dv_client.signed_in()):
                try:
                    client = dv.DataverseClient()
                    if client.try_silent_token():
                        self.dv_client = client
                except Exception:  # noqa: BLE001
                    pass
            self._done(refresh=True)
        return self._async(worker)

    def _async(self, worker):
        """Run work (incl. any file dialog) off the UI thread so the window stays live."""
        with self._busy_lock:
            if self._is_busy:
                return self._err('An operation is already in progress. Please wait for it to complete.')
            self._is_busy = True

        def wrapped_worker():
            try:
                worker()
            finally:
                with self._busy_lock:
                    self._is_busy = False

        threading.Thread(target=wrapped_worker, daemon=True).start()
        return self._ok()

    def _done(self, message=None, error=False, refresh=True, table=None, view=None, receipt=None):
        """Terminal signal for an async op: clears busy, shows message, renders table, refreshes."""
        self.ref_tables_loading = False
        with self._busy_lock:
            self._is_busy = False
        try:
            payload = json.dumps({'message': message, 'error': bool(error), 'refresh': bool(refresh),
                                  'table': table, 'view': view, 'receipt': receipt})
            self._window().evaluate_js(f'window.onBackendDone && window.onBackendDone({payload})')
        except Exception:  # noqa: BLE001
            pass

    # -- persisted settings ------------------------------------------------
    def _read_settings(self):
        try:
            with open(_settings_path(), 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _write_settings(self):
        try:
            data = {'mode': self.mode, 'references': dict(self.remembered),
                    'references_history': {k: list(v) for k, v in self.history.items()},
                    'input_history': list(self.input_history),
                    'ui_settings': dict(self.ui_settings)}
            with open(_settings_path(), 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception:  # noqa: BLE001
            pass


    @staticmethod
    def _mru_push(items: list, value, cap: int = 6, key=None) -> list:
        """Move value to the front of an MRU list, deduping and capping length."""
        match = (lambda x: key(x) == key(value)) if key else (lambda x: x == value)
        return [value] + [x for x in items if not match(x)][:cap - 1]

    def _autoload_references(self):
        for kind, path in list(self.remembered.items()):
            if kind not in self._REF_ATTR:
                continue
            if kind == 'build':
                continue  # Build comes from Dataverse (Sync/Fetch), never an auto-loaded file
            if not path or not os.path.exists(path):
                self.boot_notices.append(f'Remembered {pt.REFERENCE_LABELS.get(kind, kind)} file is missing: {path}')
                continue
            try:
                data = pt.load_single_reference(path, kind)
                setattr(self, self._REF_ATTR[kind], data)
                self.ref_sources[kind] = os.path.basename(path)
                if kind == 'existing':
                    self.existing_name = os.path.basename(path)
            except Exception as exc:  # noqa: BLE001
                self.boot_notices.append(f'Could not reload {pt.REFERENCE_LABELS.get(kind, kind)}: {exc}')

    # -- dataverse ---------------------------------------------------------
    def connect_dataverse(self):
        try:
            if dv is None:
                return self._err('Dataverse support is not available in this build.')
            try:
                client = dv.DataverseClient()
            except FileNotFoundError as exc:
                return self._err(str(exc))
            except ValueError as exc:
                return self._err(str(exc))
            # already have a cached account?
            if client.try_silent_token():
                self.dv_client = client
                return self._ok(message='Connected to Dataverse (existing session).')
            flow = client.begin_device_flow()
            if not flow or not isinstance(flow, dict):
                return self._err('Could not initiate device login flow.')
            user_code = flow.get('user_code') or flow.get('code') or ''
            verif_url = flow.get('verification_uri') or flow.get('url') or ''
            msg = flow.get('message') or f"Open {verif_url} and enter code: {user_code}"

            # push the code to the UI, then wait for sign-in on a BACKGROUND thread
            payload = json.dumps({'code': user_code, 'url': verif_url, 'message': msg})
            try:
                self._window().evaluate_js(f'window.showDeviceCode({payload})')
            except Exception:
                pass

            def _wait():
                try:
                    client.complete_device_flow()
                    self.dv_client = client
                    self._done('Connected to Dataverse.', refresh=True)
                except Exception as exc:  # noqa: BLE001
                    self._done(f'Dataverse sign-in failed: {exc}', error=True, refresh=True)

            threading.Thread(target=_wait, daemon=True).start()
            return self._ok()  # return immediately; UI stays responsive during sign-in
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))

    # -- settings screen ---------------------------------------------------
    def get_app_config(self):
        """Environment/config values shown on the Settings screen."""
        cfg = {}
        try:
            if dv is not None:
                cfg = dv.load_config()
        except Exception:  # noqa: BLE001 - missing/invalid config is a normal first-run state
            cfg = {}
        return {
            'ok': True,
            'environmentUrl': (cfg.get('environment_url') or ''),
            'tenantId': (cfg.get('tenant_id') or ''),
            'clientId': (cfg.get('client_id') or ''),
            'solutionName': (cfg.get('solution_name') or self.ui_settings.get('solution_name') or ''),
            'solutionId': (cfg.get('solution_id') or self.ui_settings.get('solution_id') or ''),
            'connected': bool(self.dv_client and self.dv_client.signed_in()),
        }

    def set_solution_name(self, solution_name: str, solution_id: str | None = None):
        """Persist default active solution to dataverse_config.json and ui_settings."""
        try:
            solution_name = (solution_name or '').strip()
            solution_id = (solution_id or '').strip()
            if dv is not None:
                path = dv.config_path()
                if os.path.exists(path):
                    try:
                        with open(path, 'r', encoding='utf-8') as f:
                            data = json.load(f) or {}
                        data['solution_name'] = solution_name
                        if solution_id:
                            data['solution_id'] = solution_id
                        elif 'solution_id' in data:
                            data.pop('solution_id', None)
                        with open(path, 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=2)
                    except Exception:
                        pass
                if self.dv_client and self.dv_client.config is not None:
                    self.dv_client.config['solution_name'] = solution_name
                    if solution_id:
                        self.dv_client.config['solution_id'] = solution_id
                    elif 'solution_id' in self.dv_client.config:
                        self.dv_client.config.pop('solution_id', None)

            self.ui_settings['solution_name'] = solution_name
            if solution_id:
                self.ui_settings['solution_id'] = solution_id
            elif 'solution_id' in self.ui_settings:
                self.ui_settings.pop('solution_id', None)
            self._write_settings()
            return self._ok(solutionName=solution_name, solutionId=solution_id)
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))

    def set_org_url(self, url):
        """Persist a new environment URL to dataverse_config.json."""
        try:
            if dv is None:
                return self._err('Dataverse support is not available in this build.')
            url = (url or '').strip().rstrip('/')
            if not url:
                return self._err('Environment URL cannot be empty.')
            if not url.startswith('http'):
                url = 'https://' + url
            path = dv.config_path()
            data = {}
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f) or {}
            data['environment_url'] = url
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            # a different environment invalidates the current session
            self.dv_client = None
            return self._ok(message=f'Environment URL saved: {url}. Connect again to sign in.')
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))

    def set_app_registration(self, tenant_id, client_id):
        """Persist the Azure AD tenant_id + client_id to dataverse_config.json."""
        try:
            if dv is None:
                return self._err('Dataverse support is not available in this build.')
            tenant_id = (tenant_id or '').strip()
            client_id = (client_id or '').strip()
            if not tenant_id or not client_id:
                return self._err('Tenant ID and Client ID cannot be empty.')
            path = dv.config_path()
            data = {}
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f) or {}
            data['tenant_id'] = tenant_id
            data['client_id'] = client_id
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            # a different app registration invalidates the current session
            self.dv_client = None
            return self._ok(message='App registration saved. Connect again to sign in.')
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))

    def disconnect_dataverse(self):
        """Drop the live client and clear the cached MSAL token."""
        try:
            self.dv_client = None
            if dv is not None:
                try:
                    cache = dv._token_cache_path()
                    if os.path.exists(cache):
                        os.remove(cache)
                except Exception:  # noqa: BLE001 - a locked cache file is not fatal
                    pass
            return self._ok(message='Disconnected from Dataverse.')
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))

    def get_dataverse_bootstrap_data(self):
        """Return solutions, tables for default solution, and suggested mappings."""
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Connect to Dataverse first.')
        try:
            solutions = self.dv_client.get_solutions() or []
            configured_sol = (self.dv_client.config.get('solution_name') or self.ui_settings.get('solution_name') or '').strip() if self.dv_client and self.dv_client.config else (self.ui_settings.get('solution_name') or '').strip()
            configured_sol_id = (self.dv_client.config.get('solution_id') or self.ui_settings.get('solution_id') or '').strip() if self.dv_client and self.dv_client.config else (self.ui_settings.get('solution_id') or '').strip()
            configured_prod = (self.dv_client.config.get('productivity_table') or self.ui_settings.get('productivity_table') or '').strip() if self.dv_client and self.dv_client.config else (self.ui_settings.get('productivity_table') or '').strip()
            selected_sol_id = None
            if solutions:
                if configured_sol_id:
                    for s in solutions:
                        if s and isinstance(s, dict) and str(s.get('id', '')).lower() == configured_sol_id.lower():
                            selected_sol_id = s.get('id')
                            break
                if not selected_sol_id and configured_sol:
                    for s in solutions:
                        if s and isinstance(s, dict):
                            if (s.get('uniqueName', '').lower() == configured_sol.lower() or
                                    s.get('friendlyName', '').lower() == configured_sol.lower() or
                                    configured_sol.lower() in (s.get('friendlyName', '') or '').lower() or
                                    configured_sol.lower() in (s.get('uniqueName', '') or '').lower()):
                                selected_sol_id = s.get('id')
                                break
            tables = self.dv_client.get_solution_tables(selected_sol_id) or []

            # auto-detect best match for each reference category
            table_logicals = [t.get('logicalName', '') for t in tables if t and isinstance(t, dict)]
            def _find_best(*keywords):
                for kw in keywords:
                    for t in table_logicals:
                        if t and kw in t.lower():
                            return t
                return ''

            suggested = {
                'team': _find_best('teamcomposition', 'team'),
                'municipality': _find_best('municipalitycode', 'municipality', 'municode'),
                'mapping': _find_best('mappingstatus', 'mapping'),
                'build': _find_best('batangasbuild', 'areaindexbuildinfo', 'build'),
                'existing': _find_best('batangassourcingrecord', 'sourcingrecord') if self.mode == 'land-sourcing' else _find_best('batangasnegorecord', 'negorecord'),
                'productivity': _find_best('sourcingproductivity', 'productivity') if self.mode == 'land-sourcing' else _find_best('negoproductivity', 'productivity'),
            }
            return {
                'ok': True,
                'solutions': solutions,
                'selectedSolutionId': selected_sol_id,
                'tables': tables,
                'suggestedMappings': suggested,
                'configuredProductivityTable': configured_prod,
            }

        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))


    def fetch_dataverse_tables(self, mappings: dict, selected_kinds: list[str] | None = None):
        """Fetch multiple reference tables from Dataverse simultaneously."""
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Connect to Dataverse first.')

        kinds_to_load = selected_kinds if selected_kinds is not None else ['team', 'municipality', 'mapping', 'build', 'existing']

        def load_one(kind):
            """Fetch one reference table. Returns (kind, label, rows) for the caller
            to apply on the main worker thread."""
            logical = mappings.get(kind, '').strip() if mappings else ''
            if logical:
                return kind, logical, self.dv_client.load_arbitrary_reference_table(logical, kind), None
            if kind == 'team':
                return kind, 'teamcomposition', self.dv_client.get_team_composition(), None
            if kind == 'municipality':
                return kind, 'municipalitycode', self.dv_client.get_municipality_codes(), None
            if kind == 'mapping':
                return kind, 'mappingstatus', self.dv_client.get_mapping_statuses(), None
            if kind == 'build':
                return kind, 'batangasbuild', self.dv_client.get_build_rows(), None
            if kind == 'existing':
                if self.rows:
                    start_date, end_date, area_indexes = pt.get_batch_work_week_bounds(self.rows, self.mode, self.municipality_codes)
                    area_recs = self.dv_client.get_records_for_area_indexes(self.mode, area_indexes)
                    date_recs = self.dv_client.get_records_in_date_range(self.mode, start_date, end_date)
                    merged = {}
                    for r in (area_recs + date_recs):
                        rid = r.get('_record_id') or f"{r.get('aREAINDEX')}_{r.get('date')}_{r.get('iD1')}"
                        merged[rid] = r
                    recs = list(merged.values())
                    area_set = {str(r.get('aREAINDEX') or r.get('AREA-INDEX') or r.get('AREA INDEX') or r.get('areaIndex') or '').strip().lower() for r in recs if (r.get('aREAINDEX') or r.get('AREA-INDEX') or r.get('AREA INDEX') or r.get('areaIndex'))}
                    return kind, 'existingrecords', recs, area_set
                # No file loaded yet, so this is viewer-only data: the existence
                # check uses the bounded check_existing_area_indexes() query once
                # a file arrives. Cap it instead of paging the whole table.
                return kind, 'existingrecords', self.dv_client.get_existing_records(
                    self.mode, limit=self.EXISTING_PREVIEW_LIMIT), None
            return kind, kind, [], None

        self.ref_tables_loading = True

        def worker():
            try:
                results = {}
                # The reference tables are independent of one another, so fetch
                # them concurrently instead of paying each round-trip in series.
                with cf.ThreadPoolExecutor(max_workers=min(5, len(kinds_to_load) or 1)) as pool:
                    for kind, label, data, area_set in pool.map(load_one, kinds_to_load):
                        setattr(self, self._REF_ATTR[kind], data)
                        if kind == 'existing':
                            self.existing_count = len(data)
                        mapped = mappings.get(kind, '').strip() if mappings else ''
                        self.ref_sources[kind] = f"Dataverse: {mapped}" if mapped else 'Dataverse (live)'
                        if area_set is not None:
                            self.existing_area_set = area_set
                            # Explicit Existing Records fetch (with a file loaded) also
                            # refreshes the dedup history.
                            self._dedup_records = data
                            self._dedup_area_set = area_set
                        results[kind] = (label, len(data))

                summary_parts = [f"{n} {k}" for k, (_, n) in results.items()]
                self.ref_tables_loading = False
                self._done(f"Fetched from Dataverse: {', '.join(summary_parts)}.", refresh=True)
            except Exception as exc:  # noqa: BLE001
                self.ref_tables_loading = False
                self._done(f'Dataverse fetch failed: {exc}', error=True)

        return self._async(worker)

    def load_single_dataverse_table(self, kind: str, logical_name: str | None = None):
        """Load a single reference table from Dataverse."""
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Connect to Dataverse first.')
        if kind not in self._REF_ATTR:
            return self._err(f'Unknown reference table: {kind}')

        return self.fetch_dataverse_tables({kind: logical_name or ''}, [kind])

    def get_solutions(self):
        """Return all visible solutions in Dataverse."""
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Connect to Dataverse first.')
        try:
            solutions = self.dv_client.get_solutions()
            return {'ok': True, 'solutions': solutions}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def get_solution_tables(self, solution_id=None, force_refresh: bool = False):
        """Return tables in the given solution (or all non-private tables).

        Table metadata is cached for the session because the underlying
        EntityDefinitions query covers the whole environment and is slow;
        pass force_refresh=True (the Refresh button) to re-read it.
        """
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Connect to Dataverse first.')
        try:
            if force_refresh:
                self.dv_client.clear_metadata_cache()
            tables = self.dv_client.get_solution_tables(solution_id)
            return {'ok': True, 'tables': tables}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def browse_dataverse_table(self, logical_name: str):
        """Fetch columns and sample rows from ANY Dataverse table for preview."""
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Connect to Dataverse first.')
        try:
            data = self.dv_client.get_any_table_data(logical_name)
            return data
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    # -- editable Dataverse grids (existing records / productivity) --------
    # Edits are staged locally and written only on an explicit Save, unlike the
    # reference tables which sync each cell live. These grids sit on published
    # data, so an accidental keystroke must not reach Dataverse on its own.

    # Each scope maps to the two tokens a table's logical name must contain.
    # Real names look like cr63f_<province><nego|sourcing><record|productivity>,
    # so the pair identifies the bucket without ambiguity.
    _EDITOR_SCOPES = {
        'sourcing-records': ('sourcing', 'record'),
        'nego-records': ('nego', 'record'),
        'sourcing-productivity': ('sourcing', 'productivity'),
        'nego-productivity': ('nego', 'productivity'),
    }

    @staticmethod
    def _editor_pref_key(scope: str) -> str:
        """Editor-only remembered table, deliberately separate from the pipeline's
        <mode>_record_table / <mode>_productivity_table: browsing a table here must
        never repoint where stage 4/5 publishes."""
        return 'editor_table_' + str(scope or '').replace('-', '_')

    def _configured_solution(self) -> str:
        cfg = (self.dv_client.config if (self.dv_client and self.dv_client.config) else {}) or {}
        return (cfg.get('solution_id') or self.ui_settings.get('solution_id')
                or cfg.get('solution_name') or self.ui_settings.get('solution_name') or '').strip()

    def _editor_table_name(self, scope: str, table: str = '') -> str:
        """Logical table for a scope: whatever was picked, else the one remembered."""
        chosen = (table or '').strip()
        if chosen:
            return chosen
        return str(self.ui_settings.get(self._editor_pref_key(scope)) or '').strip()

    def get_editor_tables(self, scope: str):
        """Tables in the configured solution that belong to this scope."""
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Connect to Dataverse first.')
        tokens = self._EDITOR_SCOPES.get(scope)
        if not tokens:
            return self._err(f'Unknown record set: {scope}')
        kind, kind_type = tokens
        try:
            tables = self.dv_client.get_solution_tables(self._configured_solution() or None) or []
            matches = []
            for t in tables:
                logical = str((t or {}).get('logicalName') or '').lower()
                if kind in logical and kind_type in logical:
                    matches.append({'logicalName': t.get('logicalName'),
                                    'displayName': t.get('displayName') or t.get('logicalName')})
            matches.sort(key=lambda t: (t['displayName'] or '').lower())
            return {'ok': True, 'scope': scope, 'tables': matches,
                    'saved': self._editor_table_name(scope), 'mode': self.mode}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def _editor(self, scope: str) -> dict:
        return self.editors.setdefault(scope, {'table': '', 'columns': [], 'labels': [], 'types': {},
                                               'choices': {}, 'rows': [], 'dirty': {}, 'loaded': False})

    def get_editor_load_options(self, scope: str, table: str = ''):
        """Column + date-range choices for the load dialog."""
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Connect to Dataverse first.')
        logical = self._editor_table_name(scope, table)
        if not logical:
            return self._err('Choose a table to load first.')
        try:
            opts = self.dv_client.get_table_load_options(logical)
            prefs = self.ui_settings.get('editor_prefs', {}).get(scope, {})
            opts['savedColumns'] = prefs.get('columns') or []
            opts['savedDateColumn'] = prefs.get('dateColumn') or opts.get('defaultDateColumn') or ''
            opts['savedFrom'] = prefs.get('dateFrom') or ''
            opts['savedTo'] = prefs.get('dateTo') or ''
            opts['scope'] = scope
            return opts
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def load_editor_records(self, scope: str, table: str = '', columns: list | None = None,
                            date_column: str = '', date_from: str = '', date_to: str = '',
                            limit: int | None = None):
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Connect to Dataverse first.')
        logical = self._editor_table_name(scope, table)
        if not logical:
            return self._err('Choose a table to load first.')
        ed = self._editor(scope)
        if ed['dirty']:
            return self._err('Save or discard your pending edits before loading again.')

        def worker():
            try:
                data = self.dv_client.load_table_records(
                    logical, list(columns or []), date_column or '',
                    date_from or '', date_to or '', limit)
                ed.update({'table': logical, 'columns': data['columns'], 'labels': data['labels'],
                           'types': data.get('types', {}), 'choices': data.get('choices', {}),
                           'rows': data['rows'], 'dirty': {}, 'loaded': True})
                # The shared grid layer calls updateCell with (row, col, val) only,
                # so the scope on screen is tracked here rather than passed in.
                self.editor_active_scope = scope
                prefs = self.ui_settings.setdefault('editor_prefs', {}).setdefault(scope, {})
                prefs.update({'columns': data['columns'], 'dateColumn': date_column or '',
                              'dateFrom': date_from or '', 'dateTo': date_to or ''})
                self.ui_settings[self._editor_pref_key(scope)] = logical
                self._write_settings()
                scoped = 'in range' if data.get('filtered') else 'unfiltered'
                self._done(f"Loaded {data['count']} record{'' if data['count'] == 1 else 's'} "
                           f"({len(data['columns'])} columns, {scoped}).",
                           table=self._editor_table(scope))
            except Exception as exc:  # noqa: BLE001
                self._done(str(exc), error=True, refresh=False)

        return self._async(worker)

    def _editor_table(self, scope: str) -> dict:
        ed = self._editor(scope)
        return {
            'kind': f'editor:{scope}', 'scope': scope, 'table': ed['table'],
            'columns': ed['columns'], 'labels': ed['labels'], 'types': ed['types'],
            'choices': ed.get('choices', {}),
            'rows': [[r.get(c, '') for c in ed['columns']] for r in ed['rows']],
            'dirty': {f'{ri}:{ci}': True
                      for ri, cols in ed['dirty'].items()
                      for ci in [ed['columns'].index(c) for c in cols if c in ed['columns']]},
            'dirtyCount': sum(len(v) for v in ed['dirty'].values()),
            'count': len(ed['rows']), 'loaded': ed['loaded'],
        }

    def get_editor_table(self, scope: str):
        return self._ok(table=self._editor_table(scope))

    def update_editor_cell(self, scope: str, row_index: int, column: str, value: str):
        """Stage one cell edit locally. Nothing reaches Dataverse until save."""
        ed = self._editor(scope)
        if not (0 <= row_index < len(ed['rows'])):
            return self._err('Row index out of range.')
        if column not in ed['columns']:
            return self._err(f'Unknown column: {column}')
        val = '' if value is None else str(value).strip()
        label = ed['labels'][ed['columns'].index(column)] if ed['labels'] else column
        # Same date rule the CSV and productivity workspaces enforce. Only date
        # columns get it: date_cell_error assumes its caller already checked.
        reason = pt.date_cell_error(label, val) if pt.is_date_column(label) else ''
        if reason:
            return self._err(f"'{label}': {val} {reason}.")
        row = ed['rows'][row_index]
        original = row.get(f'_orig_{column}', row.get(column, ''))
        row.setdefault(f'_orig_{column}', row.get(column, ''))
        row[column] = val
        pending = ed['dirty'].setdefault(row_index, {})
        if val == original:
            pending.pop(column, None)
            if not pending:
                ed['dirty'].pop(row_index, None)
        else:
            pending[column] = val
        return self._ok(table=self._editor_table(scope))

    def update_editor_cell_active(self, row_index: int, column: str, value: str):
        """3-argument form the shared grid layer calls (it has no scope to pass)."""
        return self.update_editor_cell(getattr(self, 'editor_active_scope', '') or '', row_index, column, value)

    def discard_editor_edits(self, scope: str):
        ed = self._editor(scope)
        for ri, cols in ed['dirty'].items():
            for col in cols:
                key = f'_orig_{col}'
                if key in ed['rows'][ri]:
                    ed['rows'][ri][col] = ed['rows'][ri].pop(key)
        ed['dirty'] = {}
        return self._ok(table=self._editor_table(scope), message='Pending edits discarded.')

    def save_editor_edits(self, scope: str):
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Connect to Dataverse first.')
        ed = self._editor(scope)
        if not ed['dirty']:
            return self._err('No pending edits to save.')

        def worker():
            try:
                updates = []
                order = []
                for ri, cols in ed['dirty'].items():
                    row = ed['rows'][ri]
                    updates.append({'_record_id': row.get('_record_id'), 'fields': dict(cols),
                                    'label': row.get(ed['columns'][0], '') if ed['columns'] else f'Row {ri + 1}'})
                    order.append(ri)
                res = self.dv_client.update_records_fields_batch(ed['table'], updates)
                failures = [r for r in res.get('results', []) if r.get('status') != 'updated']
                # Only clear the rows Dataverse confirmed; failures stay dirty so
                # they are not silently lost.
                for pos, ri in enumerate(order):
                    status = res['results'][pos].get('status') if pos < len(res.get('results', [])) else 'failed'
                    if status == 'updated':
                        for col in list(ed['dirty'].get(ri, {})):
                            ed['rows'][ri].pop(f'_orig_{col}', None)
                        ed['dirty'].pop(ri, None)
                msg = f"Saved {res.get('updated', 0)} record{'' if res.get('updated') == 1 else 's'}."
                if failures:
                    msg += f" {len(failures)} failed: {failures[0].get('error') or 'unknown error'}"
                self._done(msg, error=bool(failures), table=self._editor_table(scope))
            except Exception as exc:  # noqa: BLE001
                self._done(str(exc), error=True, refresh=False)

        return self._async(worker)

    # -- per-source reference loading -------------------------------------
    _REF_ATTR = {
        'team': 'teams', 'municipality': 'municipality_codes', 'mapping': 'mapping_statuses',
        'build': 'build_rows', 'existing': 'existing_records',
    }



    def save_input_template(self, mode):
        mode = 'land-sourcing' if mode == 'land-sourcing' else 'negotiation'

        def worker():
            try:
                cols = pt.get_expected_csv_upload_columns(mode)
                result = self._window().create_file_dialog(webview.SAVE_DIALOG, save_filename=f'{mode}-input-template.csv',
                                                           file_types=('CSV (*.csv)',))
                if not result:
                    self._done(refresh=False)
                    return
                path = result if isinstance(result, str) else result[0]
                if not path.lower().endswith('.csv'):
                    path += '.csv'
                import csv as _csv
                with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                    _csv.writer(f).writerow(cols)
                self._done(f'Saved input template to {path}', refresh=False)
            except Exception as exc:  # noqa: BLE001
                self._done(f'Could not save input template: {exc}', error=True)

        return self._async(worker)

    def remove_reference(self, kind):
        try:
            if kind not in self._REF_ATTR:
                return self._err(f'Unknown reference table: {kind}')
            active_path = self.remembered.get(kind)
            setattr(self, self._REF_ATTR[kind], [])
            self.ref_sources.pop(kind, None)
            self.remembered.pop(kind, None)
            if active_path:
                self.history[kind] = [p for p in self.history.get(kind, []) if p != active_path]
            if kind == 'existing':
                self.existing_name = ''
                self.existing_count = 0
            self._write_settings()
            return self._ok(message=f'Removed {pt.REFERENCE_LABELS.get(kind, kind)} (forgotten).', refKind=kind)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def clear_reference_table(self, kind: str):
        """Clear in-memory records and source mapping for a reference table when user switches table."""
        try:
            if kind in self._REF_ATTR:
                setattr(self, self._REF_ATTR[kind], [])
                self.ref_sources.pop(kind, None)
                if kind == 'existing':
                    self.existing_name = ''
                    self.existing_area_set = set()
                    self.existing_count = 0
                self._write_settings()
            return self._ok()
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))

    def clear_reference_history(self, kind):
        try:
            if kind not in self._REF_ATTR:
                return self._err(f'Unknown reference table: {kind}')
            self.history[kind] = []
            self._write_settings()
            return self._ok()
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def clear_input_history(self):
        try:
            self.input_history = []
            self._write_settings()
            return self._ok()
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def forget_reference_history_entry(self, kind, path):
        try:
            if kind not in self._REF_ATTR:
                return self._err(f'Unknown reference table: {kind}')
            self.history[kind] = [p for p in self.history.get(kind, []) if p != path]
            self._write_settings()
            return self._ok()
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def get_reference_rows(self, kind):
        try:
            if kind not in self._REF_ATTR:
                return self._err(f'Unknown reference table: {kind}')
            data = getattr(self, self._REF_ATTR[kind])
            # Existing Records comes from several fetch paths (bounded preview,
            # area-index subset, dedup refresh) with inconsistent server order.
            # Present it consistently sorted by ID, newest (highest) first.
            if kind == 'existing' and data:
                def _existing_id(r):
                    try:
                        return int(str(r.get('iD1', 0) or 0).strip() or 0)
                    except (TypeError, ValueError):
                        return 0
                data = sorted(data, key=_existing_id, reverse=True)
            cols = pt.REFERENCE_VIEW_COLUMNS[kind]
            labels = [label for _, label in cols]
            keys = [key for key, _ in cols]
            rows = [[str(r.get(key, '') if r.get(key, '') is not None else '') for key, _ in cols] for r in data]
            return {'ok': True, 'title': pt.REFERENCE_LABELS.get(kind, kind), 'labels': labels, 'keys': keys,
                    'rows': rows, 'count': len(data), 'source': self.ref_sources.get(kind, ''), 'kind': kind}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def update_reference_cell(self, kind: str, row_index: int, key: str, value: str):
        try:
            if kind not in self._REF_ATTR:
                return self._err(f'Unknown reference table: {kind}')
            data = getattr(self, self._REF_ATTR[kind])
            if 0 <= row_index < len(data):
                val_clean = value.strip()
                data[row_index][key] = val_clean
                synced = False
                sync_msg = ''
                
                # Check if connected to Dataverse and record has GUID
                row = data[row_index]
                record_id = row.get('_record_id')
                ent_logical = row.get('_entity_logical')
                if not ent_logical:
                    defaults = {'team': 'cr63f_teamcomposition', 'municipality': 'cr63f_municipalitycode',
                                'mapping': 'cr63f_mappingstatus', 'build': 'cr63f_batangasbuild',
                                'existing': 'cr63f_batangasnegorecord' if self.mode != 'land-sourcing' else 'cr63f_batangassourcingrecord'}
                    ent_logical = defaults.get(kind, '')

                if self.dv_client and self.dv_client.signed_in() and ent_logical:
                    if record_id:
                        dv_field = self.dv_client.resolve_field_name(ent_logical, kind, key)
                        try:
                            self.dv_client.update_record(ent_logical, record_id, {dv_field: val_clean})
                            synced = True
                            sync_msg = f'Saved to Dataverse live ({key}: {val_clean})'
                        except Exception as dv_exc:  # noqa: BLE001
                            sync_msg = f'Local updated, Dataverse sync notice: {dv_exc}'
                    else:
                        # A newly-added row has no Dataverse record yet. Create it as
                        # soon as its key field (first column) is filled, then future
                        # edits update it live — so new rows sync instead of staying local.
                        cols = pt.REFERENCE_VIEW_COLUMNS.get(kind, [])
                        key_field = cols[0][0] if cols else ''
                        if key_field and str(row.get(key_field, '') or '').strip():
                            payload = {}
                            for k2, v2 in row.items():
                                if k2.startswith('_') or not str(v2 or '').strip():
                                    continue
                                payload[self.dv_client.resolve_field_name(ent_logical, kind, k2)] = v2
                            try:
                                new_guid = self.dv_client.create_record(ent_logical, payload)
                                if new_guid:
                                    row['_record_id'] = new_guid
                                    synced = True
                                    sync_msg = 'Created in Dataverse live'
                                else:
                                    sync_msg = 'Saved locally (Dataverse did not return a record ID)'
                            except Exception as dv_exc:  # noqa: BLE001
                                sync_msg = f'Local updated, Dataverse sync notice: {dv_exc}'
                        else:
                            key_label = cols[0][1] if cols else 'the first column'
                            sync_msg = f'Saved locally — fill in {key_label} to sync this new row to Dataverse'
                elif not (self.dv_client and self.dv_client.signed_in()):
                    sync_msg = 'Saved locally (Dataverse not connected)'

                return self._ok(row=data[row_index], synced=synced, msg=sync_msg)
            return self._err('Row index out of range.')
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)


    def add_reference_row(self, kind: str, row_data: dict | None = None):
        try:
            if kind not in self._REF_ATTR:
                return self._err(f'Unknown reference table: {kind}')
            data = getattr(self, self._REF_ATTR[kind])
            cols = pt.REFERENCE_VIEW_COLUMNS[kind]
            new_row = {key: '' for key, _ in cols}
            if row_data:
                new_row.update(row_data)

            defaults = {'team': 'cr63f_teamcomposition', 'municipality': 'cr63f_municipalitycode',
                        'mapping': 'cr63f_mappingstatus', 'build': 'cr63f_batangasbuild',
                        'existing': 'cr63f_batangasnegorecord' if self.mode != 'land-sourcing' else 'cr63f_batangassourcingrecord'}
            ent_logical = defaults.get(kind, '')
            if data and data[0].get('_entity_logical'):
                ent_logical = data[0]['_entity_logical']
            new_row['_entity_logical'] = ent_logical

            synced = False
            sync_msg = ''
            if self.dv_client and self.dv_client.signed_in() and ent_logical:
                payload = {}
                for k, v in new_row.items():
                    if k.startswith('_') or not v:
                        continue
                    dv_field = self.dv_client.resolve_field_name(ent_logical, kind, k)
                    payload[dv_field] = v
                if payload:
                    try:
                        new_guid = self.dv_client.create_record(ent_logical, payload)
                        if new_guid:
                            new_row['_record_id'] = new_guid
                            synced = True
                            sync_msg = 'Created in Dataverse live'
                    except Exception as dv_exc:
                        sync_msg = f'Local added, Dataverse sync notice: {dv_exc}'

            data.insert(0, new_row)
            return self._ok(count=len(data), synced=synced, msg=sync_msg)
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def delete_reference_row(self, kind: str, row_index: int):
        try:
            if kind not in self._REF_ATTR:
                return self._err(f'Unknown reference table: {kind}')
            data = getattr(self, self._REF_ATTR[kind])
            if 0 <= row_index < len(data):
                row = data[row_index]
                record_id = row.get('_record_id')
                ent_logical = row.get('_entity_logical')
                synced = False
                sync_msg = ''
                if self.dv_client and self.dv_client.signed_in() and record_id and ent_logical:
                    try:
                        self.dv_client.delete_record(ent_logical, record_id)
                        synced = True
                        sync_msg = 'Deleted from Dataverse live'
                    except Exception as dv_exc:
                        sync_msg = f'Local deleted, Dataverse sync notice: {dv_exc}'

                data.pop(row_index)
                return self._ok(count=len(data), synced=synced, msg=sync_msg)
            return self._err('Row index out of range.')
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)


    def export_reference_table(self, kind: str, fmt: str = 'csv'):
        if kind not in self._REF_ATTR:
            return self._err(f'Unknown reference table: {kind}')

        def worker():
            try:
                ext = '.xlsx' if fmt == 'xlsx' else '.csv'
                file_types = ('Excel (*.xlsx)',) if fmt == 'xlsx' else ('CSV (*.csv)',)
                label = pt.REFERENCE_LABELS.get(kind, kind).lower().replace(' ', '-')
                result = self._window().create_file_dialog(webview.SAVE_DIALOG, save_filename=f'{label}{ext}', file_types=file_types)
                if not result:
                    self._done(refresh=False)
                    return
                path = result if isinstance(result, str) else result[0]
                if not path.lower().endswith(ext):
                    path += ext
                data = getattr(self, self._REF_ATTR[kind])
                cols = pt.REFERENCE_VIEW_COLUMNS[kind]
                headers = [label for _, label in cols]
                rows = [[r.get(key, '') for key, _ in cols] for r in data]
                if fmt == 'xlsx':
                    from openpyxl import Workbook
                    wb = Workbook()
                    ws = wb.active
                    ws.title = pt.REFERENCE_LABELS.get(kind, kind)[:31]
                    ws.append(headers)
                    for row in rows:
                        ws.append(row)
                    wb.save(path)
                else:
                    import csv as _csv
                    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                        writer = _csv.writer(f)
                        writer.writerow(headers)
                        writer.writerows(rows)
                self._done(f'Exported {len(data)} rows to {os.path.basename(path)}.', refresh=False)
            except Exception as exc:  # noqa: BLE001
                self._done(f'Export failed: {exc}', error=True)

        return self._async(worker)

    def upload_reference_file(self, kind: str):
        """Open file dialog to upload an Excel or CSV file and replace the reference table records."""
        if kind not in self._REF_ATTR:
            return self._err(f'Unknown reference table: {kind}')

        def worker():
            try:
                table_name = pt.REFERENCE_LABELS.get(kind, kind)
                result = self._window().create_file_dialog(
                    webview.OPEN_DIALOG,
                    allow_multiple=False,
                    file_types=('Data files (*.csv;*.xlsx;*.xlsm;*.xls)',)
                )
                if not result:
                    self._done(refresh=False)
                    return
                path = result if isinstance(result, str) else result[0]
                if not os.path.exists(path):
                    self._done('File not found.', error=True)
                    return

                data = pt.load_single_reference(path, kind)
                if not data:
                    self._done(f'No valid {table_name} records found in {os.path.basename(path)}.', error=True)
                    return

                # Replace in-memory rows
                setattr(self, self._REF_ATTR[kind], data)
                fname = os.path.basename(path)
                self.ref_sources[kind] = f"File: {fname}"
                self.remembered[kind] = path
                self.history[kind] = self._mru_push(self.history.get(kind, []), path)
                if kind == 'existing':
                    self.existing_name = fname
                    self.existing_count = len(data)

                self._write_settings()
                self._done(
                    f'Replaced {table_name} with {len(data)} rows from {fname}.',
                    refresh=True
                )
            except Exception as exc:  # noqa: BLE001
                self._done(f'Could not load {pt.REFERENCE_LABELS.get(kind, kind)}: {exc}', error=True)

        return self._async(worker)


    def preview_build_sync(self, logical_name: str | None = None):
        """Pick a Build file and stage a non-destructive upsert plan against the
        SELECTED Build table: insert new Area-Indexes, overwrite a row only when
        the file's Work Week is strictly newer, keep everything else, never delete.
        Stages the plan; commit_build_sync() writes it after a backup + confirm."""
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Connect to Dataverse first.')

        def worker():
            try:
                target = (logical_name or '').strip() or 'cr63f_batangasbuild'
                path = self._open_dialog(('Data files (*.csv;*.xlsx;*.xlsm;*.xls)',))
                if not path:
                    self._done(refresh=False)
                    return
                if not os.path.exists(path):
                    self._done('File not found.', error=True)
                    return

                file_rows = pt.load_single_reference(path, 'build')
                if not file_rows:
                    self._pending_build_sync = None
                    self._done('No Build rows found in the file — refusing to sync an empty file.', error=True)
                    return

                # Duplicate Area-Index guard: abort & report conflicting duplicates.
                seen, dupes, file_map = {}, [], {}
                for r in file_rows:
                    area = (r.get('areaIndex', '') or '').strip()
                    if not area:
                        continue
                    key = area.lower()
                    ww = pt.normalize_work_week(r.get('workWeek'))
                    build_val = (r.get('build', '') or '').strip()
                    if key in seen:
                        if seen[key]['build'] != build_val or seen[key]['ww'] != ww:
                            dupes.append(area)
                        continue
                    seen[key] = {'build': build_val, 'ww': ww}
                    file_map[key] = {'areaIndex': area, 'build': build_val, 'workWeek': ww}
                if dupes:
                    self._pending_build_sync = None
                    shown = ', '.join(sorted(set(dupes))[:10])
                    self._done(f'{len(set(dupes))} Area-Index value(s) appear more than once with different '
                               f'values (e.g. {shown}). Fix the file before syncing.', error=True)
                    return

                current = self.dv_client.get_build_rows(target)
                cur_map = {}
                for c in current:
                    k = (c.get('areaIndex', '') or '').strip().lower()
                    if k and k not in cur_map:
                        cur_map[k] = c

                inserts, updates, unchanged = [], [], 0
                for key, fr in file_map.items():
                    cur = cur_map.get(key)
                    if not cur:
                        inserts.append(fr)
                        continue
                    fw = fr['workWeek']
                    cw = pt.normalize_work_week(cur.get('workWeek'))
                    if fw is not None and (cw is None or fw > cw):
                        updates.append({'_record_id': cur.get('_record_id'), 'areaIndex': fr['areaIndex'],
                                        'build': fr['build'], 'workWeek': fw,
                                        'fromWeek': cur.get('workWeek') or '', 'toWeek': f"WW-{fw:02d}"})
                    else:
                        unchanged += 1

                self._pending_build_sync = {'target': target, 'path': path,
                                            'inserts': inserts, 'updates': updates, 'current': current}
                target_title = dv.clean_table_title(target) if dv else target
                # No toast message here — the preview modal (receipt) already shows the counts.
                self._done(
                    refresh=False,
                    receipt={'kind': 'syncPreview', 'title': 'Sync Build → Dataverse',
                             'commit': 'commit_build_sync', 'cancel': 'cancel_build_sync',
                             'target': target, 'targetTitle': target_title,
                             'fileName': os.path.basename(path), 'currentCount': len(current),
                             'insertCount': len(inserts), 'updateCount': len(updates), 'unchangedCount': unchanged})
            except Exception as exc:  # noqa: BLE001
                self._pending_build_sync = None
                self._done(f'Build sync preview failed: {exc}', error=True)

        return self._async(worker)

    def cancel_build_sync(self):
        """Discard a staged Build sync plan without writing."""
        self._pending_build_sync = None
        return self._ok(message='Build sync cancelled.')

    def _emit_build_sync_progress(self, done: int, total: int):
        try:
            self._window().evaluate_js(
                f'window.onBuildSyncProgress && window.onBuildSyncProgress({int(done)}, {int(total)})')
        except Exception:  # noqa: BLE001
            pass

    def commit_build_sync(self, do_backup: bool = True):
        """Apply the staged Build upsert. Optionally back up the current table first
        (Save-As); if `do_backup` and the dialog is cancelled, abort with no changes.
        Streams write progress to the UI via window.onBuildSyncProgress."""
        plan = self._pending_build_sync
        if not plan:
            return self._err('No staged Build sync. Run the preview first.')
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Connect to Dataverse first.')

        def worker():
            try:
                target = plan['target']
                inserts, updates = plan['inserts'], plan['updates']
                backup_name = None
                if do_backup:
                    current = plan.get('current') or self.dv_client.get_build_rows(target)
                    title = dv.clean_table_title(target) if dv else target
                    safe = re.sub(r'[^A-Za-z0-9]+', '-', title).strip('-') or 'build'
                    result = self._window().create_file_dialog(webview.SAVE_DIALOG,
                                                               save_filename=f'{safe}-backup.csv',
                                                               file_types=('CSV (*.csv)',))
                    if not result:
                        self._pending_build_sync = None
                        self._done('Sync cancelled — backup not saved, no changes made.', refresh=False,
                                   receipt={'kind': 'syncClosed'})
                        return
                    bpath = result if isinstance(result, str) else result[0]
                    if not bpath.lower().endswith('.csv'):
                        bpath += '.csv'
                    import csv as _csv
                    with open(bpath, 'w', encoding='utf-8-sig', newline='') as f:
                        w = _csv.writer(f)
                        w.writerow(['Area Index', 'Build', 'Work Week'])
                        for c in current:
                            w.writerow([c.get('areaIndex', ''), c.get('build', ''), c.get('workWeek', '')])
                    backup_name = os.path.basename(bpath)

                self._emit_build_sync_progress(0, len(inserts) + len(updates))

                # Batching yields coarse, infrequent progress calls (one per chunk), so emit each.
                def _progress(done, tot):
                    self._emit_build_sync_progress(done, tot)

                res = self.dv_client.sync_build_records(target, inserts, updates, progress_cb=_progress)
                self._pending_build_sync = None
                try:
                    self.build_rows = self.dv_client.get_build_rows(target)
                except Exception:  # noqa: BLE001
                    pass
                errs = res.get('errors') or []
                backup_msg = f" Backup: {backup_name}." if backup_name else " (no backup)."
                self._done(
                    f"Build sync complete: {res.get('created', 0)} inserted, {res.get('updated', 0)} updated, "
                    f"{len(errs)} failed.{backup_msg}",
                    error=bool(errs), refresh=True,
                    receipt={'kind': 'syncResult', 'created': res.get('created', 0),
                             'updated': res.get('updated', 0), 'failed': len(errs),
                             'errors': errs[:200], 'backup': backup_name})
            except Exception as exc:  # noqa: BLE001
                self._done(f'Build sync failed: {exc}', error=True, receipt={'kind': 'syncClosed'})

        return self._async(worker)

    _REF_SYNC_DEFAULTS = {'team': 'cr63f_teamcomposition', 'municipality': 'cr63f_municipalitycode',
                          'mapping': 'cr63f_mappingstatus'}

    def preview_reference_sync(self, kind: str, logical_name: str | None = None):
        """Pick a file and stage a non-destructive upsert of a reference table
        (team/municipality/mapping): overwrite rows whose key (first column) matches,
        insert new keys, never delete. Two-phase; commit_reference_sync() writes it."""
        if kind not in self._REF_SYNC_DEFAULTS:
            return self._err('Sync is not available for this reference table.')
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Connect to Dataverse first.')

        def worker():
            try:
                target = (logical_name or '').strip() or self._REF_SYNC_DEFAULTS.get(kind, '')
                path = self._open_dialog(('Data files (*.csv;*.xlsx;*.xlsm;*.xls)',))
                if not path:
                    self._done(refresh=False)
                    return
                if not os.path.exists(path):
                    self._done('File not found.', error=True)
                    return
                file_rows = pt.load_single_reference(path, kind)
                if not file_rows:
                    self._pending_ref_sync = None
                    self._done(f'No {pt.REFERENCE_LABELS.get(kind, kind)} rows found in the file.', error=True)
                    return

                cols = pt.REFERENCE_VIEW_COLUMNS.get(kind, [])
                key_field = cols[0][0]
                data_fields = [k for k, _ in cols]

                def _norm(v):
                    return re.sub(r'\s+', ' ', str(v or '').strip()).lower()

                # File map keyed on the first column; abort on conflicting duplicates.
                seen, dupes, file_map = {}, [], {}
                for r in file_rows:
                    kv = str(r.get(key_field, '') or '').strip()
                    if not kv:
                        continue
                    nk = _norm(kv)
                    vals = {f: str(r.get(f, '') or '').strip() for f in data_fields}
                    if nk in seen:
                        if seen[nk] != vals:
                            dupes.append(kv)
                        continue
                    seen[nk] = vals
                    file_map[nk] = dict(vals)
                if dupes:
                    self._pending_ref_sync = None
                    shown = ', '.join(sorted(set(dupes))[:10])
                    self._done(f'{len(set(dupes))} value(s) appear more than once with different data '
                               f'(e.g. {shown}). Fix the file before syncing.', error=True)
                    return

                current = self.dv_client.load_arbitrary_reference_table(target, kind)
                cur_map = {}
                for c in current:
                    nk = _norm(c.get(key_field, ''))
                    if nk and nk not in cur_map:
                        cur_map[nk] = c

                inserts, updates, unchanged = [], [], 0
                for nk, fr in file_map.items():
                    cur = cur_map.get(nk)
                    if not cur:
                        inserts.append({f: fr.get(f, '') for f in data_fields})
                        continue
                    changed = any(str(cur.get(f, '') or '').strip() != str(fr.get(f, '') or '').strip()
                                  for f in data_fields)
                    if changed:
                        upd = {f: fr.get(f, '') for f in data_fields}
                        upd['_record_id'] = cur.get('_record_id')
                        updates.append(upd)
                    else:
                        unchanged += 1

                self._pending_ref_sync = {'kind': kind, 'target': target, 'inserts': inserts,
                                          'updates': updates, 'current': current}
                target_title = dv.clean_table_title(target) if dv else target
                self._done(refresh=False, receipt={
                    'kind': 'syncPreview',
                    'title': f'Sync {pt.REFERENCE_LABELS.get(kind, kind)} → Dataverse',
                    'commit': 'commit_reference_sync', 'cancel': 'cancel_reference_sync',
                    'target': target, 'targetTitle': target_title, 'fileName': os.path.basename(path),
                    'currentCount': len(current), 'insertCount': len(inserts),
                    'updateCount': len(updates), 'unchangedCount': unchanged})
            except Exception as exc:  # noqa: BLE001
                self._pending_ref_sync = None
                self._done(f'Reference sync preview failed: {exc}', error=True)

        return self._async(worker)

    def cancel_reference_sync(self):
        self._pending_ref_sync = None
        return self._ok(message='Sync cancelled.')

    def commit_reference_sync(self, do_backup: bool = True):
        """Back up (optional Save-As) then apply the staged reference-table upsert."""
        plan = self._pending_ref_sync
        if not plan:
            return self._err('No staged sync. Run the preview first.')
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Connect to Dataverse first.')

        def worker():
            try:
                kind, target = plan['kind'], plan['target']
                cols = pt.REFERENCE_VIEW_COLUMNS.get(kind, [])
                current = plan.get('current') or self.dv_client.load_arbitrary_reference_table(target, kind)
                backup_name = None
                if do_backup:
                    title = dv.clean_table_title(target) if dv else target
                    safe = re.sub(r'[^A-Za-z0-9]+', '-', title).strip('-') or kind
                    result = self._window().create_file_dialog(webview.SAVE_DIALOG,
                                                               save_filename=f'{safe}-backup.csv',
                                                               file_types=('CSV (*.csv)',))
                    if not result:
                        self._done('Sync cancelled — backup not saved, no changes made.', refresh=False,
                                   receipt={'kind': 'syncClosed'})
                        return
                    bpath = result if isinstance(result, str) else result[0]
                    if not bpath.lower().endswith('.csv'):
                        bpath += '.csv'
                    import csv as _csv
                    with open(bpath, 'w', encoding='utf-8-sig', newline='') as f:
                        w = _csv.writer(f)
                        w.writerow([l for _, l in cols])
                        for c in current:
                            w.writerow([c.get(k, '') for k, _ in cols])
                    backup_name = os.path.basename(bpath)

                self._emit_build_sync_progress(0, len(plan['inserts']) + len(plan['updates']))

                def _progress(done, tot):
                    self._emit_build_sync_progress(done, tot)

                res = self.dv_client.sync_reference_records(target, kind, plan['inserts'],
                                                            plan['updates'], progress_cb=_progress)
                self._pending_ref_sync = None
                try:
                    setattr(self, self._REF_ATTR[kind], self.dv_client.load_arbitrary_reference_table(target, kind))
                except Exception:  # noqa: BLE001
                    pass
                errs = res.get('errors') or []
                backup_msg = f' Backup: {backup_name}.' if backup_name else ' (no backup).'
                self._done(
                    f"Sync complete: {res.get('created', 0)} inserted, {res.get('updated', 0)} updated, "
                    f"{len(errs)} failed.{backup_msg}",
                    error=bool(errs), refresh=True,
                    receipt={'kind': 'syncResult', 'created': res.get('created', 0),
                             'updated': res.get('updated', 0), 'failed': len(errs),
                             'errors': errs[:200], 'backup': backup_name})
            except Exception as exc:  # noqa: BLE001
                self._done(f'Reference sync failed: {exc}', error=True, receipt={'kind': 'syncClosed'})

        return self._async(worker)

    def export_publish_log(self):
        """Save the last publish's skipped + failed rows to CSV via a native dialog."""
        receipt = getattr(self, '_last_receipt', None)
        if not receipt:
            return self._err('No publish log to export yet.')

        def worker():
            try:
                skipped = receipt.get('skipped') or []
                failed = receipt.get('failed') or []
                if not skipped and not failed:
                    self._done('Nothing skipped or failed to export.', refresh=False)
                    return
                stage = receipt.get('stage', 'records')
                fname = f'publish-{stage}-skipped-failed.csv'
                result = self._window().create_file_dialog(webview.SAVE_DIALOG, save_filename=fname,
                                                           file_types=('CSV (*.csv)',))
                if not result:
                    self._done(refresh=False)
                    return
                path = result if isinstance(result, str) else result[0]
                if not path.lower().endswith('.csv'):
                    path += '.csv'
                import csv as _csv
                with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                    w = _csv.writer(f)
                    w.writerow(['status', 'row', 'label', 'detail'])
                    for it in skipped:
                        w.writerow(['skipped-duplicate', it.get('row', ''), it.get('label', ''), it.get('reason', '')])
                    for it in failed:
                        w.writerow(['failed', it.get('row', ''), it.get('label', ''), it.get('error', '')])
                self._done(f'Exported {len(skipped) + len(failed)} rows to {os.path.basename(path)}.', refresh=False)
            except Exception as exc:  # noqa: BLE001
                self._done(f'Export failed: {exc}', error=True)

        return self._async(worker)

    def _load_input_path(self, path: str, forced_mode: str | None = None):
        """Parse and load one data file into the workspace. Shared by Browse and the recent-files dropdown."""
        probe_mode = forced_mode or self.mode
        grid = pt.read_grid_from_file(path, probe_mode)
        mode = forced_mode or pt.detect_mode(grid)
        if mode != probe_mode:
            grid = pt.read_grid_from_file(path, mode)
        added_columns = pt.add_missing_upload_columns(grid, mode)
        rows = pt.parse_grid(grid, mode)
        if not rows:
            raise ValueError('No data rows found in the file.')
        if self.dv_client and self.dv_client.signed_in():
            try:
                start_date, end_date, area_indexes = pt.get_batch_work_week_bounds(rows, mode, self.municipality_codes)
                latest_id = self.dv_client.get_latest_id(mode)
                # Fetch batch history into the SEPARATE dedup fields only — do NOT
                # touch the Existing Records reference display (count/name/source).
                # Fetches area-matching records for duplicate checking and Work Week records for weekly LO cross-checking.
                area_recs = self.dv_client.get_records_for_area_indexes(mode, area_indexes)
                date_recs = self.dv_client.get_records_in_date_range(mode, start_date, end_date)
                merged = {}
                for r in (area_recs + date_recs):
                    rid = r.get('_record_id') or f"{r.get('aREAINDEX')}_{r.get('date')}_{r.get('iD1')}"
                    merged[rid] = r
                self._dedup_records = list(merged.values())
                self._dedup_area_set = {str(r.get('aREAINDEX') or r.get('AREA-INDEX') or r.get('AREA INDEX') or r.get('areaIndex') or '').strip().lower() for r in self._dedup_records if (r.get('aREAINDEX') or r.get('AREA-INDEX') or r.get('AREA INDEX') or r.get('areaIndex'))}
            except Exception:
                all_recs = (self._dedup_records or []) + (self.existing_records or [])
                latest_id = max((int(r.get('iD1') or r.get('ID') or 0) for r in all_recs if str(r.get('iD1') or r.get('ID') or '').isdigit()), default=0)
        else:
            all_recs = (self._dedup_records or []) + (self.existing_records or [])
            latest_id = max((int(r.get('iD1') or r.get('ID') or 0) for r in all_recs if str(r.get('iD1') or r.get('ID') or '').isdigit()), default=0)
        rows = pt.apply_sequential_ids(rows, latest_id)
        self.mode = mode
        self.auto_mode = False
        self.rows = rows
        self.calculated = []
        self.productivity = []
        self.has_calculated = False
        self.input_name = os.path.basename(path)
        self.input_path = path
        self.input_history = self._mru_push(self.input_history, {'path': path, 'mode': mode}, key=lambda e: e['path'])
        self._write_settings()
        return rows, mode, latest_id, added_columns

    def load_input(self):
        def worker():
            try:
                path = self._open_dialog(('Data files (*.csv;*.xlsx;*.xlsm)',))
                if not path:
                    self._done(refresh=False)
                    return
                rows, mode, latest_id, added_columns = self._load_input_path(path)
                msg = f'Loaded {len(rows)} rows ({mode}). IDs start at {latest_id + 1}.'
                if added_columns:
                    msg += (f' Missing column{"s" if len(added_columns) != 1 else ""} auto-added as blank: '
                            f'{", ".join(added_columns)}.')
                self._done(msg, table=self._review_table(rows), view='review', refresh=True)
            except Exception as exc:  # noqa: BLE001
                self._done(f'Could not load the file: {exc}', error=True)

        return self._async(worker)

    def load_input_from_history(self, path):
        if not path or not os.path.exists(path):
            self.input_history = [e for e in self.input_history if e.get('path') != path]
            self._write_settings()
            return self._err(f'That file is no longer available: {path}')

        entry = next((e for e in self.input_history if e.get('path') == path), None)
        forced_mode = entry.get('mode') if entry else None

        def worker():
            try:
                rows, mode, latest_id, added_columns = self._load_input_path(path, forced_mode=forced_mode)
                msg = f'Loaded {len(rows)} rows ({mode}). IDs start at {latest_id + 1}.'
                if added_columns:
                    msg += (f' Missing column{"s" if len(added_columns) != 1 else ""} auto-added as blank: '
                            f'{", ".join(added_columns)}.')
                self._done(msg, table=self._review_table(rows), view='review', refresh=True)
            except Exception as exc:  # noqa: BLE001
                self._done(f'Could not load the file: {exc}', error=True)

        return self._async(worker)

    def forget_input_history_entry(self, path):
        try:
            self.input_history = [e for e in self.input_history if e.get('path') != path]
            self._write_settings()
            return self._ok()
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def _get_combined_existing(self):
        all_recs = (self.existing_records or []) + (self._dedup_records or [])
        all_areas = set(self.existing_area_set or set()) | set(self._dedup_area_set or set())
        for r in all_recs:
            ai = str(r.get('aREAINDEX') or r.get('AREA-INDEX') or r.get('AREA INDEX') or r.get('areaIndex') or '').strip().lower()
            if ai:
                all_areas.add(ai)
        return all_recs, all_areas

    # -- calculate / generate ---------------------------------------------
    def calculate(self, use_current_build: bool = False):
        if not self.rows:
            return self._err('Upload a CSV/Excel file first.')

        def worker():
            try:
                self.use_current_build = bool(use_current_build)
                # An explicit Calculate re-derives everything fresh. Drop the manual
                # overrides AND the raw values they wrote into the row, so columns like
                # MUNICODE / MAPPING STATUS / AREA-INDEX (whose derivation returns the
                # row's existing value if present) actually recompute instead of
                # returning the stale manual value.
                for r in self.rows:
                    ov = r.pop('_overrides', None)
                    if ov:
                        for c in ov:
                            r.pop(c, None)
                all_recs, all_areas = self._get_combined_existing()
                self.calculated = pt.calculate_review_rows(self.rows, self.municipality_codes, self.mapping_statuses,
                                                           self.build_rows, all_recs, all_areas,
                                                           ignore_build_work_week=self.use_current_build,
                                                           mode=self.mode)
                self.has_calculated = True
                self.records_published_clean = False  # records must be (re)published before stage 5
                build_errs = pt.get_build_week_errors(self.calculated, self.build_rows, self.mode,
                                                      ignore_work_week=self.use_current_build)
                if build_errs:
                    msg = (f"Calculated. {len(build_errs)} Build mismatch(es) flagged (see the banner) — "
                           f"you can choose to use the current Build as reference or proceed.")
                elif self.use_current_build:
                    msg = 'Review values calculated using current Build reference.'
                else:
                    msg = 'Review values calculated.'
                self._done(msg, table=self._review_table(self.calculated), view='review', refresh=True)
            except Exception:  # noqa: BLE001
                self._done(traceback.format_exc(), error=True)

        return self._async(worker)

    def generate(self):
        if not self.rows:
            return self._err('Upload a CSV/Excel file first.')

        def worker():
            try:
                # Require an explicit Calculate first (step 2) before generating.
                if not self.has_calculated or not self.calculated:
                    self._done('Click Calculate first, then Generate productivity.', error=True, refresh=False)
                    return

                # block on invalid dates or format errors before generating productivity
                record_columns = (pt.LAND_SOURCING_RECORD_COLUMNS if self.mode == 'land-sourcing'
                                  else pt.BATANGAS_NEGO_RECORD_COLUMNS)
                invalid = pt.get_invalid_date_cells(self.calculated or self.rows, record_columns)
                if invalid:
                    first = invalid[0]
                    self._done(f"Fix {len(invalid)} invalid date value{'' if len(invalid) == 1 else 's'} before "
                                f"generating productivity. First issue: row {first['row']}, "
                                f"{first['column']} = {first['value']} — {first['reason']}.",
                                error=True, refresh=False)
                    return
                # NOTE: Build work-week mismatches do NOT block here — the UI warns
                # and asks the user to confirm before calling generate.
                all_recs, _ = self._get_combined_existing()
                productivity = pt.build_productivity_rows(self.calculated, self.teams, self.mapping_statuses,
                                                          all_recs, self.municipality_codes,
                                                          mode=self.mode)
                self.productivity = pt.enrich_workspace_rows(productivity, self.teams)
                self.records_published_clean = False  # productivity changed; re-publish records to unlock stage 5
                self._done(f'Generated {len(self.productivity)} productivity rows.',
                           table=self._productivity_table(), view='productivity', refresh=True)
            except Exception:  # noqa: BLE001
                self._done(traceback.format_exc(), error=True)

        return self._async(worker)

    def update_matched(self, index, name):
        try:
            index = int(index)
            if index < 0 or index >= len(self.productivity):
                return self._err('Row out of range.')
            lookup = pt.build_team_lookup(self.teams)
            defaults = lookup.get((name or '').strip().lower(), {'correctTeam': 'TEAM 0', 'correctGroup': 'GROUP 0'})
            row = self.productivity[index]
            row['MATCHED NEGOTIATOR NAME'] = name
            row['CORRECT TEAM'] = defaults['correctTeam']
            row['TEAM MATCH'] = pt.get_match_status(pt.normalize_team(row.get('TEAM')), defaults['correctTeam'])
            row['CORRECT GROUP'] = defaults['correctGroup']
            row['GROUP MATCH'] = pt.get_match_status(pt.normalize_group(row.get('GROUP')), defaults['correctGroup'])
            return {'ok': True, 'row': self._productivity_row_cells(row)}
        except Exception as exc:  # noqa: BLE001
            return self._err(exc)

    def update_review_cell(self, row_index: int, key: str, value: str):
        try:
            row_index = int(row_index)
            target_list = self.calculated if self.has_calculated and self.calculated else self.rows
            if row_index < 0 or row_index >= len(target_list):
                return self._err('Row index out of range.')

            val_clean = str(value or '').strip()
            if key == 'TEAM':
                val_clean = pt.normalize_team(val_clean)
            elif key == 'GROUP':
                val_clean = pt.normalize_group(val_clean)
            # Update source row and calculated row
            if row_index < len(self.rows):
                self.rows[row_index][key] = val_clean
                # Remember manual edits to derived columns (BUILD, DATA USABILITY,
                # MAPPING STATUS, ...) so re-calculating keeps the user's choice
                # instead of reverting to the lookup/derived value.
                if key in pt.DERIVED_REVIEW_COLUMNS:
                    self.rows[row_index].setdefault('_overrides', {})[key] = val_clean
            if self.calculated and row_index < len(self.calculated):
                self.calculated[row_index][key] = val_clean
                if key in pt.DERIVED_REVIEW_COLUMNS:
                    self.calculated[row_index].setdefault('_overrides', {})[key] = val_clean

            # Recalculate if already calculated
            if self.has_calculated:
                all_recs, all_areas = self._get_combined_existing()
                self.calculated = pt.calculate_review_rows(self.rows, self.municipality_codes, self.mapping_statuses,
                                                           self.build_rows, all_recs, all_areas,
                                                           ignore_build_work_week=self.use_current_build,
                                                           mode=self.mode)
                updated_row = self.calculated[row_index]
            else:
                updated_row = self.rows[row_index]

            return {
                'ok': True,
                'state': self.state(),
                'table': self._review_table(self.calculated if self.has_calculated else self.rows),
                'row': updated_row,
                'msg': f'Updated {key}.'
            }
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))

    def delete_review_row(self, row_index: int):
        try:
            row_index = int(row_index)
            if row_index < 0 or row_index >= len(self.rows):
                return self._err('Row index out of range.')
            self.rows.pop(row_index)
            if self.calculated and row_index < len(self.calculated):
                self.calculated.pop(row_index)
            if self.has_calculated:
                all_recs, all_areas = self._get_combined_existing()
                self.calculated = pt.calculate_review_rows(self.rows, self.municipality_codes, self.mapping_statuses,
                                                           self.build_rows, all_recs, all_areas,
                                                           ignore_build_work_week=self.use_current_build,
                                                           mode=self.mode)
            return {
                'ok': True,
                'state': self.state(),
                'table': self._review_table(self.calculated if self.has_calculated else self.rows),
                'msg': 'Row deleted.'
            }
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))

    # -- export & publish --------------------------------------------------
    def export(self, fmt):
        if not self.productivity:
            return self._err('Generate productivity rows first.')
        ext = '.xlsx' if fmt == 'xlsx' else '.csv'

        def worker():
            try:
                result = self._window().create_file_dialog(webview.SAVE_DIALOG, save_filename=f'productivity-output{ext}',
                                                           file_types=(f'{"Excel" if ext == ".xlsx" else "CSV"} (*{ext})',))
                if not result:
                    self._done(refresh=False)
                    return
                path = result if isinstance(result, str) else result[0]
                if not path.lower().endswith(ext):
                    path += ext
                pt.write_output(self.productivity, path, self.mode)
                self._done(f'Exported {len(self.productivity)} rows to {path}', refresh=False)
            except Exception as exc:  # noqa: BLE001
                self._done(f'Export failed: {exc}', error=True)

        return self._async(worker)

    # -- publish helpers ---------------------------------------------------
    def _row_keys(self, row: dict):
        """The duplicate identity of a row: AREA-INDEX + DATE + NEGOTIATOR only.

        Per project rule, duplicates are compared ONLY on this composite — never on
        ID or UNIQUE ID. The negotiator is the MATCHED negotiator/sourcer, falling
        back to the raw name when no matched value is present (kept symmetric with
        get_existing_keys on the Dataverse side). Returns (area_key, label).
        """
        row_area = str(row.get('AREA-INDEX', '') or row.get('AREA INDEX', '') or '').strip().upper()
        row_date = str(row.get('NEGO DATE', '') or row.get('SOURCING DATE', '')
                       or row.get('REPORT DATE', '') or '').strip()
        row_neg = str(row.get('MATCHED NEGOTIATOR NAME', '') or row.get('MATCHED SOURCER', '')
                      or row.get('MATCHED SOURCER NAME', '')
                      or row.get('NEGOTIATOR NAME', '') or row.get('SOURCER NAME', '')
                      or row.get('SOURCER', '') or row.get('LSA NAME', '') or '').strip().upper()
        area_key = ''
        if row_area and row_date:
            norm_d = (pt.format_date_to_mm_dd_yyyy(row_date) or row_date).upper()
            area_key = f"{row_area}___{norm_d}___{row_neg}"
        label = ((row_area or '') + ((' / ' + row_date) if row_date else '')
                 + ((' / ' + row_neg) if row_neg else ''))
        return area_key, label

    def _partition_rows(self, rows, existing_keys, override=False, key_id_map=None):
        """Split rows into (writable, duplicates, updatable) and collect hard errors.

        - A duplicate is a row whose ID / UNIQUE ID / AREA-INDEX+date+negotiator
          already exists in Dataverse, or repeats earlier in this same batch.
        - When `override` is on, a duplicate that matches an EXISTING Dataverse
          record (any of the three keys) becomes 'updatable' — it will PATCH that
          record instead of being skipped. In-batch repeats are still skipped
          (there is no stored record to update yet). `key_id_map` maps a matched
          key to the target record id.
        - A hard error is a bad date value. Hard errors block the whole publish.
        Returns (writable[(idx,row,label)], duplicates[dict], updatable[(idx,row,label,record_id)], hard_errors[dict]).
        """
        writable, duplicates, updatable, hard_errors = [], [], [], []
        breakdown = {}  # diagnostic: {category -> count} across overwrite + skip buckets

        def bump(cat):
            breakdown[cat] = breakdown.get(cat, 0) + 1

        key_id_map = key_id_map or {}

        for idx, row in enumerate(rows):
            row_num = idx + 1
            area_key, label = self._row_keys(row)
            # Duplicate identity against Dataverse is AREA-INDEX + DATE + NEGOTIATOR (matched/
            # sourcer). In-batch rows with the same key are all published (with their
            # Mapping Status / Data Usability tags already set by Stage 2 Review).
            in_existing = bool(area_key and area_key in existing_keys)
            existing_id = key_id_map.get(area_key) if in_existing else None

            # hard errors: invalid date cells (these must be fixed, never auto-skipped)
            bad = []
            for k, v in row.items():
                if pt.is_date_column(k):
                    val = str(v or '').strip()
                    reason = pt.date_cell_error(k, val)
                    if reason:
                        bad.append(f"{k} = '{val}' ({reason})")
            if bad:
                hard_errors.append({'row': row_num, 'label': label, 'issues': bad})
                continue

            if in_existing:
                if override and existing_id:
                    updatable.append((idx, row, label, existing_id))
                    bump('overwrite:composite')
                elif override:
                    # Key matched existing in Dataverse but without GUID index -> write to create/ensure present
                    writable.append((idx, row, label))
                    bump('write:override-fallback')
                else:
                    duplicates.append({'row': row_num, 'label': label, 'reason': label,
                                       'category': 'existing-composite'})
                    bump('skip:existing-composite')
            else:
                writable.append((idx, row, label))
        return writable, duplicates, updatable, hard_errors, breakdown

    def validate_publish_records(self, workspace_mode: str, target_table: str, override: bool = False):
        """Pre-publish check. Separates duplicates (skipped/overridden) from hard errors (block)."""
        try:
            if not (self.dv_client and self.dv_client.signed_in()):
                return self._err('Please connect to Dataverse first.')
            target = (target_table or '').strip()
            if not target:
                return self._err('Please select a target Dataverse table.')
            rows = self._rows_for_stage(workspace_mode)
            if not rows:
                return self._err('No records in workspace to validate.')
            override = bool(override)
            # Override needs the key->record-id map (to know what to PATCH); the plain
            # skip path only needs the key set. Cache both so the publish that follows
            # doesn't re-scan the whole target table again.
            if override:
                key_id_map = self.dv_client.get_existing_key_map(target)
                existing_keys = set(key_id_map.keys())
            else:
                existing_keys = self.dv_client.get_existing_keys(target)
                key_id_map = None
            self._existing_keys_cache = {'target': target, 'keys': existing_keys,
                                         'id_map': key_id_map, 'override': override, 'ts': time.time()}
            writable, duplicates, updatable, hard_errors, breakdown = self._partition_rows(rows, existing_keys, override, key_id_map)
            build_errs = pt.get_build_week_errors(rows, self.build_rows, self.mode) if workspace_mode == 'review' else []
            return {
                'ok': True,
                'total': len(rows),
                'writableCount': len(writable),
                'duplicateCount': len(duplicates),
                'duplicates': duplicates[:200],
                'updatableCount': len(updatable),
                'skipBreakdown': breakdown,
                'override': override,
                'errorCount': len(hard_errors),
                'errors': hard_errors[:200],
                # Build mismatches are a soft warning (confirmed in the UI), not a publish blocker.
                'buildErrorCount': len(build_errs),
                'hasErrors': len(hard_errors) > 0,
                'canPublish': len(hard_errors) == 0 and (len(writable) + len(updatable)) > 0,
            }
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))

    def _rows_for_stage(self, workspace_mode: str):
        """Rows a stage publishes: 'review' -> calculated record rows, else productivity."""
        if workspace_mode == 'review':
            return self.calculated if self.has_calculated else self.rows
        columns = pt.get_output_columns(self.mode)
        return [{c: r.get(c, '') for c in columns} for r in self.productivity]

    def _publish_stage(self, workspace_mode, target, reassign_ids=False, override=False):
        """Shared worker for both publish stages. Returns a receipt dict.

        Writable rows are created via $batch. Duplicates are skipped, unless
        `override` is on — then duplicates that match an existing Dataverse record
        are PATCHed in place instead. Hard errors abort before any write.
        """
        rows = self._rows_for_stage(workspace_mode)
        if not rows:
            return {'ok': False, 'error': 'No records to publish.'}

        override = bool(override)
        # Partition FIRST, against the same rows/IDs validation used, so the
        # publish counts always match what the user just saw. (Re-assigning IDs
        # before this step made publish disagree with validation - if
        # get_latest_id came back low, the fresh IDs collided with existing
        # record IDs and every row was wrongly flagged a duplicate.)
        # Reuse the keys fetched by the validation step moments earlier (same target,
        # <30s old, same override mode) so the receipt isn't delayed by a re-scan.
        cache = self._existing_keys_cache
        if (cache and cache.get('target') == target and (time.time() - cache.get('ts', 0)) < 30
                and bool(cache.get('override')) == override):
            existing_keys = cache['keys']
            key_id_map = cache.get('id_map')
        elif override:
            key_id_map = self.dv_client.get_existing_key_map(target)
            existing_keys = set(key_id_map.keys())
        else:
            existing_keys = self.dv_client.get_existing_keys(target)
            key_id_map = None
        writable, duplicates, updatable, hard_errors, _breakdown = self._partition_rows(rows, existing_keys, override, key_id_map)
        if hard_errors:
            return {'ok': False, 'error': f'{len(hard_errors)} record(s) have invalid dates. Fix them before publishing.',
                    'hard_errors': hard_errors}
        # NOTE: Build work-week mismatches do NOT block the publish — the UI warns
        # and asks the user to confirm before calling publish.

        # Records stage: give ONLY the rows we're about to write fresh sequential
        # IDs (past the current max), so a new record can't collide with one
        # added since the file was loaded. Duplicates/updates keep their IDs. Done
        # after partitioning so dedup is unaffected.
        if reassign_ids and workspace_mode == 'review' and writable:
            try:
                latest = self.dv_client.get_latest_id(self.mode)
                for offset, (_i, row, _l) in enumerate(writable):
                    row['ID'] = str(latest + offset + 1)
            except Exception:
                pass  # fall back to load-time IDs

        target_display = dv.clean_table_title(target)
        written, failed, updated = [], [], []
        if writable:
            wr_rows = [r for (_i, r, _l) in writable]
            wr_labels = [l for (_i, _r, l) in writable]
            res = self.dv_client.publish_records_batch(target, wr_rows, self.mode, row_labels=wr_labels)
            for r in res.get('results', []):
                if r['status'] == 'created':
                    written.append({'row': r['row'], 'label': r['label']})
                else:
                    failed.append({'row': r['row'], 'label': r['label'], 'error': r.get('error') or 'Write failed'})

        # Override: PATCH the duplicates that matched an existing Dataverse record.
        if override and updatable:
            up_payload = [{'_record_id': rid, 'row': row, 'label': label} for (_i, row, label, rid) in updatable]
            ures = self.dv_client.update_records_batch(target, up_payload, self.mode)
            for r in ures.get('results', []):
                if r['status'] == 'updated':
                    updated.append({'row': r['row'], 'label': r['label']})
                else:
                    failed.append({'row': r['row'], 'label': r['label'], 'error': r.get('error') or 'Update failed'})

        # Records publish adds to the record table — bump the Existing Records row's
        # count (only if it was already fetched, so an unfetched row stays "Not loaded").
        if workspace_mode == 'review' and written and self.existing_count:
            self.existing_count += len(written)

        receipt = {
            'stage': 'records' if workspace_mode == 'review' else 'productivity',
            'target': target, 'targetDisplay': target_display,
            'total': len(rows),
            'written': written, 'writtenCount': len(written),
            'updated': updated, 'updatedCount': len(updated),
            'skipped': duplicates, 'skippedCount': len(duplicates),
            'failed': failed, 'failedCount': len(failed),
            'override': override,
        }
        receipt['ok'] = True
        receipt['clean'] = (len(failed) == 0)
        self._last_receipt = receipt   # for export_publish_log()
        self._existing_keys_cache = None  # a write changes the keys — force a fresh fetch next time
        return receipt

    def mark_records_skipped(self):
        """All records already exist in Dataverse (nothing to write) — unlock stage 5
        immediately, without a publish round-trip."""
        self.records_published_clean = True
        return self._ok()

    def publish_review_records(self, target_table: str | None = None, override: bool = False):
        """STAGE 4 - Publish records. Create new; skip or (override) update duplicates; block on bad dates."""
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Please connect to Dataverse first.')
        if not (self.calculated if self.has_calculated else self.rows):
            return self._err('No records in CSV workspace to publish.')
        target = (target_table or '').strip()
        if not target:
            target = 'cr63f_batangassourcingrecord' if self.mode == 'land-sourcing' else 'cr63f_batangasnegorecord'
        override = bool(override)

        def worker():
            try:
                r = self._publish_stage('review', target, reassign_ids=True, override=override)
                if not r.get('ok'):
                    self._done(r.get('error', 'Publish failed.'), error=True, refresh=True, receipt=r)
                    return
                # Stage 5 unlocks only when every calculated row is now in the
                # record table (created here, or already present as a duplicate)
                # with zero real failures.
                self.records_published_clean = (r['failedCount'] == 0)
                parts = [f"{r['writtenCount']} written"]
                if r.get('updatedCount'): parts.append(f"{r['updatedCount']} updated")
                if r['skippedCount']: parts.append(f"{r['skippedCount']} skipped (duplicate)")
                if r['failedCount']: parts.append(f"{r['failedCount']} failed")
                msg = f"Records: {', '.join(parts)} \u2192 {r['targetDisplay']}."
                self._done(msg, error=(r['failedCount'] > 0), refresh=True, receipt=r)
            except Exception as exc:  # noqa: BLE001
                self._done(f'Publish failed: {exc}', error=True)

        return self._async(worker)

    def publish_productivity_records(self, target_table: str, override: bool = False):
        """STAGE 5 - Publish productivity. Gated on a clean stage 4; create new; skip or (override) update dupes."""
        if not (self.dv_client and self.dv_client.signed_in()):
            return self._err('Please connect to Dataverse first.')
        if not self.productivity:
            return self._err('No productivity records to publish. Generate productivity first.')
        if not self.records_published_clean:
            return self._err('Publish records (stage 4) with no failures before publishing productivity.')
        target = (target_table or '').strip()
        if not target:
            return self._err('Please select a target Productivity table.')
        override = bool(override)

        try:
            target_mode = (self.mode or 'negotiation').strip().lower()
            mode_key = 'sourcing' if target_mode == 'land-sourcing' else 'nego'
            if self.dv_client:
                cfg = self.dv_client.config
                cfg[f'{mode_key}_productivity_table'] = target
                cfg['productivity_table'] = target
                with open(dv.config_path(), 'w', encoding='utf-8') as f:
                    json.dump(cfg, f, indent=2)
            self.ui_settings[f'{mode_key}_productivity_table'] = target
            self.ui_settings['productivity_table'] = target
            self._write_settings()
        except Exception:
            pass

        def worker():
            try:
                r = self._publish_stage('prod', target, reassign_ids=False, override=override)
                if not r.get('ok'):
                    self._done(r.get('error', 'Publish failed.'), error=True, refresh=True, receipt=r)
                    return
                parts = [f"{r['writtenCount']} written"]
                if r.get('updatedCount'): parts.append(f"{r['updatedCount']} updated")
                if r['skippedCount']: parts.append(f"{r['skippedCount']} skipped (duplicate)")
                if r['failedCount']: parts.append(f"{r['failedCount']} failed")
                msg = f"Productivity: {', '.join(parts)} \u2192 {r['targetDisplay']}."
                self._done(msg, error=(r['failedCount'] > 0), refresh=True, receipt=r)
            except Exception as exc:  # noqa: BLE001
                self._done(f'Publish failed: {exc}', error=True)

        return self._async(worker)

    def save_productivity_table_config(self, table_logical: str, mode: str | None = None):
        try:
            table_val = str(table_logical or '').strip()
            target_mode = (mode or self.mode or 'negotiation').strip().lower()
            mode_key = 'sourcing' if target_mode == 'land-sourcing' else 'nego'
            if self.dv_client:
                cfg = self.dv_client.config
                cfg[f'{mode_key}_productivity_table'] = table_val
                cfg['productivity_table'] = table_val
                cfg_file = dv.config_path()
                with open(cfg_file, 'w', encoding='utf-8') as f:
                    json.dump(cfg, f, indent=2)
            self.ui_settings[f'{mode_key}_productivity_table'] = table_val
            self.ui_settings['productivity_table'] = table_val
            self._write_settings()
            return self._ok(message=f"Saved default {target_mode} productivity table: {table_logical}")
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))

    def get_range_work_weeks(self, date_from='', date_to=''):
        """Work weeks (Wed-Tue rule) covered by the selected date range — powers the
        dashboard Work Week dropdown. Fast, no Dataverse call."""
        try:
            return {'ok': True, 'workWeeks': pt.work_weeks_in_range(date_from, date_to)}
        except Exception as exc:  # noqa: BLE001
            return {'ok': False, 'error': str(exc), 'workWeeks': []}

    def get_dashboard_data(self, params: dict | None = None):
        params = params or {}
        date_from = (params.get('date_from') or '').strip()
        date_to = (params.get('date_to') or '').strip()
        work_week = (params.get('work_week') or '').strip()
        query_mode = (params.get('mode') or 'all').strip()

        try:
            muni_map = {}
            for m in self.municipality_codes:
                m_name = (m.get('municipality') or '').strip().upper()
                if m_name:
                    muni_map[m_name] = {
                        'municipality': m_name,
                        'muniCode': (m.get('muniCode') or '').strip(),
                        'province': (m.get('province') or '').strip(),
                    }

            def _parse_date(raw):
                s = str(raw or '').strip()
                if not s:
                    return '', '00000000', ''
                m = re.match(r'^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$', s)
                if m:
                    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    fmt = f"{month:02d}/{day:02d}/{year:04d}"
                    sort_k = f"{year:04d}{month:02d}{day:02d}"
                    ww = pt.get_work_week(fmt)
                    return fmt, sort_k, ww
                m = re.match(r'^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$', s)
                if m:
                    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    fmt = f"{month:02d}/{day:02d}/{year:04d}"
                    sort_k = f"{year:04d}{month:02d}{day:02d}"
                    ww = pt.get_work_week(fmt)
                    return fmt, sort_k, ww
                ww = pt.get_work_week(s)
                return s, s, ww

            from_key = date_from.replace('-', '').replace('/', '') if date_from else '00000000'
            to_key = date_to.replace('-', '').replace('/', '') if date_to else '99999999'
            ww_filter = work_week.upper()

            def _passes_query(sort_k, ww):
                if (date_from or date_to) and (not sort_k or sort_k == '00000000' or sort_k < from_key or sort_k > to_key):
                    return False
                if ww_filter and ww.upper() != ww_filter:
                    return False
                return True

            nego_records = []
            sourcing_records = []

            # 1. From workspace
            current_active = self.calculated if self.has_calculated else self.rows
            include_nego = query_mode in ('all', 'nego', 'negotiation')
            include_sourcing = query_mode in ('all', 'sourcing', 'land-sourcing')

            if self.mode == 'land-sourcing' and include_sourcing:
                for r in current_active:
                    d_raw = r.get('REPORT DATE') or r.get('SOURCING DATE') or r.get('DATE EXTRACTED') or ''
                    m_raw = (r.get('MUNICIPALITY') or '').strip().upper()
                    c_raw = (r.get('MUNICODE') or r.get('MUNI CODE') or '').strip()
                    d_fmt, sort_k, ww = _parse_date(d_raw)
                    if _passes_query(sort_k, ww):
                        sourcing_records.append({'date': d_fmt, 'sort_k': sort_k, 'ww': ww, 'muni': m_raw, 'code': c_raw})
            elif include_nego:
                for r in current_active:
                    d_raw = r.get('NEGO DATE') or r.get('DATE EXTRACTED') or ''
                    m_raw = (r.get('MUNICIPALITY') or '').strip().upper()
                    c_raw = (r.get('MUNICODE') or r.get('MUNI CODE') or '').strip()
                    d_fmt, sort_k, ww = _parse_date(d_raw)
                    if _passes_query(sort_k, ww):
                        nego_records.append({'date': d_fmt, 'sort_k': sort_k, 'ww': ww, 'muni': m_raw, 'code': c_raw})

            # 2. From Dataverse (if connected)
            source_label = 'Active Workspace'
            if self.dv_client and self.dv_client.signed_in():
                try:
                    dv_data = self.dv_client.get_dashboard_records(date_from, date_to, work_week, query_mode)
                    if dv_data.get('nego') is not None and include_nego:
                        nego_records = []
                        for r in dv_data.get('nego', []):
                            d_fmt = r.get('date') or ''
                            sort_k = r.get('sort_k') or '00000000'
                            ww = r.get('ww') or ''
                            m_raw = (r.get('municipality') or '').strip().upper()
                            c_raw = (r.get('municode') or '').strip()
                            nego_records.append({'date': d_fmt, 'sort_k': sort_k, 'ww': ww, 'muni': m_raw, 'code': c_raw})
                    if dv_data.get('sourcing') is not None and include_sourcing:
                        sourcing_records = []
                        for r in dv_data.get('sourcing', []):
                            d_fmt = r.get('date') or ''
                            sort_k = r.get('sort_k') or '00000000'
                            ww = r.get('ww') or ''
                            m_raw = (r.get('municipality') or '').strip().upper()
                            c_raw = (r.get('municode') or '').strip()
                            sourcing_records.append({'date': d_fmt, 'sort_k': sort_k, 'ww': ww, 'muni': m_raw, 'code': c_raw})
                    if dv_data.get('nego') or dv_data.get('sourcing'):
                        source_label = 'Dataverse Live Query'
                except Exception:
                    pass

            # Aggregate by date
            date_dict = {}
            for r in nego_records:
                d = r['date'] or 'UNASSIGNED'
                k = r['sort_k']
                ww = r['ww']
                if d not in date_dict:
                    date_dict[d] = {'date': d, 'sort_k': k, 'work_week': ww, 'nego_count': 0, 'sourcing_count': 0, 'total': 0}
                date_dict[d]['nego_count'] += 1
                date_dict[d]['total'] += 1

            for r in sourcing_records:
                d = r['date'] or 'UNASSIGNED'
                k = r['sort_k']
                ww = r['ww']
                if d not in date_dict:
                    date_dict[d] = {'date': d, 'sort_k': k, 'work_week': ww, 'nego_count': 0, 'sourcing_count': 0, 'total': 0}
                date_dict[d]['sourcing_count'] += 1
                date_dict[d]['total'] += 1

            by_date = list(date_dict.values())
            by_date.sort(key=lambda x: x['sort_k'], reverse=True)


            # Aggregate by municipality
            muni_dict = {}
            for m_name, m_info in muni_map.items():
                muni_dict[m_name] = {
                    'municipality': m_name,
                    'municode': m_info['muniCode'],
                    'province': m_info['province'],
                    'nego_count': 0,
                    'sourcing_count': 0,
                    'total': 0,
                }

            for r in nego_records:
                m = r['muni'] or 'UNASSIGNED'
                if m not in muni_dict:
                    muni_dict[m] = {'municipality': m, 'municode': r['code'], 'province': '', 'nego_count': 0, 'sourcing_count': 0, 'total': 0}
                muni_dict[m]['nego_count'] += 1
                muni_dict[m]['total'] += 1

            for r in sourcing_records:
                m = r['muni'] or 'UNASSIGNED'
                if m not in muni_dict:
                    muni_dict[m] = {'municipality': m, 'municode': r['code'], 'province': '', 'nego_count': 0, 'sourcing_count': 0, 'total': 0}
                muni_dict[m]['sourcing_count'] += 1
                muni_dict[m]['total'] += 1

            total_reports = len(nego_records) + len(sourcing_records)
            by_muni = []
            for m_name, item in muni_dict.items():
                if item['total'] > 0:
                    pct = f"{(item['total'] / total_reports * 100):.1f}%" if total_reports > 0 else '0%'
                    item['pct'] = pct
                    item['pct_num'] = round((item['total'] / total_reports * 100), 1) if total_reports > 0 else 0
                    by_muni.append(item)

            by_muni.sort(key=lambda x: x['total'], reverse=True)

            # Per-date municipality breakdown, so clicking a day in the UI can show
            # that day's per-municipality counts.
            date_muni = {}

            def _dm_bump(dm_date, dm_muni, dm_code, kind):
                dd = dm_date or 'UNASSIGNED'
                mm = dm_muni or 'UNASSIGNED'
                slot = date_muni.setdefault(dd, {})
                info = slot.get(mm)
                if not info:
                    mi = muni_map.get(mm, {})
                    info = {'municipality': mm, 'municode': mi.get('muniCode') or dm_code or '',
                            'province': mi.get('province', ''), 'nego_count': 0, 'sourcing_count': 0, 'total': 0}
                    slot[mm] = info
                info['nego_count' if kind == 'nego' else 'sourcing_count'] += 1
                info['total'] += 1

            for r in nego_records:
                _dm_bump(r['date'], r['muni'], r['code'], 'nego')
            for r in sourcing_records:
                _dm_bump(r['date'], r['muni'], r['code'], 'sourcing')

            by_date_muni = {}
            for dd, slot in date_muni.items():
                items = [i for i in slot.values() if i['total'] > 0]
                day_total = sum(i['total'] for i in items)
                for i in items:
                    i['pct_num'] = round((i['total'] / day_total * 100), 1) if day_total > 0 else 0
                items.sort(key=lambda x: x['total'], reverse=True)
                by_date_muni[dd] = items

            # Latest dates
            latest_nego_date = next((r['date'] for r in sorted(nego_records, key=lambda x: x['sort_k'], reverse=True) if r['date']), '—')
            latest_sourcing_date = next((r['date'] for r in sorted(sourcing_records, key=lambda x: x['sort_k'], reverse=True) if r['date']), '—')
            latest_date = by_date[0]['date'] if by_date else '—'
            top_muni = by_muni[0]['municipality'] if by_muni else '—'

            return {
                'ok': True,
                'summary': {
                    'total_nego': len(nego_records),
                    'total_sourcing': len(sourcing_records),
                    'total_records': total_reports,
                    'latest_nego_date': latest_nego_date,
                    'latest_sourcing_date': latest_sourcing_date,
                    'latest_date': latest_date,
                    'total_municipalities': len(by_muni),
                    'top_municipality': top_muni,
                },
                'by_date': by_date,
                'by_municipality': by_muni,
                'by_date_muni': by_date_muni,
                'source': source_label,
            }
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))



    # -- table builders ----------------------------------------------------
    def _review_table(self, rows):
        # CORRECT TEAM / CORRECT GROUP are intentionally hidden from the CSV
        # Workspace (they're derived for the productivity workspace). The data
        # and the saved file still keep them; this only filters the display.
        _hidden = ('CORRECT TEAM', 'CORRECT GROUP')
        headers = [h for h in pt.get_review_headers(self.mode) if h not in _hidden]
        body = []
        for row in rows:
            body.append([str(row.get(h, '') if row.get(h, '') is not None else '') for h in headers])
        meta = {h: pt.get_column_type_and_choices(h, self.mode, self.teams, self.municipality_codes, self.mapping_statuses, self.build_rows) for h in headers}
        # Build work-week mismatches (only meaningful once BUILD is derived, i.e. after Calculate)
        build_errors = pt.get_build_week_errors(rows, self.build_rows, self.mode,
                                                ignore_work_week=self.use_current_build) if self.has_calculated else []
        return {'headers': headers, 'rows': body, 'meta': meta, 'buildErrors': build_errors,
                'useCurrentBuild': self.use_current_build}


    def _productivity_table(self):
        columns = pt.get_output_columns(self.mode)
        labels = [pt.get_header_label(c, self.mode) for c in columns]
        rows = [self._productivity_row_cells(r) for r in self.productivity]
        editable_index = columns.index('MATCHED NEGOTIATOR NAME') if 'MATCHED NEGOTIATOR NAME' in columns else -1
        match_cols = [i for i, c in enumerate(columns) if c in ('TEAM MATCH', 'GROUP MATCH')]
        meta = {c: pt.get_column_type_and_choices(c, self.mode, self.teams, self.municipality_codes, self.mapping_statuses, self.build_rows) for c in columns}
        return {'columns': columns, 'labels': labels, 'rows': rows,
                'matchedIndex': editable_index, 'matchColumns': match_cols, 'meta': meta}

    def _productivity_row_cells(self, row):
        columns = pt.get_output_columns(self.mode)
        return [pt.js_number_str(row.get(c, '')) for c in columns]

    def update_productivity_cell(self, row_index: int, key: str, value: str):
        try:
            row_index = int(row_index)
            if row_index < 0 or row_index >= len(self.productivity):
                return self._err('Row index out of range.')

            val_clean = str(value or '').strip()
            row = self.productivity[row_index]
            row[key] = val_clean

            # If matched negotiator was edited, update correct team, correct group, team match, group match
            if key in ('MATCHED NEGOTIATOR NAME', 'MATCHED SOURCER', 'NEGOTIATOR NAME', 'LSA NAME'):
                lookup = pt.build_team_lookup(self.teams)
                matched_name = row.get('MATCHED NEGOTIATOR NAME') or row.get('NEGOTIATOR NAME') or row.get('LSA NAME') or ''
                defaults = lookup.get(matched_name.strip().lower(), {'correctTeam': 'TEAM 0', 'correctGroup': 'GROUP 0'})
                row['CORRECT TEAM'] = defaults['correctTeam']
                row['TEAM MATCH'] = pt.get_match_status(pt.normalize_team(row.get('TEAM')), defaults['correctTeam'])
                row['CORRECT GROUP'] = defaults['correctGroup']
                row['GROUP MATCH'] = pt.get_match_status(pt.normalize_group(row.get('GROUP')), defaults['correctGroup'])

            elif key == 'TEAM':
                val_clean = pt.normalize_team(val_clean)
                row['TEAM'] = val_clean
                lookup = pt.build_team_lookup(self.teams)
                matched_name = row.get('MATCHED NEGOTIATOR NAME') or row.get('NEGOTIATOR NAME') or row.get('LSA NAME') or ''
                defaults = lookup.get(matched_name.strip().lower(), {'correctTeam': 'TEAM 0', 'correctGroup': 'GROUP 0'})
                row['TEAM MATCH'] = pt.get_match_status(val_clean, defaults['correctTeam'])

            elif key == 'GROUP':
                val_clean = pt.normalize_group(val_clean)
                row['GROUP'] = val_clean
                lookup = pt.build_team_lookup(self.teams)
                matched_name = row.get('MATCHED NEGOTIATOR NAME') or row.get('NEGOTIATOR NAME') or row.get('LSA NAME') or ''
                defaults = lookup.get(matched_name.strip().lower(), {'correctTeam': 'TEAM 0', 'correctGroup': 'GROUP 0'})
                row['GROUP MATCH'] = pt.get_match_status(val_clean, defaults['correctGroup'])

            elif key in ('NEGO DATE', 'REPORT DATE', 'SOURCING DATE'):
                row['WORK WEEK'] = pt.get_work_week(val_clean)

            return {
                'ok': True,
                'state': self.state(),
                'table': self._productivity_table(),
                'row': self._productivity_row_cells(row),
                'msg': f'Updated {key}.'
            }
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))

    def delete_productivity_row(self, row_index: int):
        try:
            row_index = int(row_index)
            if row_index < 0 or row_index >= len(self.productivity):
                return self._err('Row index out of range.')
            self.productivity.pop(row_index)
            return {
                'ok': True,
                'state': self.state(),
                'table': self._productivity_table(),
                'msg': 'Row deleted.'
            }
        except Exception as exc:  # noqa: BLE001
            return self._err(str(exc))



_mutex_handle = None

def _terminate_orphan_instances():
    """Immediately terminate any background or zombie instances of Ground-Data-Processing-Tool."""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002
        PROCESS_TERMINATE = 0x0001

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ('dwSize', wintypes.DWORD),
                ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD),
                ('th32DefaultBasePriority', ctypes.c_long),
                ('dwFlags', wintypes.DWORD),
                ('szExeFile', ctypes.c_wchar * 260),
            ]

        current_pid = os.getpid()
        target_name = 'ground-data-processing-tool'

        kernel32 = ctypes.windll.kernel32
        h_snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not h_snap or h_snap == -1:
            return

        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)

        if kernel32.Process32FirstW(h_snap, ctypes.byref(pe)):
            while True:
                exe_name = pe.szExeFile.lower()
                if target_name in exe_name and pe.th32ProcessID != current_pid:
                    h_proc = kernel32.OpenProcess(PROCESS_TERMINATE, False, pe.th32ProcessID)
                    if h_proc:
                        kernel32.TerminateProcess(h_proc, 0)
                        kernel32.CloseHandle(h_proc)
                if not kernel32.Process32NextW(h_snap, ctypes.byref(pe)):
                    break
        kernel32.CloseHandle(h_snap)
    except Exception:
        pass


def _ensure_single_instance() -> bool:
    """Ensure only one instance of the application runs simultaneously on Windows.
    If another instance is already running with a visible window, brings its window to focus and returns False.
    Otherwise cleans up background zombies.
    """
    global _mutex_handle
    if sys.platform != 'win32':
        return True

    try:
        import ctypes
        from ctypes import wintypes

        MUTEX_NAME = "Local\\GroundDataProcessingTool_SingleInstance_App_Mutex"
        ERROR_ALREADY_EXISTS = 183
        SW_RESTORE = 9

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE

        _mutex_handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        last_error = kernel32.GetLastError()

        if last_error == ERROR_ALREADY_EXISTS:
            hwnd = user32.FindWindowW(None, APP_TITLE)
            if hwnd and user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
                return False
            # Zombie background process detected - terminate it
            _terminate_orphan_instances()
            return True
        return True
    except Exception:
        return True


def main():
    multiprocessing.freeze_support()
    if not _ensure_single_instance():
        sys.exit(0)

    api = Api()
    index_html = os.path.join(_resource_dir(), 'index.html')
    window = webview.create_window(APP_TITLE, index_html, js_api=api,
                                   width=1400, height=860, min_size=(1400, 860))

    def _force_exit():
        os._exit(0)

    window.events.closed += _force_exit

    def _auto_check_updates():
        threading.Timer(2.0, lambda: api.check_for_updates(False)).start()

    window.events.loaded += _auto_check_updates

    # persistent WebView2 profile -> faster warm starts and asset caching
    storage = os.path.join(_app_base_dir(), '.webview')
    try:
        os.makedirs(storage, exist_ok=True)
        webview.start(private_mode=False, storage_path=storage)
    except Exception:  # noqa: BLE001 - fall back to defaults if the profile path is unusable
        webview.start()
    finally:
        os._exit(0)


if __name__ == '__main__':
    main()
