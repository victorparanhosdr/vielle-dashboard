const test = require('node:test');
const assert = require('node:assert/strict');
const {definitions, scaleFor, buildModel, segments, geometry, chartSvg} = require('../static/body-evolution-charts.js');
const {throughDay} = require('../static/body-evolution-data.js');
const row = (id, fields, day = '2026-09-15T10:00') => ({id, fields, exam_at:day, source:'inbody', method:'InBody120'});
const exams = [row('first',{weight_kg:88,muscle_kg:28,fat_pct:34,waist_cm:98,hip_cm:110},'2026-06-04T10:00'),row('last',{weight_kg:82,muscle_kg:29.4,fat_pct:29.5,waist_cm:91,hip_cm:105})];

test('composition uses paired kg bars and a separate fat-percent axis, without normalizing values', () => {
  const model = buildModel(exams,'composition');
  assert.deepEqual(model.series.map(s=>[s.key,s.type,s.axis]),[['weight_kg','bar','mass'],['muscle_kg','bar','mass'],['fat_pct','line','fat']]);
  assert.deepEqual(model.series[0].values,[88,82]);
  assert.equal(model.axes.mass.min,0);
  assert.equal(model.axes.fat.min,0);
  assert.ok(model.axes.mass.max >= 88);
  assert.ok(model.axes.fat.max >= 34 && model.axes.fat.max < model.axes.mass.max);
});

test('abdomen and hip share one cm axis, preserving raw measurements', () => {
  const model = buildModel(exams,'measurements');
  assert.deepEqual(Object.keys(model.axes),['length']);
  assert.ok(model.series.every(s=>s.unit === 'cm' && s.type === 'line'));
  assert.deepEqual(model.series.map(s=>s.values),[[98,91],[110,105]]);
  assert.ok(model.axes.length.min < 91 && model.axes.length.max > 110);
});

test('missing data is null, never zero, and interrupts line/fill segments', () => {
  const model = buildModel([row('a',{fat_pct:30}),row('b',{}),row('c',{fat_pct:0}),row('d',{fat_pct:NaN})],'composition');
  assert.deepEqual(model.series[2].values,[30,null,0,null]);
  assert.deepEqual(segments(model.series[2].values),[[0],[2]]);
  assert.equal(model.axes.mass,null);
  assert.equal(model.axes.fat.min,0);
  assert.equal(buildModel([row('empty',{})],'measurements').empty,true);
});

test('one observation and all-zero values have finite nonzero ranges', () => {
  for (const values of [[0],[20],[98,98],[.01,.02]]) {
    for (const zero of [false,true]) {
      const scale = scaleFor(values,zero);
      assert.ok(scale.max > scale.min);
      assert.ok(scale.ticks.every(Number.isFinite));
      assert.ok(scale.min <= Math.min(...values) && scale.max >= Math.max(...values));
      if (zero) assert.equal(scale.min,0);
    }
  }
  assert.equal(scaleFor([]),null);
});

test('legend visibility affects only requested series, no mutation of exams', () => {
  const original = JSON.stringify(exams);
  const model = buildModel(exams,'composition',new Set(['fat_pct']));
  assert.equal(model.axes.fat,null);
  assert.equal(model.series[2].visible,false);
  assert.deepEqual(model.series[2].values,[34,29.5]);
  assert.equal(buildModel(exams,'composition',new Set(definitions.composition.map(s=>s.key))).empty,true);
  assert.equal(JSON.stringify(exams),original);
});

test('exam chronology, same-day repeats and source/method/date scope stay intact', () => {
  const records = [row('repeat',{weight_kg:83},'2026-09-15T18:00'),...exams,
    {...row('cal',{weight_kg:90}),source:'handymet',method:'HandyMet'},
    row('future',{weight_kg:70},'2026-09-16T09:00')];
  const scoped = throughDay(records,'2026-09-15').filter(r=>r.source === 'inbody' && r.method === 'InBody120');
  const model = buildModel(scoped,'composition');
  assert.deepEqual(model.rows.map(r=>r.id),['first','last','repeat']);
  assert.deepEqual(model.series[0].values,[88,82,83]);
});

test('SVG values match their own axis and gradient IDs remain unique across charts', () => {
  const model = buildModel(exams,'composition');
  const g = geometry(model,720,284);
  assert.equal(g.y(0,model.axes.mass),g.bottom);
  assert.equal(g.y(model.axes.mass.max,model.axes.mass),g.top);
  assert.equal(g.y(model.axes.fat.max,model.axes.fat),g.top);
  const composition = chartSvg(model,720,284,'compositionChart');
  const measurements = chartSvg(buildModel(exams,'measurements'),720,230,'measurementsChart');
  assert.match(composition,/compositionChart-fat_pct-area/);
  assert.match(measurements,/measurementsChart-hip_cm-area/);
  assert.match(composition,/Massa muscular: 29,4 kg/);
  assert.match(composition,/Gordura corporal: 29,5 %/);
  assert.match(measurements,/Abdômen: 91 cm/);
  assert.doesNotMatch(composition+measurements,/NaN|Infinity|undefined/);
});

test('mobile, single-date, repeated-date and dense charts remain finite and labels safe', () => {
  for (const records of [[exams[0]],exams,Array.from({length:80},(_,i)=>row(String(i),{weight_kg:80,fat_pct:30,hip_cm:100}))]) {
    for (const kind of ['composition','measurements']) {
      const model = buildModel(records,kind);
      const svg = chartSvg(model,248,256,kind+'Chart');
      assert.doesNotMatch(svg,/NaN|Infinity|undefined/);
      assert.equal((svg.match(/class="chartHit"/g)||[]).length,records.length);
      assert.equal((svg.match(/tabindex="0"/g)||[]).length,1);
    }
  }
});
