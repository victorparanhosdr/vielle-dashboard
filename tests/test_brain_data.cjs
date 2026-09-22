const test = require('node:test');
const assert = require('node:assert/strict');
const {compareAnalyses, compareCode, technicalReport, syncSample, datasetDate, historyNotice, filterInventory, inventoryMetrics} = require('../static/master/brain-data.js');

const base = () => ({clinic: 'vielle', observed_at: 100, fingerprint: 'v1', inventory_status:'complete', evidence: [
  {id: 'experts_sync', state: 'error', last_success: 10},
  {id: 'booking_dates', state: 'warning', count: 5},
], diagnostic: {datasets: [{table: 'clinica_sales', records: 20}]}});

test('comparison refuses cross-clinic, missing and reverse-time records', () => {
  assert.equal(compareAnalyses(null, base()), null);
  assert.equal(compareAnalyses(base(), {...base(), clinic: 'inspire'}), null);
  assert.equal(compareAnalyses(base(), {...base(), observed_at: 99}), null);
});

test('comparison reports neutral measured changes without inventing corrections', () => {
  const before = base(), after = base();
  after.observed_at = 200;
  after.fingerprint = 'v2';
  after.evidence[0] = {id: 'experts_sync', state: 'ok', last_success: 180};
  after.evidence[1] = {id: 'booking_dates', state: 'ok', count: 0};
  after.diagnostic.datasets[0].records = 18;
  const result = compareAnalyses(before, after);
  assert.equal(result.codeChanged, true);
  assert.equal(result.datasetComparisonAvailable, true);
  assert.equal(result.changes.find(c => c.kind === 'dataset').delta, -2);
  assert.equal(result.changes.find(c => c.kind === 'count').delta, -5);
  assert.equal(result.changes.find(c => c.kind === 'sync').after, 180);
  assert.equal(result.changes.filter(c => c.kind === 'state').length, 2);
  assert.equal(before.evidence[0].state, 'error');
});

test('unavailable measurements do not become zero or resolved alerts', () => {
  const after = {...base(), evidence: [{id:'new_check', state:'unknown', message:'Unavailable'}]};
  const result = compareAnalyses(base(), after);
  assert.equal(result.changes.length, 3);
  assert.ok(result.changes.every(c => c.kind === 'measurement'));
  assert.equal(result.changes.find(c => c.id === 'experts_sync').after, null);
});

test('legacy records work without count snapshots', () => {
  const old = {...base()}; delete old.diagnostic;
  const result = compareAnalyses(old, base());
  assert.equal(result.changes.length, 0);
  assert.equal(result.datasetComparisonAvailable, false);
  const same = compareAnalyses(base(), base());
  assert.equal(same.codeChanged, false);
  assert.equal(same.changes.length, 0);
});

test('non-numeric/missing counts are not fabricated', () => {
  const old = base(), after = base();
  old.evidence[1].count = null;
  after.evidence[1].count = 0;
  old.diagnostic.datasets[0].records = '20';
  after.diagnostic.datasets[0].records = NaN;
  assert.equal(compareAnalyses(old, after).changes.length, 0);
});

test('export excludes configuration, raw logs and unrelated response fields', () => {
  const snapshot = {clinic:'vielle', clinic_name:'Vielle Clinic', generated_at:10,
    inventory:{fingerprint:'v1', revision:null, source:'not-for-export'},
    health:{database:'readable', datasets:[], integrations:{experts:{state:'ok', label:'Concluída', last_success:10, recent:[{message:'secret log'}], api_key:'secret'}}},
    evidence:[], limitations:['Historical only'], ai:{api_key:'secret'}, patient:{name:'PRIVATE-PATIENT'}};
  const report = technicalReport(snapshot, null);
  const text = JSON.stringify(report);
  for (const forbidden of ['secret', 'PRIVATE-PATIENT', 'not-for-export']) assert.ok(!text.includes(forbidden));
  assert.equal(report.analysis, null);
  assert.equal(report.version, 1);
  assert.throws(() => technicalReport(snapshot, {clinic:'inspire'}), /Clínica/);
  const analysis = {...base(), id:1, model:'test', summary:'Technical', findings:[], actor_id:10, prompt_version:'2026-09-22.1'};
  const saved = technicalReport(snapshot, analysis).analysis;
  assert.equal(saved.id, 1);
  assert.equal(saved.observed_at, 100);
  assert.equal(saved.prompt_version, '2026-09-22.1');
  assert.equal(saved.inventory_status, 'complete');
  assert.ok(!('actor_id' in saved));
});

