'use strict';

const $ = (id) => document.getElementById(id);
let negotiatorOptions = [];
let lastState = {};
let currentRefKind = null;
const MAX_RENDER = 10000;   // cap DOM rows so huge tables stay responsive


const REF_TABLES = [
  { kind: 'municipality', name: 'Municipality Code', icon: 'map', initial: 'M' },
  { kind: 'team', name: 'Team Composition', icon: 'users', initial: 'T' },
  { kind: 'mapping', name: 'Mapping Status', icon: 'flag', initial: 'S' },
  { kind: 'build', name: 'Build Table', icon: 'layers', initial: 'B' },
  { kind: 'existing', name: 'Existing Records', icon: 'archive', initial: 'E' },
];
const REF_DEFAULT_LOGICAL = {
  municipality: 'cr63f_municipalitycode', team: 'cr63f_teamcomposition',
  mapping: 'cr63f_mappingstatus', build: 'cr63f_batangasbuild', existing: 'cr63f_batangasnegorecord',
};
const REF_SYNC_KINDS = ['build', 'team', 'municipality', 'mapping'];  // reference tables that use Sync-to-Dataverse (not plain Upload)
const REF_ICON = Object.fromEntries(REF_TABLES.map(t => [t.kind, t.icon]));
const REF_NAME = Object.fromEntries(REF_TABLES.map(t => [t.kind, t.name]));

// --- API bridge -------------------------------------------------------------
function api() { return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null; }

function toast(message, isError) {
  const t = $('toast');
  t.textContent = message;
  t.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.className = 'toast'; }, 3400);
}

function handle(result) {
  if (!result) return null;
  if (result.ok === false) { toast(result.error || 'Something went wrong.', true); return result; }
  if (result.state) applyState(result.state);
  if (result.message) toast(result.message, false);
  return result;
}


function setBusy(b) { document.body.classList.toggle('busy', b); }

// sync/fast backend calls (return state immediately)
async function call(method, ...args) {
  const a = api();
  if (!a) { toast('Backend not ready.', true); return null; }
  try { return handle(await a[method](...args)); }
  catch (e) { toast(String(e), true); return null; }
}

// async backend ops (file dialogs, heavy work). Returns immediately; the real
// result arrives via window.onBackendDone, which clears the busy state.
async function fire(method, ...args) {
  const a = api();
  if (!a) { toast('Backend not ready.', true); return; }
  setBusy(true);
  try {
    const r = await a[method](...args);
    // a synchronous terminal result (validation error / early message) ends it here
    if (r && r.ok === false) { setBusy(false); if (r.error) toast(r.error, true); if (r.state) applyState(r.state); }
    else if (r && r.message) { setBusy(false); toast(r.message, false); if (r.state) applyState(r.state); }
  } catch (e) { setBusy(false); toast(String(e), true); }
}

// Publish state
let currentPublishMode = 'review'; // 'review' or 'prod'
let skipRecordsToProd = false;      // stage 4: all records already exist -> skip to stage 5
window.currentPublishMode = currentPublishMode;

// terminal signal for an async op, pushed from a Python background thread
function showPublishForm() {
  // Restore the publish button state
  const modalConfirm = $('modalPublishConfirm');
  if (modalConfirm) {
    modalConfirm.disabled = false;
    modalConfirm.textContent = modalConfirm.dataset.origText || 'Publish now';
  }
  const f = $('publishForm'), r = $('publishReceipt');
  if (f) f.classList.remove('hidden');
  if (r) r.classList.add('hidden');
}
window.showPublishForm = showPublishForm;

function showPublishReceipt(r) {
  // Restore the publish button state
  const modalConfirm = $('modalPublishConfirm');
  if (modalConfirm) {
    modalConfirm.disabled = false;
    modalConfirm.textContent = modalConfirm.dataset.origText || 'Publish now';
  }
  const f = $('publishForm'), box = $('publishReceipt');
  if (!f || !box) return;
  f.classList.add('hidden');
  box.classList.remove('hidden');

  const pubStage = (r && r.stage === 'productivity') ? 'prod' : 'records';
  currentPublishMode = pubStage;
  window.currentPublishMode = pubStage;
  const pubView = $('view-publish');
  if (pubView) pubView.dataset.pubstage = pubStage;

  const stageName = (r && r.stage === 'productivity') ? 'productivity rows' : 'records';
  const target = (r && (r.targetDisplay || r.target)) || 'Dataverse';
  const written = (r && r.writtenCount) || 0;
  const updated = (r && r.updatedCount) || 0;
  const skipped = (r && r.skippedCount) || 0;
  const failed = (r && r.failedCount) || 0;

  const heroCheck = box.querySelector('.receipt-check');
  if (heroCheck) heroCheck.textContent = failed > 0 ? '!' : '\u2713';
  if (heroCheck) heroCheck.classList.toggle('receipt-check-warn', failed > 0);

  const head = $('receiptHeadline');
  if (head) head.textContent = updated
    ? `${written} written · ${updated} updated`
    : `${written} ${stageName} published`;
  const sub = $('receiptSub');
  if (sub) {
    let bits = [`Written to ${target}.`];
    if (updated) bits.push(`${updated} existing overwritten.`);
    if (skipped) bits.push(`${skipped} skipped (already in Dataverse).`);
    if (failed) bits.push(`${failed} failed — see below.`);
    sub.textContent = bits.join(' ');
  }

  const rows = $('receiptRows');
  if (rows) {
    const summary = [
      ['Target table', target],
      ['Written', String(written)],
      ['Updated (override)', String(updated)],
      ['Skipped (duplicate)', String(skipped)],
      ['Failed', String(failed)],
    ].map(([k, v]) =>
      '<div class="pm-summary-row"><span class="pm-summary-label">' + escapeHtml(k) +
      '</span><span class="pm-summary-value pm-mono-val">' + escapeHtml(v) + '</span></div>').join('');

    // Per-row skipped/failed detail is not shown inline — it's available via the
    // "Export skipped & failed (CSV)" button below (a native save dialog).
    let note = '';
    if (skipped || failed) {
      const bits = [];
      if (skipped) bits.push(`${skipped} skipped`);
      if (failed) bits.push(`${failed} failed`);
      note = `<div class="receipt-export-hint">${escapeHtml(bits.join(' · '))} — export the full list as CSV below.</div>`;
    }
    rows.innerHTML = summary + note;
  }

  // export button visibility: only when there is something skipped/failed to export
  const exportBtn = $('btnReceiptExport');
  if (exportBtn) exportBtn.classList.toggle('hidden', !(skipped || failed));

  // Action buttons on receipt
  const nextProdBtn = $('btnReceiptNextProd');
  const dashBtn = $('btnReceiptDashboard');
  const againBtn = $('btnPublishAgain');
  const isRecordsReceipt = (r && r.stage === 'records');

  if (nextProdBtn) {
    nextProdBtn.classList.toggle('hidden', !isRecordsReceipt);
    nextProdBtn.onclick = () => {
      if (window.openPublishModal) window.openPublishModal('prod');
    };
  }

  if (againBtn) {
    againBtn.classList.toggle('hidden', isRecordsReceipt);
    againBtn.textContent = isRecordsReceipt ? 'Load another file' : 'Finish';
    againBtn.className = isRecordsReceipt ? 'btn' : 'btn primary';
    againBtn.onclick = () => {
      if (window.finishWorkflow) window.finishWorkflow();
    };
  }

  if (dashBtn) {
    dashBtn.className = isRecordsReceipt ? 'btn primary nav-item' : 'btn nav-item';
    dashBtn.classList.toggle('hidden', isRecordsReceipt);
  }

  window.__lastReceipt = r;
  window.__lastReceiptStage = r ? r.stage : null;

  showView('publish');
}
window.showPublishReceipt = showPublishReceipt;

async function finishWorkflow() {
  const a = api();
  if (a && a.reset_transaction_file) {
    try {
      const res = await a.reset_transaction_file();
      if (res && res.state) {
        lastState = res.state;
      }
    } catch (e) {
      console.warn('reset_transaction_file error:', e);
    }
  } else {
    if (lastState) {
      lastState.inputName = '';
      lastState.rowCount = 0;
      lastState.productivityCount = 0;
      lastState.hasCalculated = false;
      lastState.recordsPublishedClean = false;
    }
  }

  window.__lastReceipt = null;
  window.__lastReceiptStage = null;
  currentReviewTable = null;
  prodTable = null;
  prodFilters = {};
  reviewFilters = {};

  const revTable = $('reviewTable');
  if (revTable) revTable.classList.add('hidden');
  const revEmpty = $('reviewEmpty');
  if (revEmpty) revEmpty.classList.remove('hidden');

  const prTable = $('prodTable');
  if (prTable) prTable.classList.add('hidden');
  const prEmpty = $('prodEmpty');
  if (prEmpty) prEmpty.classList.remove('hidden');

  showPublishForm();
  if (window.applyState) window.applyState(lastState);
  showView('load');
  if (window.syncSteps) window.syncSteps();
  toast('Workflow completed. Ready to load another file.', false);
}
window.finishWorkflow = finishWorkflow;

window.onBackendDone = function (info) {
  setBusy(false);
  if (typeof lastState !== 'undefined' && lastState) {
    lastState.isBusy = false;
    lastState.refTablesLoading = false;
  }
  const modalConfirm = $('modalPublishConfirm');
  if (modalConfirm) {
    modalConfirm.disabled = false;
    modalConfirm.textContent = modalConfirm.dataset.origText || 'Publish now';
  }
  if (info && info.refKind) {
    if (typeof unfetchedRefKinds !== 'undefined') unfetchedRefKinds.delete(info.refKind);
  } else if (info && info.message && (info.message.includes('Fetched from Dataverse') || info.message.includes('Replaced'))) {
    if (typeof unfetchedRefKinds !== 'undefined') unfetchedRefKinds.clear();
  }
  if (info && info.message) toast(info.message, !!info.error);
  if (info && info.receipt && ['syncPreview', 'syncResult', 'syncClosed'].includes(info.receipt.kind)) {
    const r = info.receipt;
    if (r.kind === 'syncPreview') openSyncModal(r);
    else if (r.kind === 'syncClosed') closeSyncModal();  // cancelled backup / error: dismiss the modal
    else { closeSyncModal(); showSyncResult(r); }
  } else if (info && info.receipt) {
    const r = info.receipt;
    // "Skip anyway" flow: records were all duplicates (0 written, 0 failed);
    // the backend has now marked them present, so jump straight to stage 5
    // instead of showing the records receipt.
    if (window.__afterRecordsToProd && r.stage === 'records' && (r.failedCount || 0) === 0) {
      window.__afterRecordsToProd = false;
      if (window.openPublishModal) { window.openPublishModal('prod'); }
    } else {
      window.__afterRecordsToProd = false;
      showPublishReceipt(r);
    }
  }
  if (info && info.table) {
    if (info.view === 'review') { renderReview(info.table); showView('review'); }
    else if (info.view === 'productivity') { renderProductivity(info.table); showView('productivity'); }
  }
  if (!info || info.refresh !== false) {
    const a = api();
    if (a && a.state) {
      a.state().then(s => {
        applyState(s);
        if ($('view-ref').classList.contains('active') && currentRefKind) {
          a.get_reference_rows(currentRefKind).then(renderReferenceView);
        }
      });
    }
  }
};


// --- Themed Sync popup + confirm dialogs (theme-aware via CSS vars) ---------
function _bsEsc(v) {
  return String(v == null ? '' : v).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function closeSyncModal() {
  const ov = document.getElementById('syncOverlay');
  if (ov) ov.remove();
}

// r: { title, targetTitle/target, fileName, currentCount, insertCount, updateCount,
//      unchangedCount, noteText, commit, cancel } — commit/cancel are backend method names.
function openSyncModal(r) {
  closeSyncModal();
  const commitMethod = r.commit || 'commit_build_sync';
  const cancelMethod = r.cancel || 'cancel_build_sync';
  const note = r.noteText || "Overwrites replace the existing row's values. Nothing is deleted.";
  const ov = document.createElement('div');
  ov.id = 'syncOverlay';
  ov.className = 'app-modal-overlay';
  ov.innerHTML = `
    <div class="app-modal">
      <div class="am-body">
        <h2>${_bsEsc(r.title || 'Sync → Dataverse')}</h2>
        <p class="am-sub">Target: <b>${_bsEsc(r.targetTitle || r.target || '')}</b> &nbsp;&bull;&nbsp; File: ${_bsEsc(r.fileName || '')} &nbsp;&bull;&nbsp; ${r.currentCount || 0} row(s) in table now</p>
      </div>
      <div class="am-chips">
        <span class="am-chip ins">Insert ${r.insertCount || 0}</span>
        <span class="am-chip upd">Update ${r.updateCount || 0}</span>
        <span class="am-chip unch">Unchanged ${r.unchangedCount || 0}</span>
      </div>
      <div id="syncProgress" class="am-progress" style="display:none;">
        <div class="am-progress-track"><div id="syncBar" class="am-progress-bar"></div></div>
        <p id="syncProgressText" class="am-progress-text">Preparing…</p>
      </div>
      <p id="syncNote" class="am-note">${_bsEsc(note)}</p>
      <label id="syncBackupRow" class="am-check">
        <input type="checkbox" id="syncBackup" checked /> Create a CSV backup of the current table before applying
      </label>
      <div id="syncActions" class="am-actions">
        <button class="btn" id="syncCancel">Cancel</button>
        <button class="btn primary" id="syncApply">Apply</button>
      </div>
    </div>`;
  document.body.appendChild(ov);
  ov.addEventListener('click', e => { if (e.target === ov && !ov.dataset.working) { call(cancelMethod); closeSyncModal(); } });
  document.getElementById('syncCancel').addEventListener('click', () => { call(cancelMethod); closeSyncModal(); });
  document.getElementById('syncApply').addEventListener('click', () => {
    const doBackup = document.getElementById('syncBackup').checked;
    ov.dataset.working = '1';
    document.getElementById('syncActions').style.display = 'none';
    document.getElementById('syncBackupRow').style.display = 'none';
    document.getElementById('syncProgress').style.display = 'block';
    const noteEl = document.getElementById('syncNote');
    if (noteEl) noteEl.style.marginBottom = '20px';
    document.getElementById('syncProgressText').textContent = doBackup ? 'Choose where to save the backup…' : 'Writing to Dataverse…';
    call(commitMethod, doBackup);
  });
}

window.onBuildSyncProgress = function (done, total) {
  const bar = document.getElementById('syncBar');
  const txt = document.getElementById('syncProgressText');
  if (!bar || !txt) return;
  const pct = total > 0 ? Math.round((done / total) * 100) : 100;
  bar.style.width = pct + '%';
  txt.textContent = total > 0
    ? `Writing ${done} of ${total} record(s)… (${pct}%). Do not close this app or this window.`
    : 'No changes to write.';
};

function showSyncResult(r) {
  const failed = r.failed || 0;
  const errs = (r.errors || []).slice(0, 8).map(er => `${_bsEsc(er.area || er.key || '')}: ${_bsEsc(er.error)}`).join('\n');
  const msg = `Sync done — ${r.created || 0} inserted, ${r.updated || 0} updated, ${failed} failed.` +
    (r.backup ? ` Backup: ${r.backup}.` : '') + (errs ? `\n\n${errs}` : '');
  if (failed > 0) { try { alert(msg); } catch (e) {} }
}

// --- Generic confirm modal (themed) ----------------------------------------
function openConfirmModal({ title, message, proceedLabel = 'Proceed', cancelLabel = 'Cancel', danger = false, hideQuestion = false, onProceed, onCancel }) {
  const existing = document.getElementById('genericConfirmOverlay');
  if (existing) existing.remove();
  const ov = document.createElement('div');
  ov.id = 'genericConfirmOverlay';
  ov.className = 'app-modal-overlay';
  ov.style.zIndex = '10000';
  ov.innerHTML = `
    <div class="app-modal" style="max-width:480px;">
      <div class="am-body" style="padding-bottom:2px;">
        <h2>${_bsEsc(title)}</h2>
        <p class="am-msg" style="white-space:pre-line;">${_bsEsc(message)}</p>
      </div>
      ${hideQuestion ? '' : '<p class="am-q">Do you want to proceed?</p>'}
      <div class="am-actions">
        <button class="btn" id="genericConfirmCancel">${_bsEsc(cancelLabel)}</button>
        <button class="btn ${danger ? 'am-btn-danger' : 'primary'}" id="genericConfirmProceed">${_bsEsc(proceedLabel)}</button>
      </div>
    </div>`;
  document.body.appendChild(ov);
  const close = () => ov.remove();
  ov.addEventListener('click', e => { if (e.target === ov) { close(); if (onCancel) onCancel(); } });
  document.getElementById('genericConfirmCancel').addEventListener('click', () => { close(); if (onCancel) onCancel(); });
  document.getElementById('genericConfirmProceed').addEventListener('click', () => { close(); if (onProceed) onProceed(); });
}

// Warn (and require confirmation) about Build work-week mismatches, else proceed.
function confirmBuildMismatch(onProceed, proceedLabel) {
  const errs = (currentReviewTable && currentReviewTable.buildErrors) || [];
  if (!errs.length || (currentReviewTable && currentReviewTable.useCurrentBuild)) { if (onProceed) onProceed(); return; }
  openConfirmModal({
    title: 'Build Mismatches',
    message: `${errs.length} Build Mismatch${errs.length > 1 ? 'es' : ''}: Verify that the Build Table is updated and the report or nego date is correct.`,
    proceedLabel: 'Use current build & proceed',
    cancelLabel: proceedLabel || 'Proceed with strict matching',
    onProceed: async () => {
      const res = await call('calculate', true);
      if (res && res.table) renderReview(res.table);
      if (onProceed) onProceed();
    },
    onCancel: () => {
      if (onProceed) onProceed();
    }
  });
}

// --- view switching ---------------------------------------------------------
function showView(view) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  const targetView = $('view-' + view);
  if (targetView) targetView.classList.add('active');
  document.querySelectorAll('.nav-item').forEach(n => {
    if (n.classList.contains('step')) return; // handled by syncSteps
    n.classList.toggle('active', n.dataset.view === view);
  });
  if (window.scheduleShellSync) window.scheduleShellSync();
}
window.showView = showView;
window.getCurrentPublishMode = () => (typeof currentPublishMode !== 'undefined' ? currentPublishMode : 'review');



async function showReference(kind) {
  currentRefKind = kind;
  // Filter state is keyed by column INDEX, which means different things per table,
  // so reset it when switching tables — otherwise a filter opened/typed on column N
  // of one table carries over to column N of the next.
  openRefFilterCols.clear();
  refFilters = {};
  refSearchQuery = '';
  var refSearchEl = $('refSearchInput');
  if (refSearchEl) refSearchEl.value = '';
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  $('view-ref').classList.add('active');
  document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.ref === kind));
  const icoEl = $('refTitleIco');
  icoEl.dataset.icon = REF_ICON[kind] || 'table';
  icoEl.dataset.iconApplied = '';
  applyIcons(document);

  // "Sync to Dataverse" (non-destructive upsert) replaces the plain Upload-file
  // button for Build, Team, Municipality and Mapping. Existing Records keeps Upload.
  const syncable = REF_SYNC_KINDS.includes(kind);
  const syncBtn = $('refBuildSync');
  if (syncBtn) syncBtn.classList.toggle('hidden', !syncable);
  const upBtn = $('refUploadFile');
  if (upBtn) upBtn.classList.toggle('hidden', syncable);

  // populate viewer table dropdown from activeSolutionTables
  const sel = $('refViewerTableSelect');
  if (sel) {
    sel.innerHTML = '<option value="">— Default Dataverse Table —</option>';
    const filteredTables = getTablesForKind(kind, lastState ? lastState.mode : 'negotiation');
    if (filteredTables && filteredTables.length) {
      const hasValidSelection = filteredTables.some(t => (selectedTableMappings[kind] || '').toLowerCase() === t.logicalName.toLowerCase());
      if (!hasValidSelection) {
        selectedTableMappings[kind] = filteredTables[0].logicalName;
      }
      filteredTables.forEach(t => {
        const isSel = (selectedTableMappings[kind] || '').toLowerCase() === t.logicalName.toLowerCase();
        sel.appendChild(new Option(t.displayName, t.logicalName, false, isSel));
      });
    }
    sel.disabled = !(lastState && lastState.connected);
  }



  const btnFetch = $('refFetchDv');
  if (btnFetch) btnFetch.disabled = !lastState.connected;

  const a = api();
  if (!a) return;
  const res = await a.get_reference_rows(kind);
  renderReferenceView(res);
}


// --- Reference Table Viewer & Editor State ---------------------------------
let currentRefState = null;
let refFilters = {};
let refSearchQuery = '';
let refFilterTimer = null;
let openRefFilterCols = new Set();

function renderReferenceView(res) {
  if (!res || res.ok === false) { toast((res && res.error) || 'Could not load table.', true); return; }
  currentRefState = res;
  $('refTitle').textContent = res.title;

  var srcEl = $('refSource');
  if (srcEl) {
    if (res.source) {
      let cleanSrc = res.source;
      const match = (activeSolutionTables || []).find(t => t.logicalName.toLowerCase() === res.source.toLowerCase());
      if (match) cleanSrc = match.displayName;
      else if (cleanSrc.toLowerCase().startsWith('cr63f_')) cleanSrc = res.title || cleanSrc.replace(/^cr63f_/i, '');
      srcEl.textContent = res.source.toLowerCase().includes('dataverse') ? '\u{1F7E2} Live Dataverse Sync' : 'Source: ' + cleanSrc;
      srcEl.className = 'src-pill' + (res.source.toLowerCase().includes('dataverse') ? ' dv-live' : '');
      srcEl.classList.remove('hidden');
    } else {
      srcEl.textContent = '';
      srcEl.classList.add('hidden');
    }
  }

  var searchInput = $('refSearchInput');
  if (searchInput) searchInput.value = refSearchQuery;

  var t = $('refTable'), thead = t.querySelector('thead');
  thead.innerHTML = '';
  if (!res.count && (!res.rows || !res.rows.length)) {
    t.classList.add('hidden');
    $('refEmpty').classList.remove('hidden');
    $('refCount').textContent = '0 rows';
    return;
  }

  var tableKey = 'ref_' + (currentRefKind || 'general');
  var headerHeight = getSavedHeaderHeight(tableKey, 44);
  var tr = document.createElement('tr');

  (res.labels || []).forEach(function(l, idx) {
    var savedW = getSavedColWidth(tableKey, l, null);
    var th = document.createElement('th');
    if (savedW) {
      th.style.width = savedW + 'px';
      th.style.minWidth = savedW + 'px';
      th.style.maxWidth = savedW + 'px';
    }
    th.style.height = headerHeight + 'px';
    th.style.minHeight = headerHeight + 'px';

    var isFilterActive = !!(refFilters[idx] && refFilters[idx].trim());
    var isFilterOpen = openRefFilterCols.has(idx) || isFilterActive;

    th.innerHTML = '<div class="th-header-box">' +
      '<div class="th-title-wrap">' +
        '<span class="th-col-name" title="' + escapeHtml(l) + '">' + escapeHtml(l) + '</span>' +
        '<button type="button" class="th-chevron-btn ' + (isFilterActive ? 'active' : '') + '" data-ref-filter-toggle="' + idx + '" title="Filter column">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"></polyline></svg>' +
        '</button>' +
      '</div>' +
      '<div class="th-filter-popup ' + (isFilterOpen ? '' : 'hidden') + '" id="refFilterBox_' + idx + '">' +
        '<input class="th-filter-input" data-ref-col="' + idx + '" value="' + escapeHtml(refFilters[idx] || '') + '" placeholder="FILTER ' + escapeHtml(l) + '..." />' +
      '</div>' +
    '</div>' +
    '<div class="th-resizer" data-resizer="' + idx + '" title="Drag to resize column width"></div>' +
    '<div class="th-row-resizer" title="Drag to resize header height"></div>';
    tr.appendChild(th);
  });

  var thAction = document.createElement('th');
  thAction.className = 'col-action';
  thAction.style.width = '54px';
  thAction.style.minWidth = '54px';
  thAction.style.maxWidth = '54px';
  thAction.style.height = headerHeight + 'px';
  thAction.style.minHeight = headerHeight + 'px';
  thAction.innerHTML = '<div class="th-header-box"><span class="th-col-name">ACT</span></div>' +
    '<div class="th-row-resizer" title="Drag to resize header height"></div>';
  tr.appendChild(thAction);
  thead.appendChild(tr);


  // Assign (not addEventListener) so re-renders replace the handler instead of
  // stacking duplicates on the persistent <thead> — stacked handlers toggled the
  // popup multiple times per click, making the filter open only intermittently.
  thead.onclick = function(e) {
    if (e.target.closest('.th-resizer') || e.target.closest('.th-row-resizer')) return;
    var chevronBtn = e.target.closest('[data-ref-filter-toggle]');
    if (chevronBtn) {
      var col = parseInt(chevronBtn.dataset.refFilterToggle, 10);
      if (openRefFilterCols.has(col)) openRefFilterCols.delete(col);
      else openRefFilterCols.add(col);
      var box = document.getElementById('refFilterBox_' + col);
      if (box) {
        box.classList.toggle('hidden');
        if (!box.classList.contains('hidden')) {
          var inp2 = box.querySelector('input');
          if (inp2) inp2.focus();
        }
      }
    }
  };

  thead.querySelectorAll('input[data-ref-col]').forEach(function(inp) {
    inp.addEventListener('input', function(e) {
      var col = e.target.dataset.refCol;
      var v = e.target.value.trim().toLowerCase();
      if (v) refFilters[col] = v; else delete refFilters[col];
      clearTimeout(refFilterTimer);
      refFilterTimer = setTimeout(renderReferenceBody, 120);
    });
  });

  attachHeaderResizers(t, tableKey, res.labels);
  applyIcons(thead);
  renderReferenceBody();
}


// --- Reference table: virtualized rendering (scroll all rows, paint only the
// visible window so 10k+ rows stay smooth) --------------------------------
var refFiltered = [];        // all rows matching the current search/filters
var refRowH = 44;            // measured row height, kept accurate after first paint
var refVScrollBound = false; // scroll listener attached once

function buildRefRow(rowCells, origIdx, tableKey) {
  var tr = document.createElement('tr');
  rowCells.forEach(function(c, cIdx) {
    var key = (currentRefState.keys && currentRefState.keys[cIdx]) || '';
    var label = (currentRefState.labels && currentRefState.labels[cIdx]) || key;
    var savedW = getSavedColWidth(tableKey, label, null);
    var td = document.createElement('td');
    if (savedW) { td.style.width = savedW + 'px'; td.style.minWidth = savedW + 'px'; td.style.maxWidth = savedW + 'px'; }
    var input = document.createElement('input');
    input.className = 'cell-input';
    input.value = String(c || '');
    input.dataset.rowIdx = origIdx;
    input.dataset.colKey = key;
    input.dataset.colIdx = cIdx;
    input.style.width = '100%';
    (function(inp, rIdx, k, ci) {
      var initialVal = inp.value;
      inp.addEventListener('focus', function() { initialVal = inp.value; });
      inp.addEventListener('change', function() {
        var newVal = inp.value.trim();
        if (newVal !== initialVal) {
          inp.classList.add('modified');
          currentRefState.rows[rIdx][ci] = newVal;
          // Call the API directly (not via call()/handle) so the returned state
          // does NOT trigger applyState -> a full ref-viewer re-render, which would
          // destroy the input the user has already tabbed into and steal focus.
          var a = api();
          if (a) {
            a.update_reference_cell(currentRefKind, rIdx, k, newVal).then(function(res) {
              if (res && res.ok !== false) toast(res.msg || 'Saved: ' + k + ' updated.', false);
              else toast((res && res.error) || 'Failed to save cell.', true);
            }).catch(function(e) { toast(String(e), true); });
          }
        }
      });
      inp.addEventListener('keydown', function(e) { if (e.key === 'Enter') { e.preventDefault(); inp.blur(); } });
    })(input, origIdx, key, cIdx);
    td.appendChild(input);
    tr.appendChild(td);
  });
  var tdAct = document.createElement('td');
  tdAct.className = 'col-action';
  tdAct.style.width = '54px'; tdAct.style.minWidth = '54px'; tdAct.style.maxWidth = '54px';
  tdAct.innerHTML = '<button class="btn xs ghost danger rc-del-btn" data-del-row="' + origIdx + '" title="Delete this entry"><span class="ico" data-icon="trash"></span></button>';
  tr.appendChild(tdAct);
  return tr;
}

