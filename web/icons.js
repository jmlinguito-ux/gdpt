'use strict';

// Minimal stroke-icon set (24x24, currentColor). Keep additions to this shape.
const ICON_PATHS = {
  upload: '<path d="M12 3v12"/><path d="M7 8l5-5 5 5"/><path d="M4 21h16"/>',
  grid: '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/>',
  chart: '<path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M3 20h18"/>',
  map: '<path d="M9 4l-6 2v14l6-2 6 2 6-2V4l-6 2-6-2Z"/><path d="M9 4v14"/><path d="M15 6v14"/>',
  users: '<circle cx="9" cy="8" r="3"/><path d="M3.5 20a5.5 5.5 0 0 1 11 0"/><circle cx="17" cy="9" r="2.4"/><path d="M14.8 20a4.4 4.4 0 0 1 6.7-3.6"/>',
  flag: '<path d="M5 21V4"/><path d="M5 4h13l-3 4 3 4H5"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5 9-5Z"/><path d="M3 13l9 5 9-5"/>',
  archive: '<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8"/><path d="M10 13h4"/>',
  plug: '<path d="M9 2v6"/><path d="M15 2v6"/><path d="M6 8h12v3a6 6 0 0 1-12 0V8Z"/><path d="M12 17v5"/>',
  'cloud-download': '<path d="M7 18a4.5 4.5 0 0 1-.5-8.98A5.5 5.5 0 0 1 17.4 8.1 4 4 0 0 1 17 18H7Z"/><path d="M12 11v6"/><path d="M9.5 15L12 17.5 14.5 15"/>',
  'file-plus': '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z"/><path d="M14 3v5h5"/><path d="M12 12v6"/><path d="M9 15h6"/>',
  download: '<path d="M12 3v12"/><path d="M7 12l5 5 5-5"/><path d="M4 21h16"/>',
  refresh: '<path d="M20 11A8 8 0 0 0 6.3 6.3L4 8.6"/><path d="M4 4v5h5"/><path d="M4 13a8 8 0 0 0 13.7 4.7L20 15.4"/><path d="M20 20v-5h-5"/>',
  bolt: '<path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/>',
  'x-circle': '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5l5 5"/><path d="M14.5 9.5l-5 5"/>',
  'folder-open': '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l1.5 1.8H19a1.5 1.5 0 0 1 1.45 1.9l-1.8 7A1.5 1.5 0 0 1 17.2 18H5.7a1.5 1.5 0 0 1-1.45-1.13L3 7.5Z"/>',
  trash: '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13"/><path d="M10 11v6"/><path d="M14 11v6"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  alert: '<path d="M12 3l10 18H2L12 3Z"/><path d="M12 10v4"/><path d="M12 17h.01"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>',
  'file-text': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z"/><path d="M14 2v6h6"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
  'cloud-upload': '<path d="M7 18a4.5 4.5 0 0 1-.5-8.98A5.5 5.5 0 0 1 17.4 8.1 4 4 0 0 1 17 18H7Z"/><path d="M12 17v-6"/><path d="M9.5 13.5L12 11l2.5 2.5"/>',
  'arrow-right': '<path d="M5 12h14"/><path d="M12 5l7 7-7 7"/>',
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  'chart-bar': '<path d="M12 20V10"/><path d="M18 20V4"/><path d="M6 20v-4"/><path d="M3 20h18"/>',
};



function iconSvg(name) {
  const inner = ICON_PATHS[name];
  if (!inner) return '';
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
}

function applyIcons(root) {
  (root || document).querySelectorAll('[data-icon]').forEach(el => {
    if (!el.dataset.iconApplied) { el.innerHTML = iconSvg(el.dataset.icon); el.dataset.iconApplied = '1'; }
  });
}