test('sync sample shows the real denominator and never invents zero failures', () => {
  assert.equal(syncSample({sample_size:1,completed_in_sample:1,failures_last_10:0}).label, 'Última tentativa observada');
  assert.equal(syncSample({sample_size:3,completed_in_sample:2,failures_last_10:1}).failures, '1 de 2 concluídas');
  assert.equal(syncSample({sample_size:0}).failures, 'Não medidas');
  assert.equal(syncSample({sample_size:2,completed_in_sample:0,failures_last_10:0}).failures, 'Não medidas');
  assert.equal(syncSample({sample_size:0}).label, 'Nenhuma tentativa registrada');
  assert.equal(syncSample({}).label, 'Histórico não medido');
  assert.equal(syncSample({unclassified_in_sample:1}).inconsistent, '1 registro sem dados consistentes');
});

test('dataset dates distinguish unavailable, empty, invalid and not applicable', () => {
  assert.equal(datasetDate({date_status:'unavailable'},'first_date'),'Não disponível');
  assert.equal(datasetDate({date_status:'empty'},'first_date'),'Sem data registrada');
  assert.equal(datasetDate({date_status:'invalid'},'first_date'),'Data inconsistente');
  assert.equal(datasetDate({date_status:'not_applicable'},'first_date'),'Não aplicável');
  assert.equal(datasetDate({date_status:'available',first_date:'2026-09-01'},'first_date'),'2026-09-01');
  assert.equal(datasetDate({},'first_date'),'Não disponível');
});

test('history notices distinguish unreadable history from an empty history', () => {
  assert.equal(historyNotice(undefined), '');
  assert.equal(historyNotice({state:'empty'}), '');
  assert.equal(historyNotice({state:'ok'}), '');
  assert.match(historyNotice({state:'unavailable',skipped:null}), /temporariamente indisponível/);
  assert.match(historyNotice({state:'partial',skipped:1}), /1 análise salva não pôde/);
  assert.match(historyNotice({state:'partial',skipped:3}), /3 análises salvas não puderam/);
  assert.match(historyNotice({state:'partial'}), /Parte do histórico/);
});

test('technical export preserves history-read limitations without raw errors', () => {
  const snapshot = {clinic:'vielle',inventory:{},health:{integrations:{},datasets:[]},
    history_status:{state:'unavailable',skipped:null,error:'private-path'},evidence:[]};
  const report = technicalReport(snapshot,null);
  assert.deepEqual(report.history_status,{state:'unavailable',skipped:null});
  assert.ok(!JSON.stringify(report).includes('private-path'));
});

const inventory = () => ({files: [
  {name:'app.py',dependencies:['auth_store.py','body_evolution.py']},
  {name:'static/master/brain.js',dependencies:[]},
  {name:'body_evolution.py',dependencies:['clinica_patient_link.py']},
  {name:'relatório.py',dependencies:[]},
],endpoints:['/api/master/brain','/api/body/patients','/auth/login']});

test('inventory search covers filenames, dependencies and routes case-insensitively', () => {
  const result = filterInventory(inventory(),'BODY');
  assert.deepEqual(result.files.map(f=>f.name),['app.py','body_evolution.py']);
  assert.deepEqual(result.routes,['/api/body/patients']);
  assert.equal(result.totalFiles,4);
  assert.equal(result.totalRoutes,3);
  assert.deepEqual(filterInventory(inventory(),'RELATORIO').files.map(f=>f.name),['relatório.py']);
});

