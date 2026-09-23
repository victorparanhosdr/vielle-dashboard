const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = code.indexOf('function renderGeneralReceipts(');
const end = code.indexOf('\nfunction ', start + 1);
const nodes = Object.fromEntries(['generalReceived', 'generalReceivedCount', 'generalReceivedBasis',
  'generalReceivedWarning'].map(id => [id, {}]));
const context = vm.createContext({brlCents: new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'}),
  document: {getElementById: id => nodes[id]}});
vm.runInContext(code.slice(start, end), context);
context.renderGeneralReceipts({net_total: 9750.55, count: 2, excluded: {}, basis: 'Teste'});
assert.match(nodes.generalReceived.textContent, /9\.750,55/);
assert.equal(nodes.generalReceivedWarning.hidden, true);
context.renderGeneralReceipts({net_total: 0, count: 0, excluded: {professional: 2, date: 1}});
assert.match(nodes.generalReceivedWarning.textContent, /2 sem vínculo/);
assert.equal(nodes.generalReceivedWarning.hidden, false);
context.renderGeneralReceipts({net_total: 0, count: 0, excluded: {}});
assert.match(nodes.generalReceived.textContent, /0,00/);
assert.equal(nodes.generalReceivedWarning.hidden, true);
context.renderGeneralReceipts(undefined);
assert.equal(nodes.generalReceivedCount.textContent, 'Recebimentos indisponíveis');
assert(!nodes.generalReceived.textContent.includes('0'));
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
assert(html.includes('<span>Total vendido</span>'));
assert(html.includes('<span>Total recebido</span>'));
assert(!html.includes('Faturamento total'));
console.log('PASS: net receipts, cents, unavailable data, partial warnings and sale labels');
