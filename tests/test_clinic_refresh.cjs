const assert = require('node:assert/strict');
const Controller = require('../static/clinic-refresh.js');
const test = require('node:test');

function fixture(t) {
  const originals = { fetch: global.fetch, setTimeout: global.setTimeout, clearTimeout: global.clearTimeout };
  t.after(() => Object.assign(global, originals));
  const pending = [], timers = new Map(), completed = [], label = {};
  const button = { title: '', attributes: {}, querySelector: () => label, addEventListener() {},
    setAttribute(key, value) { this.attributes[key] = value; } };
  const status = { dataset: {} };
  let timerId = 0;
  global.setTimeout = (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; };
  global.clearTimeout = id => timers.delete(id);
  global.fetch = (url, options) => new Promise((resolve, reject) => pending.push({ url, options, resolve, reject }));
  const controller = new Controller({ button, status, onComplete: async clinic => completed.push(clinic),
    getPeriod: () => ({ date_from: '2026-09-01', date_to: '2026-09-30' }) });
  const reply = async (request, data, ok = true) => {
    request.resolve({ ok, json: async () => ({ ok: true, clinic: 'vielle', phase: 'idle', ...data }) });
    await new Promise(setImmediate);
  };
  return { controller, button, label, status, pending, reply, timers, completed };
}

test('one scoped POST, busy progress, completion reload and cooldown', async t => {
  const f = fixture(t);
  f.controller.setClinic('vielle');
  assert.equal(f.pending[0].options, undefined);
  await f.reply(f.pending.shift(), { running: false });
  const request = f.controller.request(true);
  assert.equal(f.button.disabled, true);
  assert.equal(f.label.textContent, 'Atualizando...');
  await f.controller.request(true);
  assert.equal(f.pending.length, 1);
  const post = f.pending.shift();
  assert.equal(post.url, '/api/refresh?clinic=vielle');
  assert.equal(post.options.method, 'POST');
  assert.deepEqual(JSON.parse(post.options.body), { date_from: '2026-09-01', date_to: '2026-09-30' });
  await f.reply(post, { running: true, phase: 'running', job_id: 'job1', message: 'Atualizando Kommo...' });
  await request;
  assert.equal(f.status.textContent, 'Atualizando Kommo...');
  const poll = f.controller.request(false);
  await f.reply(f.pending.shift(), { running: false, phase: 'done', job_id: 'job1', message: 'Atualizado.', retry_after: 60 });
  await poll;
  assert.deepEqual(f.completed, ['vielle']);
  assert.equal(f.label.textContent, 'Atualizar tudo');
  assert.equal(f.button.disabled, true);
  const repeat = f.controller.request(false);
  await f.reply(f.pending.shift(), { running: false, phase: 'done', job_id: 'job1', retry_after: 0 });
  await repeat;
  assert.deepEqual(f.completed, ['vielle']);
  assert.equal(f.button.disabled, false);
});

test('late idle status cannot overwrite a newer POST', async t => {
  const f = fixture(t);
  f.controller.setClinic('vielle');
  const idle = f.pending.shift();
  const post = f.controller.request(true);
  await f.reply(f.pending.shift(), { running: true, phase: 'running', message: 'Atualizando...' });
  await post;
  await f.reply(idle, { running: false });
  assert.equal(f.button.disabled, true);
  assert.equal(f.status.textContent, 'Atualizando...');
});

test('changing clinic ignores stale responses and cancels polling', async t => {
  const f = fixture(t);
  f.controller.setClinic('vielle');
  const old = f.pending.shift();
  f.controller.setClinic('inspire');
  await f.reply(f.pending.shift(), { clinic: 'inspire', running: false });
  await f.reply(old, { running: true, message: 'Wrong clinic' });
  assert.equal(f.status.hidden, true);
  assert.equal(f.timers.size, 0);
  assert.equal(f.button.disabled, false);
  f.controller.setClinic('');
  assert.equal(f.button.disabled, true);
});

test('failed request allows retry without starting automatically', async t => {
  const f = fixture(t);
  f.controller.setClinic('vielle');
  await f.reply(f.pending.shift(), { running: false });
  const post = f.controller.request(true);
  await f.reply(f.pending.shift(), { ok: false, error: 'Falha temporária' }, false);
  await post;
  assert.equal(f.button.disabled, false);
  assert.equal(f.status.dataset.phase, 'error');
  assert.equal(f.status.textContent, 'Falha temporária');
  assert.equal(f.timers.size, 0);
});