function renderRefWindow() {
  if (!currentRefState) return;
  var t = $('refTable'), tbody = t.querySelector('tbody'), wrap = $('refWrap');
  var total = refFiltered.length;
  if (!total) { tbody.innerHTML = ''; return; }
  var scrollTop = wrap ? wrap.scrollTop : 0;
  var viewH = (wrap && wrap.clientHeight) || 600;
  var BUF = 6;
  var start = Math.max(0, Math.floor(scrollTop / refRowH) - BUF);
  var visCount = Math.ceil(viewH / refRowH) + BUF * 2;
  var end = Math.min(total, start + visCount);
  var cols = ((currentRefState.keys || []).length) + 1;
  var tableKey = 'ref_' + (currentRefKind || 'general');

  var frag = document.createDocumentFragment();
  if (start > 0) {
    var topTr = document.createElement('tr');
    topTr.className = 'ref-spacer';
    var topTd = document.createElement('td');
    topTd.colSpan = cols; topTd.style.padding = '0'; topTd.style.border = '0';
    topTd.style.height = (start * refRowH) + 'px';
    topTr.appendChild(topTd); frag.appendChild(topTr);
  }
  for (var i = start; i < end; i++) {
    frag.appendChild(buildRefRow(refFiltered[i].rowCells, refFiltered[i].origIdx, tableKey));
  }
  if (end < total) {
    var botTr = document.createElement('tr');
    botTr.className = 'ref-spacer';
    var botTd = document.createElement('td');
    botTd.colSpan = cols; botTd.style.padding = '0'; botTd.style.border = '0';
    botTd.style.height = ((total - end) * refRowH) + 'px';
    botTr.appendChild(botTd); frag.appendChild(botTr);
  }
  tbody.innerHTML = '';
  tbody.appendChild(frag);
  applyIcons(tbody);

  // keep the row-height estimate accurate (once) so spacer math doesn't drift
  var sample = tbody.querySelector('tr:not(.ref-spacer)');
  if (sample) {
    var h = sample.offsetHeight;
    if (h && Math.abs(h - refRowH) > 2 && !renderRefWindow._remeasuring) {
      refRowH = h;
      renderRefWindow._remeasuring = true;
      renderRefWindow();
      renderRefWindow._remeasuring = false;
    }
  }
}

function renderReferenceBody() {
  if (!currentRefState || !currentRefState.rows) return;
  var t = $('refTable'), tbody = t.querySelector('tbody');
  var tableKey = 'ref_' + (currentRefKind || 'general');

  var q = (refSearchQuery || '').trim().toLowerCase();
  var activeColFilters = Object.entries(refFilters).filter(function(e) { return (e[1] || '').trim() !== ''; });
  var hasAnyFilter = q !== '' || activeColFilters.length > 0;
  var btnClear = $('refClearFilters');
  if (btnClear) btnClear.disabled = !hasAnyFilter;

  var filtered = [];
  currentRefState.rows.forEach(function(rowCells, origIdx) {
    if (q) {
      var matchAny = rowCells.some(function(cell) { return String(cell || '').toLowerCase().includes(q); });
      if (!matchAny) return;
    }
    for (var fi = 0; fi < activeColFilters.length; fi++) {
      var cIdx = parseInt(activeColFilters[fi][0], 10);
      var cellText = String(rowCells[cIdx] || '').toLowerCase();
      if (!cellText.includes(activeColFilters[fi][1].trim().toLowerCase())) return;
    }
    filtered.push({ rowCells: rowCells, origIdx: origIdx });
  });

  refFiltered = filtered;
  var total = currentRefState.rows.length;
  $('refCount').textContent = hasAnyFilter
    ? 'Showing ' + filtered.length + ' of ' + total + ' rows'
    : total + ' rows';

  if (!filtered.length) {
    if (total === 0) {
      // Genuinely nothing loaded for this table.
      t.classList.add('hidden');
      $('refEmpty').classList.remove('hidden');
      tbody.innerHTML = '';
      return;
    }
    // Rows exist but the search/filter matched none — keep the headers visible
    // and show an in-table message instead of hiding the whole table.
    $('refEmpty').classList.add('hidden');
    t.classList.remove('hidden');
    var emptyCols = ((currentRefState.keys || []).length) + 1;
    tbody.innerHTML = '<tr><td colspan="' + emptyCols + '" class="empty-cell">No rows match your search or filters.</td></tr>';
    return;
  }
  $('refEmpty').classList.add('hidden');
  t.classList.remove('hidden');

  // Delete delegation lives on tbody, so it survives windowed re-renders.
  tbody.onclick = function(e) {
    var btn = e.target.closest('[data-del-row]');
    if (!btn) return;
    var idx = parseInt(btn.dataset.delRow, 10);
    var rowObj = (currentRefState.rows && currentRefState.rows[idx]) || [];
    var label = (rowObj[0] != null && String(rowObj[0]).trim()) ? String(rowObj[0]).trim() : ('entry #' + (idx + 1));
    openConfirmModal({
      title: 'Delete row',
      message: 'Delete "' + label + '"? This removes it from Dataverse and cannot be undone.',
      proceedLabel: 'Delete', cancelLabel: 'Cancel', danger: true, hideQuestion: true,
      onProceed: function () {
        call('delete_reference_row', currentRefKind, idx).then(function (res) {
          if (res && res.ok !== false) {
            currentRefState.rows.splice(idx, 1);
            currentRefState.count = currentRefState.rows.length;
            toast(res.msg || 'Entry deleted.', false);
            renderReferenceBody();
          } else {
            toast((res && res.error) || 'Failed to delete row.', true);
          }
        });
      }
    });
  };

  // Bind the scroll handler once; re-render only the visible window as it scrolls.
  var wrap = $('refWrap');
  if (wrap && !refVScrollBound) {
    refVScrollBound = true;
    var vTick = false;
    wrap.addEventListener('scroll', function() {
      if (vTick) return;
      vTick = true;
      requestAnimationFrame(function() { renderRefWindow(); vTick = false; });
    });
  }
  if (wrap) wrap.scrollTop = 0;
  renderRefWindow();
}


