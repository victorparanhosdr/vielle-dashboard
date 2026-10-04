const test=require('node:test');
const assert=require('node:assert/strict');
const {money,integer,percent,escape,date}=require('../static/institutes.js');
test('Unknown investment is not falsely displayed as zero',()=>{assert.equal(money(null),'—');assert.match(money(0),/0,00/);});
test('Money uses integer cents and PT-BR',()=>{assert.match(money(123456),/1\.234,56/);assert.equal(percent(7.45),'7,5%');assert.equal(integer(1200),'1.200');});
test('Provider content cannot inject HTML',()=>assert.equal(escape('<script>"&'), '&lt;script&gt;&quot;&amp;'));
test('Day formatting avoids UTC midnight shift',()=>assert.equal(date('2026-09-01'),'01/09'));
