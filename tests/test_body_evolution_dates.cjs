const test = require('node:test');
const assert = require('node:assert/strict');
const {examDay, groupDays, primaryRecord, throughDay} = require('../static/body-evolution-data.js');

const bio = {id:'bio', source:'inbody', method:'InBody120', exam_at:'2026-09-15T09:30', fields:{weight_kg:61.5, bmr_kcal:1405, fat_pct:22.1}};
const cal = {id:'cal', source:'handymet', method:'HandyMet', exam_at:'2026-09-15T15:31', fields:{weight_kg:61, bmr_kcal:1589, rq:0.81, fat_fuel_pct:63.1, carb_fuel_pct:36.9, vo2:5.08}};

test('one date contains both exams, preserving time, ID and conflicting measurements', () => {
  const original = JSON.stringify([cal,bio]);
  const days = groupDays([cal,bio]);
  assert.equal(days.length,1);
  assert.equal(days[0].day,'2026-09-15');
  assert.deepEqual(days[0].records.map(r=>r.id),['bio','cal']);
  assert.equal(days[0].records[0].fields.bmr_kcal,1405);
  assert.equal(days[0].records[1].fields.bmr_kcal,1589);
  assert.equal(days[0].records[0].fields.fat_pct,22.1);
  assert.equal(days[0].records[1].fields.fat_fuel_pct,63.1);
  assert.equal(JSON.stringify([cal,bio]),original);
  assert.equal(primaryRecord(days[0].records).id,'bio');
});

test('different exam days stay separate even near midnight or imported out of order', () => {
  const later = {...cal,id:'later',exam_at:'2026-09-16T00:01',created_at:'2026-09-16T10:00:00Z'};
  const earlier = {...bio,exam_at:'2026-09-15T23:59',created_at:'2026-09-16T11:00:00Z'};
  assert.equal(examDay(earlier),'2026-09-15');
  assert.deepEqual(groupDays([later,earlier]).map(day=>day.day),['2026-09-15','2026-09-16']);
  assert.deepEqual(throughDay([later,cal,earlier],'2026-09-15').map(r=>r.id),['cal','bio']);
});

test('the selected date includes later times on that day but not future days', () => {
  const next = {...cal,id:'next',exam_at:'2026-09-16T09:00'};
  assert.deepEqual(throughDay([next,bio,cal],examDay(bio)).map(r=>r.id),['bio','cal']);
});

test('multiple exams from one source are retained, with latest bioimpedance as composition', () => {
  const repeat = {...bio,id:'repeat',exam_at:'2026-09-15T16:00'};
  assert.equal(groupDays([bio,cal,repeat])[0].records.length,3);
  assert.equal(primaryRecord([bio,repeat,cal]).id,'repeat');
});

test('excluding or restoring a record leaves the other exam in the same date', () => {
  assert.equal(groupDays([bio,cal]).length,1);
  assert.equal(groupDays([bio]).length,1);
  assert.equal(primaryRecord([cal]).id,'cal');
  assert.deepEqual(groupDays([]),[]);
});

test('missing calorimetry values are not substituted by body composition or zero', () => {
  const partial = {...cal,fields:{rq:0.8}};
  const day = groupDays([bio,partial])[0];
  assert.equal(day.records[1].fields.fat_fuel_pct,undefined);
  assert.equal(day.records[1].fields.vo2,undefined);
});