function exportReferenceTableData(fmt) {
  if (!currentRefKind) {
    toast('No reference table selected.', true);
    return;
  }
  if (!currentRefState || !currentRefState.rows || !currentRefState.rows.length) {
    toast('No rows to export for this table.', true);
    return;
  }

  const a = api();
  if (a && a.export_reference_table && window.pywebview) {
    fire('export_reference_table', currentRefKind, fmt);
    return;
  }

  // Instant direct client download
  const headers = currentRefState.labels || [];
  const rows = currentRefState.rows || [];
  let csvContent = '\uFEFF' + headers.map(h => '"' + String(h).replace(/"/g, '""') + '"').join(',') + '\n';
  rows.forEach(r => {
    csvContent += r.map(c => '"' + String(c || '').replace(/"/g, '""') + '"').join(',') + '\n';
  });
  const filename = (currentRefState.title || currentRefKind || 'reference').toLowerCase().replace(/\s+/g, '-') + '.csv';
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.setAttribute('href', url);
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
  toast('Exported ' + rows.length + ' rows to ' + filename + '.', false);
}

function downloadInputTemplate(mode) {
  const a = api();
  if (a && a.save_input_template && window.pywebview) {
    fire('save_input_template', mode);
    return;
  }
  let headers = [];
  if (mode === 'land-sourcing') {
    headers = [
      'REPORT DATE', 'LEAD SOURCE', 'NAME OF PROPERTY OWNER / BROKER / AGENT',
      'CONTACT NUMBER', 'MUNICIPALITY / CITY', 'BARANGAY', 'AREA (SQM)',
      'NO. OF PARCEL', 'OFFER PRICE / SQM', 'MODE OF ACQUISITION',
      'DEVELOPMENT COST / SQM', 'TOTAL ESTIMATED COST / SQM', 'TOTAL PURCHASE PRICE',
      'COORDINATES (GOOGLE)', 'TAX DECLARATION NUMBER', 'LAND TITLE / OCT / TCT',
      'TOPOGRAPHY', 'ROAD ACCESS', 'DISTANCE TO HIGHWAY', 'INTEREST LEVEL',
      'STATUS / NEXT STEP', 'REMARKS', 'TEAM', 'GROUP', 'LAND SOURCING SPECIALIST'
    ];
  } else {
    headers = [
      'ID', 'NEGO DATE', 'STAGE', 'STATUS', 'REMARKS', 'AREA-INDEX', 'BARANGAY',
      'MUNICIPALITY', 'PROVINCE', 'LOT AREA (SQM)', 'NAME OF OWNER', 'TITLE / TD / CAD',
      'OCT / TCT / TAX DEC NUMBER', 'NO. OF PARCEL / TITLE', 'TYPE OF PROPERTY',
      'NEGOTIATOR NAME', 'TEAM', 'GROUP'
    ];
  }
  const csvContent = '\uFEFF' + headers.map(h => `"${h}"`).join(',') + '\n';
  const filename = `${mode}-input-template.csv`;
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.setAttribute('href', url);
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
  toast(`Downloaded ${filename}`, false);
}


// --- Dashboard Analytics (Parameterized Query) ------------------------------
let currentDashboardData = null;
let dbActiveMode = 'nego';
let dbDateFrom = '';
let dbDateTo = '';
let dbWorkWeekFilter = '';
let dbSelectedDate = null;   // when a day row is clicked, the muni table filters to it

// Populate the Work Week dropdown with the Wed-Tue work weeks the selected date
// range covers (calculated by the backend), preserving the current selection.
async function refreshDbWorkWeeks() {
  const sel = $('dbWwSelect');
  if (!sel) return;
  const prev = dbWorkWeekFilter || sel.value || '';
  let weeks = [];
  const a = api();
  if (a && a.get_range_work_weeks && (dbDateFrom || dbDateTo)) {
    try {
      const res = await a.get_range_work_weeks(dbDateFrom, dbDateTo);
      if (res && res.workWeeks) weeks = res.workWeeks;
    } catch (e) { /* ignore */ }
  }
  sel.innerHTML = '<option value="">All work weeks</option>';
  weeks.forEach(function (ww) { sel.appendChild(new Option(ww, ww)); });
  if (prev && weeks.indexOf(prev) !== -1) {
    sel.value = prev;
  } else {
    if (prev) dbWorkWeekFilter = '';
    sel.value = '';
  }
}

async function loadDashboardData() {
  const a = api();
  if (!a) return;
  refreshDbWorkWeeks();
  dbSelectedDate = null;
  const btnFetch = $('btnFetchDashboard');
  if (btnFetch) {
    btnFetch.disabled = true;
    btnFetch.innerHTML = `<span class="ico spinning" data-icon="refresh"></span> Querying…`;
    applyIcons(btnFetch);
  }
  setBusy(true);
  try {
    const queryParams = {
      date_from: dbDateFrom,
      date_to: dbDateTo,
      work_week: dbWorkWeekFilter,
      mode: 'all'  // fetch both; the Negotiation/Land Sourcing toggle filters client-side
    };
    const res = await a.get_dashboard_data(queryParams);
    if (!res || res.ok === false) {
      toast((res && res.error) || 'Failed to query dashboard data.', true);
      return;
    }
    currentDashboardData = res;
    renderDashboard();
    toast(`Fetched ${res.summary ? res.summary.total_records : 0} dashboard record(s).`, false);
  } catch (e) {
    toast(String(e), true);
  } finally {
    setBusy(false);
    if (btnFetch) {
      btnFetch.disabled = false;
      btnFetch.innerHTML = `<span class="ico" data-icon="cloud-download"></span> Fetch Data`;
      applyIcons(btnFetch);
    }
  }
}

function renderDashboard() {
  if (!currentDashboardData) return;
  const { summary, source, by_date, by_municipality } = currentDashboardData;
  if (!summary) return;

  const hasDateFilter = !!(dbDateFrom || dbDateTo || dbWorkWeekFilter);

  const totalElem = $('dbTotalBadge');
  if (totalElem) {
    totalElem.textContent = hasDateFilter ? `${summary.total_records || 0} Filtered Reports` : `${summary.total_records || 0} Reports`;
  }
  const srcElem = $('dbSourcePill'); if (srcElem) srcElem.textContent = source || 'Workspace & Dataverse';

  // Work Week dropdown is populated from the selected date range (Wed-Tue rule)
  // by refreshDbWorkWeeks(); nothing to derive from the returned data here.

  const filterPill = $('dbFilterStatusPill');
  if (filterPill) {
    if (hasDateFilter) {
      const parts = [];
      if (dbDateFrom && dbDateTo) parts.push(`${dbDateFrom} to ${dbDateTo}`);
      else if (dbDateFrom) parts.push(`From ${dbDateFrom}`);
      else if (dbDateTo) parts.push(`Up to ${dbDateTo}`);
      if (dbWorkWeekFilter) parts.push(`WW: ${dbWorkWeekFilter}`);
      filterPill.textContent = `Query: ${parts.join(' | ')}`;
      filterPill.classList.remove('hidden');
    } else {
      filterPill.classList.add('hidden');
    }
  }

  const cardNego = $('kpiCardNego');
  const cardSrc = $('kpiCardSourcing');
  if (cardNego) cardNego.classList.toggle('hidden', dbActiveMode === 'sourcing');
  if (cardSrc) cardSrc.classList.toggle('hidden', dbActiveMode === 'nego');

  const kpiNego = $('kpiNegoCount'); if (kpiNego) kpiNego.textContent = summary.total_nego || 0;
  const kpiNegoDate = $('kpiLatestNegoDate'); if (kpiNegoDate) kpiNegoDate.textContent = summary.latest_nego_date || '—';

  const kpiSrc = $('kpiSourcingCount'); if (kpiSrc) kpiSrc.textContent = summary.total_sourcing || 0;
  const kpiSrcDate = $('kpiLatestSourcingDate'); if (kpiSrcDate) kpiSrcDate.textContent = summary.latest_sourcing_date || '—';

  // Busiest day card — the date with the most reports in the current mode/filter.
  const dbCount = (r) => (dbActiveMode === 'sourcing') ? (r.sourcing_count || 0) : (r.nego_count || 0);
  let peak = null;
  (by_date || []).forEach(r => { const c = dbCount(r); if (c > 0 && (!peak || c > peak.c)) peak = { c: c, date: r.date }; });
  const kpiPeak = $('kpiPeakCount'); if (kpiPeak) kpiPeak.textContent = peak ? peak.c : 0;
  const kpiPeakDate = $('kpiPeakDate'); if (kpiPeakDate) kpiPeakDate.textContent = peak ? peak.date : '—';

  const kpiMuni = $('kpiMuniCount'); if (kpiMuni) kpiMuni.textContent = summary.total_municipalities || 0;
  const kpiTopMuni = $('kpiTopMuni'); if (kpiTopMuni) kpiTopMuni.textContent = summary.top_municipality || '—';

  renderDbDateTable();
  renderDbMuniTable();
}

function dbModeCount(r) { return (dbActiveMode === 'sourcing') ? (r.sourcing_count || 0) : (r.nego_count || 0); }

function renderDbDateTable() {
  if (!currentDashboardData || !currentDashboardData.by_date) return;
  const tbody = $('dbDateTbody');
  if (!tbody) return;

  const list = currentDashboardData.by_date.filter(r => dbModeCount(r) > 0);

  const countBadge = $('dbDateCount');
  if (countBadge) countBadge.textContent = `${list.length} date${list.length === 1 ? '' : 's'}`;

  if (!list.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty-cell">No records found matching query parameters.</td></tr>`;
    return;
  }

  const peak = list.reduce((mx, r) => Math.max(mx, dbModeCount(r)), 0) || 1;
  const frag = document.createDocumentFragment();
  list.forEach(r => {
    const total = dbModeCount(r);
    const share = Math.min(100, Math.max(4, Math.round(total / peak * 100)));
    const tr = document.createElement('tr');
    tr.className = 'db-click-row' + (dbSelectedDate === r.date ? ' db-row-selected' : '');
    tr.dataset.date = r.date;
    tr.innerHTML = `
      <td><strong>${escapeHtml(r.date)}</strong></td>
      <td><span class="db-ww-pill">${escapeHtml(r.work_week || '—')}</span></td>
      <td class="num"><strong>${total}</strong></td>
      <td class="bar-col">
        <div class="db-progress" title="${total} report(s) — click the row to see this day's per-municipality counts">
          <div class="db-progress-bar" style="width:${share}%"></div>
        </div>
      </td>`;
    frag.appendChild(tr);
  });
  tbody.innerHTML = '';
  tbody.appendChild(frag);

  tbody.onclick = (e) => {
    const row = e.target.closest('tr[data-date]');
    if (!row) return;
    dbSelectedDate = (dbSelectedDate === row.dataset.date) ? null : row.dataset.date;  // toggle
    renderDbDateTable();
    renderDbMuniTable();
  };
}

function renderDbMuniTable() {
  if (!currentDashboardData) return;
  const tbody = $('dbMuniTbody');
  if (!tbody) return;

  // Source list: a specific clicked day, or the whole filtered range.
  const dayMap = currentDashboardData.by_date_muni || {};
  let source = (dbSelectedDate && dayMap[dbSelectedDate]) ? dayMap[dbSelectedDate] : (currentDashboardData.by_municipality || []);
  let list = source.filter(m => dbModeCount(m) > 0);
  list = list.slice().sort((a, b) => dbModeCount(b) - dbModeCount(a));

  // Panel title + "Show all days" affordance reflect the selection.
  const title = $('dbMuniTitle');
  const clearBtn = $('dbMuniClearDay');
  if (title) title.textContent = dbSelectedDate ? `Count per municipality — ${dbSelectedDate}` : 'Count per municipality';
  if (clearBtn) clearBtn.classList.toggle('hidden', !dbSelectedDate);

  const countBadge = $('dbMuniCount');
  if (countBadge) countBadge.textContent = `${list.length} municipalit${list.length === 1 ? 'y' : 'ies'}`;

  if (!list.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty-cell">${dbSelectedDate ? 'No municipality records for this day.' : 'No municipality records matching query parameters.'}</td></tr>`;
    return;
  }

  const dayTotal = list.reduce((s, m) => s + dbModeCount(m), 0) || 1;
  const frag = document.createDocumentFragment();
  list.forEach(m => {
    const total = dbModeCount(m);
    const pct = Math.round(total / dayTotal * 1000) / 10;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>${escapeHtml(m.municipality)}</strong> ${m.province ? `<span class="muted font-xs">(${escapeHtml(m.province)})</span>` : ''}</td>
      <td><code>${escapeHtml(m.municode || '—')}</code></td>
      <td class="num"><strong>${total}</strong></td>
      <td class="bar-col">
        <div class="db-progress" title="${pct}% of ${dbSelectedDate ? 'the day' : 'total reports'}">
          <div class="db-progress-bar" style="width:${Math.min(100, Math.max(4, pct))}%"></div>
        </div>
      </td>`;
    frag.appendChild(tr);
  });
  tbody.innerHTML = '';
  tbody.appendChild(frag);
}


// --- Sort dropdown choices: UNASSIGNED first, then numbered numerically, then alphabetically ---
function sortChoices(choices) {
  if (!Array.isArray(choices) || choices.length === 0) return choices;
  const filtered = choices
    .map(c => String(c || '').trim().toUpperCase())
    .filter(c => c && c !== 'UNASSIGNED');
  const unique = [...new Set(filtered)];
  unique.sort((a, b) => {
    const numA = a.match(/(\d+)\s*$/);
    const numB = b.match(/(\d+)\s*$/);
    // Numbered entries always come before non-numbered
    if (numA && !numB) return -1;
    if (!numA && numB) return 1;
    // Both have numbers — sort by prefix alphabetically, then by number
    if (numA && numB) {
      const prefA = a.replace(/\d+\s*$/, '').trim();
      const prefB = b.replace(/\d+\s*$/, '').trim();
      if (prefA !== prefB) return prefA.localeCompare(prefB);
      return parseInt(numA[1], 10) - parseInt(numB[1], 10);
    }
    // Neither has a number — pure alphabetical
    return a.localeCompare(b);
  });
  return unique;
}

// --- Validation & Data Integrity Helpers ----------------------------------
function validateCellValue(val, meta, header) {
  const s = String(val == null ? '' : val).trim();
  const hUpper = String(header || '').toUpperCase();

  // 1. Required fields
  const isRequired = hUpper === 'AREA INDEX' || hUpper === 'AREA-INDEX' || hUpper === 'NEGO DATE' || hUpper === 'REPORT DATE' || hUpper === 'SOURCING DATE' || hUpper === 'TEAM' || hUpper === 'GROUP';
  if (isRequired && (!s || s.toUpperCase() === 'UNASSIGNED')) {
    return { valid: false, error: `'${header}' is required and cannot be empty.` };
  }
  if (!s) {
    return { valid: true };
  }

  // 2. Integer Numbers
  if (meta && meta.type === 'number') {
    const clean = s.replace(/,/g, '');
    if (!/^-?\d+$/.test(clean)) {
      return { valid: false, error: `'${header}': Expected whole number, got "${s}".` };
    }
  }

  // 3. Decimals / Floats / Currency
  if (meta && meta.type === 'decimal') {
    const clean = s.replace(/,/g, '');
    if (!/^-?\d+(\.\d+)?$/.test(clean)) {
      return { valid: false, error: `'${header}': Expected decimal number, got "${s}".` };
    }
  }

  // 4. Dates
  if ((meta && meta.type === 'date') || isDateColumn(header)) {
    if (isInvalidDateVal(s)) {
      return { valid: false, error: `'${header}': Invalid date format "${s}". Expected MM/DD/YYYY.` };
    }
    // Negotiation and sourcing record work already done, so a future value is a typo.
    if (isNoFutureDateColumn(header) && isFutureDateVal(s)) {
      return { valid: false, error: `'${header}': "${s}" is in the future. ${header} cannot be later than today.` };
    }
  }

  // 5. Choices / Picklists
  if (meta && meta.type === 'choice' && Array.isArray(meta.choices) && meta.choices.length > 0) {
    const upperVal = s.toUpperCase();
    if (hUpper !== 'TEAM' && hUpper !== 'GROUP') {
      if (upperVal === 'UNASSIGNED' || upperVal === '') return { valid: true };
    }
    if (hUpper === 'CHECKER') {
      if (upperVal === 'FIRST CONTACT' || upperVal === 'FOLLOW UP' || upperVal === 'REVISITED' || upperVal.includes('DUPLICATE')) {
        return { valid: true };
      }
    }
    const upperChoices = meta.choices.map(c => String(c).trim().toUpperCase()).filter(c => c && c !== 'UNASSIGNED');
    if (!upperChoices.includes(upperVal)) {
      if (hUpper === 'TEAM' || hUpper === 'GROUP') {
        return { valid: false, error: `'${header}': "${s}" is not found in Team Composition reference table.` };
      }
      return { valid: false, error: `'${header}': "${s}" is not an allowed choice.` };
    }
  }

  return { valid: true };
}

function countReviewTableValidationErrors(tableData) {
  if (!tableData || !tableData.rows || !tableData.headers) return 0;
  let count = 0;
  tableData.rows.forEach(row => {
    tableData.headers.forEach((h, cIdx) => {
      const meta = (tableData.meta && tableData.meta[h]) || { type: 'text' };
      const val = row[cIdx];
      const res = validateCellValue(val, meta, h);
      if (!res.valid) count++;
    });
  });
  return count;
}
window.validateCellValue = validateCellValue;
window.countReviewTableValidationErrors = countReviewTableValidationErrors;

function updateReviewValidationState() {
  const bar = $('reviewValidationBar');
  const btnCalc = $('btnCalculate');
  const btnGen = $('btnGenerate');
  const stripBtn = document.querySelector('#wfStripRun, .wf-strip-action');

  const errCount = countReviewTableValidationErrors(currentReviewTable);
  const buildErrs = (currentReviewTable && currentReviewTable.buildErrors) || [];
  const buildCount = buildErrs.length;

  if (bar) {
    if (errCount > 0 || buildCount > 0) {
      bar.classList.remove('hidden');
      const parts = [];
      if (errCount > 0) parts.push(`<strong>${errCount} invalid cell${errCount > 1 ? 's' : ''}</strong> (data type or required-field problems).`);
      if (buildCount > 0) parts.push(`<strong>${buildCount} Build Mismatch${buildCount > 1 ? 'es' : ''}</strong>: Record work-week does not match Build table.`);
      bar.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;width:100%;gap:12px;flex-wrap:wrap;">
          <div style="display:flex;align-items:center;gap:8px;">
            <span class="ico" data-icon="alert-triangle"></span>
            <span>${parts.join(' ')}</span>
          </div>
          ${buildCount > 0 ? `<button class="btn xs outline" id="btnUseCurrentBuildBanner" style="white-space:nowrap;cursor:pointer;">Use current build as reference</button>` : ''}
        </div>
      `;
      applyIcons(bar);
      const btnUseBanner = bar.querySelector('#btnUseCurrentBuildBanner');
      if (btnUseBanner) {
        btnUseBanner.addEventListener('click', async () => {
          const res = await call('calculate', true);
          if (res && res.table) renderReview(res.table);
        });
      }
    } else {
      bar.classList.add('hidden');
    }
  }

  const hasRows = !!(currentReviewTable && currentReviewTable.rows && currentReviewTable.rows.length > 0);
  if (btnCalc) btnCalc.disabled = !hasRows;

  // Generate is blocked until Calculate has run (step 2), and by invalid cells.
  // Build mismatches are a soft warning (confirm on click), so they don't disable.
  const hasCalculated = !!(lastState && lastState.hasCalculated);
  const genBlocked = (errCount > 0) || !hasRows || !hasCalculated;
  if (btnGen) btnGen.disabled = genBlocked;
  if (stripBtn) {
    stripBtn.disabled = genBlocked;
    if (!hasCalculated && hasRows) stripBtn.title = 'Click Calculate first';
    else if (errCount > 0) stripBtn.title = `Fix ${errCount} invalid cell(s) first`;
    else stripBtn.removeAttribute('title');
  }
}

function countProdTableValidationErrors(tableData) {
  if (!tableData || !tableData.rows) return 0;
  const cols = tableData.columns || tableData.labels || [];
  const meta = tableData.meta || {};
  let count = 0;
  tableData.rows.forEach(row => {
    cols.forEach((colKey, cIdx) => {
      const colMeta = meta[colKey] || { type: 'text' };
      const val = row[cIdx];
      const res = validateCellValue(val, colMeta, colKey);
      if (!res.valid) count++;
    });
  });
  return count;
}
window.countProdTableValidationErrors = countProdTableValidationErrors;

function updateProdValidationState() {
  const bar = $('prodValidationBar');
  const modalConfirm = $('modalPublishConfirm');
  const btnPublish = $('btnPublish');

  const errCount = countProdTableValidationErrors(prodTable);

  if (bar) {
    if (errCount > 0) {
      bar.classList.remove('hidden');
      bar.innerHTML = `<span class="ico" data-icon="alert-triangle"></span> <span><strong>${errCount} invalid cell${errCount > 1 ? 's' : ''} detected</strong> with data type mismatches. Fix all red cells before publishing.</span>`;
      applyIcons(bar);
    } else {
      bar.classList.add('hidden');
    }
  }

  const blocked = errCount > 0;
  if (modalConfirm) modalConfirm.disabled = blocked;
  if (btnPublish) btnPublish.disabled = blocked;
  // Refresh the workflow strip so "Continue to publish" re-evaluates its
  // enabled state (it also blocks on non-validation issues like unassigned
  // matches). scheduleShellSync is the exposed entry point; syncStrip is not.
  if (window.scheduleShellSync) window.scheduleShellSync();
  else if (window.syncStrip) window.syncStrip();
}

// --- Dataverse Reference Table Selection & State ---------------------------
let activeSolutionTables = [];
let selectedTableMappings = { team: '', municipality: '', mapping: '', build: '', existing: '' };
let unfetchedRefKinds = new Set();

let selectedReferenceKinds = { team: true, municipality: true, mapping: true, build: true, existing: true };
let dvBootstrapped = false;

function updateUploadActionState(s) {
  if (!s) s = (typeof lastState !== 'undefined' ? lastState : null) || {};
  const actionRow = $('inputActionRow');
  const statusPill = $('inputLoadedStatus');
  const btnInput = $('btnInput');
  const actionTitle = $('inputActionTitle');
  const actionSub = $('inputActionSub');

  const counts = s.refCounts || {};
  const totalRefRows = (counts.teams || 0) + (counts.municipality || 0) + (counts.mapping || 0) + (counts.build || 0) + (counts.existing || 0);
  const hasUnfetched = unfetchedRefKinds.size > 0;
  const isLoading = (!!s.refTablesLoading || !!s.isBusy) && totalRefRows === 0;
  const refsLoaded = totalRefRows > 0 && !hasUnfetched;

  if (actionRow) {
    actionRow.classList.toggle('disabled', !refsLoaded);
    if (btnInput) btnInput.disabled = !refsLoaded;
    if (isLoading) {
      if (actionTitle) {
        actionTitle.innerHTML = '<span class="ico spinning" data-icon="refresh"></span> Loading reference tables from Dataverse…';
        applyIcons(actionTitle);
      }
      if (actionSub) actionSub.textContent = 'Please wait while reference tables are being loaded';
    } else if (!refsLoaded) {
      if (hasUnfetched) {
        if (actionTitle) actionTitle.textContent = 'Fetch reference table first';
        if (actionSub) actionSub.textContent = 'Fetch the newly selected table in Section A before uploading a transaction file';
      } else {
        if (actionTitle) actionTitle.textContent = 'Load reference tables above first';
        if (actionSub) actionSub.textContent = 'Fetch or select tables in Section A before uploading a transaction file';
      }
    } else {
      if (actionTitle) actionTitle.textContent = 'Drop a file here, or click to browse';
      if (actionSub) actionSub.textContent = '.csv \u00b7 .xlsx \u00b7 .xlsm';
    }
  }

  if (s.inputName) {
    if (actionRow) actionRow.classList.add('hidden');
    if (statusPill) {
      statusPill.classList.remove('hidden');
      const fn = $('inputFileName'); if (fn) fn.textContent = s.inputName;
      const mb = $('inputModeBadge'); if (mb) mb.textContent = s.mode === 'land-sourcing' ? 'Land Sourcing' : 'Negotiation';
      const rb = $('inputFileRowsBadge'); if (rb) rb.textContent = `${s.rowCount || 0} rows`;
    }
  } else {
    if (actionRow) actionRow.classList.remove('hidden');
    if (statusPill) statusPill.classList.add('hidden');
  }

  if (window.syncSteps) window.syncSteps();
}
window.updateUploadActionState = updateUploadActionState;

function getTablesForKind(kind, mode) {
  if (!activeSolutionTables || !activeSolutionTables.length) return [];
  if (kind === 'build') {
    return activeSolutionTables.filter(t => {
      const dn = (t.displayName || '').toUpperCase();
      const ln = (t.logicalName || '').toUpperCase();
      return dn.includes('BUILD') || ln.includes('BUILD');
    });
  }
  if (kind === 'existing') {
    const isSourcing = (mode === 'land-sourcing');
    return activeSolutionTables.filter(t => {
      const dn = (t.displayName || '').toUpperCase();
      const ln = (t.logicalName || '').toUpperCase();
      if (isSourcing) {
        return dn.includes('SOURCING RECORD') || dn.includes('SOURCINGRECORD') || ln.includes('SOURCINGRECORD') ||
               (dn.includes('SOURCING') && dn.includes('RECORD')) || (ln.includes('SOURCING') && ln.includes('RECORD'));
      } else {
        return dn.includes('NEGO RECORD') || dn.includes('NEGORECORD') || ln.includes('NEGORECORD') ||
               (dn.includes('NEGO') && dn.includes('RECORD')) || (ln.includes('NEGO') && ln.includes('RECORD'));
      }
    });
  }
  if (kind === 'productivity') {
    // Stage 5 target: nego-productivity for negotiation, sourcing-productivity for land sourcing.
    const isSourcing = (mode === 'land-sourcing');
    const filtered = activeSolutionTables.filter(t => {
      const dn = (t.displayName || '').toUpperCase();
      const ln = (t.logicalName || '').toUpperCase();
      if (!(dn.includes('PRODUCTIVITY') || ln.includes('PRODUCTIVITY'))) return false;
      if (isSourcing) return dn.includes('SOURCING') || ln.includes('SOURCING');
      return dn.includes('NEGO') || ln.includes('NEGO');
    });
    // Fall back to any productivity table if the nego/sourcing split does not match a table name.
    if (filtered.length) return filtered;
    return activeSolutionTables.filter(t =>
      (t.displayName || '').toUpperCase().includes('PRODUCTIVITY') ||
      (t.logicalName || '').toUpperCase().includes('PRODUCTIVITY'));
  }
  return activeSolutionTables;
}

// Given a selected record ("... RECORD") table, find the matching productivity
// ("... PRODUCTIVITY") table in the same solution — e.g. BATANGAS NEGO RECORD ->
// BATANGAS NEGO PRODUCTIVITY. Returns '' when there is no clean match.
function deriveProductivityForRecord(recordLogical, prodList) {
  if (!recordLogical) return '';
  const list = (prodList && prodList.length) ? prodList : (activeSolutionTables || []);
  const recTable = (activeSolutionTables || []).find(
    t => (t.logicalName || '').toLowerCase() === recordLogical.toLowerCase());
  const norm = s => (s || '').toUpperCase().replace(/\s+/g, ' ').trim();

  // 1) Match on display name: "... RECORD" -> "... PRODUCTIVITY".
  if (recTable && recTable.displayName) {
    const want = norm(recTable.displayName).replace(/RECORDS?/g, 'PRODUCTIVITY');
    const hit = list.find(t => norm(t.displayName) === want && (t.logicalName || '').toLowerCase().includes('productivity'));
    if (hit) return hit.logicalName;
  }

  // 2) Match on logical name: "...record..." -> "...productivity...".
  const wantLogical = recordLogical.toLowerCase().replace(/records?/g, 'productivity');
  const hitL = list.find(t => (t.logicalName || '').toLowerCase() === wantLogical);
  if (hitL) return hitL.logicalName;

  return '';
}




// --- state ------------------------------------------------------------------
function applyState(s) {
  if (!s) return;
  if (s.state) s = s.state;
  lastState = s;
  window.lastState = s;
  window.applyState = applyState;
  negotiatorOptions = s.negotiatorOptions || [];

  if (s.bootNotices && s.bootNotices.length) s.bootNotices.forEach(m => toast(m, true));
  document.querySelectorAll('#modeToggle button').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === s.mode);
  });

  // Restore uiSettings from backend (persisted in app_settings.json)
  if (s.uiSettings) {
    if (s.uiSettings.column_widths) {
      Object.keys(s.uiSettings.column_widths).forEach(k => {
        try {
          const savedMap = s.uiSettings.column_widths[k] || {};
          const localRaw = localStorage.getItem(`col_widths_${k}`);
          const localMap = localRaw ? JSON.parse(localRaw) : {};
          const merged = { ...savedMap, ...localMap };
          if (merged['ACTION'] && merged['ACTION'] < 100) merged['ACTION'] = 160;
          if (merged['ACTION ITEM'] && merged['ACTION ITEM'] < 100) merged['ACTION ITEM'] = 160;
          localStorage.setItem(`col_widths_${k}`, JSON.stringify(merged));
        } catch (e) {}
      });
    }
    if (s.uiSettings.header_heights) {
      Object.keys(s.uiSettings.header_heights).forEach(k => {
        if (!localStorage.getItem(`header_height_${k}`)) {
          localStorage.setItem(`header_height_${k}`, String(s.uiSettings.header_heights[k]));
        }
      });
    }
    const curMode = ((s.mode || 'negotiation') + '').toLowerCase();
    const modeKey = (curMode === 'land-sourcing') ? 'sourcing' : 'nego';
    if (s.uiSettings[`${modeKey}_productivity_table`]) {
      selectedProdTargetTable = s.uiSettings[`${modeKey}_productivity_table`];
    } else if (s.uiSettings.productivity_table && !selectedProdTargetTable) {
      selectedProdTargetTable = s.uiSettings.productivity_table;
    }
    // Only hydrate the record-table selection from saved settings when the
    // in-session selection is empty or belongs to the other mode. Otherwise a
    // routine state push (e.g. the response to fetching Bulacan) would clobber
    // the user's current choice and snap the dropdown back to the saved default.
    const curExisting = (selectedTableMappings.existing || '').toLowerCase();
    const curValidForMode = !!curExisting && (modeKey === 'sourcing'
      ? curExisting.includes('sourcing')
      : (curExisting.includes('nego') && !curExisting.includes('sourcing')));
    if (!curValidForMode) {
      if (s.uiSettings[`${modeKey}_record_table`]) {
        selectedTableMappings.existing = s.uiSettings[`${modeKey}_record_table`];
      } else if (s.uiSettings.existing_table && !selectedTableMappings.existing) {
        // Legacy mode-agnostic fallback: only apply it if it matches the current
        // mode, so a Nego record table never lands in Land Sourcing (or vice versa).
        const legacy = (s.uiSettings.existing_table + '').toLowerCase();
        const matchesMode = modeKey === 'sourcing' ? legacy.includes('sourcing') : legacy.includes('nego');
        if (matchesMode) selectedTableMappings.existing = s.uiSettings.existing_table;
      }
    }
    // Never let a record table from the OTHER mode linger (e.g. a Nego record
    // table selected while in Land Sourcing). Clears to "— Default —" if mismatched.
    if (selectedTableMappings.existing) {
      const ln = selectedTableMappings.existing.toLowerCase();
      const wrongMode = (modeKey === 'sourcing')
        ? (ln.includes('nego') && !ln.includes('sourcing'))
        : (ln.includes('sourcing') && !ln.includes('nego'));
      if (wrongMode) selectedTableMappings.existing = '';
    }
    // Restore the other reference-table dropdowns (team/municipality/mapping/
    // build) for this mode — only when nothing is selected yet, so a routine
    // state push never clobbers the user's current choice.
    ['team', 'municipality', 'mapping', 'build'].forEach(k => {
      if (selectedTableMappings[k]) return;
      let saved = s.uiSettings[`${modeKey}_${k}_table`] || '';
      if (!saved) { try { saved = localStorage.getItem(`dv_${modeKey}_${k}_table`) || ''; } catch (e) {} }
      if (saved) selectedTableMappings[k] = saved;
    });
  }


  // dataverse
  const dvStatus = $('dvStatus');
  const solSelect = $('refSolutionSelect');
  const btnFetchAll = $('btnFetchAllDv');
  if (s.connected) {
    dvStatus.textContent = 'Connected';
    dvStatus.className = 'dv-badge connected';
    $('deviceCode').classList.add('hidden');
    if (btnFetchAll) {
      btnFetchAll.disabled = false;
      btnFetchAll.innerHTML = `<span class="ico" data-icon="cloud-download"></span> Fetch Selected Tables`;
    }
    if (solSelect) solSelect.disabled = false;
    if (!dvBootstrapped) {
      dvBootstrapped = true;
      bootstrapDataverse();
    }
  } else {
    dvStatus.textContent = 'Not connected';
    dvStatus.className = 'dv-badge';
    if (btnFetchAll) {
      btnFetchAll.disabled = true;
      btnFetchAll.innerHTML = `<span class="ico" data-icon="cloud-download"></span> Fetch Selected Tables`;
    }
    if (solSelect) { solSelect.disabled = true; solSelect.innerHTML = '<option value="">Connect to Dataverse…</option>'; }
    dvBootstrapped = false;
  }

  $('sideDvDot').classList.toggle('on', !!s.connected);
  $('sideDvText').textContent = s.connected ? 'Dataverse: connected' : 'Dataverse: not connected';

  // reference cards
  renderRefGrid(s);

  // upload file status display
  updateUploadActionState(s);

  // if the reference viewer is open, refresh its rows too
  if ($('view-ref').classList.contains('active') && currentRefKind) {
    const a = api(); if (a) a.get_reference_rows(currentRefKind).then(renderReferenceView);
  }

  // counts
  const lrc = $('loadRowCount'); if (lrc) lrc.textContent = `${s.rowCount||0} rows`;
  const rvc = $('reviewCount'); if (rvc) rvc.textContent = `${s.rowCount||0} rows`;
  const prc = $('prodCount'); if (prc) prc.textContent = `${s.productivityCount||0} rows`;
  const btnCalc = $('btnCalculate'); if (btnCalc) btnCalc.disabled = !(s.rowCount > 0);
  const btnSaveSrc = $('btnSaveSource'); if (btnSaveSrc) btnSaveSrc.disabled = !(s.rowCount > 0 && s.canSaveToSource);
  const btnGen = $('btnGenerate'); if (btnGen) btnGen.disabled = !(s.rowCount > 0);
  const hasProd = (s.productivityCount||0) > 0;
  const btnPublishRev = $('btnPublishReview'); if (btnPublishRev) btnPublishRev.disabled = !s.connected || !(s.rowCount > 0);
  const btnPublishProd = $('btnPublishProd'); if (btnPublishProd) btnPublishProd.disabled = !s.connected || !hasProd;
  const btnClrFilters = $('btnClearFilters'); if (btnClrFilters) btnClrFilters.disabled = !hasProd;

  // Populate Productivity Target Select if tables are available
  const prodTblSel = $('prodTargetTableSelect');
  if (prodTblSel && activeSolutionTables && activeSolutionTables.length) {
    const cur = prodTblSel.value || selectedProdTargetTable || localStorage.getItem('dv_prod_target_table') || '';
    prodTblSel.innerHTML = '<option value="">Select Productivity Table…</option>';
    activeSolutionTables.forEach(tbl => {
      const opt = new Option(tbl.displayName || tbl.logicalName, tbl.logicalName);
      prodTblSel.appendChild(opt);
    });

    if (cur && activeSolutionTables.some(t => t.logicalName === cur)) {
      prodTblSel.value = cur;
    } else {
      const best = activeSolutionTables.find(t => t.logicalName.toLowerCase().includes('productivity') || t.displayName.toLowerCase().includes('productivity'));
      if (best) {
        prodTblSel.value = best.logicalName;
        selectedProdTargetTable = best.logicalName;
      }
    }
  }

  if ($('view-dashboard') && $('view-dashboard').classList.contains('active')) {
    loadDashboardData();
  }

  updateReviewValidationState();
  updateProdValidationState();
  applyIcons(document);
}


async function bootstrapDataverse() {
  const a = api();
  if (!a) return;
  setBusy(true);
  try {
    const res = await a.get_dataverse_bootstrap_data();
    if (!res || res.ok === false) {
      toast((res && res.error) || 'Failed to fetch Dataverse solutions.', true);
      return;
    }
    const solSelect = $('refSolutionSelect');
    if (solSelect) {
      solSelect.innerHTML = '<option value="">All Custom / Default Tables</option>';
      (res.solutions || []).forEach(sol => {
        const opt = new Option(`${sol.friendlyName} (${sol.uniqueName})`, sol.id);
        solSelect.appendChild(opt);
      });
      if (res.selectedSolutionId) {
        solSelect.value = res.selectedSolutionId;
      }
    }
    activeSolutionTables = res.tables || [];
    if (res.suggestedMappings) {
      // Keep the user's already-selected (restored) tables — suggestions only
      // fill the kinds that are still unset, so a remembered choice isn't lost.
      const kept = {};
      Object.keys(selectedTableMappings).forEach(k => { if (selectedTableMappings[k]) kept[k] = selectedTableMappings[k]; });
      selectedTableMappings = { ...res.suggestedMappings, ...kept };
    }
    if (res.configuredProductivityTable) {
      selectedProdTargetTable = res.configuredProductivityTable;
      localStorage.setItem('dv_prod_target_table', res.configuredProductivityTable);
    } else if (res.suggestedMappings && res.suggestedMappings.productivity) {
      selectedProdTargetTable = res.suggestedMappings.productivity;
    }
    renderRefGrid(lastState);
  } catch (e) {
    toast(String(e), true);
  } finally {
    setBusy(false);
  }
}

async function onSolutionChange(solutionId) {
  const a = api();
  if (!a) return;
  setBusy(true);
  try {
    let solName = '';
    const solSelect = $('refSolutionSelect') || $('setSolutionSelect');
    if (solSelect && solSelect.options && solSelect.selectedIndex >= 0) {
      const opt = solSelect.options[solSelect.selectedIndex];
      solName = (opt && opt.value) ? opt.text : '';
    }
    if (a.set_solution_name) {
      a.set_solution_name(solName, solutionId || '');
    }
    const res = await a.get_solution_tables(solutionId || null);
    if (res && res.tables) {
      activeSolutionTables = res.tables;
      // auto-suggest matches for this solution
      const tableLogicals = activeSolutionTables.map(t => t.logicalName.toLowerCase());
      const findBest = (...kws) => {
        for (const kw of kws) {
          const match = activeSolutionTables.find(t => t.logicalName.toLowerCase().includes(kw));
          if (match) return match.logicalName;
        }
        return '';
      };
      selectedTableMappings.team = findBest('teamcomposition', 'team') || selectedTableMappings.team;
      selectedTableMappings.municipality = findBest('municipalitycode', 'municipality', 'municode') || selectedTableMappings.municipality;
      selectedTableMappings.mapping = findBest('mappingstatus', 'mapping') || selectedTableMappings.mapping;
      selectedTableMappings.build = findBest('batangasbuild', 'areaindexbuildinfo', 'build') || selectedTableMappings.build;
      if (lastState && lastState.mode === 'land-sourcing') {
        selectedTableMappings.existing = findBest('batangassourcingrecord', 'sourcingrecord', 'sourcing record') || selectedTableMappings.existing;
      } else {
        selectedTableMappings.existing = findBest('batangasnegorecord', 'negorecord', 'nego record') || selectedTableMappings.existing;
      }
      const bestProd = findBest('negoproductivity', 'sourcingproductivity', 'productivity', 'prod');
      if (bestProd) {
        selectedProdTargetTable = bestProd;
      }
      REF_TABLES.forEach(({ kind }) => unfetchedRefKinds.add(kind));
      if (lastState && lastState.refCounts) {
        lastState.refCounts = { teams: 0, municipality: 0, mapping: 0, build: 0, existing: 0 };
      }
      if (lastState && lastState.refSources) {
        lastState.refSources = {};
      }
      renderRefGrid(lastState);
      updateUploadActionState(lastState);
    }
  } catch (e) {
    toast(String(e), true);
  } finally {
    setBusy(false);
  }
}


function renderRecentSelect(select, entries, activePath, withMode) {
  if (!select) return;
  const current = select.value;
  select.innerHTML = '';
  select.appendChild(new Option(entries.length ? 'Recent files…' : 'No recent files', ''));
  entries.forEach(e => {
    const label = withMode && e.mode ? `${e.name}  ·  ${e.mode === 'land-sourcing' ? 'Land Sourcing' : 'Negotiation'}` : e.name;
    const o = new Option(label, e.path);
    o.title = e.path;
    if (e.path === activePath) o.selected = true;
    select.appendChild(o);
  });
  if (!activePath && entries.some(e => e.path === current)) select.value = current;
}

function renderRefGrid(s) {
  const grid = $('refGrid');
  if (!grid) return;
  const counts = s ? (s.refCounts || {}) : {};
  const sources = s ? (s.refSources || {}) : {};
  const countKey = { municipality: 'municipality', team: 'teams', mapping: 'mapping', build: 'build', existing: 'existing' };
  grid.innerHTML = '';

  REF_TABLES.forEach(({ kind, name, icon, initial }) => {
    const isUnfetched = unfetchedRefKinds.has(kind);
    const n = isUnfetched ? 0 : (counts[countKey[kind]] || 0);
    const src = isUnfetched ? '' : (sources[kind] || '');
    const isIncluded = selectedReferenceKinds[kind] !== false;

    // The logical name is only shown if the table has been fetched/loaded (n > 0 and src exists)
    let logicalName = '';
    if (n > 0 && src) {
      if (src.toLowerCase().startsWith('dataverse:')) {
        logicalName = src.replace(/^dataverse:\s*/i, '').trim();
      } else if (src.toLowerCase().startsWith('file:')) {
        logicalName = src.replace(/^file:\s*/i, '').trim();
      } else if (src.toLowerCase() === 'dataverse (live)') {
        logicalName = selectedTableMappings[kind] || '';
      } else {
        logicalName = src;
      }
    }

    // build table options
    let optionsHtml = '<option value="">— Default Dataverse Table —</option>';
    const filteredTables = getTablesForKind(kind, s ? s.mode : 'negotiation');
    if (filteredTables && filteredTables.length) {
      const hasValidSelection = filteredTables.some(t => (selectedTableMappings[kind] || '').toLowerCase() === t.logicalName.toLowerCase());
      if (!hasValidSelection) {
        selectedTableMappings[kind] = filteredTables[0].logicalName;
      }
      filteredTables.forEach(t => {
        const isSel = (selectedTableMappings[kind] || '').toLowerCase() === t.logicalName.toLowerCase();
        optionsHtml += `<option value="${escapeHtml(t.logicalName)}" ${isSel ? 'selected' : ''}>${escapeHtml(t.displayName)}</option>`;
      });
    } else if (selectedTableMappings[kind]) {
      const match = (activeSolutionTables || []).find(t => t.logicalName.toLowerCase() === selectedTableMappings[kind].toLowerCase());
      const label = match ? match.displayName : selectedTableMappings[kind];
      optionsHtml += `<option value="${escapeHtml(selectedTableMappings[kind])}" selected>${escapeHtml(label)}</option>`;
    }

    const card = document.createElement('div');
    card.className = `ref-card ref-row ${isIncluded ? '' : 'excluded'}`;
    card.innerHTML = `
      <label class="rr-check-cell">
        <input type="checkbox" class="rc-check" data-ref-check="${kind}" ${isIncluded ? 'checked' : ''} title="Include in Fetch Selected" />
        <span class="rr-box"></span>
      </label>
      <div class="rr-name-cell">
        <span class="rr-chip">${escapeHtml(initial)}</span>
        <span class="rr-name">${escapeHtml(name)}</span>
      </div>
      <div class="rr-table-cell">
        <select class="rr-select" data-ref-select="${kind}" ${s && s.connected ? '' : 'disabled'}>
          ${optionsHtml}
        </select>
      </div>
      <div class="rr-last-cell">
        <span class="rr-last ${n ? 'ok' : ''}">${n ? n + ' rows' : 'Not loaded'}</span>
      </div>
      <div class="rr-logical-cell ${logicalName ? '' : 'rr-logical-empty'}" data-ref-logical="${kind}" title="${escapeHtml(logicalName)}">${escapeHtml(logicalName || '—')}</div>
      <div class="rr-act-cell">
        <button class="btn xs" data-fetch-single="${kind}" ${s && s.connected ? '' : 'disabled'} title="Fetch only this table live from Dataverse">Fetch</button>
        ${kind === 'build'
          ? `<button class="btn xs" data-build-sync="1" title="Upload a Build file and sync it to the selected Dataverse Build table (insert new, overwrite on newer Work Week, never delete)">Upload</button>`
          : `<button class="btn xs" data-upload-ref="${kind}" title="Upload Excel or CSV file to replace records">Upload</button>`}
        <button class="btn xs ghost" data-view-ref="${kind}">View</button>
      </div>
    `;
    grid.appendChild(card);
  });

  // check handlers
  grid.querySelectorAll('[data-ref-check]').forEach(cb => {
    cb.addEventListener('change', () => {
      selectedReferenceKinds[cb.dataset.refCheck] = cb.checked;
      const card = cb.closest('.ref-card');
      if (card) card.classList.toggle('excluded', !cb.checked);
    });
  });

  // table select handlers
  grid.querySelectorAll('[data-ref-select]').forEach(sel => {
    sel.addEventListener('change', async () => {
      const kind = sel.dataset.refSelect;
      const newLogical = sel.value;
      selectedTableMappings[kind] = newLogical;
      unfetchedRefKinds.add(kind);

      // Persist EVERY table dropdown choice (team/municipality/mapping/build/
      // existing) for this mode so it survives state re-hydration and future
      // sessions. Without this, applyState()/restart reverts the dropdowns.
      const modeKey = (lastState && lastState.mode === 'land-sourcing') ? 'sourcing' : 'nego';
      try { localStorage.setItem(`dv_${modeKey}_${kind}_table`, newLogical); } catch (e) {}
      if (lastState) {
        lastState.uiSettings = lastState.uiSettings || {};
        lastState.uiSettings[`${modeKey}_${kind}_table`] = newLogical;
      }
      const uiUpdate = {};
      uiUpdate[`${modeKey}_${kind}_table`] = newLogical;

      if (kind === 'existing') {
        uiUpdate['existing_table'] = newLogical;
        uiUpdate[`${modeKey}_record_table`] = newLogical;
        if (lastState) lastState.uiSettings[`${modeKey}_record_table`] = newLogical;
        try { localStorage.setItem(`dv_${modeKey}_record_table`, newLogical); } catch (e) {}

        // Auto-select the matching productivity table for the chosen record
        // region (e.g. BATANGAS NEGO RECORD -> BATANGAS NEGO PRODUCTIVITY).
        const prodList = getTablesForKind('productivity', (lastState && lastState.mode) || 'negotiation');
        const derivedProd = deriveProductivityForRecord(newLogical, prodList);
        if (derivedProd) {
          selectedProdTargetTable = derivedProd;
          try {
            localStorage.setItem(`dv_${modeKey}_prod_target_table`, derivedProd);
            localStorage.setItem('dv_prod_target_table', derivedProd);
          } catch (e) {}
          if (lastState) lastState.uiSettings[`${modeKey}_productivity_table`] = derivedProd;
          uiUpdate[`${modeKey}_productivity_table`] = derivedProd;
          toast(`Productivity target set to ${derivedProd}.`, false);
        }
      }
      call('save_ui_settings', uiUpdate);

      // Clear record count and logical name in local state
      if (lastState && lastState.refCounts) {
        lastState.refCounts[countKey[kind]] = 0;
      }
      if (lastState && lastState.refSources) {
        delete lastState.refSources[kind];
      }

      // Immediately clear record count and logical name on the card UI
      const card = sel.closest('.ref-card');
      if (card) {
        const lastCell = card.querySelector('.rr-last');
        if (lastCell) {
          lastCell.textContent = 'Not loaded';
          lastCell.classList.remove('ok');
        }
        const logCell = card.querySelector(`[data-ref-logical="${kind}"]`);
        if (logCell) {
          logCell.textContent = '—';
          logCell.title = '';
          logCell.classList.add('rr-logical-empty');
        }
      }

      // Disable uploading of transaction data file until fetched
      updateUploadActionState(lastState);

      // Call backend to clear in-memory records
      try {
        const a = api();
        if (a && a.clear_reference_table) {
          const res = await a.clear_reference_table(kind);
          if (res && res.state) {
            lastState = res.state;
            updateUploadActionState(lastState);
          }
        }
      } catch (e) {
        console.warn('clear_reference_table error:', e);
      }
    });
  });

  // single fetch handlers
  grid.querySelectorAll('[data-fetch-single]').forEach(b => {
    b.addEventListener('click', () => {
      const kind = b.dataset.fetchSingle;
      const logical = selectedTableMappings[kind] || '';
      b.disabled = true;
      b.innerHTML = `<span class="ico spinning" data-icon="refresh"></span> Fetching…`;
      applyIcons(b);
      const card = b.closest('.ref-card');
      if (card) card.classList.add('loading-pulse');
      toast(`Fetching ${REF_NAME[kind] || kind} live from Dataverse…`, false);
      fire('fetch_dataverse_tables', { [kind]: logical }, [kind]);
    });
  });

  // upload handlers
  grid.querySelectorAll('[data-upload-ref]').forEach(b => {
    b.addEventListener('click', () => {
      const kind = b.dataset.uploadRef;
      fire('upload_reference_file', kind);
    });
  });

  // Build "Sync" handler (replaces Upload for the Build row)
  grid.querySelectorAll('[data-build-sync]').forEach(b => {
    b.addEventListener('click', () => {
      if (!(lastState && lastState.connected)) { toast('Connect to Dataverse first.', true); return; }
      fire('preview_build_sync', selectedTableMappings.build || '');
    });
  });

  // view handlers
  grid.querySelectorAll('[data-view-ref]').forEach(b => {
    b.addEventListener('click', () => showReference(b.dataset.viewRef));
  });

  applyIcons(grid);
}



// --- review / productivity tables (Fixed-Width Spreadsheet Layout) -------------------------------------------
let currentReviewTable = null;
let reviewFilters = {};
let reviewFilterTimer = null;
let openReviewFilterCols = new Set();
let reviewIssuesOnly = false;   // when true, show only rows with validation/build issues

let prodTable = null;
let prodFilters = {};
let prodIssuesOnly = false;   // when true, show only productivity rows with issues
let filterTimer = null;
let openProdFilterCols = new Set();

let selectedProdTargetTable = localStorage.getItem('dv_prod_target_table') || '';
let uiSaveTimer = null;

function scheduleSaveUiSettings(data) {
  clearTimeout(uiSaveTimer);
  uiSaveTimer = setTimeout(() => {
    call('save_ui_settings', data);
  }, 350);
}

function getSavedFrozenCols(tableKey) {
  try {
    const raw = localStorage.getItem(`frozen_cols_${tableKey}`);
    if (raw) {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) return new Set(arr);
    }
  } catch (e) {}
  return new Set();
}

