const test = require('node:test');
const assert = require('node:assert/strict');
const {instituteUrl, initials} = require('../static/institute-entry.js');

test('Institute cards open a course inside the selected account', () => {
  const url = new URL(instituteUrl({key:'victor-paranhos', courses:[{key:'regen-code'}]}), 'https://doc4docs.com.br');
  assert.equal(url.pathname, '/institutes.html');
  assert.equal(url.searchParams.get('institute'), 'victor-paranhos');
  assert.equal(url.searchParams.get('course'), 'regen-code');
  assert.equal(url.searchParams.has('clinic'), false);
});
test('Future accounts use their own identifiers, not a fixed course', () => {
  const url = new URL(instituteUrl({key:'another&account', courses:[{key:'another/course'}]}), 'https://doc4docs.com.br');
  assert.equal(url.searchParams.get('institute'), 'another&account');
  assert.equal(url.searchParams.get('course'), 'another/course');
});
test('Accounts without courses cannot open another institute by accident', () => {
  assert.equal(instituteUrl({key:'empty', courses:[]}), '');
  assert.equal(instituteUrl({key:'empty'}), '');
});
test('Institute initials preserve the clinic card design', () => {
  assert.equal(initials('Instituto Dr. Victor Paranhos'), 'VP');
  assert.equal(initials('Instituto Dra. Carla Ferreira'), 'CF');
  assert.equal(initials(''), 'IN');
});
