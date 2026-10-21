const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/crm.js'), 'utf8');
const code = source.slice(source.indexOf('  function pipelineForm('), source.indexOf('  function leadForm('));
const current = {id: 'pipeline', revision: 7, name: 'Importado', stages: Array.from({length: 18}, (_, i) => ({id: `s${i}`, name: `Fase ${i}`, kind: 'open'}))};
const nodes = new Map();
let submit, posted;
const context = {
  pipeline: () => current, state: {doctor: 'doctor'},
  $: id => {if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id);},
  document: {querySelectorAll: () => []},
  modal: () => {}, icons: () => {}, actions: () => '', icon: () => '', esc: value => String(value), option: () => '',
  wireForm: callback => {submit = callback;}, api: (action, payload) => {posted = {action, payload};},
};
vm.createContext(context);
vm.runInContext(`${code};pipelineForm(true);`, context);
for (let i = 0; i < 4; i++) nodes.get('addStage').onclick();
nodes.get('stageRows').onclick({target: {closest: () => ({dataset: {stageUp: '17'}})}});
submit({get: () => 'Renomeado'});
assert.equal(posted.action, 'pipeline');
assert.equal(posted.payload.stages.length, 22);
assert.equal(posted.payload.revision, 7);
assert.equal(posted.payload.doctor_id, 'doctor');
assert.equal(posted.payload.stages[16].id, 's17');
assert.equal(posted.payload.stages[17].id, 's16');
assert.equal(new Set(posted.payload.stages.slice(0, 18).map(stage => stage.id)).size, 18);
assert.equal(current.stages[16].id, 's16', 'Editing must not mutate the loaded pipeline before saving');
vm.runInContext('pipelineForm(false);', context);
for (let i = 0; i < 12; i++) nodes.get('addStage').onclick();
submit({get: () => 'Novo funil'});
assert.equal(posted.payload.stages.length, 16);
assert.equal(posted.payload.id, undefined);
console.log('CRM pipeline editor: imported/new funnels exceed 12 phases; IDs, order, revision and scope preserved OK');