function setSavedFrozenCols(tableKey, set) {
  try {
    const arr = Array.from(set);
    localStorage.setItem(`frozen_cols_${tableKey}`, JSON.stringify(arr));
    scheduleSaveUiSettings({ frozen_columns: { [tableKey]: arr } });
  } catch (e) {}
}

let frozenReviewCols = getSavedFrozenCols('review');
let frozenProdCols = getSavedFrozenCols('prod');

function getSavedColWidth(tableKey, headerName, defaultWidth) {
  try {
    const raw = localStorage.getItem(`col_widths_${tableKey}`);
    if (raw) {
      const map = JSON.parse(raw);
      if (map && map[headerName]) {
        const parsed = parseInt(map[headerName], 10);
        const norm = String(headerName || '').trim().toUpperCase();
        if ((norm === 'ACTION' || norm === 'ACTION ITEM') && parsed < 100) {
          return defaultWidth;
        }
        return Math.max(50, Math.min(800, parsed));
      }
    }
  } catch (e) {}
  return defaultWidth;
}

function setSavedColWidth(tableKey, headerName, width) {
  try {
    const raw = localStorage.getItem(`col_widths_${tableKey}`) || '{}';
    const map = JSON.parse(raw);
    map[headerName] = width;
    localStorage.setItem(`col_widths_${tableKey}`, JSON.stringify(map));
    scheduleSaveUiSettings({ column_widths: { [tableKey]: map } });
  } catch (e) {}
}

function getSavedHeaderHeight(tableKey, defaultHeight = 44) {
  try {
    const raw = localStorage.getItem(`header_height_${tableKey}`);
    if (raw) {
      const h = parseInt(raw, 10);
      if (h >= 32 && h <= 240) return h;
    }
  } catch (e) {}
  return defaultHeight;
}

function setSavedHeaderHeight(tableKey, height) {
  try {
    const h = Math.max(32, Math.min(240, parseInt(height, 10)));
    localStorage.setItem(`header_height_${tableKey}`, String(h));
    scheduleSaveUiSettings({ header_heights: { [tableKey]: h } });
  } catch (e) {}
}

function isCompactCol(headerName) {
  const norm = String(headerName || '').trim().toUpperCase();
  return norm === 'ID' || norm === '#' || norm === 'TEAM' || norm === 'GROUP' || norm === 'CORRECT TEAM' || norm === 'CORRECT GROUP' || norm === 'ACT' || norm === 'ACTION_BTN';
}

function getColWidth(headerName, tableKey = 'review') {
  const norm = String(headerName || '').trim().toUpperCase();
  let defaultWidth = 176;
  if (norm === 'ACT' || norm === 'ACTION_BTN' || norm === 'ACTIONS') {
    defaultWidth = 60;
  } else if (norm === 'ACTION' || norm === 'ACTION ITEM') {
    defaultWidth = 160;
  } else if (norm === 'ID' || norm === '#') {
    defaultWidth = 90;
  } else if (norm === 'TEAM' || norm === 'GROUP' || norm === 'CORRECT TEAM' || norm === 'CORRECT GROUP') {
    defaultWidth = 135;
  } else if (isCompactCol(norm)) {
    defaultWidth = 120;
  }
  return getSavedColWidth(tableKey, headerName, defaultWidth);
}

function getFrozenColOffset(headers, colIdx, tableKey = 'review') {
  const frozenSet = (tableKey === 'prod') ? frozenProdCols : frozenReviewCols;
  const colName = headers[colIdx];
  if (!frozenSet.has(colName)) return 0;

  let left = 0;
  for (let c = 0; c < headers.length; c++) {
    const h = headers[c];
    if (h === colName) break;
    if (frozenSet.has(h)) {
      left += getColWidth(h, tableKey);
    }
  }
  return left;
}

function isLastFrozenCol(headers, colIdx, tableKey = 'review') {
  const frozenSet = (tableKey === 'prod') ? frozenProdCols : frozenReviewCols;
  const colName = headers[colIdx];
  if (!frozenSet.has(colName)) return false;

  let lastFrozenName = null;
  for (let c = 0; c < headers.length; c++) {
    if (frozenSet.has(headers[c])) {
      lastFrozenName = headers[c];
    }
  }
  return colName === lastFrozenName;
}

function getColLeftOffset(headers, colIdx, tableKey = 'review') {
  return getFrozenColOffset(headers, colIdx, tableKey);
}


function attachHeaderResizers(tableEl, tableKey, headers, onResizeDone) {
  if (!tableEl) return;
  const thead = tableEl.querySelector('thead');
  if (!thead) return;

  // 1. Column width resizing (Excel-like horizontal drag)
  const colResizers = tableEl.querySelectorAll('.th-resizer[data-resizer]');
  colResizers.forEach(resizer => {
    resizer.addEventListener('mousedown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const colIdx = parseInt(resizer.dataset.resizer, 10);
      const th = resizer.closest('th');
      if (!th) return;

      const startX = e.pageX;
      const startWidth = th.offsetWidth;
      const headerName = headers[colIdx] || (th.classList.contains('col-action') ? 'ACT' : '');
      resizer.classList.add('is-resizing');
      document.body.classList.add('col-resizing');

      const onMouseMove = (moveEvent) => {
        const delta = moveEvent.pageX - startX;
        const newWidth = Math.max(40, Math.min(800, startWidth + delta));
        th.style.width = `${newWidth}px`;
        th.style.minWidth = `${newWidth}px`;
        th.style.maxWidth = `${newWidth}px`;

        const rows = tableEl.querySelectorAll('tbody tr');
        rows.forEach(r => {
          const td = r.children[colIdx];
          if (td) {
            td.style.width = `${newWidth}px`;
            td.style.minWidth = `${newWidth}px`;
            td.style.maxWidth = `${newWidth}px`;
          }
        });
      };

      const onMouseUp = () => {
        window.removeEventListener('mousemove', onMouseMove);
        window.removeEventListener('mouseup', onMouseUp);
        resizer.classList.remove('is-resizing');
        document.body.classList.remove('col-resizing');
        const finalWidth = th.offsetWidth;
        if (headerName) {
          setSavedColWidth(tableKey, headerName, finalWidth);
        }
        if (onResizeDone) onResizeDone();
      };

      window.addEventListener('mousemove', onMouseMove);
      window.addEventListener('mouseup', onMouseUp);
    });

    // Double-click to reset column width to default
    resizer.addEventListener('dblclick', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const colIdx = parseInt(resizer.dataset.resizer, 10);
      const headerName = headers[colIdx] || (resizer.closest('th')?.classList.contains('col-action') ? 'ACT' : '');
      if (headerName) {
        const norm = String(headerName || '').trim().toUpperCase();
        let defaultWidth = 176;
        if (norm === 'ACT' || norm === 'ACTION_BTN' || norm === 'ACTIONS') defaultWidth = 60;
        else if (norm === 'ACTION' || norm === 'ACTION ITEM') defaultWidth = 160;
        else if (norm === 'ID' || norm === '#') defaultWidth = 90;
        else if (norm === 'TEAM' || norm === 'GROUP' || norm === 'CORRECT TEAM' || norm === 'CORRECT GROUP') defaultWidth = 135;
        else if (isCompactCol(norm)) defaultWidth = 120;
        setSavedColWidth(tableKey, headerName, defaultWidth);
        if (onResizeDone) onResizeDone();
      }
    });
  });


  // 2. Header row height resizing (Excel-like vertical drag on bottom border)
  const rowResizers = tableEl.querySelectorAll('.th-row-resizer');
  rowResizers.forEach(rowResizer => {
    rowResizer.addEventListener('mousedown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const startY = e.pageY;
      const allTh = thead.querySelectorAll('th');
      const startHeight = (allTh[0] && allTh[0].offsetHeight) || 44;
      rowResizer.classList.add('is-resizing');
      document.body.classList.add('row-resizing');

      const onMouseMove = (moveEvent) => {
        const delta = moveEvent.pageY - startY;
        const newHeight = Math.max(34, Math.min(220, startHeight + delta));
        allTh.forEach(th => {
          th.style.height = `${newHeight}px`;
          th.style.minHeight = `${newHeight}px`;
        });
      };

      const onMouseUp = () => {
        window.removeEventListener('mousemove', onMouseMove);
        window.removeEventListener('mouseup', onMouseUp);
        rowResizer.classList.remove('is-resizing');
        document.body.classList.remove('row-resizing');
        const finalHeight = (allTh[0] && allTh[0].offsetHeight) || startHeight;
        setSavedHeaderHeight(tableKey, finalHeight);
      };

      window.addEventListener('mousemove', onMouseMove);
      window.addEventListener('mouseup', onMouseUp);
    });

    // Double-click to reset height to default (44px)
    rowResizer.addEventListener('dblclick', (e) => {
      e.preventDefault();
      e.stopPropagation();
      setSavedHeaderHeight(tableKey, 44);
      const allTh = thead.querySelectorAll('th');
      allTh.forEach(th => {
        th.style.height = `44px`;
        th.style.minHeight = `44px`;
      });
    });
  });
}

function isInvalidDateVal(val) {
  if (!val || !String(val).trim()) return false;
  const str = String(val).trim();
  const match = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(str);
  if (!match) return true;
  const m = parseInt(match[1], 10);
  const d = parseInt(match[2], 10);
  const y = parseInt(match[3], 10);
  if (m < 1 || m > 12 || d < 1 || d > 31 || y < 1900 || y > 2100) return true;
  const dt = new Date(y, m - 1, d);
  return dt.getFullYear() !== y || dt.getMonth() !== (m - 1) || dt.getDate() !== d;
}

function isDateColumn(headerName) {
  const norm = String(headerName || '').trim().toUpperCase();
  return ['NEGO DATE', 'REPORT DATE', 'SOURCING DATE', 'DATE UPLOADED', 'DATE EXTRACTED', 'NEXT CALL DATE'].includes(norm);
}

// Mirrors NO_FUTURE_DATE_COLUMNS in productivity_tool.py - keep both in step.
// NEXT CALL DATE is excluded on purpose: it is supposed to be ahead of today.
function isNoFutureDateColumn(headerName) {
  const norm = String(headerName || '').trim().toUpperCase();
  return ['NEGO DATE', 'SOURCING DATE', 'REPORT DATE'].includes(norm);
}

function isFutureDateVal(val) {
  const match = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(String(val || '').trim());
  if (!match) return false;
  const dt = new Date(Number(match[3]), Number(match[1]) - 1, Number(match[2]));
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return dt.getTime() > today.getTime();
}

function isCalculatedColumn(headerName) {
  const norm = String(headerName || '').trim().toUpperCase();
  return ['LOT AREA (HA)', 'BUILD', 'DATA USABILITY', 'YEAR', 'CORRECT TEAM', 'CORRECT GROUP', 'MATCHED TEAM', 'MATCHED GROUP'].includes(norm);
}

function isMatchedNegotiatorColumn(headerName) {
  const norm = String(headerName || '').trim().toUpperCase();
  return ['MATCHED NEGOTIATOR NAME', 'MATCHED SOURCER', 'MATCHED SOURCER NAME', 'MATCHED NEGOTIATOR'].includes(norm);
}

function isMatchColumn(headerName) {
  const norm = String(headerName || '').trim().toUpperCase();
  if (isMatchedNegotiatorColumn(norm)) return false;
  return ['TEAM MATCH', 'GROUP MATCH', 'MATCH', 'MATCH STATUS', 'NEGOTIATOR MATCH', 'SOURCER MATCH'].includes(norm) || (norm.endsWith(' MATCH') && !norm.startsWith('MATCHED'));
}

function isNegotiatorSourceColumn(headerName) {
  const norm = String(headerName || '').trim().toUpperCase();
  if (isMatchedNegotiatorColumn(norm)) return false;
  return ['NEGOTIATOR NAME', 'SOURCER NAME', 'NEGOTIATOR', 'SOURCER', 'LSA NAME'].includes(norm);
}

