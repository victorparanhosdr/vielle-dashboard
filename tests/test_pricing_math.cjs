const assert = require('node:assert/strict');
const { test } = require('node:test');
const { calculate, parseNumber } = require('../static/pricing-math.js');

const example = {
  monthlyCost: 12000, days: 20, hours: 8, rooms: 2, occupancy: 75,
  duration: 90, tax: 10, card: 4, commission: 50, margin: 20,
  actualPrice: 2000,
  materials: [{ name: 'Material', packPrice: 725, packQuantity: 1, used: 1 }],
};

test('example deducts every cost before commission and reconciles cents', () => {
  const r = calculate(example);
  assert.equal(r.ok, true);
  assert.equal(r.capacity, 240);
  assert.equal(r.hourlyCost, 50);
  assert.equal(r.timeCost, 7500);
  assert.equal(r.materialCost, 72500);
  assert.equal(r.suggested, 173914);
  assert.equal(r.taxes, 20000);
  assert.equal(r.fees, 8000);
  assert.equal(r.profit, 92000);
  assert.equal(r.professional, 46000);
  assert.equal(r.clinic, 46000);
  assert.equal(r.actualMargin, 23);
  assert.equal(r.price, r.costs + r.professional + r.clinic);
});

test('suggested price targets clinic margin AFTER commission', () => {
  const r = calculate({ ...example, actualPrice: null });
  assert.equal(r.price, r.suggested);
  assert.ok(r.actualMargin >= 20);
  assert.ok(r.actualMargin < 20.01);
  const withoutCommission = calculate({ ...example, commission: 0, actualPrice: null });
  assert.ok(withoutCommission.suggested < r.suggested);
  assert.ok(withoutCommission.actualMargin >= 20);
});

test('consumption uses package units and includes occupied clinical time', () => {
  const r = calculate({ ...example, occupancy: 50, materials: [
    { name: 'Seringa', packPrice: 100, packQuantity: 50, used: 2 },
  ] });
  assert.equal(r.materialCost, 400);
  assert.equal(r.hourlyCost, 75);
  assert.equal(r.timeCost, 11250);
});

test('losses never create negative commission', () => {
  const r = calculate({ ...example, actualPrice: 100 });
  assert.equal(r.profit, -71400);
  assert.equal(r.professional, 0);
  assert.equal(r.clinic, -71400);
});

test('invalid or impossible inputs cannot produce misleading prices', () => {
  for (const changes of [
    { days: 0 }, { days: 1.5 }, { rooms: 0 }, { rooms: 2.5 }, { hours: 25 },
    { occupancy: 0 }, { occupancy: 101 }, { duration: NaN }, { monthlyCost: Infinity },
    { tax: -1 }, { card: 101 }, { commission: 100 }, { margin: 80 },
    { actualPrice: 0 }, { actualPrice: -1 }, { tax: 80, card: 20 },
    { materials: [{ name: 'Item', packPrice: 50, packQuantity: 0, used: 1 }] },
    { materials: [{ name: '', packPrice: 50, packQuantity: 1, used: 1 }] },
  ]) assert.equal(calculate({ ...example, ...changes }).ok, false, JSON.stringify(changes));
  assert.equal(calculate({ ...example, commission: 100, margin: 0 }).ok, true);
  assert.equal(calculate({ ...example, materials: [], monthlyCost: 0 }).ok, false);
});

test('rounding preserves the target and a non-loss-making break-even price', () => {
  for (let n = 1; n <= 2000; n++) {
    const input = { ...example, monthlyCost: 0, actualPrice: null,
      tax: (n % 17) / 3, card: (n % 11) / 2,
      commission: n % 100, margin: ((n % 5) / 100) * (100 - n % 100),
      materials: [{ name: 'Test', packPrice: n / 100, packQuantity: 1, used: 1 }],
    };
    const r = calculate(input);
    assert.equal(r.ok, true);
    assert.ok(r.actualMargin + 1e-9 >= input.margin, JSON.stringify({ n, r }));
    assert.equal(r.price, r.costs + r.professional + r.clinic);
    assert.ok(calculate({ ...input, actualPrice: r.minimum / 100 }).profit >= 0);
  }
});

test('Brazilian currency parser handles pasted and plain values', () => {
  assert.equal(parseNumber('R$ 12.345,67'), 12345.67);
  assert.equal(parseNumber('1.000'), 1000);
  assert.equal(parseNumber('12,5'), 12.5);
  assert.equal(parseNumber('12.50'), 12.5);
  for (const value of ['', 'abc', '1,2,3', 'Infinity']) assert.ok(Number.isNaN(parseNumber(value)));
});
