/* Run with node tests/test_offer_letter_ui.js. Exercises the actual UI functions. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
const context = vm.createContext({document: {activeElement: null}});
vm.runInContext(source.slice(0, source.indexOf('// --- API bridge')), context);
vm.runInContext(`
  if (availableReferenceTables('negotiation').length !== 6) throw Error('Negotiation references missing');
  if (availableReferenceTables('land-sourcing').length !== 5) throw Error('Sourcing references changed');
  if (availableReferenceTables('land-sourcing').some(t => t.kind === 'offer')) throw Error('Offer visible for sourcing');
`, context);

vm.runInContext(`let activeSolutionTables = [
  {logicalName: 'cr63f_offerletter', displayName: 'OFFER LETTER'},
  {logicalName: 'cr63f_batangasnegorecord', displayName: 'BATANGAS NEGO RECORD'}
];`, context);
vm.runInContext(source.slice(source.indexOf('function getTablesForKind('),
  source.indexOf('// Given a selected record')), context);
assert.equal(vm.runInContext("getTablesForKind('offer', 'negotiation').length", context), 1);
assert.equal(vm.runInContext("getTablesForKind('offer', 'land-sourcing').length", context), 0);

vm.runInContext(`let currentReviewTable = {
  headers: ['OFFER LETTER ACTION'], rows: [['']],
  offerErrors: {0: 'Load OFFER LETTER reference'}, meta: {}
};
function validateCellValue() {return {valid: true};}
function isDateColumn() {return false;}
function isMatchColumn() {return false;}`, context);
vm.runInContext(source.slice(source.indexOf('function refreshReviewRow('),
  source.indexOf('function refreshReviewVisibleRows(')), context);
function element() {
  const classes = new Set();
  return {classList: {toggle: (name, on) => on ? classes.add(name) : classes.delete(name)},
    classes, querySelector: () => null, removeAttribute(key) {delete this[key];}};
}
const cell = element();
const row = {...element(), dataset: {rowIdx: '0'}, children: [cell]};
context.row = row;
vm.runInContext('refreshReviewRow(row, new Map())', context);
assert.equal(cell.title, 'Load OFFER LETTER reference');
assert.ok(cell.classes.has('cell-invalid'));
assert.ok(row.classes.has('row-invalid-date'));
vm.runInContext("currentReviewTable.offerErrors = {}; currentReviewTable.rows = [['INITIAL VISIT']]; refreshReviewRow(row, new Map())", context);
assert.equal(cell.title, undefined);
assert.ok(!cell.classes.has('cell-invalid'));
assert.ok(!row.classes.has('row-invalid-date'));
context.document.createElement = () => ({children: [], dataset: {}, style: {}, listeners: {},
  classList: {add() {}}, appendChild(child) {this.children.push(child);},
  addEventListener(name, handler) {this.listeners[name] = handler;}});
vm.runInContext(`currentRefKind = 'offer';
let currentRefState = {keys: ['offerDate'], labels: ['OFFER DATE'], rows: [['01/02/2026']]};
function getSavedColWidth() {return null;}
function toast() {}
function api() {return {update_reference_cell: () => Promise.resolve({ok: true, row: {offerDate: '01/02/2026'}})};}`, context);
vm.runInContext(source.slice(source.indexOf('function buildRefRow('),
  source.indexOf('function renderRefWindow(')), context);
async function checkDateCorrection() {
  const dateRow = vm.runInContext("buildRefRow(['01/02/2026'], 0, 'ref_offer')", context);
  const input = dateRow.children[0].children[0];
  assert.equal(input.placeholder, 'MM/DD/YYYY');
  assert.ok(input.className.includes('cell-date'));
  input.listeners.focus();
  input.value = '1/2/26';
  input.listeners.change();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(input.value, '01/02/2026');
  assert.equal(vm.runInContext('currentRefState.rows[0][0]', context), '01/02/2026');
  console.log('OFFER LETTER UI checks passed, including automatic date correction after saving.');
}
checkDateCorrection().catch(error => {console.error(error); process.exitCode = 1;});