function normalizeNamePart(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

function checkNegotiatorMatch(sourceName, matchedName) {
  const s = String(sourceName || '').trim();
  const m = String(matchedName || '').trim();
  if (!s || !m || m.toUpperCase() === 'UNASSIGNED') return false;

  // 1. Direct case-insensitive match
  if (s.toUpperCase() === m.toUpperCase()) return true;

  // 2. Compact alphanumeric match (ignoring spaces, dots, dashes, punctuation)
  const normS = normalizeNamePart(s);
  const normM = normalizeNamePart(m);
  if (normS && normS === normM) return true;

  // 3. First initial + Last Name token match (e.g. "J. DELA CRUZ" vs "JUAN DELA CRUZ")
  const sParts = s.toLowerCase().split(/[\s._,-]+/).map(normalizeNamePart).filter(Boolean);
  const mParts = m.toLowerCase().split(/[\s._,-]+/).map(normalizeNamePart).filter(Boolean);
  if (sParts.length >= 2 && mParts.length >= 2) {
    const sFirstInit = sParts[0][0];
    const sLast = sParts[sParts.length - 1];
    const mFirstInit = mParts[0][0];
    const mLast = mParts[mParts.length - 1];
    if (sFirstInit && sLast && sFirstInit === mFirstInit && sLast === mLast) {
      return true;
    }
  }
  return false;
}




function renderReview(table) {
  currentReviewTable = table;
  const t = $('reviewTable'), thead = t.querySelector('thead');
  thead.innerHTML = '';
  const tr = document.createElement('tr');
  const headerHeight = getSavedHeaderHeight('review', 44);

  table.headers.forEach((h, colIdx) => {
    const colWidth = getColWidth(h, 'review');
    const isFrozen = frozenReviewCols.has(h);
    const isLastFrozen = isLastFrozenCol(table.headers, colIdx, 'review');
    const leftOffset = isFrozen ? getFrozenColOffset(table.headers, colIdx, 'review') : 0;

    const th = document.createElement('th');
    th.className = `${isFrozen ? 'col-frozen' : ''} ${isLastFrozen ? 'col-frozen-last' : ''}`;
    th.style.width = `${colWidth}px`;
    th.style.minWidth = `${colWidth}px`;
    th.style.maxWidth = `${colWidth}px`;
    th.style.height = `${headerHeight}px`;
    th.style.minHeight = `${headerHeight}px`;
    if (isFrozen) {
      th.style.left = `${leftOffset}px`;
    }

    const isFilterActive = !!(reviewFilters[colIdx] && reviewFilters[colIdx].trim());
    const isFilterOpen = openReviewFilterCols.has(colIdx) || isFilterActive;

    th.innerHTML = `
      <div class="th-header-box">
        <div class="th-title-wrap">
          <button type="button" class="th-pin-btn ${isFrozen ? 'pinned' : ''}" data-freeze-review="${colIdx}" title="${isFrozen ? 'Unfreeze' : 'Freeze'} column ${escapeHtml(h)}">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" stroke="none">
              <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z"/>
            </svg>
          </button>
          <span class="th-col-name" data-freeze-review="${colIdx}" title="Click to ${isFrozen ? 'unfreeze' : 'freeze'} ${escapeHtml(h)}">${escapeHtml(h)}</span>
          <button type="button" class="th-chevron-btn ${isFilterActive ? 'active' : ''}" data-filter-toggle-review="${colIdx}" title="Filter column">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"></polyline></svg>
          </button>
        </div>
        <div class="th-filter-popup ${isFilterOpen ? '' : 'hidden'}" id="reviewFilterBox_${colIdx}">
          <input class="th-filter-input" data-review-col="${colIdx}" value="${escapeHtml(reviewFilters[colIdx] || '')}" placeholder="FILTER ${escapeHtml(h)}..." />
        </div>
      </div>
      <div class="th-resizer" data-resizer="${colIdx}" title="Drag column width (Double-click to reset)"></div>
      <div class="th-row-resizer" title="Drag header height (Double-click to reset)"></div>`;
    tr.appendChild(th);
  });

  // Action column
  const actWidth = getColWidth('ACT', 'review');
  const thAct = document.createElement('th');
  thAct.className = 'col-action';
  thAct.style.width = `${actWidth}px`;
  thAct.style.minWidth = `${actWidth}px`;
  thAct.style.maxWidth = `${actWidth}px`;
  thAct.style.height = `${headerHeight}px`;
  thAct.style.minHeight = `${headerHeight}px`;
  thAct.innerHTML = `
    <div class="th-header-box"><span class="th-col-name">ACT</span></div>
    <div class="th-resizer" data-resizer="${table.headers.length}" title="Drag column width (Double-click to reset)"></div>
    <div class="th-row-resizer" title="Drag header height (Double-click to reset)"></div>`;
  tr.appendChild(thAct);

  thead.appendChild(tr);

  // Click handler for header freeze & filter toggling
  thead.onclick = (e) => {
    if (e.target.closest('.th-resizer') || e.target.closest('.th-row-resizer')) return;

    const freezeEl = e.target.closest('[data-freeze-review]');
    if (freezeEl) {
      const col = parseInt(freezeEl.dataset.freezeReview, 10);
      const h = currentReviewTable.headers[col];
      if (h) {
        if (frozenReviewCols.has(h)) {
          frozenReviewCols.delete(h);
        } else {
          frozenReviewCols.add(h);
        }
        setSavedFrozenCols('review', frozenReviewCols);
        renderReview(currentReviewTable);
      }
      return;
    }

    const chevronBtn = e.target.closest('[data-filter-toggle-review]');
    if (chevronBtn) {
      const col = parseInt(chevronBtn.dataset.filterToggleReview, 10);
      if (openReviewFilterCols.has(col)) {
        openReviewFilterCols.delete(col);
      } else {
        openReviewFilterCols.add(col);
      }
      const box = document.getElementById(`reviewFilterBox_${col}`);
      if (box) {
        box.classList.toggle('hidden');
        if (!box.classList.contains('hidden')) {
          const inp = box.querySelector('input');
          if (inp) inp.focus();
        }
      }
    }
  };

  // Column filter input handlers
  thead.querySelectorAll('input[data-review-col]').forEach(inp => {
    inp.addEventListener('input', () => {
      const col = +inp.dataset.reviewCol, v = inp.value.trim().toLowerCase();
      if (v) reviewFilters[col] = v; else delete reviewFilters[col];
      clearTimeout(reviewFilterTimer);
      reviewFilterTimer = setTimeout(renderReviewBody, 150);
    });
  });

  attachHeaderResizers(t, 'review', [...table.headers, 'ACT'], () => renderReview(currentReviewTable));


  $('reviewEmpty').classList.add('hidden');
  t.classList.remove('hidden');
  renderReviewBody();
}


// --- CLASS depends on CLASSIFICATION -------------------------------------
// The CLASS dropdown offers a different option set depending on the row's
// CLASSIFICATION value: NA -> not-available reasons, NU -> not-usable reasons,
// SS/SL -> "WITH RELOC SURVEY", OS/OL -> "FLIPPED".
const CLASS_DEPENDENT_GROUPS = [
  { test: c => c.includes('NOT AVAILABLE') || c === 'NA',
    options: ['1-SOURCE OF INCOME', '2-INHERITANCE', '3-NOT IN NEED OF MONEY',
              '4-PROPERTY ISSUES', '5-UNDISCLOSED BUYERS', '6-OTHERS', '7-HARD NO'] },
  { test: c => c.includes('NOT USABLE') || c === 'NU',
    options: ['1-GEOGRAPHICAL', '2-STRUCTURAL', '3-GOVERNMENT'] },
  { test: c => c.includes('SECURED') || c === 'SS' || c === 'SL',
    options: ['WITH RELOC SURVEY'] },
  { test: c => c.includes('OPEN TO') || c === 'OS' || c === 'OL',
    options: ['FLIPPED'] },
];

function isClassColumn(header) { return String(header || '').toUpperCase() === 'CLASS'; }

// Fill a <datalist> with option strings (de-duped) plus the current value, so a
// searchable choice cell can show suggestions as the user types.
function addChoiceOptions(listEl, choices, currentVal) {
  if (!listEl) return;
  listEl.innerHTML = '';
  const seen = new Set();
  const push = (v) => {
    const s = String(v == null ? '' : v).trim();
    if (!s || seen.has(s)) return;
    seen.add(s);
    listEl.appendChild(new Option(s, s));
  };
  (choices || []).forEach(push);
  push(currentVal);
}

// ---------------------------------------------------------------------------
// Combobox: a text input the user can type into, backed by a custom filtered
// dropdown list that keeps the native-select look (styled option list, all
// options shown when empty). The panel is fixed-positioned on <body> so the
// table's overflow never clips it, and only one combo is open at a time.
// Used by BOTH the CSV workspace (review) and productivity tables.
// ---------------------------------------------------------------------------
let __openComboClose = null;
function makeCombo(opts) {
  const { value = '', choices = [], invalid = false, extraClass = '', title = '', onCommit } = opts || {};
  const all = choices.filter(c => c != null && String(c).trim() !== '');

  const input = document.createElement('input');
  input.type = 'text';
  input.className = `cell-input cell-select combo-input ${invalid ? 'cell-invalid' : ''} ${extraClass}`.replace(/\s+/g, ' ').trim();
  input.value = String(value || '').toUpperCase();
  input.spellcheck = false;
  input.autocomplete = 'off';
  input.setAttribute('role', 'combobox');
  if (title) input.title = title;

  let panel = null;
  let activeIdx = -1;
  let committed = input.value;

  const filtered = (q) => {
    const s = (q || '').trim().toUpperCase();
    if (!s) return all.slice();
    const starts = [], has = [];
    all.forEach(o => {
      const u = String(o).toUpperCase();
      if (u.startsWith(s)) starts.push(o);
      else if (u.includes(s)) has.push(o);
    });
    return starts.concat(has);
  };

  const renderList = (q) => {
    if (!panel) return;
    panel.innerHTML = '';
    activeIdx = -1;
    const list = filtered(q);
    if (!list.length) {
      const none = document.createElement('div');
      none.className = 'combo-empty';
      none.textContent = 'No matches';
      panel.appendChild(none);
      return;
    }
    const cur = input.value.trim().toUpperCase();
    list.forEach((o) => {
      const item = document.createElement('div');
      item.className = 'combo-option' + (String(o).toUpperCase() === cur ? ' sel' : '');
      item.textContent = o;
      item.dataset.val = o;
      item.addEventListener('mousedown', (e) => { e.preventDefault(); choose(o); });
      item.addEventListener('mousemove', () => setActive(Array.prototype.indexOf.call(panel.children, item)));
      panel.appendChild(item);
    });
  };

  const position = () => {
    if (!panel) return;
    const r = input.getBoundingClientRect();
    panel.style.left = Math.round(r.left) + 'px';
    panel.style.width = Math.round(Math.max(r.width, 190)) + 'px';
    const ph = panel.offsetHeight;
    const below = window.innerHeight - r.bottom;
    if (below < ph + 10 && r.top > below) panel.style.top = Math.round(r.top - ph - 2) + 'px';
    else panel.style.top = Math.round(r.bottom + 2) + 'px';
  };

  const onScroll = () => { if (panel) position(); };
  const onDocDown = (e) => {
    if (e.target === input || (panel && panel.contains(e.target))) return;
    commitCurrent(); close();
  };

  const open = () => {
    if (__openComboClose && __openComboClose !== close) __openComboClose();
    if (!panel) {
      panel = document.createElement('div');
      panel.className = 'combo-panel';
    }
    document.body.appendChild(panel);
    renderList('');
    position();
    __openComboClose = close;
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onScroll, true);
    document.addEventListener('mousedown', onDocDown, true);
  };

  function close() {
    if (panel && panel.parentNode) panel.parentNode.removeChild(panel);
    window.removeEventListener('scroll', onScroll, true);
    window.removeEventListener('resize', onScroll, true);
    document.removeEventListener('mousedown', onDocDown, true);
    if (__openComboClose === close) __openComboClose = null;
  }

  const isOpen = () => !!(panel && panel.parentNode);

  const setActive = (i) => {
    if (!panel) return;
    const items = panel.querySelectorAll('.combo-option');
    items.forEach(x => x.classList.remove('active'));
    activeIdx = i;
    if (i >= 0 && items[i]) { items[i].classList.add('active'); items[i].scrollIntoView({ block: 'nearest' }); }
  };

  const choose = (val) => { input.value = String(val || '').toUpperCase(); commitCurrent(); close(); };

  function commitCurrent() {
    const v = input.value.trim().toUpperCase();
    input.value = v;
    if (v === committed) return;
    committed = v;
    if (typeof onCommit === 'function') onCommit(v, input);
  }

  input.addEventListener('focus', () => open());
  input.addEventListener('click', () => { if (!isOpen()) open(); });
  input.addEventListener('input', () => { if (!isOpen()) open(); renderList(input.value); position(); });
  input.addEventListener('keydown', (e) => {
    const items = panel ? panel.querySelectorAll('.combo-option') : [];
    if (e.key === 'ArrowDown') { e.preventDefault(); if (!isOpen()) { open(); return; } setActive(Math.min(activeIdx + 1, items.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(Math.max(activeIdx - 1, 0)); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      if (isOpen() && activeIdx >= 0 && items[activeIdx]) choose(items[activeIdx].dataset.val);
      else { commitCurrent(); close(); }
    } else if (e.key === 'Escape') { e.preventDefault(); input.value = committed; close(); }
    else if (e.key === 'Tab') { commitCurrent(); close(); }
  });
  input.addEventListener('blur', () => {
    setTimeout(() => { if (isOpen()) { commitCurrent(); close(); } }, 120);
  });

  return { input, setCommitted: (v) => { committed = String(v || '').toUpperCase(); } };
}

// The CLASS options allowed for a given CLASSIFICATION value ([] if none apply).
function classChoicesFor(classificationVal) {
  const c = String(classificationVal || '').toUpperCase().replace(/[\s\-_\/–—]+/g, ' ').trim();
  for (const g of CLASS_DEPENDENT_GROUPS) { if (g.test(c)) return g.options.slice(); }
  return [];
}

function reviewColIdx(name) {
  if (!currentReviewTable || !currentReviewTable.headers) return -1;
  return currentReviewTable.headers.findIndex(h => String(h).toUpperCase() === name);
}

// (Re)build a CLASS <select>'s options from the row's CLASSIFICATION value.
// Keeps an out-of-list existing value visible so stored data is never hidden.
function populateClassSelect(sel, classificationVal, curUpper) {
  const opts = classChoicesFor(classificationVal);
  sel.innerHTML = '<option value="">UNASSIGNED</option>';
  let matched = false;
  opts.forEach(o => {
    const isSel = o.toUpperCase() === curUpper;
    if (isSel) matched = true;
    sel.appendChild(new Option(o, o, false, isSel));
  });
  if (curUpper && curUpper !== 'UNASSIGNED' && !matched) {
    sel.appendChild(new Option(curUpper, curUpper, false, true));
  }
}

// After CLASSIFICATION changes, rebuild the row's CLASS dropdown and clear the
// stored CLASS value if it no longer fits the new classification (persisted).
async function reconcileClassCell(row, origIdx) {
  const classIdx = reviewColIdx('CLASS');
  const clsIdx = reviewColIdx('CLASSIFICATION');
  if (classIdx < 0 || clsIdx < 0) return;
  const classificationVal = currentReviewTable.rows[origIdx][clsIdx];
  const allowed = classChoicesFor(classificationVal).map(o => o.toUpperCase());
  const curClass = String(currentReviewTable.rows[origIdx][classIdx] || '').toUpperCase();

  const classTd = row.children[classIdx];
  const ctrl = classTd ? classTd.querySelector('.cell-select, .cell-input') : null;

  const mustClear = curClass && curClass !== 'UNASSIGNED' && !allowed.includes(curClass);

  // Refresh the option list: <datalist> for the searchable input, else <select>.
  const dl = classTd ? classTd.querySelector('datalist') : null;
  if (dl) addChoiceOptions(dl, classChoicesFor(classificationVal), mustClear ? '' : curClass);
  else if (ctrl && ctrl.tagName === 'SELECT') populateClassSelect(ctrl, classificationVal, mustClear ? '' : curClass);

  if (mustClear) {
    currentReviewTable.rows[origIdx][classIdx] = '';
    if (ctrl) { ctrl.value = ''; ctrl.classList.add('modified'); }
    const res = await call('update_review_cell', origIdx, currentReviewTable.headers[classIdx], '');
    updateReviewValidationState();
    if (res && res.ok === false) toast(res.error || 'Failed to reset CLASS.', true);
    else toast('CLASS cleared — pick an option valid for the new classification.', false);
  }
}

// A row "has an issue" if it has a Build mismatch, an invalid date, or any
// cell that fails validation. Used by the "Show issues only" filter.
function reviewRowHasIssue(origIdx, rowCells, buildErrMap) {
  if (buildErrMap && buildErrMap.has(origIdx)) return true;
  const headers = currentReviewTable.headers;
  const meta = currentReviewTable.meta || {};
  for (let cIdx = 0; cIdx < headers.length; cIdx++) {
    const h = headers[cIdx];
    if (isDateColumn(h) && isInvalidDateVal(rowCells[cIdx])) return true;
    const m = meta[h] || { type: 'text' };
    const valStr = String(rowCells[cIdx] || '').trim();
    if (!validateCellValue(valStr, m, h).valid) return true;
  }
  return false;
}

function reviewBuildErrMap() {
  const map = new Map();
  (currentReviewTable && currentReviewTable.buildErrors || []).forEach(e => {
    if (e && typeof e.row === 'number') map.set(e.row - 1, e.reason || 'Build work-week mismatch');
  });
  return map;
}

// Re-sync a single already-rendered <tr> from currentReviewTable WITHOUT
// rebuilding its DOM — updates control values, validity classes and row
// highlight in place. Far cheaper than re-creating thousands of rows.
function refreshReviewRow(tr, buildErrMap) {
  if (!tr || !currentReviewTable) return;
  const origIdx = parseInt(tr.dataset.rowIdx, 10);
  if (isNaN(origIdx)) return;
  const rowCells = currentReviewTable.rows[origIdx];
  if (!rowCells) return;
  if (!buildErrMap) buildErrMap = reviewBuildErrMap();

  const headers = currentReviewTable.headers;
  const meta = currentReviewTable.meta || {};
  const buildErr = buildErrMap.get(origIdx);
  let hasInvalidDate = false;

  headers.forEach((h, cIdx) => {
    const td = tr.children[cIdx];
    if (!td) return;
    const m = meta[h] || { type: 'text' };
    const valStr = String(rowCells[cIdx] || '').trim();
    const valRes = validateCellValue(valStr, m, h);
    if (isDateColumn(h) && isInvalidDateVal(rowCells[cIdx])) hasInvalidDate = true;

    const ctrl = td.querySelector('.cell-select, .cell-input');
    if (ctrl) {
      // Never overwrite the field the user is actively editing.
      if (document.activeElement !== ctrl) {
        const upper = valStr.toUpperCase();
        if (ctrl.tagName === 'SELECT' && isClassColumn(h)) {
          // Rebuild CLASS options from this row's CLASSIFICATION value.
          const clsIdx = headers.findIndex(x => String(x).toUpperCase() === 'CLASSIFICATION');
          populateClassSelect(ctrl, clsIdx >= 0 ? rowCells[clsIdx] : '', upper);
        } else {
          if (ctrl.tagName === 'SELECT' && upper && upper !== 'UNASSIGNED'
              && !Array.from(ctrl.options).some(o => o.value === upper)) {
            ctrl.appendChild(new Option(upper, upper));
          }
          if (ctrl.value !== upper) ctrl.value = upper;
        }
      }
      ctrl.classList.toggle('cell-invalid', !valRes.valid);
      if (!valRes.valid) ctrl.title = valRes.error; else ctrl.removeAttribute('title');
    } else if (isMatchColumn(h)) {
      td.innerHTML = matchBadge(valStr);
    }

    const buildCellBad = !!(buildErr && String(h).toUpperCase() === 'BUILD');
    const cellBad = !valRes.valid || buildCellBad;
    td.classList.toggle('cell-invalid', cellBad);
    if (cellBad) td.title = valRes.valid ? buildErr : valRes.error;
    else td.removeAttribute('title');
  });

  tr.classList.toggle('row-invalid-date', !!(hasInvalidDate || buildErr));
}

function refreshReviewVisibleRows() {
  const tbody = $('reviewTable').querySelector('tbody');
  if (!tbody) return;
  const buildErrMap = reviewBuildErrMap();
  Array.from(tbody.children).forEach(tr => refreshReviewRow(tr, buildErrMap));
}

// Shared post-edit handler for every cell type. Avoids the old full-table
// rebuild on each edit (the main source of lag on large sheets): rebuilds
// only when the visible row SET can change (issues-only / active filters,
// which are always small), otherwise refreshes rows in place.
function applyReviewCellUpdate(res, tr, origIdx, header) {
  if (!res || res.ok === false) {
    toast((res && res.error) || 'Failed to update cell.', true);
    return;
  }
  toast(`Updated ${header}.`);
  if (res.table) currentReviewTable = res.table;

  const recalculated = !!(res.state && res.state.hasCalculated);
  if (reviewIssuesOnly || Object.keys(reviewFilters).length) {
    // Membership can change (a fixed row leaves the issues view / a filter) —
    // rebuild, but these views are already narrowed to few rows.
    renderReviewBody();
  } else if (recalculated) {
    // A recalculation may change many rows' derived values.
    refreshReviewVisibleRows();
  } else {
    // Only the edited row changed.
    refreshReviewRow(tr);
  }
  updateReviewValidationState();
}

function renderReviewBody() {
  if (!currentReviewTable || !currentReviewTable.rows) return;
  const t = $('reviewTable'), tbody = t.querySelector('tbody');
  tbody.innerHTML = '';
  invalidateGridCache('reviewTable');

  const buildErrMap = new Map();
  (currentReviewTable.buildErrors || []).forEach(e => {
    if (e && typeof e.row === 'number') buildErrMap.set(e.row - 1, e.reason || 'Build work-week mismatch');
  });

  const activeFilters = Object.entries(reviewFilters).filter(([_, val]) => (val || '').trim() !== '');
  const filtered = [];

  currentReviewTable.rows.forEach((rowCells, origIdx) => {
    for (const [colIdxStr, filterVal] of activeFilters) {
      const cIdx = parseInt(colIdxStr, 10);
      const cellText = String(rowCells[cIdx] || '').toLowerCase();
      if (!cellText.includes(filterVal.trim().toLowerCase())) return;
    }
    if (reviewIssuesOnly && !reviewRowHasIssue(origIdx, rowCells, buildErrMap)) return;
    filtered.push({ rowCells, origIdx });
  });

  const total = currentReviewTable.rows.length;
  const shown = Math.min(filtered.length, MAX_RENDER);
  const filterActive = activeFilters.length > 0 || reviewIssuesOnly;
  $('reviewCount').textContent = filterActive
    ? `Showing ${shown < filtered.length ? shown + ' of ' : ''}${filtered.length}${reviewIssuesOnly ? ' issue' : ''} row${filtered.length === 1 ? '' : 's'} of ${total}`
    : (shown < total ? `Showing ${shown} of ${total} rows` : `${total} rows`);

  if (!filtered.length) {
    t.classList.add('hidden');
    $('reviewEmpty').classList.remove('hidden');
    const emptyMsg = $('reviewEmpty').querySelector('p');
    if (emptyMsg) emptyMsg.textContent = reviewIssuesOnly
      ? 'No rows with issues — everything looks valid.'
      : 'Upload a file in step 1 to see the review table.';
    return;
  }
  $('reviewEmpty').classList.add('hidden');
  t.classList.remove('hidden');

  const frag = document.createDocumentFragment();
  for (let i = 0; i < shown; i++) {
    const { rowCells, origIdx } = filtered[i];
    const row = document.createElement('tr');
    row.dataset.rowIdx = origIdx;
    const buildErr = buildErrMap.get(origIdx);

    // Check for invalid dates in row
    let hasInvalidDate = false;
    currentReviewTable.headers.forEach((h, cIdx) => {
      if (isDateColumn(h) && isInvalidDateVal(rowCells[cIdx])) {
        hasInvalidDate = true;
      }
    });
    if (hasInvalidDate || buildErr) {
      row.classList.add('row-invalid-date');
    }

    rowCells.forEach((c, colIdx) => {
      const header = currentReviewTable.headers[colIdx];
      const meta = (currentReviewTable.meta && currentReviewTable.meta[header]) || { type: 'text' };
      const colWidth = getColWidth(header, 'review');
      const isFrozen = frozenReviewCols.has(header);
      const isLastFrozen = isLastFrozenCol(currentReviewTable.headers, colIdx, 'review');
      const leftOffset = isFrozen ? getFrozenColOffset(currentReviewTable.headers, colIdx, 'review') : 0;

      const td = document.createElement('td');
      td.className = `${isFrozen ? 'col-frozen' : ''} ${isLastFrozen ? 'col-frozen-last' : ''}`;
      td.style.width = `${colWidth}px`;
      td.style.minWidth = `${colWidth}px`;
      td.style.maxWidth = `${colWidth}px`;
      if (isFrozen) {
        td.style.left = `${leftOffset}px`;
      }

      td.dataset.rowIdx = origIdx;
      td.dataset.colKey = header;
      td.dataset.colIdx = colIdx;

      const valStr = String(c || '').trim();
      const valRes = validateCellValue(valStr, meta, header);
      if (!valRes.valid) {
        td.classList.add('cell-invalid');
        td.title = valRes.error;
      }
      // Flag the BUILD cell when this row fails the Build work-week check.
      if (buildErr && String(header).toUpperCase() === 'BUILD') {
        td.classList.add('cell-invalid');
        td.title = buildErr;
      }

      if (isMatchColumn(header)) {
        td.innerHTML = matchBadge(valStr);
      } else if (meta.type === 'choice') {
        // Combobox: type-to-filter text input with a styled dropdown list.
        const isClass = isClassColumn(header);
        const clsIdx = currentReviewTable.headers.findIndex(x => String(x).toUpperCase() === 'CLASSIFICATION');
        const isTeamOrGroup = header === 'TEAM' || header === 'GROUP';
        const choices = isClass
          ? classChoicesFor(clsIdx >= 0 ? rowCells[clsIdx] : '')
          : sortChoices(meta.choices || []);
        if (!isClass && !isTeamOrGroup) choices.unshift('UNASSIGNED');

        const combo = makeCombo({
          value: valStr,
          choices,
          invalid: !valRes.valid,
          title: !valRes.valid ? valRes.error : '',
          onCommit: async (newVal, inputEl) => {
            inputEl.classList.add('modified');
            currentReviewTable.rows[origIdx][colIdx] = newVal;
            const editRes = validateCellValue(newVal, meta, header);
            if (!editRes.valid) {
              td.classList.add('cell-invalid');
              inputEl.classList.add('cell-invalid');
              td.title = editRes.error;
              inputEl.title = editRes.error;
            } else {
              td.classList.remove('cell-invalid');
              inputEl.classList.remove('cell-invalid');
              td.removeAttribute('title');
              inputEl.removeAttribute('title');
            }
            updateReviewValidationState();
            const res = await call('update_review_cell', origIdx, header, newVal);
            applyReviewCellUpdate(res, row, origIdx, header);
            // Changing CLASSIFICATION redefines which CLASS options are valid.
            if (String(header).toUpperCase() === 'CLASSIFICATION') {
              await reconcileClassCell(row, origIdx);
            }
          },
        });

        td.appendChild(combo.input);

      } else if (isCalculatedColumn(header)) {
        const input = document.createElement('input');
        input.type = 'text';
        input.className = `cell-input cell-readonly ${!valRes.valid ? 'cell-invalid' : ''}`;
        if (!valRes.valid) input.title = valRes.error;
        input.readOnly = true;
        input.value = valStr.toUpperCase();
        input.spellcheck = false;
        td.appendChild(input);
      } else if (isDateColumn(header)) {
        const input = document.createElement('input');
        input.type = 'text';
        input.maxLength = 10;
        input.placeholder = 'MM/DD/YYYY';
        input.className = `cell-input cell-date ${!valRes.valid ? 'cell-invalid cell-invalid-date' : ''}`;
        if (!valRes.valid) input.title = valRes.error;
        input.value = valStr.toUpperCase();
        input.spellcheck = false;

        let initialVal = valStr;
        input.addEventListener('focus', () => {
          initialVal = input.value;
          input.classList.remove('cell-invalid-date', 'cell-invalid');
        });

        input.addEventListener('blur', async () => {
          let rawVal = input.value.trim().toUpperCase();
          if (rawVal === initialVal) return;

          currentReviewTable.rows[origIdx][colIdx] = rawVal;
          const editRes = validateCellValue(rawVal, meta, header);
          if (!editRes.valid) {
            input.classList.add('cell-invalid', 'cell-invalid-date');
            td.classList.add('cell-invalid');
            row.classList.add('row-invalid-date');
            input.title = editRes.error;
            td.title = editRes.error;
            toast(`Validation Error: ${editRes.error}`, true);
          } else {
            input.classList.remove('cell-invalid', 'cell-invalid-date');
            td.classList.remove('cell-invalid');
            input.removeAttribute('title');
            td.removeAttribute('title');
            let rowStillInvalid = false;
            currentReviewTable.headers.forEach((h, cIdx) => {
              const checkVal = (cIdx === colIdx) ? rawVal : (currentReviewTable.rows[origIdx][cIdx] || '');
              if (isDateColumn(h) && isInvalidDateVal(checkVal)) rowStillInvalid = true;
            });
            if (!rowStillInvalid) row.classList.remove('row-invalid-date');
          }
          updateReviewValidationState();

          input.classList.add('modified');
          const res = await call('update_review_cell', origIdx, header, rawVal);
          applyReviewCellUpdate(res, row, origIdx, header);
        });

        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            input.blur();
          }
        });

        td.appendChild(input);
      } else {
        const input = document.createElement('input');
        input.type = 'text';
        input.className = `cell-input ${!valRes.valid ? 'cell-invalid' : ''}`;
        if (!valRes.valid) input.title = valRes.error;
        input.value = valStr.toUpperCase();
        input.spellcheck = false;

        let initialVal = valStr;
        input.addEventListener('focus', () => {
          initialVal = input.value;
          input.classList.remove('cell-invalid');
        });

        input.addEventListener('blur', async () => {
          let rawVal = input.value.trim().toUpperCase();
          if (rawVal === initialVal) return;

          currentReviewTable.rows[origIdx][colIdx] = rawVal;
          const editRes = validateCellValue(rawVal, meta, header);
          if (!editRes.valid) {
            input.classList.add('cell-invalid');
            td.classList.add('cell-invalid');
            input.title = editRes.error;
            td.title = editRes.error;
            toast(`Validation Error: ${editRes.error}`, true);
          } else {
            input.classList.remove('cell-invalid');
            td.classList.remove('cell-invalid');
            input.removeAttribute('title');
            td.removeAttribute('title');
          }
          updateReviewValidationState();

          input.classList.add('modified');
          const res = await call('update_review_cell', origIdx, header, rawVal);
          applyReviewCellUpdate(res, row, origIdx, header);
        });

        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            input.blur();
          }
        });

        td.appendChild(input);
      }

      row.appendChild(td);
    });

    // Delete row action
    const actWidth = getColWidth('ACT', 'review');
    const tdAct = document.createElement('td');
    tdAct.className = 'col-action';
    tdAct.style.width = `${actWidth}px`;
    tdAct.style.minWidth = `${actWidth}px`;
    tdAct.style.maxWidth = `${actWidth}px`;
    tdAct.innerHTML = `
      <button class="btn xs ghost danger rc-del-btn" data-del-review="${origIdx}" title="Delete Row">
        <span class="ico" data-icon="trash"></span>
      </button>
    `;
    row.appendChild(tdAct);
    frag.appendChild(row);


  }

  tbody.appendChild(frag);

  tbody.onclick = (e) => {
    const btn = e.target.closest('[data-del-review]');
    if (!btn) return;
    const rowIdx = parseInt(btn.dataset.delReview, 10);
    openConfirmModal({
      title: 'Delete record',
      message: `Delete record #${rowIdx + 1}? This cannot be undone.`,
      proceedLabel: 'Delete', cancelLabel: 'Cancel', danger: true, hideQuestion: true,
      onProceed: async () => {
        const res = await call('delete_review_row', rowIdx);
        if (res && res.ok !== false) {
          toast(`Row #${rowIdx + 1} deleted.`);
          if (res.table) renderReview(res.table);
        } else {
          toast((res && res.error) || 'Failed to delete row.', true);
        }
      }
    });
  };
  applyIcons(tbody);
  updateReviewValidationState();
}




function renderProductivity(table) {
  prodTable = table;
  window.prodTable = table;
  const t = $('prodTable'), thead = t.querySelector('thead');
  thead.innerHTML = '';
  const tr = document.createElement('tr');
  const headerHeight = getSavedHeaderHeight('prod', 44);

  table.labels.forEach((label, i) => {
    const colKey = (table.columns && table.columns[i]) || label;
    const colWidth = getColWidth(label, 'prod');
    const isFrozen = frozenProdCols.has(label);
    const isLastFrozen = isLastFrozenCol(table.labels, i, 'prod');
    const leftOffset = isFrozen ? getFrozenColOffset(table.labels, i, 'prod') : 0;

    const th = document.createElement('th');
    th.className = `${isFrozen ? 'col-frozen' : ''} ${isLastFrozen ? 'col-frozen-last' : ''}`;
    th.style.width = `${colWidth}px`;
    th.style.minWidth = `${colWidth}px`;
    th.style.maxWidth = `${colWidth}px`;
    th.style.height = `${headerHeight}px`;
    th.style.minHeight = `${headerHeight}px`;
    if (isFrozen) {
      th.style.left = `${leftOffset}px`;
    }

    const isFilterActive = !!(prodFilters[i] && prodFilters[i].trim());
    const isFilterOpen = openProdFilterCols.has(i) || isFilterActive;

    th.innerHTML = `
      <div class="th-header-box">
        <div class="th-title-wrap">
          <button type="button" class="th-pin-btn ${isFrozen ? 'pinned' : ''}" data-freeze-prod="${i}" title="${isFrozen ? 'Unfreeze' : 'Freeze'} column ${escapeHtml(label)}">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" stroke="none">
              <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z"/>
            </svg>
          </button>
          <span class="th-col-name" data-freeze-prod="${i}" title="Click to ${isFrozen ? 'unfreeze' : 'freeze'} ${escapeHtml(label)}">${escapeHtml(label)}</span>
          <button type="button" class="th-chevron-btn ${isFilterActive ? 'active' : ''}" data-filter-toggle-prod="${i}" title="Filter column">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"></polyline></svg>
          </button>
        </div>
        <div class="th-filter-popup ${isFilterOpen ? '' : 'hidden'}" id="prodFilterBox_${i}">
          <input class="th-filter-input" data-col="${i}" value="${escapeHtml(prodFilters[i] || '')}" placeholder="FILTER ${escapeHtml(label)}..." />
        </div>
      </div>
      <div class="th-resizer" data-resizer="${i}" title="Drag column width (Double-click to reset)"></div>
      <div class="th-row-resizer" title="Drag header height (Double-click to reset)"></div>`;
    tr.appendChild(th);
  });

  // Action column
  const actWidth = getColWidth('ACT', 'prod');
  const thAct = document.createElement('th');
  thAct.className = 'col-action';
  thAct.style.width = `${actWidth}px`;
  thAct.style.minWidth = `${actWidth}px`;
  thAct.style.maxWidth = `${actWidth}px`;
  thAct.style.height = `${headerHeight}px`;
  thAct.style.minHeight = `${headerHeight}px`;
  thAct.innerHTML = `
    <div class="th-header-box"><span class="th-col-name">ACT</span></div>
    <div class="th-resizer" data-resizer="${table.labels.length}" title="Drag column width (Double-click to reset)"></div>
    <div class="th-row-resizer" title="Drag header height (Double-click to reset)"></div>`;
  tr.appendChild(thAct);

  thead.appendChild(tr);

  // Click handler for header freeze & filter toggling
  thead.onclick = (e) => {
    if (e.target.closest('.th-resizer') || e.target.closest('.th-row-resizer')) return;

    const freezeEl = e.target.closest('[data-freeze-prod]');
    if (freezeEl) {
      const col = parseInt(freezeEl.dataset.freezeProd, 10);
      const label = prodTable.labels[col];
      if (label) {
        if (frozenProdCols.has(label)) {
          frozenProdCols.delete(label);
        } else {
          frozenProdCols.add(label);
        }
        setSavedFrozenCols('prod', frozenProdCols);
        renderProductivity(prodTable);
      }
      return;
    }

    const chevronBtn = e.target.closest('[data-filter-toggle-prod]');
    if (chevronBtn) {
      const col = parseInt(chevronBtn.dataset.filterToggleProd, 10);
      if (openProdFilterCols.has(col)) {
        openProdFilterCols.delete(col);
      } else {
        openProdFilterCols.add(col);
      }
      const box = document.getElementById(`prodFilterBox_${col}`);
      if (box) {
        box.classList.toggle('hidden');
        if (!box.classList.contains('hidden')) {
          const inp = box.querySelector('input');
          if (inp) inp.focus();
        }
      }
    }
  };

  // Column filter input handlers
  thead.querySelectorAll('input[data-col]').forEach(inp => {
    inp.addEventListener('input', () => {
      const col = +inp.dataset.col, v = inp.value.trim().toLowerCase();
      if (v) prodFilters[col] = v; else delete prodFilters[col];
      clearTimeout(filterTimer);
      filterTimer = setTimeout(renderProductivityBody, 150);
    });
  });

  attachHeaderResizers(t, 'prod', [...table.labels, 'ACT'], () => renderProductivity(prodTable));


  $('prodEmpty').classList.add('hidden');
  t.classList.remove('hidden');
  renderProductivityBody();
}



