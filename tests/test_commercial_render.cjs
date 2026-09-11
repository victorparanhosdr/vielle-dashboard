const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('function render() {');
const end = source.indexOf('\nfunction ', start + 1);
assert(start >= 0 && end > start);
const calls = [];
const state = { activeView: 'commercialView', selectedClinic: 'vielle', selectedPipelines: new Set() };
const context = { state, document: { getElementById: () => ({}) },
  clinics: { vielle: { connected: true } },
  renderSalesIntelligence: data => calls.push(data),
  filteredBookingDailyItems: rows => rows,
};
for (const name of ['syncFilterState', 'fmtDate', 'renderPipelineChoices', 'renderDailyChart',
  'renderClinicaExperts', 'renderDoctorCross', 'renderFinancial', 'renderPaidTraffic',
  'renderGeneralDoctorFilter', 'renderGeneralPanel', 'renderStatusColumnChart',
  'applyClinicHeader', 'applyActionPermissions', 'showNotice']) context[name] = () => {};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
const intelligence = { top_patients: [{ patient: 'Teste', amount: 600 }] };
state.report = { connected: true, sales_intelligence: intelligence };
vm.runInContext('render()', context);
assert.equal(calls.pop(), intelligence);
state.report = { connected: true, financial: { sales_intelligence: intelligence } };
vm.runInContext('render()', context);
assert.equal(calls.pop(), intelligence);
state.report = { connected: true };
vm.runInContext('render()', context);
assert.equal(Object.keys(calls.pop()).length, 0);
console.log('PASS: commercial rankings receive scoped data, legacy fallback, and empty reset');
