const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('function renderQuoteFollowupMonthFilter(');
const end = source.indexOf('function quoteFollowupItemKey(', start);
assert(start >= 0 && end > start);
const elements = new Map();
const state = {
  dateTo: '2026-09-30', selectedQuoteFollowupMonth: '', selectedQuoteFollowupWallet: 'pending',
  selectedQuoteFollowupContact: '', selectedQuoteFollowupStatus: '',
  quoteFollowupItems: [
    { quote_date: '2026-01-02', wallet_status: 'expired', quote_total: 100, status: 'red', contact_count: 1 },
    { quote_date: '2026-09-10', wallet_status: 'lost', quote_total: 200, status: 'due' },
    { quote_date: '2026-09-11', wallet_status: 'active', quote_total: 300, status: 'monitor' },
    { quote_date: '2026-09-11', wallet_status: 'won', won: true, quote_total: 400 },
  ],
};
const context = vm.createContext({
  state, Intl, Date,
  Option: function(text, value) { this.text = text; this.value = value; },
  document: { getElementById(id) {
    if (!elements.has(id)) elements.set(id, { textContent: '', value: '', replaceChildren(...options) { this.options = options; } });
    return elements.get(id);
  } },
  integerFormat: String,
  brl: new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }),
});
vm.runInContext(source.slice(start, end), context);
const run = code => vm.runInContext(code, context);
run('renderQuoteFollowupMonthFilter("2026-09-11"); renderQuoteFollowupTotals()');
assert.equal(elements.get('quoteFollowupMonthFilter').options.length, 13);
assert.equal(elements.get('quoteFollowupTotal').textContent, '3');
assert(elements.get('quoteFollowupAmount').textContent.includes('600,00'));
assert.equal(run('filteredQuoteFollowupItems().length'), 3);

state.selectedQuoteFollowupMonth = '2026-09';
run('renderQuoteFollowupMonthFilter("2026-09-11"); renderQuoteFollowupTotals()');
assert.equal(state.selectedQuoteFollowupMonth, '2026-09');
assert.equal(run('filteredQuoteFollowupItems().length'), 2);
assert.equal(elements.get('quoteFollowupTotal').textContent, '2');
assert(elements.get('quoteFollowupAmount').textContent.includes('500,00'));
state.selectedQuoteFollowupWallet = 'lost';
assert.equal(run('filteredQuoteFollowupItems().length'), 1);
state.selectedQuoteFollowupContact = 'contacted';
assert.equal(run('filteredQuoteFollowupItems().length'), 0);

state.selectedQuoteFollowupMonth = '2026-02';
run('renderQuoteFollowupTotals()');
assert.equal(elements.get('quoteFollowupTotal').textContent, '0');
assert(elements.get('quoteFollowupAmount').textContent.includes('0,00'));
state.dateTo = '2027-09-30';
run('renderQuoteFollowupMonthFilter("2026-09-11")');
assert.equal(state.selectedQuoteFollowupMonth, '');
assert.equal(elements.get('quoteFollowupMonthFilter').options[1].value, '2027-01');
console.log('PASS: monthly options, pending totals, combined filters, empty month, year reset');