function renderProductivityBody() {
  if (!prodTable || !prodTable.rows) return;
  const tbody = $('prodTable').querySelector('tbody');
  tbody.innerHTML = '';
  invalidateGridCache('prodTable');
  const { matchColumns, rows, columns, meta, labels } = prodTable;
  const matchSet = new Set(matchColumns || []);
  const frag = document.createDocumentFragment();
  let matched = 0, shown = 0;

  for (let rowIndex = 0; rowIndex < rows.length; rowIndex++) {
    const cells = rows[rowIndex];
    if (!passesFilters(cells)) continue;
    if (prodIssuesOnly && !prodRowHasIssue(cells)) continue;
    matched++;
    if (shown >= MAX_RENDER) continue;
    shown++;
    const tr = document.createElement('tr');

    // Check for invalid dates in row
    let hasInvalidDate = false;
    const headerList = labels || columns || [];
    headerList.forEach((h, cIdx) => {
      if (isDateColumn(h) && isInvalidDateVal(cells[cIdx])) {
        hasInvalidDate = true;
      }
    });
    if (hasInvalidDate) {
      tr.classList.add('row-invalid-date');
    }

    // Find source negotiator value for the current row
    let sourceNegVal = '';
    const sourceColIdx = (columns || labels || []).findIndex(h => isNegotiatorSourceColumn(h));
    if (sourceColIdx >= 0 && cells[sourceColIdx] !== undefined) {
      sourceNegVal = String(cells[sourceColIdx] || '').trim();
    }

    // Unassigned-match flag: a matched negotiator/sourcer is blank while the
    // source name is filled in. Tints the row (amber) and marks the blank
    // matched cell so the missing assignment is easy to find and fix. No-op
    // when the table lacks either a source-name or a matched column.
    const matchedColIdx = (columns || labels || []).findIndex(h => isMatchedNegotiatorColumn(h));
    const isBlankMatchVal = (v) => {
      const s = String(v == null ? '' : v).trim().toUpperCase();
      return s === '' || s === 'UNASSIGNED';
    };
    const refreshBlankMatchHighlight = (rowCells) => {
      if (sourceColIdx < 0 || matchedColIdx < 0) return;
      const src = String((rowCells && rowCells[sourceColIdx]) || '').trim();
      const blank = src !== '' && isBlankMatchVal(rowCells && rowCells[matchedColIdx]);
      tr.classList.toggle('row-unassigned-match', blank);
      const mtd = tr.children[matchedColIdx];
      if (mtd) mtd.classList.toggle('cell-unassigned-match', blank);
    };

    cells.forEach((c, ci) => {
      const colKey = (columns && columns[ci]) || '';
      const colLabel = (labels && labels[ci]) || colKey;
      const colMeta = (meta && meta[colKey]) || { type: 'text' };
      const colWidth = getColWidth(colLabel, 'prod');
      const isFrozen = frozenProdCols.has(colLabel);
      const isLastFrozen = isLastFrozenCol(labels || columns, ci, 'prod');
      const leftOffset = isFrozen ? getFrozenColOffset(labels || columns, ci, 'prod') : 0;

      const td = document.createElement('td');
      td.className = `${isFrozen ? 'col-frozen' : ''} ${isLastFrozen ? 'col-frozen-last' : ''}`;
      td.style.width = `${colWidth}px`;
      td.style.minWidth = `${colWidth}px`;
      td.style.maxWidth = `${colWidth}px`;
      if (isFrozen) {
        td.style.left = `${leftOffset}px`;
      }
      td.dataset.rowIdx = rowIndex;
      td.dataset.colKey = colKey;
      td.dataset.colIdx = ci;

      const valStr = String(c || '').trim();
      const valRes = validateCellValue(valStr, colMeta, colLabel);
      if (!valRes.valid) {
        td.classList.add('cell-invalid');
        td.title = valRes.error;
      }

      if (isMatchColumn(colLabel) || isMatchColumn(colKey) || matchSet.has(ci)) {
        td.innerHTML = matchBadge(valStr.toUpperCase());
      } else if (colMeta.type === 'choice' || isMatchedNegotiatorColumn(colLabel) || isMatchedNegotiatorColumn(colKey)) {
        const isMatchedNeg = isMatchedNegotiatorColumn(colLabel) || isMatchedNegotiatorColumn(colKey);
        const rawChoices = (colMeta.choices && colMeta.choices.length)
          ? colMeta.choices
          : (isMatchedNeg ? (negotiatorOptions || []) : []);
        const choices = sortChoices(rawChoices);

        const isTeamOrGroup = colKey === 'TEAM' || colKey === 'GROUP' || colLabel === 'TEAM' || colLabel === 'GROUP';
        if (!isTeamOrGroup) choices.unshift('UNASSIGNED');
        // Blank matched + filled source = "unassigned" (amber), NOT red no-match.
        const cellBlankMatch = isMatchedNeg && sourceNegVal !== '' && isBlankMatchVal(valStr);
        const isMatchedVal = isMatchedNeg && checkNegotiatorMatch(sourceNegVal, valStr);
        const matchClass = (!isMatchedNeg || cellBlankMatch) ? ''
          : (isMatchedVal ? 'match matched-negotiator-match' : 'nomatch matched-negotiator-nomatch');

        const combo = makeCombo({
          value: valStr,
          choices,
          invalid: !valRes.valid,
          extraClass: matchClass,
          title: !valRes.valid ? valRes.error : '',
          onCommit: async (rawVal, inputEl) => {
            // Non-team/group "UNASSIGNED" clears the cell (stored empty).
            const newVal = (!isTeamOrGroup && rawVal === 'UNASSIGNED') ? '' : rawVal;
            if (isMatchedNeg) {
              // Blank matched + filled source is "unassigned" (amber via the cell
              // class below), not a red no-match, so leave off match/nomatch here.
              const blankNow = sourceNegVal !== '' && isBlankMatchVal(newVal);
              if (blankNow) {
                inputEl.className = 'cell-input cell-select combo-input modified';
              } else {
                const isMatchNow = checkNegotiatorMatch(sourceNegVal, newVal);
                inputEl.className = `cell-input cell-select combo-input modified ${isMatchNow ? 'match matched-negotiator-match' : 'nomatch matched-negotiator-nomatch'}`;
              }
            } else {
              inputEl.classList.add('modified');
            }
            prodTable.rows[rowIndex][ci] = newVal;
            refreshBlankMatchHighlight(prodTable.rows[rowIndex]);
            const editRes = validateCellValue(newVal, colMeta, colLabel);
            if (!editRes.valid) {
              td.classList.add('cell-invalid');
              inputEl.classList.add('cell-invalid');
              td.title = editRes.error;
              inputEl.title = editRes.error;
            } else {
              td.classList.remove('cell-invalid');
              inputEl.classList.remove('cell-invalid');
              td.removeAttribute('title');
              inputEl.removeAttribute('title');
            }
            updateProdValidationState();
            const res = await call('update_productivity_cell', rowIndex, colKey, newVal);
            if (res && res.ok !== false) {
              toast(`Updated ${colKey}.`);
              if (res.row) {
                prodTable.rows[rowIndex] = res.row;
                const curSourceVal = (sourceColIdx >= 0 && res.row[sourceColIdx] !== undefined) ? String(res.row[sourceColIdx]).trim() : sourceNegVal;
                columns.forEach((h, hIdx) => {
                  const updatedVal = res.row[hIdx] !== undefined ? String(res.row[hIdx]).toUpperCase() : '';
                  const siblingTd = tr.children[hIdx];
                  if (siblingTd && hIdx !== ci) {
                    const siblingCtrl = siblingTd.querySelector('.cell-select, .cell-input');
                    if (siblingCtrl) {
                      siblingCtrl.value = updatedVal;
                      if (isMatchedNegotiatorColumn(h) || isMatchedNegotiatorColumn(labels && labels[hIdx])) {
                        const isMatchSibling = checkNegotiatorMatch(curSourceVal, updatedVal);
                        siblingCtrl.className = `cell-input cell-select combo-input ${isMatchSibling ? 'match matched-negotiator-match' : 'nomatch matched-negotiator-nomatch'}`;
                      }
                    } else if (matchSet.has(hIdx) || isMatchColumn(h) || isMatchColumn(labels && labels[hIdx])) {
                      siblingTd.innerHTML = matchBadge(updatedVal);
                    }
                  }
                });
              }
            } else {
              toast((res && res.error) || 'Failed to update cell.', true);
            }
            // In "issues only" view, a fixed row should leave the filtered set.
            if (prodIssuesOnly) setTimeout(renderProductivityBody, 0);
          },
        });

        td.appendChild(combo.input);
      } else if (isCalculatedColumn(colLabel) || isCalculatedColumn(colKey)) {
        const input = document.createElement('input');
        input.type = 'text';
        input.className = `cell-input cell-readonly ${!valRes.valid ? 'cell-invalid' : ''}`;
        if (!valRes.valid) input.title = valRes.error;
        input.readOnly = true;
        input.value = valStr.toUpperCase();
        input.spellcheck = false;
        td.appendChild(input);
      } else if (isDateColumn(colLabel) || isDateColumn(colKey)) {
        const input = document.createElement('input');
        input.type = 'text';
        input.maxLength = 10;
        input.placeholder = 'MM/DD/YYYY';
        input.className = `cell-input cell-date ${!valRes.valid ? 'cell-invalid cell-invalid-date' : ''}`;
        if (!valRes.valid) input.title = valRes.error;
        input.value = valStr.toUpperCase();
        input.spellcheck = false;

        let initialVal = valStr;
        input.addEventListener('focus', () => {
          initialVal = input.value;
          input.classList.remove('cell-invalid-date', 'cell-invalid');
        });

        input.addEventListener('blur', async () => {
          let rawVal = input.value.trim().toUpperCase();
          if (rawVal === initialVal) return;

          prodTable.rows[rowIndex][ci] = rawVal;
          const editRes = validateCellValue(rawVal, colMeta, colLabel);
          if (!editRes.valid) {
            input.classList.add('cell-invalid', 'cell-invalid-date');
            td.classList.add('cell-invalid');
            tr.classList.add('row-invalid-date');
            input.title = editRes.error;
            td.title = editRes.error;
            toast(`Validation Error: ${editRes.error}`, true);
          } else {
            input.classList.remove('cell-invalid', 'cell-invalid-date');
            td.classList.remove('cell-invalid');
            input.removeAttribute('title');
            td.removeAttribute('title');
            let rowStillInvalid = false;
            (labels || columns || []).forEach((h, cIdx) => {
              const checkVal = (cIdx === ci) ? rawVal : (prodTable.rows[rowIndex][cIdx] || '');
              if (isDateColumn(h) && isInvalidDateVal(checkVal)) rowStillInvalid = true;
            });
            if (!rowStillInvalid) tr.classList.remove('row-invalid-date');
          }
          updateProdValidationState();

          input.classList.add('modified');
          const res = await call('update_productivity_cell', rowIndex, colKey, rawVal);
          if (res && res.ok !== false) {
            toast(`Updated ${colKey}.`);
            if (res.row) {
              prodTable.rows[rowIndex] = res.row;
              const curSourceVal = (sourceColIdx >= 0 && res.row[sourceColIdx] !== undefined) ? String(res.row[sourceColIdx]).trim() : sourceNegVal;
              columns.forEach((h, hIdx) => {
                const updatedVal = res.row[hIdx] !== undefined ? String(res.row[hIdx]).toUpperCase() : '';
                const siblingTd = tr.children[hIdx];
                if (siblingTd && hIdx !== ci) {
                  const siblingCtrl = siblingTd.querySelector('.cell-select, .cell-input');
                  if (siblingCtrl) {
                    siblingCtrl.value = updatedVal;
                    if (isMatchedNegotiatorColumn(h) || isMatchedNegotiatorColumn(labels && labels[hIdx])) {
                      const isMatchSibling = checkNegotiatorMatch(curSourceVal, updatedVal);
                      siblingCtrl.className = `cell-select ${isMatchSibling ? 'match matched-negotiator-match' : 'nomatch matched-negotiator-nomatch'}`;
                    }
                  } else if (matchSet.has(hIdx) || isMatchColumn(h) || isMatchColumn(labels && labels[hIdx])) {
                    siblingTd.innerHTML = matchBadge(updatedVal);
                  }
                }
              });
            }
          } else {
            toast((res && res.error) || 'Failed to update cell.', true);
          }
          if (prodIssuesOnly) setTimeout(renderProductivityBody, 0);
        });

        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            input.blur();
          }
        });

        td.appendChild(input);
      } else {
        const input = document.createElement('input');
        input.type = 'text';
        input.className = `cell-input ${!valRes.valid ? 'cell-invalid' : ''}`;
        if (!valRes.valid) input.title = valRes.error;
        input.value = valStr.toUpperCase();
        input.spellcheck = false;

        let initialVal = valStr;
        input.addEventListener('focus', () => {
          initialVal = input.value;
          input.classList.remove('cell-invalid');
        });

        input.addEventListener('blur', async () => {
          let rawVal = input.value.trim().toUpperCase();
          if (rawVal === initialVal) return;

          prodTable.rows[rowIndex][ci] = rawVal;
          const editRes = validateCellValue(rawVal, colMeta, colLabel);
          if (!editRes.valid) {
            input.classList.add('cell-invalid');
            td.classList.add('cell-invalid');
            input.title = editRes.error;
            td.title = editRes.error;
            toast(`Validation Error: ${editRes.error}`, true);
          } else {
            input.classList.remove('cell-invalid');
            td.classList.remove('cell-invalid');
            input.removeAttribute('title');
            td.removeAttribute('title');
          }
          updateProdValidationState();

          input.classList.add('modified');
          const res = await call('update_productivity_cell', rowIndex, colKey, rawVal);
          if (res && res.ok !== false) {
            toast(`Updated ${colKey}.`);
            if (res.row) {
              prodTable.rows[rowIndex] = res.row;
              const curSourceVal = (sourceColIdx >= 0 && res.row[sourceColIdx] !== undefined) ? String(res.row[sourceColIdx]).trim() : sourceNegVal;
              columns.forEach((h, hIdx) => {
                const updatedVal = res.row[hIdx] !== undefined ? String(res.row[hIdx]).toUpperCase() : '';
                const siblingTd = tr.children[hIdx];
                if (siblingTd && hIdx !== ci) {
                  const siblingCtrl = siblingTd.querySelector('.cell-select, .cell-input');
                  if (siblingCtrl) {
                    siblingCtrl.value = updatedVal;
                    if (isMatchedNegotiatorColumn(h) || isMatchedNegotiatorColumn(labels && labels[hIdx])) {
                      const isMatchSibling = checkNegotiatorMatch(curSourceVal, updatedVal);
                      siblingCtrl.className = `cell-select ${isMatchSibling ? 'match matched-negotiator-match' : 'nomatch matched-negotiator-nomatch'}`;
                    }
                  } else if (matchSet.has(hIdx) || isMatchColumn(h) || isMatchColumn(labels && labels[hIdx])) {
                    siblingTd.innerHTML = matchBadge(updatedVal);
                  }
                }
              });
            }
          } else {
            toast((res && res.error) || 'Failed to update cell.', true);
          }
          if (prodIssuesOnly) setTimeout(renderProductivityBody, 0);
        });

        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            input.blur();
          }
        });

        td.appendChild(input);
      }

      tr.appendChild(td);
    });

    // Initial unassigned-match highlight (row tint + blank matched cell).
    refreshBlankMatchHighlight(cells);

    // Delete row action
    const actWidth = getColWidth('ACT', 'prod');
    const tdAct = document.createElement('td');
    tdAct.className = 'col-action';
    tdAct.style.width = `${actWidth}px`;
    tdAct.style.minWidth = `${actWidth}px`;
    tdAct.style.maxWidth = `${actWidth}px`;
    tdAct.innerHTML = `
      <button class="btn xs ghost danger rc-del-btn" data-del-prod-row="${rowIndex}" title="Delete Row">
        <span class="ico" data-icon="trash"></span>
      </button>
    `;
    tr.appendChild(tdAct);
    frag.appendChild(tr);
  }

  tbody.appendChild(frag);

  // Bind delete handlers
  tbody.querySelectorAll('[data-del-prod-row]').forEach(btn => {
    btn.addEventListener('click', () => {
      const rowIdx = parseInt(btn.dataset.delProdRow, 10);
      if (isNaN(rowIdx)) return;
      openConfirmModal({
        title: 'Delete row',
        message: `Delete productivity row #${rowIdx + 1}? This cannot be undone.`,
        proceedLabel: 'Delete', cancelLabel: 'Cancel', danger: true, hideQuestion: true,
        onProceed: async () => {
          const res = await call('delete_productivity_row', rowIdx);
          if (res && res.ok !== false) {
            toast('Row deleted.');
            if (res.state) applyState(res.state);
            if (res.table) renderProductivity(res.table);
          } else {
            toast((res && res.error) || 'Failed to delete row.', true);
          }
        }
      });
    });
  });

  applyIcons(tbody);
  updateProdValidationState();
  const issueWord = prodIssuesOnly ? ' issue' : '';
  const matchedText = shown < matched
    ? `showing ${shown} of ${matched}${issueWord} rows`
    : `${matched}${issueWord} row${matched === 1 ? '' : 's'}`;
  $('prodCount').textContent = matchedText;
}


function passesFilters(cells) {
  for (const col in prodFilters) if (!String(cells[col] ?? '').toLowerCase().includes(prodFilters[col])) return false;
  return true;
}

// A productivity row "has an issue" if any cell fails validation, a date is
// invalid, or the matched negotiator/sourcer is blank while the source name is
// filled (unassigned). Assigning ANY name resolves the unassigned issue, so a
// present-but-non-matching name (red "no-match") is NOT treated as an issue —
// it stays visible as a red cue but does not keep the row in the issues filter
// or block publishing.
function prodRowHasIssue(cells) {
  if (!prodTable) return false;
  const { columns, labels, meta } = prodTable;
  const cols = columns || labels || [];
  const labs = labels || columns || [];
  for (let i = 0; i < cols.length; i++) {
    const colKey = cols[i];
    const colLabel = labs[i] || colKey;
    if (isDateColumn(colLabel) && isInvalidDateVal(cells[i])) return true;
    const m = (meta && meta[colKey]) || { type: 'text' };
    const valStr = String(cells[i] == null ? '' : cells[i]).trim();
    if (!validateCellValue(valStr, m, colLabel).valid) return true;
  }
  const sIdx = cols.findIndex(h => isNegotiatorSourceColumn(h));
  const mIdx = cols.findIndex(h => isMatchedNegotiatorColumn(h));
  if (sIdx >= 0 && mIdx >= 0) {
    const src = String(cells[sIdx] || '').trim();
    if (src !== '') {
      const mv = String(cells[mIdx] || '').trim();
      if (mv === '' || mv.toUpperCase() === 'UNASSIGNED') return true;  // unassigned
    }
  }
  return false;
}

// Total productivity rows with any issue (invalid cell/date, unassigned match,
// or no-match). Used to gate the step-3 "Continue to publish" strip button.
function countProdIssueRows(tableData) {
  const t = tableData || prodTable;
  if (!t || !t.rows) return 0;
  let count = 0;
  for (let i = 0; i < t.rows.length; i++) {
    if (prodRowHasIssue(t.rows[i])) count++;
  }
  return count;
}
window.countProdIssueRows = countProdIssueRows;

function matchBadge(v) {
  const u = String(v || '').trim().toUpperCase();
  if (u === 'MATCH') return '<span class="badge match">MATCH</span>';
  if (u === 'NO MATCH') return '<span class="badge nomatch">NO MATCH</span>';
  if (u) return `<span class="badge match">${escapeHtml(u)}</span>`;
  return '';
}



// --- device code (from Python) ---------------------------------------------
window.showDeviceCode = function (info) {
  setBusy(false);   // sign-in is now user-driven; free the UI
  $('deviceCode').classList.remove('hidden');
  const a = $('dcUrl'); a.textContent = info.url; a.href = info.url;
  $('dcCode').textContent = info.code;
  const st = $('dvStatus'); st.textContent = 'Waiting for sign-in…'; st.className = 'dv-badge working';
};

// --- Solution Explorer -----------------------------------------------------
let allTablesList = [];
let currentPreviewLogical = null;

async function showSolutionExplorer() {
  showView('explorer');
  if (!lastState.connected) {
    $('explorerGrid').innerHTML = '<div class="empty"><p>Please connect to Dataverse first.</p></div>';
    $('explorerCount').textContent = '0 tables';
    return;
  }
  await refreshSolutionsList();
}

async function refreshSolutionsList(forceRefresh) {
  const a = api();
  if (!a) return;
  setBusy(true);
  try {
    // the explicit Refresh button drops the cached environment metadata
    if (forceRefresh && a.get_solution_tables) await a.get_solution_tables(null, true);
    const res = await a.get_solutions();
    const sel = $('solutionSelect');
    sel.innerHTML = '<option value="">All Tables / Select Solution…</option>';
    if (res && res.solutions) {
      res.solutions.forEach(s => {
        const opt = new Option(`${s.friendlyName} (${s.uniqueName})`, s.id);
        sel.appendChild(opt);
      });
    }
    await loadExplorerTables(sel.value || null);
  } catch (e) {
    toast(String(e), true);
  } finally {
    setBusy(false);
  }
}

async function loadExplorerTables(solutionId) {
  const a = api();
  if (!a) return;
  setBusy(true);
  try {
    const res = await a.get_solution_tables(solutionId || null);
    if (res && res.tables) {
      allTablesList = res.tables;
      renderExplorerGrid();
    } else {
      allTablesList = [];
      $('explorerGrid').innerHTML = '<div class="empty"><p>No tables found in this solution.</p></div>';
      $('explorerCount').textContent = '0 tables';
    }
  } catch (e) {
    toast(String(e), true);
  } finally {
    setBusy(false);
  }
}

function renderExplorerGrid() {
  const query = ($('tableSearch').value || '').trim().toLowerCase();
  const filtered = allTablesList.filter(t => 
    !query || (t.displayName && t.displayName.toLowerCase().includes(query)) || (t.logicalName && t.logicalName.toLowerCase().includes(query))
  );
  $('explorerCount').textContent = `${filtered.length} table${filtered.length === 1 ? '' : 's'}`;
  const grid = $('explorerGrid');
  grid.innerHTML = '';
  if (!filtered.length) {
    grid.innerHTML = '<div class="empty"><p>No matching tables found.</p></div>';
    return;
  }
  const frag = document.createDocumentFragment();
  filtered.forEach(t => {
    const card = document.createElement('div');
    card.className = 'table-card';
    const customBadge = t.isCustom ? '<span class="badge custom">Custom</span>' : '<span class="badge standard">Standard</span>';
    card.innerHTML = `
      <div class="tc-header">
        <span class="tc-title"><span class="ico" data-icon="table"></span>${escapeHtml(t.displayName)}</span>
        ${customBadge}
      </div>
      <div class="tc-actions">
        <button class="btn xs" data-preview-tbl="${escapeHtml(t.logicalName)}"><span class="ico" data-icon="table"></span> Preview</button>
        <select class="select rc-recent" data-map-tbl="${escapeHtml(t.logicalName)}">
          <option value="">Map as…</option>
          <option value="team">Team Composition</option>
          <option value="municipality">Municipality Code</option>
          <option value="mapping">Mapping Status</option>
          <option value="build">Build Table</option>
          <option value="existing">Existing Records</option>
        </select>
      </div>
    `;
    frag.appendChild(card);
  });
  grid.appendChild(frag);

  grid.querySelectorAll('[data-preview-tbl]').forEach(b => {
    b.addEventListener('click', () => previewDataverseTable(b.dataset.previewTbl));
  });
  grid.querySelectorAll('[data-map-tbl]').forEach(sel => {
    sel.addEventListener('change', () => {
      if (sel.value) {
        fire('load_solution_reference_table', sel.value, sel.dataset.mapTbl);
        sel.value = '';
      }
    });
  });
  applyIcons(grid);
}

async function previewDataverseTable(logicalName) {
  const a = api();
  if (!a) return;
  setBusy(true);
  try {
    currentPreviewLogical = logicalName;
    const res = await a.browse_dataverse_table(logicalName);
    if (!res || res.ok === false) {
      toast((res && res.error) || 'Could not fetch table preview.', true);
      return;
    }
    $('previewTableName').textContent = res.title || 'Table Preview';
    $('previewRowCount').textContent = `${res.count} rows`;
    $('previewTableSource').textContent = '';

    
    const t = $('previewTable'), thead = t.querySelector('thead'), tbody = t.querySelector('tbody');
    thead.innerHTML = ''; tbody.innerHTML = '';
    const tr = document.createElement('tr');
    (res.labels || []).forEach(l => {
      const th = document.createElement('th');
      th.innerHTML = `<div class="th-inner">${escapeHtml(l)}</div>`;
      tr.appendChild(th);
    });
    thead.appendChild(tr);

    const frag = document.createDocumentFragment();
    (res.rows || []).forEach(r => {
      const row = document.createElement('tr');
      r.forEach(cell => {
        const td = document.createElement('td');
        td.textContent = cell;
        row.appendChild(td);
      });
      frag.appendChild(row);
    });
    tbody.appendChild(frag);

    t.classList.remove('hidden');
    $('cardTablePreview').classList.remove('hidden');
    $('cardTablePreview').scrollIntoView({ behavior: 'smooth' });
  } catch (e) {
    toast(String(e), true);
  } finally {
    setBusy(false);
  }
}

// --- helpers ----------------------------------------------------------------
function isNumeric(v) { return v !== '' && !isNaN(v); }
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

// --- wiring -----------------------------------------------------------------

