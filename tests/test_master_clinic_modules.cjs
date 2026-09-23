const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/master-access.js'), 'utf8');
const moduleKeys = ['dashboard', 'commercial', 'financial', 'patient_followup',
  'budget_followup', 'paid_traffic', 'whatsapp_review', 'body_evolution'];
const context = vm.createContext({moduleCatalog: Object.fromEntries(moduleKeys.map(key => [key, {}]))});
vm.runInContext(source.slice(0, source.indexOf('function choiceOption')), context);
const allowed = clinic => Array.from(context.allowedModuleKeys(clinic));
for (const clinic of ['brandao', 'carla']) {
  assert.deepEqual(allowed(clinic), moduleKeys.filter(key => !['patient_followup', 'body_evolution'].includes(key)));
}
assert.deepEqual(allowed('vielle'), moduleKeys.filter(key => key !== 'body_evolution'));
assert.deepEqual(allowed('inspire'), moduleKeys.filter(key => key !== 'patient_followup'));
assert.deepEqual(allowed(null), moduleKeys, 'Profiles must keep all modules available');
console.log('Master clinic module tests passed');
