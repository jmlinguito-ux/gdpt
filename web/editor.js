/* Records editor — edit published Dataverse rows (sourcing/nego records and productivity).
 *
 * Two things set this apart from the CSV and Productivity workspaces:
 *  - Loading is opt-in and scoped. You choose the table, date range and exact
 *    columns, because an unscoped read pages the whole table before anything
 *    renders. Results then open on their own page.
 *  - Edits are staged locally and only written on an explicit Save. These rows
 *    are live published data, so a stray keystroke must not reach Dataverse the
 *    way reference-table edits do.
 *
 * The grid itself reuses app.js's shared machinery (getColWidth,
 * attachHeaderResizers, initGridSelection, makeCombo) so selection, copy,
 * resizing and typed cells behave exactly as they do in the CSV workspace.
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
  const TABLE_ID = 'editorTableGrid';
  const TABLE_KEY = 'editor';
  const MAX_RENDER = 2000;
  // The pinned ACT column that holds the per-row delete button. Keep in step with
  // the #editorTableGrid .col-action width in style.css.
  const ACT_COL_W = 48;

  let scope = 'nego-records';
  let columns = [];          // [{logical, label, type}] for the picker
  let selected = new Set();
  let table = null;          // last payload from the backend
  let loadedOnce = false;
  let filters = {};          // colIdx -> filter text
  let openFilterCols = new Set();
  let frozen = null;         // Set of frozen header labels, lazily restored

  /* ---------------- load options ---------------- */

  function renderColumnList() {
    const box = $('editorColList');
    if (!box) return;
    const term = ($('editorColSearch').value || '').trim().toLowerCase();
    const shown = columns.filter(c => !term || c.label.toLowerCase().includes(term) || c.logical.includes(term));
    if (!columns.length) {
      box.innerHTML = '<p class="muted">Connect to Dataverse, then choose a record set and table to list its columns.</p>';
    } else if (!shown.length) {
      box.innerHTML = '<p class="muted">No columns match that filter.</p>';
    } else {
      box.innerHTML = shown.map(c => `
        <label class="editor-col" title="${c.logical}">
          <input type="checkbox" data-col="${c.logical}"${selected.has(c.logical) ? ' checked' : ''} />
          <span class="editor-col-label">${escapeHtml(c.label)}</span>
          <span class="editor-col-type">${escapeHtml(c.type || '')}</span>
        </label>`).join('');
      box.querySelectorAll('input[data-col]').forEach(cb => {
        cb.addEventListener('change', () => {
          if (cb.checked) selected.add(cb.dataset.col); else selected.delete(cb.dataset.col);
          syncCounts();
        });
      });
    }
    syncCounts();
  }

  function syncCounts() {
    const n = selected.size;
    const label = $('editorColCount');
    if (label) label.textContent = `${n} column${n === 1 ? '' : 's'} selected`;
    const load = $('btnEditorLoad');
    // Every scope needs an explicit table before anything can be read.
    if (load) load.disabled = !n || !$('editorTable').value;
  }

  async function loadOptions() {
    const a = api();
    if (!a || !a.get_editor_load_options) return;
    const tableName = $('editorTable').value || '';
    if (!tableName) { columns = []; selected = new Set(); renderColumnList(); return; }
    const res = await a.get_editor_load_options(scope, tableName);
    if (!res || !res.ok) {
      columns = []; selected = new Set();
      const box = $('editorColList');
      if (box) box.innerHTML = `<p class="muted">${escapeHtml((res && res.error) || 'Could not read columns.')}</p>`;
      syncCounts();
      return;
    }
    columns = res.columns || [];
    // Re-apply the previous selection where the columns still exist; otherwise
    // start with a readable default rather than every column at once.
    const saved = (res.savedColumns || []).filter(c => columns.some(x => x.logical === c));
    selected = new Set(saved.length ? saved : columns.slice(0, 8).map(c => c.logical));

    const dateSel = $('editorDateCol');
    if (dateSel) {
      const dcols = res.dateColumns || [];
      dateSel.innerHTML = '<option value="">No date filter</option>' +
        dcols.map(c => `<option value="${c.logical}">${escapeHtml(c.label)}</option>`).join('');
      dateSel.value = res.savedDateColumn || res.defaultDateColumn || '';
    }
    if (res.savedFrom) $('editorFrom').value = res.savedFrom;
    if (res.savedTo) $('editorTo').value = res.savedTo;
    renderColumnList();
  }

  // Only the tables belonging to the chosen record set, resolved server-side
  // from the configured solution.
  async function populateTables() {
    const dst = $('editorTable');
    const a = api();
    if (!dst) return;
    if (!a || !a.get_editor_tables) { dst.innerHTML = '<option value="">Connect to Dataverse…</option>'; return; }
    dst.innerHTML = '<option value="">Loading…</option>';
    const res = await a.get_editor_tables(scope);
    if (!res || !res.ok) {
      dst.innerHTML = `<option value="">${escapeHtml((res && res.error) || 'Could not list tables')}</option>`;
      syncCounts();
      return;
    }
    const tables = res.tables || [];
    dst.innerHTML = (tables.length ? '<option value="">Select table…</option>' : '<option value="">No matching tables</option>')
      + tables.map(t => `<option value="${t.logicalName}">${escapeHtml(t.displayName)}</option>`).join('');
    // Re-select the table last used for this record set, when it still exists.
    if (res.saved && tables.some(t => t.logicalName === res.saved)) dst.value = res.saved;
    syncCounts();
  }

  /* ---------------- grid ---------------- */

  function escapeHtml(v) {
    return String(v == null ? '' : v).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function colWidth(header) {
    return (typeof getColWidth === 'function') ? getColWidth(header, TABLE_KEY) : 160;
  }

  function frozenCols() {
    if (!frozen) {
      frozen = (typeof getSavedFrozenCols === 'function') ? getSavedFrozenCols(TABLE_KEY) : new Set();
    }
    return frozen;
  }

  // This grid remembers its own frozen columns (localStorage 'frozen_cols_editor'),
  // while app.js's getFrozenColOffset keys off the review/prod sets. Offsets have to
  // come from THIS set, otherwise frozen editor columns all stick to the same left
  // edge and overlap the pinned ACT column.
  function frozenOffsetLeft(colIdx) {
    const labels = (table && table.labels) || [];
    const set = frozenCols();
    let left = 0;
    for (let c = 0; c < colIdx && c < labels.length; c++) {
      if (set.has(labels[c])) left += colWidth(labels[c]);
    }
    return left;
  }

  function isTrailingFrozen(colIdx) {
    const labels = (table && table.labels) || [];
    const set = frozenCols();
    if (!set.has(labels[colIdx])) return false;
    for (let c = colIdx + 1; c < labels.length; c++) {
      if (set.has(labels[c])) return false;
    }
    return true;
  }

  function matchCell(cellVal, query) {
    if (typeof window.cellMatchesFilter === 'function') {
      return window.cellMatchesFilter(cellVal, query);
    }
    if (!query) return true;
    const q = String(query).trim().toLowerCase();
    if (!q) return true;
    const cellStr = (cellVal == null ? '' : String(cellVal)).trim();
    if (q === '(blank)' || q === '[blank]' || q === '(empty)' || q === '[empty]' ||
        q === '=""' || q === '""' || q === 'null' || q === 'is:blank' || q === 'is:empty') {
      return cellStr === '';
    }
    if (q === '(not blank)' || q === '[not blank]' || q === '(non-blank)' ||
        q === '!blank' || q === '!= ""' || q === '!=""' || q === 'is:notblank' || q === 'is:notempty') {
      return cellStr !== '';
    }
    return cellStr.toLowerCase().includes(q);
  }

  // Mirrors the CSV workspace header markup so it picks up the same styling and
  // behaviour (pin to freeze, chevron to filter, drag to resize).
  function renderHead() {
    const grid = $(TABLE_ID);
    const labels = (table && table.labels) || [];
    const head = grid.querySelector('thead');
    const fz = frozenCols();
    const headerHeight = (typeof getSavedHeaderHeight === 'function') ? getSavedHeaderHeight(TABLE_KEY, 44) : 44;
    head.innerHTML = '';
    const tr = document.createElement('tr');

    labels.forEach((h, colIdx) => {
      const w = colWidth(h);
      const isFrozen = fz.has(h);
      const isLastFrozen = isFrozen && isTrailingFrozen(colIdx);
      const left = frozenOffsetLeft(colIdx);

      const th = document.createElement('th');
      th.className = `${isFrozen ? 'col-frozen' : ''} ${isLastFrozen ? 'col-frozen-last' : ''}`;
      th.style.width = `${w}px`; th.style.minWidth = `${w}px`; th.style.maxWidth = `${w}px`;
      th.style.height = `${headerHeight}px`;
      th.style.minHeight = `${headerHeight}px`;
      if (isFrozen) th.style.left = `${left}px`;

      const isFilterActive = !!(filters[colIdx] && filters[colIdx].trim());
      const isFilterOpen = openFilterCols.has(colIdx) || isFilterActive;
      th.innerHTML = `
        <div class="th-header-box">
          <div class="th-title-wrap">
            <button type="button" class="th-pin-btn ${isFrozen ? 'pinned' : ''}" data-freeze-editor="${colIdx}" title="${isFrozen ? 'Unfreeze' : 'Freeze'} ${escapeHtml(h)}">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" stroke="none">
                <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z"/>
              </svg>
            </button>
            <span class="th-col-name" data-freeze-editor="${colIdx}" title="Click to ${isFrozen ? 'unfreeze' : 'freeze'} ${escapeHtml(h)}">${escapeHtml(h)}</span>
            <button type="button" class="th-chevron-btn ${isFilterActive ? 'active' : ''}" data-filter-toggle-editor="${colIdx}" title="Filter column">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"></polyline></svg>
            </button>
          </div>
          <div class="th-filter-popup ${isFilterOpen ? '' : 'hidden'}">
            <input class="th-filter-input" data-editor-col="${colIdx}" value="${escapeHtml(filters[colIdx] || '')}" placeholder="FILTER ${escapeHtml(h)}..." />
          </div>
        </div>
        <div class="th-resizer" data-resizer="${colIdx}" title="Drag column width (Double-click to reset)"></div>
        <div class="th-row-resizer" title="Drag header height (Double-click to reset)"></div>`;
      tr.appendChild(th);
    });

    // Last column on the right: ACT (Delete action button)
    const thAction = document.createElement('th');
    thAction.className = 'col-action';
    thAction.style.width = '54px';
    thAction.style.minWidth = '54px';
    thAction.style.maxWidth = '54px';
    thAction.style.height = `${headerHeight}px`;
    thAction.style.minHeight = `${headerHeight}px`;
    thAction.innerHTML = `
      <div class="th-header-box" style="align-items:center;justify-content:center;"><span class="th-col-name" style="text-align:center;">ACT</span></div>
      <div class="th-row-resizer" title="Drag header height (Double-click to reset)"></div>`;
    tr.appendChild(thAction);

    head.appendChild(tr);

    head.querySelectorAll('[data-freeze-editor]').forEach(el => el.addEventListener('click', (e) => {
      e.stopPropagation();
      const h = labels[Number(el.dataset.freezeEditor)];
      const set = frozenCols();
      if (set.has(h)) set.delete(h); else set.add(h);
      if (typeof setSavedFrozenCols === 'function') setSavedFrozenCols(TABLE_KEY, set);
      renderTable();
    }));

    head.querySelectorAll('[data-filter-toggle-editor]').forEach(el => el.addEventListener('click', (e) => {
      e.stopPropagation();
      const i = Number(el.dataset.filterToggleEditor);
      if (openFilterCols.has(i)) openFilterCols.delete(i); else openFilterCols.add(i);
      renderHead();
      const inp = head.querySelector(`input[data-editor-col="${i}"]`);
      if (inp) inp.focus();
    }));

    head.querySelectorAll('input[data-editor-col]').forEach(inp => {
      inp.addEventListener('click', (e) => e.stopPropagation());
      inp.addEventListener('input', () => {
        filters[inp.dataset.editorCol] = inp.value;
        renderBody();
        const btn = $('btnEditorClearFilters');
        if (btn) btn.disabled = !Object.values(filters).some(v => (v || '').trim() !== '');
      });
    });

    const clearBtn = $('btnEditorClearFilters');
    if (clearBtn) clearBtn.disabled = !Object.values(filters).some(v => (v || '').trim() !== '');

    if (typeof attachHeaderResizers === 'function') {
      attachHeaderResizers(grid, TABLE_KEY, [...labels, 'ACT'], () => { renderHead(); renderBody(); });
    }
  }

  function renderBody() {
    const grid = $(TABLE_ID);
    const tbody = grid.querySelector('tbody');
    const rows = (table && table.rows) || [];
    const labels = (table && table.labels) || [];
    const cols = (table && table.columns) || [];
    const choices = (table && table.choices) || {};
    const dirty = (table && table.dirty) || {};
    const fz = frozenCols();
    tbody.innerHTML = '';
    if (typeof invalidateGridCache === 'function') invalidateGridCache(TABLE_ID);

    const active = Object.entries(filters).filter(([, v]) => (v || '').trim() !== '');
    const kept = [];
    rows.forEach((cells, origIdx) => {
      for (const [ci, val] of active) {
        if (!matchCell(cells[ci], val)) return;
      }
      kept.push({ cells, origIdx });
    });

    const shown = Math.min(kept.length, MAX_RENDER);
    const count = $('editorCount');
    if (count) {
      count.textContent = rows.length
        ? (active.length
            ? `Showing ${kept.length} of ${rows.length} rows · ${labels.length} columns`
            : `${rows.length} row${rows.length === 1 ? '' : 's'} · ${labels.length} columns`)
        : (loadedOnce ? 'No records matched' : 'Nothing loaded');
    }

    const empty = $('editorEmpty');
    if (!rows.length) {
      grid.classList.add('hidden');
      if (empty) {
        empty.classList.remove('hidden');
        const p = empty.querySelector('p');
        if (p) p.textContent = 'Choose your columns and date range, then load records to edit them.';
      }
      return;
    }

    // Rows are loaded — keep the grid and headers visible even if filters match 0 rows
    if (empty) empty.classList.add('hidden');
    grid.classList.remove('hidden');

    if (!shown) {
      const totalCols = labels.length + 1; // data columns + ACT
      tbody.innerHTML = `<tr><td colspan="${totalCols}" class="empty-cell" style="text-align: center; padding: 40px 16px; color: var(--ink3);">No rows match these filters.</td></tr>`;
      return;
    }

    const frag = document.createDocumentFragment();
    for (let i = 0; i < shown; i++) {
      const { cells, origIdx } = kept[i];
      const tr = document.createElement('tr');
      tr.dataset.rowIdx = origIdx;

      cells.forEach((cell, ci) => {
        const header = labels[ci];
        const logical = cols[ci];
        const w = colWidth(header);
        const isFrozen = fz.has(header);
        const isLastFrozen = isFrozen && isTrailingFrozen(ci);
        const td = document.createElement('td');
        td.className = `${isFrozen ? 'col-frozen' : ''} ${isLastFrozen ? 'col-frozen-last' : ''}`;
        td.style.width = `${w}px`;
        td.style.minWidth = `${w}px`;
        td.style.maxWidth = `${w}px`;
        if (isFrozen && typeof getFrozenColOffset === 'function') {
          td.style.left = `${frozenOffsetLeft(ci)}px`;
        }
        td.dataset.rowIdx = origIdx;
        td.dataset.colKey = logical;   // the shared selection layer keys off this
        td.dataset.colIdx = ci;
        if (dirty[origIdx + ':' + ci]) td.classList.add('is-dirty');

        const valStr = String(cell == null ? '' : cell).trim();
        const opts = choices[logical];

        if (Array.isArray(opts) && opts.length && typeof makeCombo === 'function') {
          td.appendChild(makeCombo({
            value: valStr,
            choices: opts.slice(),
            onCommit: (newVal) => commit(origIdx, ci, logical, newVal),
          }));
        } else {
          const input = document.createElement('input');
          input.type = 'text';
          input.className = 'cell-input';
          input.value = valStr;
          input.spellcheck = false;
          let before = valStr;
          input.addEventListener('focus', () => { before = input.value; });
          input.addEventListener('blur', () => {
            if (input.value === before) return;
            commit(origIdx, ci, logical, input.value);
          });
          input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
            else if (e.key === 'Escape') { input.value = before; input.blur(); }
          });
          td.appendChild(input);
        }
        tr.appendChild(td);
      });

      // Last cell on the right: ACT (Delete button) for this record.
      const tdAct = document.createElement('td');
      tdAct.className = 'col-action';
      tdAct.style.width = '54px';
      tdAct.style.minWidth = '54px';
      tdAct.style.maxWidth = '54px';
      tdAct.innerHTML = `
        <button type="button" class="btn xs ghost danger rc-del-btn" data-del-editor="${origIdx}" title="Delete this record">
          <span class="ico" data-icon="trash"></span>
        </button>`;
      tr.appendChild(tdAct);

      frag.appendChild(tr);
    }
    tbody.appendChild(frag);
    if (typeof applyIcons === 'function') applyIcons(tbody);

    if (typeof initGridSelection === 'function') {
      initGridSelection(grid, {
        // 3-arg bridge: the shared layer has no scope to pass, so the backend
        // applies edits to whichever scope is currently loaded.
        updateCell: 'update_editor_cell_active',
        setMem: (r, c, val) => { if (table && table.rows[r]) table.rows[r][c] = val; },
        onTable: (t) => renderTable(t),
      });
    }
  }

  async function commit(rowIdx, colIdx, logical, value) {
    const a = api();
    if (!a || !a.update_editor_cell) return;
    const res = await a.update_editor_cell(scope, rowIdx, logical, value);
    if (res && res.ok) renderTable(res.table);
    else {
      // Rejected (e.g. a future nego date) — restore and say why.
      renderTable();
      if (typeof toast === 'function') toast((res && res.error) || 'Edit rejected.', true);
    }
  }

  function renderTable(payload) {
    if (payload) table = payload;
    if (!$(TABLE_ID)) return;
    renderHead();
    renderBody();
    syncDirty();
  }

  function syncDirty() {
    const n = (table && table.dirtyCount) || 0;
    const chip = $('editorDirty');
    if (chip) {
      chip.textContent = `${n} unsaved edit${n === 1 ? '' : 's'}`;
      chip.classList.toggle('hidden', !n);
    }
    const save = $('btnEditorSave');
    const discard = $('btnEditorDiscard');
    if (save) save.disabled = !n;
    if (discard) discard.disabled = !n;
  }

  /* ---------------- wiring ---------------- */

  // Same busy treatment as the "Fetch selected tables" button: disabled, with a
  // spinning icon, restored when the backend signals it is done.
  const busyBtns = new Map();   // element -> original innerHTML

  function setBusy(btn, label) {
    if (!btn || busyBtns.has(btn)) return;
    busyBtns.set(btn, btn.innerHTML);
    btn.disabled = true;
    btn.innerHTML = `<span class="ico spinning" data-icon="refresh"></span> ${escapeHtml(label)}`;
    if (typeof applyIcons === 'function') applyIcons(btn);
  }

  function clearBusy() {
    busyBtns.forEach((html, btn) => {
      btn.innerHTML = html;
      btn.disabled = false;
      if (typeof applyIcons === 'function') applyIcons(btn);
    });
    busyBtns.clear();
    // Re-apply the real enabled/disabled rules now the buttons are back.
    syncCounts();
    syncDirty();
  }

  function scopeLabel() {
    const sel = $('editorScope');
    const opt = sel && sel.options[sel.selectedIndex];
    return opt ? opt.textContent : 'Records';
  }

  function init() {
    const scopeSel = $('editorScope');
    if (!scopeSel) return;

    // Default the record set from the pipeline's current mode, but changing it
    // here never flips that mode — this is a side tool, not part of the flow.
    const mode = (window.lastState && window.lastState.mode) || '';
    scope = mode === 'land-sourcing' ? 'sourcing-records' : 'nego-records';
    scopeSel.value = scope;

    scopeSel.addEventListener('change', async () => {
      scope = scopeSel.value;
      table = null; loadedOnce = false; filters = {}; openFilterCols = new Set();
      columns = []; selected = new Set();
      renderColumnList();
      await populateTables();
      await loadOptions();
    });

    const tableSel = $('editorTable');
    if (tableSel) tableSel.addEventListener('change', () => { loadOptions(); });

    $('editorColSearch').addEventListener('input', renderColumnList);
    $('btnEditorColsAll').addEventListener('click', () => {
      const term = ($('editorColSearch').value || '').trim().toLowerCase();
      columns.filter(c => !term || c.label.toLowerCase().includes(term)).forEach(c => selected.add(c.logical));
      renderColumnList();
    });
    $('btnEditorColsNone').addEventListener('click', () => { selected.clear(); renderColumnList(); });

    $('btnEditorLoad').addEventListener('click', async () => {
      const a = api();
      if (!a || !a.load_editor_records) return;
      loadedOnce = true;
      filters = {}; openFilterCols = new Set();
      const title = $('editorResultsTitle');
      if (title) title.textContent = scopeLabel();
      setBusy($('btnEditorLoad'), 'Loading records…');
      const res = await a.load_editor_records(
        scope,
        $('editorTable').value || '',
        Array.from(selected),
        $('editorDateCol').value || '',
        $('editorFrom').value || '',
        $('editorTo').value || ''
      );
      // A rejection here (not connected, unsaved edits) returns immediately and
      // never reaches onBackendDone, so release the button now.
      if (res && res.ok === false) {
        clearBusy();
        if (typeof toast === 'function') toast(res.error || 'Could not load records.', true);
      }
    });

    $('btnEditorSave').addEventListener('click', async () => {
      const a = api();
      if (!a || !a.save_editor_edits) return;
      setBusy($('btnEditorSave'), 'Saving…');
      const res = await a.save_editor_edits(scope);
      if (res && res.ok === false) {
        clearBusy();
        if (typeof toast === 'function') toast(res.error || 'Could not save edits.', true);
      }
    });

    $('btnEditorDiscard').addEventListener('click', async () => {
      const a = api();
      if (!a || !a.discard_editor_edits) return;
      const res = await a.discard_editor_edits(scope);
      if (res && res.ok) renderTable(res.table);
    });

    const clearFilters = $('btnEditorClearFilters');
    if (clearFilters) clearFilters.addEventListener('click', () => { filters = {}; openFilterCols = new Set(); renderTable(); });

    const grid = $(TABLE_ID);
    if (grid) {
      grid.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-del-editor]');
        if (!btn) return;
        const rowIdx = parseInt(btn.dataset.delEditor, 10);
        const rowCells = (table && table.rows && table.rows[rowIdx]) || [];
        const label = (rowCells[0] != null && String(rowCells[0]).trim())
          ? String(rowCells[0]).trim()
          : `record #${rowIdx + 1}`;

        const doDelete = async () => {
          const a = api();
          if (!a || !a.delete_editor_row) return;
          const res = await a.delete_editor_row(scope, rowIdx);
          if (res && res.ok !== false) {
            // The backend words this: a synced delete, or a local-only removal.
            if (typeof toast === 'function') toast(res.msg || 'Record deleted.', false);
            if (res.table) renderTable(res.table);
          } else {
            if (typeof toast === 'function') toast((res && res.error) || 'Failed to delete record.', true);
          }
        };

        if (typeof openConfirmModal === 'function') {
          openConfirmModal({
            title: 'Delete record',
            message: `Delete "${escapeHtml(label)}"? This removes it from Dataverse and cannot be undone.`,
            proceedLabel: 'Delete',
            cancelLabel: 'Cancel',
            danger: true,
            hideQuestion: true,
            onProceed: doDelete,
          });
        } else if (confirm(`Delete "${label}"? This removes it from Dataverse and cannot be undone.`)) {
          doDelete();
        }
      });
    }

    // Results live on their own page; these move between the two.
    const back = $('btnEditorBack');
    if (back) back.addEventListener('click', () => {
      window.showView('editor');
      const b = $('btnEditorBackToResults');
      if (b) b.classList.toggle('hidden', !(table && table.rows && table.rows.length));
    });
    const toResults = $('btnEditorBackToResults');
    if (toResults) toResults.addEventListener('click', () => window.showView('editor-results'));

    // Populate on first open: the table list needs a live connection, which is
    // rarely there at page load.
    let primed = false;
    document.querySelectorAll('.nav-item[data-view="editor"]').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (primed) return;
        primed = true;
        await populateTables();
        await loadOptions();
      });
    });

    // The backend hands editor tables back through the shared done-signal.
    const prevDone = window.onBackendDone;
    window.onBackendDone = function (info) {
      // Clear on ANY completion: a failed load reports an error with no table,
      // and the button must not stay stuck spinning.
      clearBusy();
      if (info && info.table && info.table.kind === ('editor:' + scope)) {
        renderTable(info.table);
        // A finished load opens the results page.
        if (info.table.loaded && window.showView) window.showView('editor-results');
      }
      if (prevDone) prevDone.call(this, info);
    };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  window.recordsEditor = { loadOptions, renderTable, populateTables, init };
})();