let wired = false;
function wire() {
  if (wired) return;   // guard: pywebview can trigger boot paths more than once
  wired = true;
  document.querySelectorAll('.nav-item[data-view]').forEach(n => n.addEventListener('click', () => {
    if (n.dataset.view === 'explorer') showSolutionExplorer();
    else showView(n.dataset.view);
  }));
  document.querySelectorAll('.nav-item[data-ref]').forEach(n => n.addEventListener('click', () => showReference(n.dataset.ref)));

  const modeTgl = $('modeToggle');
  if (modeTgl) {
    modeTgl.addEventListener('click', async (e) => {
      const btn = e.target.closest('button'); if (!btn) return;
      const targetMode = btn.dataset.mode;
      if (btn.classList.contains('active') && !btn.classList.contains('loading-pulse')) return;

      // 1. Instant feedback: highlight clicked button and show loading spinner
      document.querySelectorAll('#modeToggle button').forEach(b => {
        b.classList.toggle('active', b === btn);
        b.disabled = true;
      });
      btn.classList.add('loading-pulse');
      modeTgl.classList.add('is-loading');
      setBusy(true);

      try {
        // Mark all reference tables as needing to be re-fetched
        REF_TABLES.forEach(({ kind }) => unfetchedRefKinds.add(kind));

        // Auto-select candidate table for the new mode if tables are available
        if (activeSolutionTables && activeSolutionTables.length) {
          const findBest = (...kws) => {
            for (const kw of kws) {
              const match = activeSolutionTables.find(t => (t.logicalName || '').toLowerCase().includes(kw) || (t.displayName || '').toLowerCase().includes(kw));
              if (match) return match.logicalName;
            }
            return '';
          };
          if (targetMode === 'land-sourcing') {
            const bestSourcing = findBest('sourcing record', 'batangassourcingrecord', 'sourcingrecord', 'sourcing');
            if (bestSourcing) selectedTableMappings.existing = bestSourcing;
            const bestProd = findBest('sourcingproductivity', 'sourcing productivity', 'productivity', 'prod');
            if (bestProd) selectedProdTargetTable = bestProd;
          } else {
            const bestNego = findBest('nego record', 'batangasnegorecord', 'negorecord', 'nego');
            if (bestNego) selectedTableMappings.existing = bestNego;
            const bestProd = findBest('negoproductivity', 'nego productivity', 'productivity', 'prod');
            if (bestProd) selectedProdTargetTable = bestProd;
          }
        }

        // Clear local state row counts and reference data
        if (lastState) {
          lastState.inputName = '';
          lastState.rowCount = 0;
          lastState.productivityCount = 0;
          lastState.hasCalculated = false;
          lastState.recordsPublishedClean = false;
          lastState.refCounts = { teams: 0, municipality: 0, mapping: 0, build: 0, existing: 0 };
          lastState.refSources = {};
        }

        const res = await call('set_mode', targetMode);
        if (res) applyState(res.state || res);
        renderRefGrid(lastState);
        updateUploadActionState(lastState);

        if ($('reviewTable')) $('reviewTable').classList.add('hidden');
        if ($('reviewEmpty')) $('reviewEmpty').classList.remove('hidden');
        if ($('prodTable')) $('prodTable').classList.add('hidden');
        if ($('prodEmpty')) $('prodEmpty').classList.remove('hidden');
        prodTable = null;
        currentReviewTable = null;
        showView('load');
        toast(`Switched load type to ${targetMode === 'land-sourcing' ? 'Land Sourcing' : 'Negotiation'}. Please fetch reference tables to continue.`, false);
      } finally {
        btn.classList.remove('loading-pulse');
        modeTgl.classList.remove('is-loading');
        document.querySelectorAll('#modeToggle button').forEach(b => { b.disabled = false; });
        setBusy(false);
      }
    });
  }


  const btnConn = $('btnConnect');
  if (btnConn) {
    btnConn.addEventListener('click', () => {
      const st = $('dvStatus');
      if (st) { st.textContent = 'Connecting…'; st.className = 'dv-badge working'; }
      fire('connect_dataverse');
    });
  }


  
  // Dataverse Reference Controls
  const solSel = $('refSolutionSelect');
  if (solSel) {
    solSel.addEventListener('change', (e) => onSolutionChange(e.target.value));
  }
  const btnFetchAll = $('btnFetchAllDv');
  if (btnFetchAll) {
    btnFetchAll.addEventListener('click', () => {
      const kindsToFetch = Object.keys(selectedReferenceKinds).filter(k => selectedReferenceKinds[k]);
      if (!kindsToFetch.length) {
        toast('Please check at least one table to fetch.', true);
        return;
      }
      btnFetchAll.disabled = true;
      btnFetchAll.innerHTML = `<span class="ico spinning" data-icon="refresh"></span> Fetching ${kindsToFetch.length} Table${kindsToFetch.length > 1 ? 's' : ''}…`;
      applyIcons(btnFetchAll);
      kindsToFetch.forEach(k => {
        const card = document.querySelector(`.ref-card input[data-ref-check="${k}"]`)?.closest('.ref-card');
        if (card) card.classList.add('loading-pulse');
      });
      toast(`Fetching ${kindsToFetch.length} reference table(s) live from Dataverse…`, false);
      fire('fetch_dataverse_tables', selectedTableMappings, kindsToFetch);
    });
  }

  const btnSelAll = $('btnSelectAllRefs');
  if (btnSelAll) {
    btnSelAll.addEventListener('click', () => {
      REF_TABLES.forEach(({ kind }) => { selectedReferenceKinds[kind] = true; });
      renderRefGrid(lastState);
    });
  }
  const btnDeselAll = $('btnDeselAllRefs');
  if (btnDeselAll) {
    btnDeselAll.addEventListener('click', () => {
      REF_TABLES.forEach(({ kind }) => { selectedReferenceKinds[kind] = false; });
      renderRefGrid(lastState);
    });
  }

  const btnClosePrev = $('btnClosePreview');
  if (btnClosePrev) {
    btnClosePrev.addEventListener('click', () => {
      if ($('cardTablePreview')) $('cardTablePreview').classList.add('hidden');
      currentPreviewLogical = null;
    });
  }

  const dcCopyBtn = $('dcCopy');
  if (dcCopyBtn) dcCopyBtn.addEventListener('click', () => { navigator.clipboard && navigator.clipboard.writeText($('dcCode').textContent); toast('Code copied.', false); });


  const inputActionRow = $('inputActionRow');
  if (inputActionRow) {
    inputActionRow.addEventListener('click', () => {
      if (inputActionRow.classList.contains('disabled')) {
        toast('Please load reference tables in Section A first.', true);
        return;
      }
      fire('load_input');
    });
  }

  const btnChangeInput = $('btnChangeInput');
  if (btnChangeInput) btnChangeInput.addEventListener('click', () => fire('load_input'));

  const btnSaveSource = $('btnSaveSource');
  if (btnSaveSource) btnSaveSource.addEventListener('click', () => fire('reflect_to_source_file'));

  
  const btnCalc = $('btnCalculate');
  if (btnCalc) {
    btnCalc.addEventListener('click', async () => {
      const res = await call('calculate', false);
      if (res && res.ok !== false) {
        if (res.table && res.table.buildErrors && res.table.buildErrors.length > 0 && !res.table.useCurrentBuild) {
          const errs = res.table.buildErrors;
          openConfirmModal({
            title: 'Build Mismatch Detected',
            message: `${errs.length} record${errs.length > 1 ? 's have' : ' has a'} Build work-week mismatch (record date work-week does not match Build table work-week).\n\nWould you like to use the current Build table as reference anyway (derive BUILD without work-week filtering)?`,
            proceedLabel: 'Use Current Build as Reference',
            cancelLabel: 'Keep Strict Matching',
            onProceed: async () => {
              const res2 = await call('calculate', true);
              if (res2 && res2.table) renderReview(res2.table);
            },
          });
        }
      }
    });
  }

  const btnIssuesOnly = $('btnReviewIssuesOnly');
  if (btnIssuesOnly) btnIssuesOnly.addEventListener('click', () => {
    reviewIssuesOnly = !reviewIssuesOnly;
    btnIssuesOnly.classList.toggle('primary', reviewIssuesOnly);
    btnIssuesOnly.classList.toggle('ghost', !reviewIssuesOnly);
    btnIssuesOnly.setAttribute('aria-pressed', reviewIssuesOnly ? 'true' : 'false');
    btnIssuesOnly.textContent = reviewIssuesOnly ? 'Show all rows' : 'Show issues only';
    renderReviewBody();
  });

  const btnProdIssues = $('btnProdIssuesOnly');
  if (btnProdIssues) btnProdIssues.addEventListener('click', () => {
    prodIssuesOnly = !prodIssuesOnly;
    btnProdIssues.classList.toggle('primary', prodIssuesOnly);
    btnProdIssues.classList.toggle('ghost', !prodIssuesOnly);
    btnProdIssues.setAttribute('aria-pressed', prodIssuesOnly ? 'true' : 'false');
    btnProdIssues.textContent = prodIssuesOnly ? 'Show all rows' : 'Show issues only';
    renderProductivityBody();
  });

  const btnGen = $('btnGenerate');
  if (btnGen) btnGen.addEventListener('click', () => confirmBuildMismatch(() => fire('generate')));

  // --- Publish to Dataverse Modal Logic ---
  let currentValidationResult = null;


  async function runPublishValidation(mode, targetTable) {
    const valBox = $('modalPublishValBox');
    const valTitle = $('mvbTitle');
    const valErrors = $('mvbErrors');
    const fixBtn = $('modalPublishFixErrors');
    const confirmBtn = $('modalPublishConfirm');
    const stageWord = (mode === 'review') ? 'records' : 'productivity rows';

    if (!valBox) return;
    skipRecordsToProd = false;
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = (mode === 'review') ? 'Publish records' : 'Publish productivity';
    }

    if (!targetTable) {
      valBox.className = 'pm-val-box';
      valTitle.textContent = 'Select a target table to validate.';
      valErrors.classList.add('hidden');
      if (fixBtn) fixBtn.classList.add('hidden');
      if (confirmBtn) confirmBtn.disabled = true;
      return;
    }

    valBox.className = 'pm-val-box validating';
    valTitle.textContent = 'Validating & checking duplicates in Dataverse…';
    valErrors.classList.add('hidden');
    valErrors.innerHTML = '';
    if (fixBtn) fixBtn.classList.add('hidden');
    if (confirmBtn) confirmBtn.disabled = true;

    document.querySelectorAll('tbody tr.publish-error-row').forEach(r => r.classList.remove('publish-error-row'));

    const overrideEl = $('modalPublishOverride');
    const override = !!(overrideEl && overrideEl.checked);

    try {
      const res = await call('validate_publish_records', mode, targetTable, override);
      currentValidationResult = res;

      if (!res || res.ok === false) {
        valBox.className = 'pm-val-box has-errors';
        valTitle.textContent = (res && res.error) ? `Validation error: ${res.error}` : 'Validation failed (no response).';
        if (confirmBtn) confirmBtn.disabled = true;
        return;
      }

      const writable = res.writableCount || 0;
      const dupes = res.duplicateCount || 0;
      const updatable = res.updatableCount || 0;
      const hard = res.errorCount || 0;
      skipRecordsToProd = false;

      // Hard errors (bad dates) BLOCK the publish and must be fixed in the grid.
      if (hard > 0) {
        valBox.className = 'pm-val-box has-errors';
        valTitle.innerHTML = `⚠️ <b>${hard} record${hard === 1 ? '' : 's'} need${hard === 1 ? 's' : ''} fixing</b> before publishing (${writable} ready, ${dupes} duplicate).`;
        let errHtml = '';
        (res.errors || []).forEach(err => {
          const rowBadge = `<span class="row-badge">Row #${err.row}</span>`;
          errHtml += `<div class="mvb-error-item">${rowBadge}<span>${escapeHtml((err.issues || []).join('; '))}</span></div>`;
          const tblId = (mode === 'review') ? 'reviewTable' : 'prodTable';
          const tableEl = document.getElementById(tblId);
          if (tableEl) {
            const tr = tableEl.querySelector(`tbody tr:nth-child(${err.row})`);
            if (tr) tr.classList.add('publish-error-row');
          }
        });
        valErrors.innerHTML = errHtml;
        valErrors.classList.remove('hidden');
        if (fixBtn) fixBtn.classList.remove('hidden');
        if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Fix errors to publish'; }
        return;
      }

      // No hard errors. Actionable = new rows to write + (override) duplicates to
      // update. Publish is allowed as long as there is at least one of those.
      const actionable = writable + updatable;

      if (actionable === 0) {
        if (fixBtn) fixBtn.classList.add('hidden');
        // Stage 4, everything already in Dataverse and not overriding: nothing to
        // write, but the records ARE all present, so let the user skip to stage 5.
        if (mode === 'review' && dupes > 0) {
          skipRecordsToProd = true;
          valBox.className = 'pm-val-box valid';
          valTitle.textContent = `All ${dupes} records already exist in Dataverse — nothing to write. Skip to publish productivity.`;
          if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.dataset.isFinish = 'false';
            confirmBtn.textContent = 'Skip anyway → productivity';
          }
          return;
        }
        valBox.className = 'pm-val-box valid';
        valTitle.textContent = dupes > 0
          ? `Nothing new to publish — all ${dupes} ${stageWord} already exist in Dataverse.`
          : `No ${stageWord} to publish.`;
        if (confirmBtn) {
          if (mode === 'prod' || mode === 'productivity') {
            confirmBtn.disabled = false;
            confirmBtn.dataset.isFinish = 'true';
            confirmBtn.textContent = 'Finish';
          } else {
            confirmBtn.disabled = true;
            confirmBtn.dataset.isFinish = 'false';
            confirmBtn.textContent = 'Nothing to publish';
          }
        }
        return;
      }

      valBox.className = 'pm-val-box valid';
      const bits = [];
      if (writable) bits.push(`${writable} ${stageWord} ready to publish`);
      if (updatable) bits.push(`${updatable} duplicate${updatable === 1 ? '' : 's'} will be overwritten`);
      if (dupes) bits.push(`${dupes} duplicate${dupes === 1 ? '' : 's'} will be skipped`);
      valTitle.textContent = bits.join(' · ') + '.';
      if (fixBtn) fixBtn.classList.add('hidden');
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.dataset.isFinish = 'false';
        confirmBtn.textContent = (updatable && !writable)
          ? `Update ${updatable} ${stageWord}`
          : `Publish ${actionable} ${stageWord}`;
      }
    } catch (e) {
      valBox.className = 'pm-val-box has-errors';
      valTitle.textContent = 'Validation error: ' + e;
      if (confirmBtn) confirmBtn.disabled = true;
    }
  }

  function openPublishModal(mode) {
    currentPublishMode = mode;
    window.currentPublishMode = mode;
    const pubView = $('view-publish');
    if (pubView) pubView.dataset.pubstage = (mode === 'prod' || mode === 'productivity') ? 'prod' : 'records';
    const modal = $('modalPublish');
    if (!modal) return;

    const sel = $('modalPublishTableSelect');
    const rowCountEl = $('modalPublishRowCount');
    const wsEl = $('modalPublishWorkspace');
    const solEl = $('modalPublishSolutionName');
    const descEl = $('modalPublishDesc');
    const statusPill = $('modalPublishStatus');
    const statusText = $('modalPublishStatusText');

    const dvStatusEl = $('dvStatus');
    const stateObj = lastState || window.lastState;
    const isConnected = !!((stateObj && stateObj.connected) || (dvStatusEl && dvStatusEl.classList.contains('connected')));
    if (statusPill && statusText) {
      if (isConnected) {
        statusPill.className = 'pm-status-pill';
        statusText.textContent = 'Connected';
      } else {
        statusPill.className = 'pm-status-pill offline';
        statusText.textContent = 'Not Connected';
      }
    }

    const solSelect = $('refSolutionSelect') || $('solutionSelect');
    const solText = (solSelect && solSelect.options[solSelect.selectedIndex] ? solSelect.options[solSelect.selectedIndex].text : '') || 'SOURCING AND NEGO (BATANGASNEGO)';
    if (solEl) solEl.textContent = solText;

    if (sel) {
      sel.innerHTML = '';
      const tables = (activeSolutionTables && activeSolutionTables.length) ? activeSolutionTables : (window.activeSolutionTables || []);

      const titleEl = document.querySelector('#publishForm .pm-title');
      if (mode === 'review') {
        if (titleEl) titleEl.textContent = 'Stage 4 · Publish records';
        if (wsEl) wsEl.textContent = 'CSV Workspace (record rows)';
        if (descEl) descEl.textContent = 'Create the calculated records in the record table. Duplicates already in Dataverse are skipped; nothing is overwritten.';
        const rCount = stateObj ? (stateObj.rowCount || 0) : 0;
        if (rowCountEl) rowCountEl.textContent = `${rCount} rows`;

        const curMode = ((stateObj ? stateObj.mode : 'negotiation') + '').toLowerCase();
        const modeKey = (curMode === 'land-sourcing') ? 'sourcing' : 'nego';
        const existingTables = getTablesForKind('existing', curMode);
        const tableList = (existingTables && existingTables.length) ? existingTables : tables;

        if (!tableList.length) {
          sel.innerHTML = '<option value="">No tables found in solution. Please fetch solution tables first.</option>';
        } else {
          tableList.forEach(t => {
            const opt = new Option(t.displayName || t.logicalName, t.logicalName);
            sel.appendChild(opt);
          });

          // Check mode-specific saved preference
          const savedPref = (stateObj && stateObj.uiSettings && stateObj.uiSettings[`${modeKey}_record_table`])
            || selectedTableMappings.existing
            || localStorage.getItem(`dv_${modeKey}_record_table`) || '';

          if (savedPref && tableList.some(t => t.logicalName === savedPref)) {
            sel.value = savedPref;
          } else {
            // Smart auto-detect: best keyword match for current mode
            const best = tableList.find(t => {
              const name = ((t.displayName || '') + ' ' + (t.logicalName || '')).toUpperCase();
              return (modeKey === 'sourcing')
                ? (name.includes('SOURCING') && name.includes('RECORD'))
                : (name.includes('NEGO') && name.includes('RECORD'));
            });
            sel.value = best ? best.logicalName : tableList[0].logicalName;
          }
        }
      } else {
        if (titleEl) titleEl.textContent = 'Stage 5 · Publish productivity';
        if (wsEl) wsEl.textContent = 'Productivity Workspace (generated rows)';
        if (descEl) descEl.textContent = 'Create the productivity rows in the productivity table. Duplicates are skipped; nothing is overwritten.';
        const prodRows = (prodTable && prodTable.rows) ? prodTable.rows.length : (stateObj ? (stateObj.productivityCount || stateObj.rowCount || 0) : 0);
        if (rowCountEl) rowCountEl.textContent = `${prodRows} rows`;

        // Limit to productivity tables for the current load type (nego vs sourcing),
        // exactly like the record table is limited on stage 4.
        const curMode = ((stateObj ? stateObj.mode : 'negotiation') + '').toLowerCase();
        const modeKey = (curMode === 'land-sourcing') ? 'sourcing' : 'nego';
        const prodTables = getTablesForKind('productivity', curMode);
        const prodList = (prodTables && prodTables.length) ? prodTables : tables;
        if (!prodList.length) {
          sel.innerHTML = '<option value="">No tables found in solution. Please fetch solution tables first.</option>';
        } else {
          prodList.forEach(t => {
            const opt = new Option(t.displayName || t.logicalName, t.logicalName);
            sel.appendChild(opt);
          });

          // Top priority: the productivity table that matches the selected
          // record region (BATANGAS NEGO RECORD -> BATANGAS NEGO PRODUCTIVITY).
          const derivedProd = deriveProductivityForRecord(selectedTableMappings.existing, prodList);

          const savedPref = derivedProd
            || (stateObj && stateObj.uiSettings && stateObj.uiSettings[`${modeKey}_productivity_table`])
            || selectedProdTargetTable
            || localStorage.getItem(`dv_${modeKey}_prod_target_table`)
            || localStorage.getItem('dv_prod_target_table') || '';

          if (savedPref && prodList.some(t => t.logicalName === savedPref)) {
            sel.value = savedPref;
          } else {
            // Smart auto-detect: best keyword match for current mode
            const best = prodList.find(t => {
              const name = ((t.displayName || '') + ' ' + (t.logicalName || '')).toUpperCase();
              return (modeKey === 'sourcing')
                ? (name.includes('SOURCING') && name.includes('PRODUCTIVITY'))
                : (name.includes('NEGO') && name.includes('PRODUCTIVITY'));
            });
            sel.value = best ? best.logicalName : prodList[0].logicalName;
          }
        }
      }
    }

    showPublishForm();
    showView('publish');

    if (sel && sel.value) {
      runPublishValidation(mode, sel.value);
    }
  }

  window.runPublishValidation = runPublishValidation;
  window.openPublishModal = openPublishModal;




  const pubTableSelect = $('modalPublishTableSelect');
  if (pubTableSelect) {
    pubTableSelect.addEventListener('change', () => {
      runPublishValidation(currentPublishMode, pubTableSelect.value);
    });
  }

  // Toggling "override existing" re-validates so the counts (overwrite vs skip)
  // and the publish button label refresh immediately.
  const pubOverride = $('modalPublishOverride');
  if (pubOverride) {
    pubOverride.addEventListener('change', () => {
      const sel = $('modalPublishTableSelect');
      if (sel && sel.value) runPublishValidation(currentPublishMode, sel.value);
    });
  }

  function closePublishModal() {
    // stage 4 is a view now: "close" means step back to the rows it publishes
    showView(currentPublishMode === 'review' ? 'review' : 'productivity');
  }

  const modalClose = $('modalPublishClose'); if (modalClose) modalClose.addEventListener('click', closePublishModal);
  const modalCancel = $('modalPublishCancel'); if (modalCancel) modalCancel.addEventListener('click', closePublishModal);
  const modalBackdrop = $('modalPublishBackdrop'); if (modalBackdrop) modalBackdrop.addEventListener('click', closePublishModal);

  const modalFixErrors = $('modalPublishFixErrors');
  if (modalFixErrors) {
    modalFixErrors.addEventListener('click', () => {
      closePublishModal();
      showView(currentPublishMode === 'review' ? 'review' : 'productivity');
      const firstErrorRow = document.querySelector('tbody tr.publish-error-row');
      if (firstErrorRow) {
        firstErrorRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      toast('Highlighted error / duplicate rows in red. Please review and fix them.', false);
    });
  }

  const modalConfirm = $('modalPublishConfirm');
  if (modalConfirm) {
    modalConfirm.addEventListener('click', () => {
      if (modalConfirm.dataset.isFinish === 'true' || modalConfirm.textContent.trim() === 'Finish') {
        if (window.finishWorkflow) {
          window.finishWorkflow();
        }
        return;
      }

      const sel = $('modalPublishTableSelect');
      const targetTable = sel ? sel.value : '';
      if (!targetTable) {
        toast('Please select a target Dataverse table.', true);
        return;
      }
      const overrideEl = $('modalPublishOverride');
      const override = !!(overrideEl && overrideEl.checked);

      // Build mismatch warning (records stage only): warn + confirm, don't block.
      if (currentPublishMode === 'review' && !modalConfirm.dataset.buildConfirmed) {
        const berrs = (currentReviewTable && currentReviewTable.buildErrors) || [];
        if (berrs.length) {
          openConfirmModal({
            title: 'Build Mismatches',
            message: `${berrs.length} Build Mismatch${berrs.length > 1 ? 'es' : ''}: Verify that the Build Table is updated and the report or nego date is correct. Fix all red cells to generate productivity.`,
            proceedLabel: 'Publish anyway',
            onProceed: () => { modalConfirm.dataset.buildConfirmed = '1'; modalConfirm.click(); },
          });
          return;
        }
      }
      delete modalConfirm.dataset.buildConfirmed;

      const saveConfig = $('modalPublishSaveConfig') ? $('modalPublishSaveConfig').checked : true;
      const curMode = ((lastState && lastState.mode) || 'negotiation').toLowerCase();
      const modeKey = (curMode === 'land-sourcing') ? 'sourcing' : 'nego';

      // "Skip anyway": nothing to write (all records already in Dataverse) — jump
      // straight to stage 5 without a publish round-trip / "Publishing…" spinner.
      if (currentPublishMode === 'review' && skipRecordsToProd) {
        if (saveConfig) {
          selectedTableMappings.existing = targetTable;
          localStorage.setItem(`dv_${modeKey}_record_table`, targetTable);
          const uiUpdate = { existing_table: targetTable };
          uiUpdate[`${modeKey}_record_table`] = targetTable;
          call('save_ui_settings', uiUpdate);
        }
        skipRecordsToProd = false;
        window.__afterRecordsToProd = false;
        call('mark_records_skipped').then(() => {
          toast('All records already in Dataverse — continuing to productivity.', false);
          if (window.openPublishModal) openPublishModal('prod');
        });
        return;
      }

      // Show publishing animation
      modalConfirm.disabled = true;
      modalConfirm.dataset.origText = modalConfirm.textContent;
      modalConfirm.innerHTML = '<span class="ico spinning" data-icon="refresh"></span> Publishing…';
      applyIcons(modalConfirm);

      if (currentPublishMode === 'review') {
        if (saveConfig) {
          selectedTableMappings.existing = targetTable;
          localStorage.setItem(`dv_${modeKey}_record_table`, targetTable);
          const uiUpdate = { existing_table: targetTable };
          uiUpdate[`${modeKey}_record_table`] = targetTable;
          call('save_ui_settings', uiUpdate);
        }
        // "Skip anyway": all records already exist. Still run the (empty) publish
        // so the backend confirms them present and unlocks stage 5, then jump there.
        window.__afterRecordsToProd = !!skipRecordsToProd;
        fire('publish_review_records', targetTable, override);
        toast(skipRecordsToProd
          ? 'All records already in Dataverse — continuing to productivity…'
          : `Publishing CSV workspace records to '${targetTable}'…`, false);
      } else {
        if (saveConfig) {
          selectedProdTargetTable = targetTable;
          localStorage.setItem(`dv_${modeKey}_prod_target_table`, targetTable);
          localStorage.setItem('dv_prod_target_table', targetTable);
          const prodTblSel = $('prodTargetTableSelect');
          if (prodTblSel) prodTblSel.value = targetTable;
          call('save_productivity_table_config', targetTable, curMode);
          const uiUpdate = { productivity_table: targetTable };
          uiUpdate[`${modeKey}_productivity_table`] = targetTable;
          call('save_ui_settings', uiUpdate);
        }
        fire('publish_productivity_records', targetTable, override);
        toast(`Publishing productivity records to '${targetTable}'…`, false);
      }
    });
  }


  const btnPublishRev = $('btnPublishReview');
  if (btnPublishRev) {
    btnPublishRev.addEventListener('click', () => {
      if (!lastState || !lastState.connected) {
        toast('Please connect to Dataverse first.', true);
        return;
      }
      openPublishModal('review');
    });
  }

  const prodTblSel = $('prodTargetTableSelect');
  if (prodTblSel) {
    prodTblSel.addEventListener('change', () => {
      const curMode = ((lastState && lastState.mode) || 'negotiation').toLowerCase();
      const modeKey = (curMode === 'land-sourcing') ? 'sourcing' : 'nego';
      selectedProdTargetTable = prodTblSel.value;
      localStorage.setItem(`dv_${modeKey}_prod_target_table`, prodTblSel.value);
      localStorage.setItem('dv_prod_target_table', prodTblSel.value);
      if (prodTblSel.value) {
        call('save_productivity_table_config', prodTblSel.value, curMode);
        const uiUpdate = { productivity_table: prodTblSel.value };
        uiUpdate[`${modeKey}_productivity_table`] = prodTblSel.value;
        call('save_ui_settings', uiUpdate);
      }
    });
  }

  const btnPublishProd = $('btnPublishProd');
  if (btnPublishProd) {
    btnPublishProd.addEventListener('click', () => {
      if (!lastState || !lastState.connected) {
        toast('Please connect to Dataverse first.', true);
        return;
      }
      openPublishModal('prod');
    });
  }
  
  const btnClrFilters = $('btnClearFilters');
  if (btnClrFilters) btnClrFilters.addEventListener('click', () => { prodFilters = {}; document.querySelectorAll('#prodTable input.filter').forEach(i => i.value = ''); renderProductivityBody(); });

  // Template download buttons
  document.querySelectorAll('[data-inputtpl]').forEach(btn => {
    btn.addEventListener('click', () => {
      const mode = btn.dataset.inputtpl;
      if (mode) downloadInputTemplate(mode);
    });
  });





  const refViewerSel = $('refViewerTableSelect');
  if (refViewerSel) {
    refViewerSel.addEventListener('change', async () => {
      if (currentRefKind) {
        selectedTableMappings[currentRefKind] = refViewerSel.value;
        unfetchedRefKinds.add(currentRefKind);
        if (lastState && lastState.refCounts) {
          const countKey = { municipality: 'municipality', team: 'teams', mapping: 'mapping', build: 'build', existing: 'existing' };
          lastState.refCounts[countKey[currentRefKind]] = 0;
        }
        if (lastState && lastState.refSources) {
          delete lastState.refSources[currentRefKind];
        }
        renderRefGrid(lastState);
        updateUploadActionState(lastState);
        const a = api();
        if (a && a.clear_reference_table) {
          try {
            const res = await a.clear_reference_table(currentRefKind);
            if (res && res.state) {
              lastState = res.state;
              updateUploadActionState(lastState);
            }
          } catch (e) {}
        }
      }
    });
  }
  const refFetchBtn = $('refFetchDv');
  if (refFetchBtn) {
    refFetchBtn.addEventListener('click', () => {
      if (currentRefKind) {
        const logical = selectedTableMappings[currentRefKind] || '';
        fire('fetch_dataverse_tables', { [currentRefKind]: logical }, [currentRefKind]);
      }
    });
  }
  const refRefreshBtn = $('refRefresh');
  if (refRefreshBtn) {
    refRefreshBtn.addEventListener('click', async () => {
      if (!currentRefKind) return;
      if (lastState && lastState.connected) {
        const logical = selectedTableMappings[currentRefKind] || '';
        toast('Refreshing table live from Dataverse…', false);
        fire('fetch_dataverse_tables', { [currentRefKind]: logical }, [currentRefKind]);
      } else {
        const a = api();
        if (a) {
          const res = await a.get_reference_rows(currentRefKind);
          renderReferenceView(res);
          toast('View refreshed.', false);
        }
      }
    });
  }
  const refAddRowBtn = $('refAddRow');
  if (refAddRowBtn) {
    refAddRowBtn.addEventListener('click', async () => {
      if (!currentRefKind) return;
      const res = await call('add_reference_row', currentRefKind, {});
      if (res && res.ok !== false) {
        if (!currentRefState) {
          const a = api();
          if (a) {
            const data = await a.get_reference_rows(currentRefKind);
            renderReferenceView(data);
          }
        } else {
          const newRow = (currentRefState.keys || []).map(() => '');
          currentRefState.rows.unshift(newRow);
          currentRefState.count = currentRefState.rows.length;
          renderReferenceBody();
          toast(res.msg || 'Added new row at top. Click any cell to edit.', false);
        }
      }
    });
  }
  const refClearFiltersBtn = $('refClearFilters');
  if (refClearFiltersBtn) {
    refClearFiltersBtn.addEventListener('click', () => {
      refFilters = {};
      refSearchQuery = '';
      const inp = $('refSearchInput'); if (inp) inp.value = '';
      document.querySelectorAll('#refTable input.filter').forEach(i => i.value = '');
      renderReferenceBody();
    });
  }
  const refSearchInp = $('refSearchInput');
  if (refSearchInp) {
    refSearchInp.addEventListener('input', (e) => {
      refSearchQuery = e.target.value;
      clearTimeout(refFilterTimer);
      refFilterTimer = setTimeout(renderReferenceBody, 120);
    });
  }
  const refUploadBtn = $('refUploadFile');
  if (refUploadBtn) {
    refUploadBtn.addEventListener('click', () => {
      if (!currentRefKind) return;
      fire('upload_reference_file', currentRefKind);
    });
  }
  const refBuildSyncBtn = $('refBuildSync');
  if (refBuildSyncBtn) {
    refBuildSyncBtn.addEventListener('click', () => {
      const k = currentRefKind;
      if (!REF_SYNC_KINDS.includes(k)) return;
      if (!(lastState && lastState.connected)) { toast('Connect to Dataverse first.', true); return; }
      if (k === 'build') fire('preview_build_sync', selectedTableMappings.build || '');
      else fire('preview_reference_sync', k, selectedTableMappings[k] || '');
    });
  }
  const refExportCsvBtn = $('refExportCsv');
  if (refExportCsvBtn) {
    refExportCsvBtn.addEventListener('click', () => {
      exportReferenceTableData('csv');
    });
  }

  // Dashboard query controls
  const btnFetchDb = $('btnFetchDashboard') || $('btnRefreshDashboard');
  if (btnFetchDb) btnFetchDb.addEventListener('click', loadDashboardData);

  document.querySelectorAll('#dbModeFilters button[data-db-filter]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#dbModeFilters button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      dbActiveMode = btn.dataset.dbFilter || 'nego';
      dbSelectedDate = null;  // reset any day drill-down when switching workstream
      if (currentDashboardData) renderDashboard();
      else loadDashboardData();
    });
  });

  const dbMuniClearDay = $('dbMuniClearDay');
  if (dbMuniClearDay) {
    dbMuniClearDay.addEventListener('click', () => {
      dbSelectedDate = null;
      renderDbDateTable();
      renderDbMuniTable();
    });
  }

  const dbDateFromInp = $('dbDateFrom');
  const dbDateToInp = $('dbDateTo');
  const dbPresetSel = $('dbDatePreset');
  const btnDbClearDate = $('btnDbClearDateRange');

  if (dbDateFromInp) {
    dbDateFromInp.addEventListener('change', () => {
      dbDateFrom = dbDateFromInp.value;
      if (dbPresetSel) dbPresetSel.value = 'all';
      refreshDbWorkWeeks();
    });
    dbDateFromInp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') loadDashboardData();
    });
  }

  if (dbDateToInp) {
    dbDateToInp.addEventListener('change', () => {
      dbDateTo = dbDateToInp.value;
      if (dbPresetSel) dbPresetSel.value = 'all';
      refreshDbWorkWeeks();
    });
    dbDateToInp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') loadDashboardData();
    });
  }

  const dbWwSelect = $('dbWwSelect');
  if (dbWwSelect) {
    dbWwSelect.addEventListener('change', () => {
      dbWorkWeekFilter = dbWwSelect.value;
    });
  }

  if (btnDbClearDate) {
    btnDbClearDate.addEventListener('click', () => {
      dbDateFrom = '';
      dbDateTo = '';
      dbWorkWeekFilter = '';
      if (dbDateFromInp) dbDateFromInp.value = '';
      if (dbDateToInp) dbDateToInp.value = '';
      if (dbPresetSel) dbPresetSel.value = 'all';
      refreshDbWorkWeeks();
      toast('Query parameters reset. Click "Fetch Data" to query.', false);
    });
  }

  if (dbPresetSel) {
    dbPresetSel.addEventListener('change', () => {
      const val = dbPresetSel.value;
      const now = new Date();
      const formatYmd = (d) => {
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        return `${y}-${m}-${day}`;
      };

      if (val === 'all') {
        dbDateFrom = '';
        dbDateTo = '';
      } else if (val === 'today') {
        dbDateFrom = formatYmd(now);
        dbDateTo = formatYmd(now);
      } else if (val === 'this_week') {
        const d = new Date(now);
        const dayOfWeek = d.getDay();
        const diffToMon = d.getDate() - dayOfWeek + (dayOfWeek === 0 ? -6 : 1);
        const mon = new Date(d.setDate(diffToMon));
        const sun = new Date(mon);
        sun.setDate(mon.getDate() + 6);
        dbDateFrom = formatYmd(mon);
        dbDateTo = formatYmd(sun);
      } else if (val === 'this_month') {
        const first = new Date(now.getFullYear(), now.getMonth(), 1);
        const last = new Date(now.getFullYear(), now.getMonth() + 1, 0);
        dbDateFrom = formatYmd(first);
        dbDateTo = formatYmd(last);
      } else if (val === 'last_30') {
        const past = new Date(now);
        past.setDate(past.getDate() - 30);
        dbDateFrom = formatYmd(past);
        dbDateTo = formatYmd(now);
      } else if (val === 'last_90') {
        const past = new Date(now);
        past.setDate(past.getDate() - 90);
        dbDateFrom = formatYmd(past);
        dbDateTo = formatYmd(now);
      } else if (val === 'this_year') {
        const first = new Date(now.getFullYear(), 0, 1);
        const last = new Date(now.getFullYear(), 11, 31);
        dbDateFrom = formatYmd(first);
        dbDateTo = formatYmd(last);
      }

      if (dbDateFromInp) dbDateFromInp.value = dbDateFrom;
      if (dbDateToInp) dbDateToInp.value = dbDateTo;
      refreshDbWorkWeeks();
    });
  }









  applyIcons(document);
}