test('inventory search requires every word and treats punctuation literally', () => {
  assert.deepEqual(filterInventory(inventory(),'  BODY  auth ').files.map(f=>f.name),['app.py']);
  assert.deepEqual(filterInventory(inventory(),'api brain').routes,['/api/master/brain']);
  assert.equal(filterInventory(inventory(),'.*').files.length,0);
  assert.equal(filterInventory(inventory(),'<script>').routes.length,0);
});

test('clearing inventory search restores all items without modifying the source', () => {
  const source = inventory(), before = structuredClone(source);
  filterInventory(source,'missing');
  assert.deepEqual(filterInventory(source,'  ').files,source.files);
  assert.deepEqual(filterInventory(source).routes,source.endpoints);
  assert.deepEqual(source,before);
});

test('inventory loading and unmatched search return safe empty results with totals', () => {
  assert.deepEqual(filterInventory(null,'app'),{files:[],routes:[],totalFiles:0,totalRoutes:0});
  assert.deepEqual(filterInventory(inventory(),'missing'),{files:[],routes:[],totalFiles:4,totalRoutes:3});
  assert.equal(filterInventory({files:[{name:'isolated.py'}]},'isolated').files.length,1);
});

test('inventory metrics retain measured zero and distinguish unmeasured source', () => {
  assert.deepEqual(inventoryMetrics({analysis_status:'measured',functions:0,dependencies:[],hash:'v1'}),
    {functions:'0',dependencies:'Nenhuma detectada',hash:'v1'});
  assert.equal(inventoryMetrics({analysis_status:'measured',functions:3,dependencies:['app.py']}).dependencies,'app.py');
  for (const file of [{analysis_status:'not_measured',functions:null},{name:'legacy.js',functions:0},{}]) {
    assert.equal(inventoryMetrics(file).functions,'Não medidas');
    assert.equal(inventoryMetrics(file).dependencies,'Não medidas');
  }
});

test('invalid and unreadable inventory metrics never become zero or expose errors', () => {
  for (const analysis_status of ['invalid','unavailable']) {
    const result = inventoryMetrics({analysis_status,functions:null,hash:null,error:'private-path'});
    assert.deepEqual(result,{functions:'Não disponíveis',dependencies:'Não disponíveis',hash:'Não disponível'});
  }
  for (const functions of [-1,NaN,Infinity,'0',null]) {
    assert.notEqual(inventoryMetrics({analysis_status:'measured',functions}).functions,'0');
  }
});

test('technical report retains partial inventory status without file contents', () => {
  const snapshot = {clinic:'vielle',inventory:{status:'partial',files:[{raw:'PRIVATE'}]},health:{integrations:{},datasets:[]}};
  const result = technicalReport(snapshot,null);
  assert.equal(result.inventory_status,'partial');
  assert.ok(!JSON.stringify(result).includes('PRIVATE'));
});

test('code comparison only states sameness or change for two complete inventories', () => {
  assert.equal(compareCode(base(),base()),false);
  assert.equal(compareCode(base(),{...base(),fingerprint:'v2'}),true);
  for (const incomplete of [null, {}, {...base(),inventory_status:'partial'},
    {...base(),inventory_status:undefined}, {...base(),fingerprint:''}, {...base(),fingerprint:'  '}]) {
    assert.equal(compareCode(incomplete,base()),null);
    assert.equal(compareCode(base(),incomplete),null);
  }
});

test('partial or legacy inventory does not block comparable dataset changes', () => {
  const before = base(), after = base();
  before.inventory_status = undefined;
  after.observed_at = 200;
  after.diagnostic.datasets[0].records = 21;
  const result = compareAnalyses(before,after);
  assert.equal(result.codeChanged,null);
  assert.equal(result.changes.find(c=>c.kind==='dataset').delta,1);
  assert.equal(result.datasetComparisonAvailable,true);
});

test('unknown observation dates prevent unsupported chronological comparisons', () => {
  for (const observed_at of [null,undefined,0,-1,'100',NaN,Infinity]) {
    assert.equal(compareAnalyses({...base(),observed_at},base()),null);
    assert.equal(compareAnalyses(base(),{...base(),observed_at}),null);
  }
  assert.ok(compareAnalyses(base(),base()));
});
