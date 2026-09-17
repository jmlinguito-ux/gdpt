'use strict';
/* =========================================================================
   shell.js - app shell behaviour for the redesigned UI.
   Owns the pieces the mockup adds on top of the original app: the workflow
   step strip (labels, gating, primary action), the top-bar connection pill,
   the Settings screen, and theme/density. Reads state that app.js already
   publishes (window.lastState + the DOM it renders) so nothing here has to
   be threaded through app.js itself.
   ========================================================================= */

(function () {
  const $ = (id) => document.getElementById(id);
  const st = () => window.lastState || {};

  /* ---------------- theme + density ---------------- */
  const LS_THEME = 'gdpt_theme';
  const LS_DENSITY = 'gdpt_density';

  function seg(box, key, val) {
    if (!box) return;
    box.querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset[key] === val));
  }
  function applyTheme(name) {
    document.documentElement.setAttribute('data-theme', name === 'dark' ? 'dark' : 'light');
    try { localStorage.setItem(LS_THEME, name); } catch (e) { /* private mode */ }
    seg($('themeSeg'), 'theme', name);
  }
  function applyDensity(name) {
    document.documentElement.setAttribute('data-density', name === 'compact' ? 'compact' : 'comfortable');
    try { localStorage.setItem(LS_DENSITY, name); } catch (e) { /* private mode */ }
    seg($('densitySeg'), 'density', name);
  }

  /* ---------------- workflow strip ---------------- */
  const VIEWS = ['load', 'review', 'productivity', 'publish', 'reference', 'dashboard', 'settings', 'explorer'];

  function activeView() {
    for (const v of VIEWS) {
      const el = document.getElementById('view-' + (v === 'reference' ? 'ref' : v));
      if (el && el.classList.contains('active')) return v;
    }
    return 'load';
  }

  function refSelectedCount() {
    const boxes = document.querySelectorAll('#refGrid [data-ref-check]');
    return Array.prototype.filter.call(boxes, b => b.checked).length;
  }

  function setText(id, txt) { const el = $(id); if (el && el.textContent !== txt) el.textContent = txt; }

  function isPublished() {
    const r = $('publishReceipt');
    return !!(r && !r.classList.contains('hidden'));
  }

  function syncSteps() {
    const s = st();
    const curView = activeView();
    const isPublish = curView === 'publish';
    const pubView = $('view-publish');
    const pubMode = (pubView && pubView.dataset.pubstage) || window.currentPublishMode || 'records';
    const isProdStage = isPublish && (pubMode === 'prod' || pubMode === 'productivity');
    const isRecordsStage = isPublish && !isProdStage;
    const rClean = !!s.recordsPublishedClean;
    const published = isPublished();
    const lastReceiptStage = (window.__lastReceipt && window.__lastReceipt.stage) || window.__lastReceiptStage;
    const isProdPublished = published && lastReceiptStage === 'productivity';
    const isRecordsPublished = rClean || (published && (lastReceiptStage === 'records' || lastReceiptStage === 'productivity'));

    setText('stepSubLoad', s.inputName ? 'File ready' : 'No file yet');
    setText('stepSubReview', s.rowCount ? s.rowCount + ' rows' : 'Needs a file');
    setText('stepSubProd', s.productivityCount ? s.productivityCount + ' rows'
      : (s.hasCalculated ? 'Ready to generate' : 'Calculates on run'));
    setText('stepSubRecords', isRecordsPublished ? 'Records published' : (s.rowCount ? 'Ready to publish' : 'Needs records'));
    setText('stepSubProd2', isProdPublished ? 'Productivity published' : ((rClean || isProdStage) ? 'Ready to publish' : 'Records first'));

    const step1 = document.querySelector('.step[data-view="load"]');
    const step2 = document.querySelector('.step[data-view="review"]');
    const step3 = document.querySelector('.step[data-view="productivity"]');
    const step4 = document.querySelector('.step[data-pubstage="records"]');
    const step5 = document.querySelector('.step[data-pubstage="prod"]');

    if (step5) step5.classList.toggle('step-locked', !rClean && !isProdStage);

    // Active state (Blue)
    const isStep1Active = curView === 'load';
    const isStep2Active = curView === 'review';
    const isStep3Active = curView === 'productivity';
    const isStep4Active = isRecordsStage && !published;
    const isStep5Active = isProdStage && !published;

    // Done state (Green)
    const isStep1Done = !isStep1Active && !!s.inputName;
    const isStep2Done = !isStep2Active && (s.rowCount > 0 && (s.hasCalculated || isStep3Active || isPublish));
    const isStep3Done = !isStep3Active && (s.productivityCount > 0 && (rClean || isPublish));
    const isStep4Done = !isStep4Active && (isRecordsPublished || isProdStage);
    const isStep5Done = !isStep5Active && isProdPublished;

    if (step1) {
      step1.classList.toggle('active', isStep1Active);
      step1.classList.toggle('step-done', isStep1Done);
    }
    if (step2) {
      step2.classList.toggle('active', isStep2Active);
      step2.classList.toggle('step-done', isStep2Done);
    }
    if (step3) {
      step3.classList.toggle('active', isStep3Active);
      step3.classList.toggle('step-done', isStep3Done);
    }
    if (step4) {
      step4.classList.toggle('active', isStep4Active);
      step4.classList.toggle('step-done', isStep4Done);
    }
    if (step5) {
      step5.classList.toggle('active', isStep5Active);
      step5.classList.toggle('step-done', isStep5Done);
    }
  }

  // Strip primary button, per view: either proxies an in-page control
  // (`target`) or runs an action of its own (`action`).
  function primaryFor(view) {
    switch (view) {
      case 'load': return { label: 'Fetch selected tables', target: 'btnFetchAllDv' };
      case 'review': return { label: 'Generate productivity', target: 'btnGenerate' };
      case 'productivity': return {
        label: 'Continue to publish',
        enabled: () => {
          const s = st();
          if ((s.productivityCount || 0) <= 0) return false;
          // Block while the productivity workspace still has any issue rows
          // (invalid cells/dates, or unassigned/blank matched negotiators).
          if (window.countProdTableValidationErrors && window.prodTable
              && window.countProdTableValidationErrors(window.prodTable) > 0) return false;
          if (window.countProdIssueRows && window.prodTable
              && window.countProdIssueRows(window.prodTable) > 0) return false;
          return true;
        },
        action: () => { if (window.openPublishModal) window.openPublishModal('review'); },
      };
      case 'reference': return { label: 'Refresh from Dataverse', target: 'refRefresh' };
      default: return null;
    }
  }

  function noteFor(view) {
    const s = st();
    switch (view) {
      case 'load': return refSelectedCount() + ' of 5 tables selected';
      case 'review': return s.rowCount ? 'Calculate, then generate productivity' : 'Load a transaction file first';
      case 'productivity': {
        if (!s.productivityCount) return 'Nothing generated yet';
        const issues = (window.countProdIssueRows && window.prodTable) ? window.countProdIssueRows(window.prodTable) : 0;
        if (issues > 0) return issues + ' row' + (issues === 1 ? '' : 's') + ' with issues — fix before publishing';
        return s.productivityCount + ' rows ready';
      }
      case 'publish': return isPublished() ? 'Published' : 'Confirm the target table below';
      case 'reference': return ($('refTitle') || {}).textContent || '';
      case 'settings': return s.connected ? 'Connected' : 'Not connected';
      case 'dashboard': return 'Published reports';
      default: return '';
    }
  }

  function syncStrip() {
    const view = activeView();
    setText('wsNote', noteFor(view));

    const btn = $('wsPrimary');
    if (!btn) return;
    const p = primaryFor(view);
    if (!p) { btn.classList.add('hidden'); btn.__run = null; return; }

    if (p.action) {
      btn.classList.remove('hidden');
      if (btn.textContent !== p.label) btn.textContent = p.label;
      btn.disabled = p.enabled ? !p.enabled() : false;
      btn.__run = p.action;
      return;
    }
    const target = $(p.target);
    if (!target) { btn.classList.add('hidden'); btn.__run = null; return; }
    btn.classList.remove('hidden');
    if (btn.textContent !== p.label) btn.textContent = p.label;
    btn.disabled = !!target.disabled;
    btn.__run = () => target.click();
  }

  /* ---------------- connection surfaces ---------------- */
  function orgShortName() {
    const url = ($('setOrgUrl') && $('setOrgUrl').value) || '';
    return url.replace(/^https?:\/\//, '').split('.')[0] || '';
  }

  function syncConnection() {
    const connected = !!st().connected;

    const dot = $('topConnDot'); if (dot) dot.classList.toggle('on', connected);
    setText('topConnLabel', connected ? (orgShortName() || 'Connected') : 'Not connected');

    const sdot = $('setConnDot'); if (sdot) sdot.classList.toggle('on', connected);
    setText('setConnText', connected ? 'Dataverse connected' : 'Dataverse not connected');
    setText('setConnHint', connected
      ? 'Signed in - the cached session is reused on next launch.'
      : 'Device code sign-in via Microsoft');

    const bc = $('btnConnect'), bd = $('btnDisconnect');
    if (bc) bc.classList.toggle('hidden', connected);
    if (bd) bd.classList.toggle('hidden', !connected);

    const ss = $('setSolutionSelect'); if (ss) ss.disabled = !connected;
  }

  /* ---------------- settings <-> app mirrors ---------------- */
  // The settings screen re-presents controls that live on other views. Mirror
  // the options across and forward changes so there is one source of truth.
  function mirror(fromId, toId) {
    const from = $(fromId), to = $(toId);
    if (!from || !to) return;
    if (to.__mirrorSig !== from.innerHTML) {
      to.innerHTML = from.innerHTML;
      to.__mirrorSig = from.innerHTML;
    }
    if (to.value !== from.value) to.value = from.value;
  }

  function syncSettings() {
    mirror('refSolutionSelect', 'setSolutionSelect');
    renderUpdate(st().update || {});
  }

  /* ---------------- application updates ---------------- */
  let promptedUpdateVersion = '';

  function updateApi() {
    return (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
  }

  function renderUpdate(u) {
    if (!u) return;
    const status = u.status || 'idle';
    setText('setAppVersion', 'Version ' + (u.currentVersion || '1.0.0'));
    // Revealed only once the real version arrives, so the top bar never shows a
    // placeholder version that contradicts the window title.
    if (u.currentVersion) {
      setText('tbVersion', 'v' + u.currentVersion);
      const wrap = $('tbVersionWrap');
      if (wrap) wrap.hidden = false;
    }
    setText('updateStatus', u.message || (u.configured ? 'Ready to check' : 'Updates are not configured'));
    setText('updateLatest', u.latestVersion ? 'Latest: ' + u.latestVersion : '');

    const hint = $('updateHint');
    if (hint) {
      if (!u.configured) hint.textContent = 'Set an update URL when creating the release build.';
      else if (u.active === false) hint.textContent = 'Install a release build to receive updates in the app.';
      else hint.textContent = 'Updates are checked automatically when the app starts.';
    }

    const dot = $('updateDot');
    if (dot) {
      dot.className = 'update-dot';
      if (['checking', 'downloading', 'installing'].includes(status)) dot.classList.add('busy');
      else if (['available', 'ready', 'up_to_date'].includes(status)) dot.classList.add('ready');
      else if (status === 'error') dot.classList.add('error');
    }

    const progress = $('updateProgress'), bar = $('updateProgressBar');
    if (progress) progress.classList.toggle('hidden', !['downloading', 'ready'].includes(status));
    if (bar) bar.style.width = Math.max(0, Math.min(100, Number(u.progress) || 0)) + '%';

    const notes = $('updateNotes');
    if (notes) {
      notes.textContent = u.releaseNotes || '';
      notes.classList.toggle('hidden', !u.releaseNotes || !['available', 'downloading', 'ready'].includes(status));
    }

    const busy = ['checking', 'downloading', 'installing'].includes(status);
    const check = $('btnCheckUpdate');
    if (check) { check.disabled = busy || !u.configured || u.active === false; check.textContent = status === 'checking' ? 'Checking…' : 'Check for updates'; }
    const download = $('btnDownloadUpdate');
    if (download) download.classList.toggle('hidden', status !== 'available');
    const install = $('btnInstallUpdate');
    if (install) install.classList.toggle('hidden', status !== 'ready');
  }

  async function startUpdateDownload() {
    const api = updateApi();
    if (!api || !api.download_update) return;
    const result = await api.download_update();
    if (result && result.update) renderUpdate(result.update);
  }

  let promptedRestartVersion = '';

  window.onUpdateStatus = function (u) {
    if (window.lastState) window.lastState.update = u;
    renderUpdate(u);
    if (u.status === 'available' && u.latestVersion && promptedUpdateVersion !== u.latestVersion) {
      promptedUpdateVersion = u.latestVersion;
      if (window.openConfirmModal) {
        const note = u.releaseNotes ? '\n\n' + u.releaseNotes : '';
        window.openConfirmModal({
          title: 'Update available',
          message: 'Ground Data Processing Tool ' + u.latestVersion + ' is ready to download.' + note,
          proceedLabel: 'Download update',
          cancelLabel: 'Later',
          onProceed: startUpdateDownload
        });
      }
    } else if (u.status === 'ready' && u.latestVersion && promptedRestartVersion !== u.latestVersion) {
      promptedRestartVersion = u.latestVersion;
      if (window.openConfirmModal) {
        window.openConfirmModal({
          title: 'Update ready to install',
          message: 'Version ' + u.latestVersion + ' has been downloaded. Restart the app now to finish installing it.',
          proceedLabel: 'Restart and install',
          cancelLabel: 'Later',
          onProceed: async () => {
            const api = updateApi();
            if (!api || !api.install_update) return;
            const result = await api.install_update();
            if (result && result.ok === false && window.toast) {
              window.toast(result.error || 'Could not install update.', true);
            }
          }
        });
      }
    } else if (u.status === 'error' && !u.quiet && window.toast) {
      window.toast(u.message || 'Update failed.', true);
    }
  };

  function forward(fromId, toId) {
    const from = $(fromId);
    if (!from) return;
    from.addEventListener('change', () => {
      const to = $(toId);
      if (!to) return;
      to.value = from.value;
      to.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }

  let savedConnBaseline = { orgUrl: '', tenantId: '', clientId: '' };
  function updateConnDirtyState() {
    const o = ($('setOrgUrl') || {}).value || '';
    const ti = ($('setTenantId') || {}).value || '';
    const ci = ($('setClientId') || {}).value || '';
    const isDirty = (o !== savedConnBaseline.orgUrl || ti !== savedConnBaseline.tenantId || ci !== savedConnBaseline.clientId);
    const dirtyLabel = $('setConnDirty');
    const discardBtn = $('btnDiscardConnection');
    if (dirtyLabel) dirtyLabel.textContent = isDirty ? 'Unsaved changes' : '';
    if (discardBtn) discardBtn.classList.toggle('hidden', !isDirty);
  }

  async function loadAppConfig() {
    const api = (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
    if (!api || !api.get_app_config) return;
    try {
      const cfg = await api.get_app_config();
      if (!cfg) return;
      const o = $('setOrgUrl'); if (o && !o.value) o.value = cfg.environmentUrl || '';
      const ti = $('setTenantId'); if (ti && !ti.value) ti.value = cfg.tenantId || '';
      const ci = $('setClientId'); if (ci && !ci.value) ci.value = cfg.clientId || '';
      savedConnBaseline = {
        orgUrl: ($('setOrgUrl') || {}).value || '',
        tenantId: ($('setTenantId') || {}).value || '',
        clientId: ($('setClientId') || {}).value || ''
      };
      updateConnDirtyState();
      syncConnection();
    } catch (e) { /* settings are informational; a failure here is not fatal */ }
  }

  /* ---------------- master sync ---------------- */
  let pending = null;
  function sync() {
    syncSteps();
    syncStrip();
    syncConnection();
    syncSettings();
  }
  function scheduleSync() {
    if (pending) return;
    pending = setTimeout(() => { pending = null; sync(); }, 40);
  }
  window.scheduleShellSync = scheduleSync;
  window.syncShell = sync;

  /* ---------------- wiring ---------------- */
  function wireShell() {
    if (window.__shellWired) return;
    window.__shellWired = true;

    let t = 'light', d = 'comfortable';
    try {
      t = localStorage.getItem(LS_THEME) || 'light';
      d = localStorage.getItem(LS_DENSITY) || 'comfortable';
    } catch (e) { /* ignore */ }
    applyTheme(t);
    applyDensity(d);

    const themeSeg = $('themeSeg');
    if (themeSeg) themeSeg.addEventListener('click', e => {
      const b = e.target.closest('button'); if (b) applyTheme(b.dataset.theme);
    });
    const densitySeg = $('densitySeg');
    if (densitySeg) densitySeg.addEventListener('click', e => {
      const b = e.target.closest('button'); if (b) applyDensity(b.dataset.density);
    });

    // strip primary proxies the real control on the active view
    const wsp = $('wsPrimary');
    if (wsp) wsp.addEventListener('click', () => {
      if (wsp.__run && !wsp.disabled) wsp.__run();
    });

    // step 4 opens the publish screen with its data populated
    document.querySelectorAll('.step[data-view="publish"]').forEach(b => {
      b.addEventListener('click', () => {
        if (!window.openPublishModal) return;
        const s = st();
        const stage = b.dataset.pubstage || 'records';
        if (stage === 'prod') {
          if (!s.recordsPublishedClean) {
            if (window.toast) window.toast('Publish records (stage 4) with no failures first.', true);
            window.openPublishModal('review');
            return;
          }
          window.openPublishModal('prod');
        } else {
          window.openPublishModal('review');
        }
      });
    });

    const again = $('btnPublishAgain');
    if (again) again.addEventListener('click', () => {
      if (window.finishWorkflow) {
        window.finishWorkflow();
      } else {
        if (window.showPublishForm) window.showPublishForm();
        if (window.showView) window.showView('load');
      }
    });

    // Export the receipt's skipped + failed rows as a CSV the viewer can save.
    // Export the last publish's skipped + failed rows via the backend's native
    // save dialog (WebView2 blocks blob <a download>, so a client-side download
    // silently does nothing - the backend must write the file).
    const exportBtn = $('btnReceiptExport');
    if (exportBtn) exportBtn.addEventListener('click', () => {
      const api = (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
      if (api && api.export_publish_log) {
        if (window.fire) window.fire('export_publish_log');
        else api.export_publish_log();
      } else if (window.toast) {
        window.toast('Export is only available in the desktop app.', true);
      }
    });


    ['setOrgUrl', 'setTenantId', 'setClientId'].forEach(id => {
      const el = $(id);
      if (el) {
        el.addEventListener('input', updateConnDirtyState);
        el.addEventListener('change', updateConnDirtyState);
      }
    });

    const discardConn = $('btnDiscardConnection');
    if (discardConn) discardConn.addEventListener('click', () => {
      const o = $('setOrgUrl'); if (o) o.value = savedConnBaseline.orgUrl;
      const ti = $('setTenantId'); if (ti) ti.value = savedConnBaseline.tenantId;
      const ci = $('setClientId'); if (ci) ci.value = savedConnBaseline.clientId;
      updateConnDirtyState();
      syncConnection();
    });

    const saveConn = $('btnSaveConnection');
    if (saveConn) saveConn.addEventListener('click', async () => {
      const api = (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
      if (!api) return;
      // Save Environment URL
      if (api.set_org_url) {
        const r = await api.set_org_url(($('setOrgUrl') || {}).value || '');
        if (r && r.ok === false) { window.toast && window.toast(r.error, true); return; }
        if (r && r.state && window.applyState) window.applyState(r.state);
      }
      // Save Tenant ID + Client ID
      if (api.set_app_registration) {
        const tid = ($('setTenantId') || {}).value || '';
        const cid = ($('setClientId') || {}).value || '';
        const r = await api.set_app_registration(tid, cid);
        if (r && r.ok === false) { window.toast && window.toast(r.error, true); return; }
        if (r && r.state && window.applyState) window.applyState(r.state);
      }
      savedConnBaseline = {
        orgUrl: ($('setOrgUrl') || {}).value || '',
        tenantId: ($('setTenantId') || {}).value || '',
        clientId: ($('setClientId') || {}).value || ''
      };
      const dirtyLabel = $('setConnDirty');
      if (dirtyLabel) dirtyLabel.textContent = 'Saved just now';
      const discardBtn = $('btnDiscardConnection');
      if (discardBtn) discardBtn.classList.add('hidden');
      window.toast && window.toast('Connection settings saved.', false);
    });

    const disc = $('btnDisconnect');
    if (disc) disc.addEventListener('click', async () => {
      const api = (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
      if (!api || !api.disconnect_dataverse) return;
      const r = await api.disconnect_dataverse();
      if (r && r.state && window.applyState) window.applyState(r.state);
      window.toast && window.toast((r && r.message) || 'Disconnected.', false);
    });

    // Explorer "Refresh" had no handler at all; it now re-reads the cached
    // environment metadata (the slow EntityDefinitions query) on demand.
    const refreshSol = $('btnRefreshSolutions');
    if (refreshSol) refreshSol.addEventListener('click', () => {
      if (window.refreshSolutionsList) window.refreshSolutionsList(true);
    });

    forward('setSolutionSelect', 'refSolutionSelect');

    const remember = $('setRememberPublish');
    if (remember) remember.addEventListener('change', () => {
      const m = $('modalPublishSaveConfig');
      if (m) m.checked = remember.checked;
    });

    const checkUpdate = $('btnCheckUpdate');
    if (checkUpdate) checkUpdate.addEventListener('click', async () => {
      const api = updateApi();
      if (!api || !api.check_for_updates) return;
      const result = await api.check_for_updates(true);
      if (result && result.update) renderUpdate(result.update);
    });

    const downloadUpdate = $('btnDownloadUpdate');
    if (downloadUpdate) downloadUpdate.addEventListener('click', startUpdateDownload);

    const installUpdate = $('btnInstallUpdate');
    if (installUpdate) installUpdate.addEventListener('click', () => {
      const run = async () => {
        const api = updateApi();
        if (!api || !api.install_update) return;
        const result = await api.install_update();
        if (result && result.update) renderUpdate(result.update);
        if (result && result.ok === false && window.toast) window.toast(result.error || 'Could not install update.', true);
      };
      if (window.openConfirmModal) {
        window.openConfirmModal({
          title: 'Install update',
          message: 'The app will close, install the downloaded update, and reopen automatically.',
          proceedLabel: 'Update and restart',
          cancelLabel: 'Not now',
          onProceed: run
        });
      } else run();
    });

    // A view switch must retarget the strip's primary button *synchronously*:
    // the debounced observer below would otherwise leave the previous view's
    // action armed for a few frames.
    ['showView', 'showReference', 'openPublishModal', 'showPublishReceipt'].forEach(fn => {
      const orig = window[fn];
      if (typeof orig !== 'function' || orig.__shellWrapped) return;
      const wrapped = function () {
        const r = orig.apply(this, arguments);
        try { sync(); } catch (e) { /* never block navigation on the shell */ }
        return r;
      };
      wrapped.__shellWrapped = true;
      window[fn] = wrapped;
    });

    // keep the shell in step with whatever app.js re-renders
    const mo = new MutationObserver(scheduleSync);
    mo.observe(document.body, {
      subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: ['class', 'disabled', 'value']
    });
    document.addEventListener('click', scheduleSync, true);
    document.addEventListener('change', scheduleSync, true);

    loadAppConfig();
    sync();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wireShell);
  else wireShell();
  window.addEventListener('pywebviewready', () => { loadAppConfig(); sync(); });
})();