function dismissSplash() {
  const splash = $('appSplash');
  try { localStorage.setItem('app_launched_before', '1'); } catch (_) {}
  if (splash) {
    if (document.documentElement.classList.contains('skip-splash')) {
      splash.remove();
    } else {
      setTimeout(() => {
        splash.classList.add('fade-out');
        setTimeout(() => splash.remove(), 350);
      }, 150);
    }
  }
}

// --- Excel-like multi-cell selection + copy/paste ---------------------------
// Highlight a block of cells (click, Shift+click, or click-drag), copy it as
// TSV with Ctrl+C and paste a TSV block back with Ctrl+V — like a spreadsheet.
const GRID_SELECTIONS = {};   // tableId -> { anchor:{r,c}, focus:{r,c}, dragging }
const GRID_CFG = {};          // tableId -> { updateCell, setMem, onRow, onTable, refresh }
let GRID_CELL_CACHE = {};     // tableId -> 2D array of data <td> elements

function invalidateGridCache(tableId) {
  if (tableId) delete GRID_CELL_CACHE[tableId];
  else GRID_CELL_CACHE = {};
}

// Rows of data cells (excludes the ACT action column) for a table, cached until
// the next re-render so drag-selection stays smooth on large sheets.
function gridCellsMatrix(tableEl) {
  const id = tableEl.id;
  if (GRID_CELL_CACHE[id]) return GRID_CELL_CACHE[id];
  const m = Array.from(tableEl.querySelectorAll('tbody > tr')).map(tr =>
    Array.from(tr.querySelectorAll('td')).filter(td => td.dataset.colKey != null));
  GRID_CELL_CACHE[id] = m;
  return m;
}

function gridPosOf(tbody, td) {
  const tr = td.closest('tr');
  if (!tr) return null;
  const r = Array.prototype.indexOf.call(tbody.children, tr);
  if (r < 0) return null;
  const cells = Array.from(tr.querySelectorAll('td')).filter(x => x.dataset.colKey != null);
  const c = cells.indexOf(td);
  if (c < 0) return null;
  return { r, c };
}

function gridNormRect(sel) {
  return {
    r1: Math.min(sel.anchor.r, sel.focus.r), r2: Math.max(sel.anchor.r, sel.focus.r),
    c1: Math.min(sel.anchor.c, sel.focus.c), c2: Math.max(sel.anchor.c, sel.focus.c),
  };
}

function gridClearHighlight(tableEl) {
  tableEl.querySelectorAll('td.cell-selected, td.cell-active')
    .forEach(td => td.classList.remove('cell-selected', 'cell-active'));
}

function gridApplyHighlight(tableEl) {
  const sel = GRID_SELECTIONS[tableEl.id];
  gridClearHighlight(tableEl);
  if (!sel) return;
  const m = gridCellsMatrix(tableEl);
  const { r1, r2, c1, c2 } = gridNormRect(sel);
  for (let r = r1; r <= r2; r++) {
    for (let c = c1; c <= c2; c++) {
      const td = m[r] && m[r][c];
      if (td) td.classList.add('cell-selected');
    }
  }
  const at = m[sel.anchor.r] && m[sel.anchor.r][sel.anchor.c];
  if (at) at.classList.add('cell-active');
}

function gridSelectionCount(tableEl) {
  const sel = GRID_SELECTIONS[tableEl.id];
  if (!sel) return 0;
  const { r1, r2, c1, c2 } = gridNormRect(sel);
  return (r2 - r1 + 1) * (c2 - c1 + 1);
}

// True when any cell in the current selection is a <select> (dropdown).
function gridSelectionHasSelect(tableEl) {
  const sel = GRID_SELECTIONS[tableEl.id];
  if (!sel) return false;
  const m = gridCellsMatrix(tableEl);
  const { r1, r2, c1, c2 } = gridNormRect(sel);
  for (let r = r1; r <= r2; r++) {
    for (let c = c1; c <= c2; c++) {
      const td = m[r] && m[r][c];
      if (td && td.querySelector('.cell-select')) return true;
    }
  }
  return false;
}

// Top-left cell of the selection + whether that cell is a dropdown.
function gridTargetInfo(tableEl) {
  const sel = GRID_SELECTIONS[tableEl.id];
  if (!sel) return null;
  const m = gridCellsMatrix(tableEl);
  const { r1, c1 } = gridNormRect(sel);
  const td = m[r1] && m[r1][c1];
  return { r1, c1, count: gridSelectionCount(tableEl), targetIsSelect: !!(td && td.querySelector('.cell-select')) };
}

// --- clipboard helpers -------------------------------------------------------
// A focused <select> (dropdown) fires neither 'copy' nor 'paste', so Ctrl+C/V are
// handled on keydown and the paste is routed through a hidden editable element.
let GRID_PASTE_PROXY = null;
let GRID_GLOBAL_WIRED = false;
function gridPasteProxy() {
  if (GRID_PASTE_PROXY && document.body.contains(GRID_PASTE_PROXY)) return GRID_PASTE_PROXY;
  const ta = document.createElement('textarea');
  ta.setAttribute('aria-hidden', 'true');
  ta.tabIndex = -1;
  ta.style.cssText = 'position:fixed;left:-9999px;top:0;width:1px;height:1px;opacity:0;';
  document.body.appendChild(ta);
  GRID_PASTE_PROXY = ta;
  return ta;
}

function gridCopyViaProxy(text) {
  const ta = gridPasteProxy();
  const prev = document.activeElement;
  ta.value = text;
  let ok = false;
  try { ta.focus(); ta.select(); ok = document.execCommand('copy'); } catch (e) { ok = false; }
  ta.value = '';
  try { if (prev && prev.focus) prev.focus(); } catch (e) {}
  return ok;
}

function gridCopyToClipboard(tableEl) {
  const count = gridSelectionCount(tableEl);
  const text = gridSelectionText(tableEl);
  const done = () => toast(`Copied ${count} cell${count === 1 ? '' : 's'}.`, false);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => {
      if (gridCopyViaProxy(text)) done(); else toast('Copy failed.', true);
    });
  } else if (gridCopyViaProxy(text)) {
    done();
  } else {
    toast('Copy failed.', true);
  }
}

function gridFocusPasteProxy() {
  // Focus the hidden editable so the browser performs the paste into it; the
  // global paste handler then routes the text back to the current selection.
  const ta = gridPasteProxy();
  ta.value = '';
  try { ta.focus(); } catch (e) {}
}

function gridCellValue(td) {
  const ctrl = td.querySelector('.cell-select, .cell-input');
  if (ctrl) return String(ctrl.value != null ? ctrl.value : '');
  return String(td.textContent || '').trim();
}

// The whole selection as tab-separated text (rows joined by newlines) — the
// format Excel/Sheets use, so it round-trips through the system clipboard.
function gridSelectionText(tableEl) {
  const sel = GRID_SELECTIONS[tableEl.id];
  if (!sel) return '';
  const m = gridCellsMatrix(tableEl);
  const { r1, r2, c1, c2 } = gridNormRect(sel);
  const lines = [];
  for (let r = r1; r <= r2; r++) {
    const cells = [];
    for (let c = c1; c <= c2; c++) {
      const td = m[r] && m[r][c];
      cells.push(td ? gridCellValue(td) : '');
    }
    lines.push(cells.join('\t'));
  }
  return lines.join('\n');
}

// The writable control inside a cell (null for read-only / badge cells).
function gridCellEditor(td) {
  const ctrl = td.querySelector('.cell-select, .cell-input');
  if (!ctrl) return null;
  if (ctrl.tagName === 'INPUT' && ctrl.readOnly) return null;
  return ctrl;
}

async function gridPasteText(tableEl, text, startR, startC, verb) {
  const cfg = GRID_CFG[tableEl.id];
  if (!cfg) return;
  const m = gridCellsMatrix(tableEl);
  const block = String(text == null ? '' : text)
    .replace(/\r\n?/g, '\n').replace(/\n$/, '').split('\n').map(l => l.split('\t'));
  if (!block.length) return;

  // If the copied block is smaller than the highlighted selection, fill/tile it
  // across the whole selection (Excel-style): a single value fills every cell.
  const sel = GRID_SELECTIONS[tableEl.id];
  let r1 = startR, c1 = startC, selH = 1, selW = 1;
  if (sel) {
    const rect = gridNormRect(sel);
    r1 = rect.r1; c1 = rect.c1;
    selH = rect.r2 - rect.r1 + 1;
    selW = rect.c2 - rect.c1 + 1;
  }
  const bh = block.length;
  const bw = Math.max(1, ...block.map(rw => rw.length));
  const rows = (bh === 1) ? selH : ((selH > bh && selH % bh === 0) ? selH : bh);
  const cols = (bw === 1) ? selW : ((selW > bw && selW % bw === 0) ? selW : bw);

  const jobs = [];
  for (let dr = 0; dr < rows; dr++) {
    const rr = r1 + dr;
    if (rr >= m.length) break;
    const srcRow = block[dr % bh];
    for (let dc = 0; dc < cols; dc++) {
      const cc = c1 + dc;
      const td = m[rr] && m[rr][cc];
      if (!td) continue;
      const ctrl = gridCellEditor(td);
      if (!ctrl) continue;                                     // skip read-only / badge cells
      const val = String(srcRow[dc % bw] == null ? '' : srcRow[dc % bw]).trim().toUpperCase();
      if (ctrl.tagName === 'SELECT') {
        if (!Array.from(ctrl.options).some(o => o.value === val)) ctrl.appendChild(new Option(val, val));
        ctrl.value = val;
      } else {
        ctrl.value = val;
      }
      ctrl.classList.add('modified');
      td.classList.remove('cell-invalid');
      td.removeAttribute('title');
      const rowIdx = parseInt(td.dataset.rowIdx, 10);
      const colIdx = parseInt(td.dataset.colIdx, 10);
      if (cfg.setMem) cfg.setMem(rowIdx, colIdx, val);
      jobs.push({ rowIdx, colIdx, colKey: td.dataset.colKey, val });
    }
  }
  if (!jobs.length) { toast('Nothing to paste into the selected cells.', true); return; }

  const a = api();
  let okCount = 0, failCount = 0, lastTable = null, lastState = null;
  for (const j of jobs) {
    if (isNaN(j.rowIdx) || !j.colKey) continue;
    let res = null;
    try { res = a ? await a[cfg.updateCell](j.rowIdx, j.colKey, j.val) : null; }
    catch (err) { res = null; }
    if (!res || res.ok === false) { failCount++; continue; }
    okCount++;
    if (res.state) lastState = res.state;
    if (res.table) lastTable = res.table;
    if (res.row && cfg.onRow) cfg.onRow(j.rowIdx, res.row);
  }

  GRID_SELECTIONS[tableEl.id] = null;
  if (lastState) applyState(lastState);
  if (lastTable && cfg.onTable) cfg.onTable(lastTable); else if (cfg.refresh) cfg.refresh();
  const msg = `${verb || 'Pasted'} ${okCount} cell${okCount === 1 ? '' : 's'}` + (failCount ? ` (${failCount} failed)` : '') + '.';
  toast(msg, failCount > 0);
}

// Clear every cell in the highlighted selection (Delete / Backspace). Done with a
// direct pass over the selection rectangle (not a text round-trip) so the LAST row
// is always cleared too — a blank TSV would lose its trailing empty row.
async function gridClearSelection(tableEl) {
  const cfg = GRID_CFG[tableEl.id];
  const sel = GRID_SELECTIONS[tableEl.id];
  if (!cfg || !sel) return;
  const m = gridCellsMatrix(tableEl);
  const rect = gridNormRect(sel);

  const jobs = [];
  for (let r = rect.r1; r <= rect.r2; r++) {
    const tdRow = m[r];
    if (!tdRow) continue;
    for (let c = rect.c1; c <= rect.c2; c++) {
      const td = tdRow[c];
      if (!td) continue;
      const ctrl = gridCellEditor(td);
      if (!ctrl) continue;                                     // skip read-only / badge cells
      if (ctrl.tagName === 'SELECT') {
        if (!Array.from(ctrl.options).some(o => o.value === '')) {
          try { ctrl.appendChild(new Option('', '')); } catch (e) {}
        }
        ctrl.value = '';
      } else {
        ctrl.value = '';
      }
      ctrl.classList.add('modified');
      td.classList.remove('cell-invalid');
      td.removeAttribute('title');
      const rowIdx = parseInt(td.dataset.rowIdx, 10);
      const colIdx = parseInt(td.dataset.colIdx, 10);
      if (cfg.setMem) cfg.setMem(rowIdx, colIdx, '');
      jobs.push({ rowIdx, colKey: td.dataset.colKey, val: '' });
    }
  }
  if (!jobs.length) { toast('Nothing to clear in the selected cells.', true); return; }

  const a = api();
  let okCount = 0, failCount = 0, lastTable = null, lastState = null;
  for (const j of jobs) {
    if (isNaN(j.rowIdx) || !j.colKey) continue;
    let res = null;
    try { res = a ? await a[cfg.updateCell](j.rowIdx, j.colKey, j.val) : null; } catch (e) { res = null; }
    if (!res || res.ok === false) { failCount++; continue; }
    okCount++;
    if (res.state) lastState = res.state;
    if (res.table) lastTable = res.table;
    if (res.row && cfg.onRow) cfg.onRow(j.rowIdx, res.row);
  }

  GRID_SELECTIONS[tableEl.id] = null;
  if (lastState) applyState(lastState);
  if (lastTable && cfg.onTable) cfg.onTable(lastTable); else if (cfg.refresh) cfg.refresh();
  const msg = `Cleared ${okCount} cell${okCount === 1 ? '' : 's'}` + (failCount ? ` (${failCount} failed)` : '') + '.';
  toast(msg, failCount > 0);
}

// Which registered grid should a global copy/paste/key event act on? Prefer the
// grid that contains the event target; otherwise (outer click, nothing focused,
// or our off-screen paste proxy) fall back to a visible grid with a selection.
function gridResolveActiveTable(target) {
  const ids = Object.keys(GRID_SELECTIONS).filter(id => GRID_SELECTIONS[id] && document.getElementById(id));
  if (!ids.length) return null;
  for (const id of ids) {
    const el = document.getElementById(id);
    if (target && el.contains(target)) return el;
  }
  const loose = !target || target === document.body || target === document.documentElement || target === GRID_PASTE_PROXY;
  if (loose) {
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el.offsetParent !== null) return el;   // visible (active view) only
    }
  }
  return null;
}

// Installed once: Ctrl+C / Ctrl+V / Escape work no matter where focus is —
// inside an input, on a dropdown, on the cell padding, or on nothing at all.
function gridInstallGlobalHandlers() {
  if (GRID_GLOBAL_WIRED) return;
  GRID_GLOBAL_WIRED = true;

  document.addEventListener('keydown', (e) => {
    const tableEl = gridResolveActiveTable(e.target);
    if (!tableEl) return;
    const sel = GRID_SELECTIONS[tableEl.id];
    if (!sel) return;

    if (e.key === 'Escape') { GRID_SELECTIONS[tableEl.id] = null; gridClearHighlight(tableEl); return; }
    if (e.key === 'Delete' || e.key === 'Backspace') {
      const selCount = gridSelectionCount(tableEl);
      const ae = document.activeElement;
      const editing = !!ae && (ae.tagName === 'INPUT' || ae.tagName === 'SELECT' || ae.tagName === 'TEXTAREA');
      // Clear a highlighted block; keep normal character-delete while editing one cell.
      if (selCount > 1 || !editing) { e.preventDefault(); gridClearSelection(tableEl); }
      return;
    }
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const key = (e.key || '').toLowerCase();
    const count = gridSelectionCount(tableEl);
    if (key === 'c') {
      if (count <= 1 && !gridSelectionHasSelect(tableEl)) return;   // let plain text copy work
      e.preventDefault();
      gridCopyToClipboard(tableEl);
    } else if (key === 'v') {
      const info = gridTargetInfo(tableEl);
      if (!info || (count <= 1 && !info.targetIsSelect)) return;    // let plain text paste work
      gridFocusPasteProxy();                                        // paste into the hidden proxy
    }
  });

  document.addEventListener('copy', (e) => {
    const tableEl = gridResolveActiveTable(e.target);
    if (!tableEl) return;
    if (gridSelectionCount(tableEl) <= 1) return;
    const text = gridSelectionText(tableEl);
    if (e.clipboardData) { e.clipboardData.setData('text/plain', text); e.preventDefault(); }
  });

  document.addEventListener('paste', (e) => {
    const tableEl = gridResolveActiveTable(e.target);
    if (!tableEl) return;
    const sel = GRID_SELECTIONS[tableEl.id];
    if (!sel) return;
    const rect = gridNormRect(sel);
    const info = gridTargetInfo(tableEl) || { r1: rect.r1, c1: rect.c1, targetIsSelect: false };
    const text = e.clipboardData ? e.clipboardData.getData('text/plain') : '';
    const multiCell = /[\t\n]/.test(text);
    const count = gridSelectionCount(tableEl);
    if (!multiCell && count <= 1 && !info.targetIsSelect) return;   // let plain text paste work
    e.preventDefault();
    gridPasteText(tableEl, text, info.r1, info.c1);
  });
}

function initGridSelection(tableEl, cfg) {
  if (!tableEl) return;
  GRID_CFG[tableEl.id] = cfg;
  if (tableEl.dataset.gridSelInit === '1') return;   // wire once per table
  tableEl.dataset.gridSelInit = '1';
  const tbody = tableEl.querySelector('tbody');
  if (!tbody) return;
  let down = null;

  tbody.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    const td = e.target.closest('td');
    if (!td || td.dataset.colKey == null) return;
    const pos = gridPosOf(tbody, td);
    if (!pos) return;
    const sel = GRID_SELECTIONS[tableEl.id];
    if (e.shiftKey && sel) {                 // extend a range from the anchor
      sel.focus = pos;
      gridApplyHighlight(tableEl);
      e.preventDefault();
      return;
    }
    GRID_SELECTIONS[tableEl.id] = { anchor: { r: pos.r, c: pos.c }, focus: { r: pos.r, c: pos.c }, dragging: false };
    down = pos;
    gridApplyHighlight(tableEl);
  });

  tbody.addEventListener('mousemove', (e) => {   // click-drag range select
    if (!down || e.buttons !== 1) return;
    const td = e.target.closest('td');
    if (!td || td.dataset.colKey == null) return;
    const pos = gridPosOf(tbody, td);
    if (!pos || (pos.r === down.r && pos.c === down.c)) return;
    const sel = GRID_SELECTIONS[tableEl.id];
    if (!sel) return;
    sel.dragging = true;
    sel.focus = pos;
    gridApplyHighlight(tableEl);
    if (window.getSelection) window.getSelection().removeAllRanges();
    e.preventDefault();
  });

  document.addEventListener('mouseup', () => { down = null; });

  // Global (installed once) keyboard + clipboard handling for all grids.
  gridInstallGlobalHandlers();
}

let booted = false;
function boot() {
  wire();
  dismissSplash();
  // Excel-like cell range selection + copy/paste for both editable grids.
  initGridSelection($('reviewTable'), {
    updateCell: 'update_review_cell',
    setMem: (rowIdx, colIdx, val) => { if (currentReviewTable && currentReviewTable.rows[rowIdx]) currentReviewTable.rows[rowIdx][colIdx] = val; },
    onTable: (t) => { if (t) renderReview(t); },
    refresh: () => renderReviewBody(),
  });
  initGridSelection($('prodTable'), {
    updateCell: 'update_productivity_cell',
    setMem: (rowIdx, colIdx, val) => { if (prodTable && prodTable.rows[rowIdx]) prodTable.rows[rowIdx][colIdx] = val; },
    onRow: (rowIdx, row) => { if (prodTable && prodTable.rows[rowIdx] && row) prodTable.rows[rowIdx] = row; },
    refresh: () => renderProductivityBody(),
  });
  const a = api();
  if (!a) return;
  if (booted) return;   // only run backend init once, even if boot() fires again
  booted = true;
  if (a.state) a.state().then(applyState);
  // load remembered reference files after the window is up (keeps startup instant)
  if (a.boot_load) a.boot_load();   // result arrives via onBackendDone
}

if (window.pywebview && window.pywebview.api) boot();
else {
  window.addEventListener('pywebviewready', boot);
  // browser-preview fallback (no pywebview): just wire the UI once
  document.addEventListener('DOMContentLoaded', () => {
    if (!api()) {
      wire();
      dismissSplash();
      renderRefGrid({ refCounts: {}, refSources: {} });
    }
  });
}

