const test = require('node:test');
const assert = require('node:assert/strict');
const NotificationHistory = require('../static/app-shell.js');

function fixture() {
  const data = new Map();
  const storage = {getItem:key => data.get(key), setItem:(key,value) => data.set(key,value)};
  const history = new NotificationHistory(storage, () => 2000000000);
  return {history, storage, data};
}

test('repeated polling does not create duplicate unread alerts', () => {
  const {history} = fixture();
  history.setUser('first');
  assert.equal(history.add('sync', 'Falha de conexão', {scope:'vielle', level:'error'}), true);
  assert.equal(history.add('sync', 'Falha de conexão', {scope:'vielle', level:'error'}), false);
  assert.equal(history.unread, 1);
  history.read(history.items[0].id);
  history.add('sync', 'Falha de conexão', {scope:'vielle', level:'error'});
  assert.equal(history.unread, 0);
});

test('notifications survive navigation only for the same account', () => {
  const {history, storage} = fixture();
  history.setUser('first');
  history.add('sync', 'Atualizado.', {scope:'inspire'});
  const next = new NotificationHistory(storage, () => 2000000000);
  next.setUser('first');
  assert.equal(next.unread, 1);
  next.setUser('second');
  assert.equal(next.items.length, 0);
});

test('notices arriving before account loading are retained', () => {
  const {history} = fixture();
  history.add('notice', 'Não foi possível carregar os dados');
  history.setUser('first');
  assert.equal(history.unread, 1);
});

test('clinics remain distinct and only read notifications are cleared', () => {
  const {history} = fixture();
  history.setUser('first');
  history.add('sync', 'Atualizado.', {scope:'vielle'});
  history.add('sync', 'Atualizado.', {scope:'inspire'});
  history.read(history.items[0].id);
  history.clearRead();
  assert.equal(history.items.length, 1);
  assert.equal(history.items[0].scope, 'vielle');
  history.read();
  assert.equal(history.unread, 0);
});

test('history is bounded and expired or invalid stored notices are ignored', () => {
  const {history, storage, data} = fixture();
  history.setUser('first');
  for (let i=0; i<50; i++) history.add('sync', `Aviso ${i}`);
  assert.equal(history.items.length, 40);
  assert.equal(history.items[0].message, 'Aviso 49');
  data.set('doc4docs:notifications:expired', JSON.stringify([
    {...history.items[0], time:1}, {message:'Registro inválido'}
  ]));
  const next = new NotificationHistory(storage, () => 2000000000);
  next.setUser('expired');
  assert.equal(next.items.length, 0);
});

test('storage failures never prevent displaying a notification', () => {
  const storage = {getItem:() => {throw new Error('blocked');}, setItem:() => {throw new Error('full');}};
  const history = new NotificationHistory(storage);
  history.setUser('first');
  history.add('notice', 'Falha de conexão');
  assert.equal(history.unread, 1);
  history.read();
  assert.equal(history.unread, 0);
});
