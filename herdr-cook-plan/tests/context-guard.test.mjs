import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import guard, { loadConfig, contextBudget } from '../extensions/context-guard.mjs';

// Patterns are built from strings so the file has no slash-delimited literals.
const rx = (source, flags) => new RegExp(source, flags);

const thresholds = { 'test/large': { contextWindow: 100000, thresholdTokens: 85000, marginTokens: 10000 },
  'test/small': { contextWindow: 50000, thresholdTokens: 40000, marginTokens: 5000 } };
function fixture(t, role = 'worker', explicit = true) {
  const dir = mkdtempSync(join(tmpdir(), 'herdr-guard-test-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const checkpoint = join(dir, 'checkpoint.md');
  writeFileSync(checkpoint, 'Fixture only');
  const path = join(dir, 'config.json');
  const config = { role, runId: 'run_test', worker: 'p02', checkpoint, thresholds };
  writeFileSync(path, JSON.stringify(config));
  const handlers = new Map(), commands = new Map(), notices = [], statuses = [], tools = new Map();
  const pi = { zod: { object: value => value }, registerTool: tool => tools.set(tool.name, tool), registerFlag() {}, getFlag: () => explicit ? path : undefined,
    on: (name, fn) => handlers.set(name, fn), registerCommand: (name, obj) => commands.set(name, obj) };
  guard(pi);
  let tokens = 1000;
  const ctx = { model: { provider: 'test', id: 'large' },
    getContextUsage: () => ({ tokens, contextWindow: ctx.model.id === 'large' ? 100000 : 50000 }),
    ui: { setStatus: (_key, value) => statuses.push(value), notify: text => notices.push(JSON.parse(text)) } };
  const emit = (name, event = {}) => handlers.get(name)(event, ctx);
  emit('session_start');
  return { path, config, ctx, handlers, commands, notices, statuses, tools, emit,
    tokens: n => { tokens = n; },
    content: (messages = [{ role: 'user', content: 'turn' }]) => emit('context', { messages }).messages.at(-1).content,
    injected: (messages = [{ role: 'user', content: 'turn' }]) => {
      const out = emit('context', { messages });
      return out.messages.some(message => message.customType === 'herdr-cook-context-guard');
    },
    success: () => emit('turn_end', { message: { stopReason: 'stop' } }) };
}

test('threshold crossing warns once until compaction; messages never accumulate', t => {
  const f = fixture(t);
  f.content(); f.success();
  f.tokens(75000);
  assert.match(f.content(), rx('context near configured limit'));
  f.success();
  assert.doesNotMatch(f.content(), rx('context near configured limit'));
  // One announcement per turn: a repeat request in the same turn neither repeats it nor stacks a
  // second copy of it.
  const stale = { role: 'custom', customType: 'herdr-cook-context-guard', content: 'stale pointer' };
  const first = f.emit('context', { messages: [stale, { role: 'user', content: 'hello' }] });
  const second = f.emit('context', first);
  assert.equal(first.messages.length, 1, 'the stale pointer is replaced, not stacked');
  assert.equal(second.messages.length, 1, 'a second request in the same turn adds nothing');
  assert.equal(second.messages[0].content, 'hello');
  f.emit('session_compact');
  assert.match(f.content(), rx('context compacted'));
});

test('fallback keeps worker role and requests recovery; budget uses new model', t => {
  const f = fixture(t);
  f.content(); f.success();
  f.ctx.model.id = 'small'; f.tokens(36000);
  f.emit('retry_fallback_applied', { from: 'test/large', to: 'test/small' });
  const text = f.content();
  assert.match(text, rx('provider fallback'));
  assert.match(text, rx('worker p02'));
  assert.match(text, rx('36000/50000; warning at 35000'));
  assert.match(text, rx('Run: run_test'));
});

test('events after context build survive the old turn_end; failed requests retain recovery', t => {
  const f = fixture(t);
  f.content();
  f.emit('session_compact'); f.success();
  assert.match(f.content(), rx('context compacted'));
  f.emit('turn_end', { message: { stopReason: 'error' } });
  assert.match(f.content(), rx('context compacted'));
  f.success();
  assert.doesNotMatch(f.content(), rx('context compacted'));
});

test('unknown or mismatched usage never invents a percentage or threshold', t => {
  const f = fixture(t);
  assert.equal(contextBudget(f.config, undefined, f.ctx.model).state, 'unknown');
  assert.equal(contextBudget(f.config, { tokens: 90000, contextWindow: 200000 }, f.ctx.model).state, 'unknown');
  f.ctx.model.id = 'unmapped';
  assert.match(f.content(), rx('Context telemetry: unknown'));
  assert.doesNotMatch(f.content(), rx('context near configured limit'));
});

test('coordinator anchor restores ownership and does not impersonate worker', t => {
  const f = fixture(t, 'coordinator');
  const anchor = f.content();
  assert.match(anchor, rx('You are the coordinator'));
  assert.match(anchor, rx('do not implement phases'));
  assert.doesNotMatch(anchor, rx('You are the worker'));
  assert.doesNotMatch(f.content(), rx('You are the coordinator')); // once per turn, not per request
});

test('bad config yields explicit blocker; loader rejects malformed limits and missing checkpoint', t => {
  const f = fixture(t);
  writeFileSync(f.path, JSON.stringify({ ...f.config, checkpoint: join(tmpdir(), 'herdr-test-missing', 'checkpoint.md') }));
  assert.throws(() => loadConfig(f.path));
  f.emit('session_start');
  const blocker = f.content();
  assert.match(blocker, rx('not configured'));
  assert.match(blocker, rx('unmonitored, Tier 1 only'));
  assert.match(blocker, rx('keeps coordinating in this session'));
  assert.doesNotMatch(blocker, rx('begin phase edits or dispatch'));
  writeFileSync(f.path, JSON.stringify({ ...f.config, thresholds: { bad: { contextWindow: 100, thresholdTokens: 110, marginTokens: 10 } } }));
  assert.throws(() => loadConfig(f.path), rx('threshold'));
});

test('status command reports configuration without starting a turn', async t => {
  const f = fixture(t);
  await f.commands.get('herdr-cook-context-status').handler('', f.ctx);
  assert.equal(f.notices[0].configured, true);
  assert.equal(f.notices[0].role, 'worker');
  assert.equal(f.handlers.has('session_before_compact'), false);
  // The fake API has no send/spawn/mutation methods, so accidental use fails these tests.
  f.emit('session_compact'); f.emit('retry_fallback_applied');
});

test('extension-only startup waits, then loads a real config without restarting', async t => {
  const f = fixture(t, 'coordinator', false);
  const status = async () => {
    await f.commands.get('herdr-cook-context-status').handler('', f.ctx);
    return f.notices.at(-1);
  };
  const initial = await status();
  assert.equal(initial.configured, false);
  assert.equal(initial.setup, 'idle');
  assert.equal(initial.active, false);
  assert.equal(initial.error, undefined);
  assert.equal(initial.state, 'unknown');
  t.after(() => rmSync(initial.configPath, { force: true }));
  assert.deepEqual(f.emit('context', { messages: [] }).messages, []);
  assert.equal(f.statuses.at(-1), undefined);
  writeFileSync(initial.configPath, JSON.stringify(f.config));
  assert.match(f.content(), rx('You are the coordinator'));
  assert.equal((await status()).configured, true);
  f.tokens(75000);
  assert.equal((await status()).state, 'near-limit');
  f.emit('session_switch');
  const switched = await status();
  assert.notEqual(switched.configPath, initial.configPath);
  assert.equal(switched.configured, false);
  assert.equal(switched.state, 'unknown');
});

test('bootstrap rejects malformed config and recovers after correction', async t => {
  const f = fixture(t, 'worker', false);
  await f.commands.get('herdr-cook-context-status').handler('', f.ctx);
  const path = f.notices.at(-1).configPath;
  t.after(() => rmSync(path, { force: true }));
  writeFileSync(path, '{');
  assert.match(f.content(), rx('not configured'));
  writeFileSync(path, JSON.stringify(f.config));
  assert.match(f.content(), rx('worker p02'));
});

test('recovery guidance is delivered once, and a config reload does not resurrect it', t => {
  const f = fixture(t);
  assert.match(f.content(), rx(String.raw`Recovery required \(session entry\)`));
  f.success();
  assert.doesNotMatch(f.content(), rx('Recovery required'));
  // A content change reloads the guard; that is not a loss event, so nothing is re-armed.
  writeFileSync(f.path, JSON.stringify({ ...f.config, worker: 'p02-rewritten' }));
  assert.doesNotMatch(f.content(), rx('Recovery required'));
  assert.equal(loadConfig(f.path).worker, 'p02-rewritten');
});

test('a model change is reported once and cleared by the next clean turn', t => {
  const f = fixture(t);
  f.content(); f.success();
  f.ctx.model.id = 'small';
  assert.match(f.content(), rx('model changed'));
  f.success();
  assert.doesNotMatch(f.content(), rx('model changed'));
});

test('an item added after delivery waits for the next request, and a failed turn keeps it', t => {
  const f = fixture(t);
  f.content();
  f.emit('session_compact');           // added after the delivery above
  f.success();                         // clears only what was delivered
  assert.match(f.content(), rx('context compacted'));
  f.emit('turn_end', { message: { stopReason: 'error' } });
  assert.match(f.content(), rx('context compacted'));
  f.success();
  assert.doesNotMatch(f.content(), rx('context compacted'));
});

test('a turn_end shaped like the real event still acknowledges delivery', t => {
  // OMP's TurnEndEvent is { type, turnIndex, message, toolResults } and its messages carry no
  // stopReason, so a clear keyed on stopReason never fires and the pointer repeats every turn.
  const f = fixture(t);
  assert.match(f.content(), rx(String.raw`Recovery required \(session entry\)`));
  f.emit('turn_end', { type: 'turn_end', turnIndex: 0, message: { role: 'assistant', content: 'done' }, toolResults: [] });
  assert.doesNotMatch(f.content(), rx('Recovery required'));
});

test('a turn_end with an explicit failure keeps the reminder armed', t => {
  const f = fixture(t);
  f.content();
  f.emit('turn_end', { message: { stopReason: 'error' } });
  assert.match(f.content(), rx('Recovery required'));
});

test('one announcement per loss event per turn, not one per request', t => {
  const f = fixture(t);
  assert.match(f.content(), rx(String.raw`Recovery required \(session entry\)`));
  // Same turn: the second request must not repeat the pointer.
  assert.doesNotMatch(f.content(), rx('Recovery required'));
  f.success();
  f.emit('session_compact');
  assert.match(f.content(), rx('context compacted'));      // the next turn announces it
  assert.doesNotMatch(f.content(), rx('context compacted')); // ...once
  f.success();
  assert.doesNotMatch(f.content(), rx('context compacted'));
});

test('a compaction loop is named, not silently tolerated', t => {
  const f = fixture(t);
  f.content();
  f.emit('session_compact'); f.emit('session_compact');
  assert.doesNotMatch(f.content(), rx('compaction loop'));
  f.success();
  f.emit('session_compact');                      // third within the window
  const text = f.content();
  assert.match(text, rx(String.raw`compaction loop \(3 in 5 min\)`));
  assert.match(text, rx("threshold is almost certainly below this session's baseline context"));
  f.success();
  assert.doesNotMatch(f.content(), rx('compaction loop'));
});

test('ambient and explicit loading register once; a fresh runtime can reload', () => {
  const flags = new Map();
  let commands = 0, hooks = 0;
  const api = { zod: { object: value => value }, registerTool() {}, getFlag: name => flags.get(name),
    registerFlag: (name, options) => flags.set(name, options.default),
    on() { hooks++; }, registerCommand() { commands++; } };
  guard(api);
  const firstHooks = hooks;
  guard({ ...api });
  assert.equal(commands, 1);
  assert.equal(hooks, firstHooks);
  flags.clear();
  guard({ ...api });
  assert.equal(commands, 2);
});

test('unrelated workflow stays silent, including compaction and fallback', async t => {
  const f = fixture(t, 'coordinator', false);
  f.emit('session_compact'); f.emit('retry_fallback_applied'); f.success();
  const messages = [{ role: 'user', content: 'Do unrelated work' }];
  assert.deepEqual(f.emit('context', { messages }).messages, messages);
  assert.ok(f.statuses.every(value => value === undefined));
  const result = await f.tools.get('herdr_cook_context_status').execute('test', {}, undefined, undefined, f.ctx);
  assert.equal(result.details.setup, 'idle');
  assert.equal(result.details.active, false);
  assert.deepEqual(result.details.pending, []);
});

test('waiting remains active; completion/cancellation stop injection and allow a new run', async t => {
  const f = fixture(t, 'coordinator');
  const tool = f.tools.get('herdr_cook_context_status');
  const status = async () => (await tool.execute('test', {}, undefined, undefined, f.ctx)).details;
  f.content(); f.success();
  assert.equal((await status()).active, true);
  f.emit('session_compact'); f.emit('retry_fallback_applied');
  assert.match(f.content(), rx('Run: run_test'));
  for (const lifecycle of ['completed', 'cancelled']) {
    writeFileSync(f.path, JSON.stringify({ ...f.config, lifecycle }));
    const messages = f.emit('context', { messages: [{ customType: 'herdr-cook-context-guard' }] }).messages;
    assert.deepEqual(messages, []);
    assert.equal(f.statuses.at(-1), undefined);
    assert.equal((await status()).active, false);
    f.emit('session_compact'); f.emit('retry_fallback_applied'); f.success();
    assert.equal((await status()).setup, 'idle');
    writeFileSync(f.path, JSON.stringify({ ...f.config, runId: 'run_next', lifecycle: 'active' }));
    assert.match(f.content(), rx('Run: run_next'));
    assert.equal((await status()).active, true);
  }
});

test('invalid lifecycle or missing active config blocks instead of silently disabling', t => {
  const f = fixture(t);
  writeFileSync(f.path, JSON.stringify({ ...f.config, lifecycle: 'waiting' }));
  assert.match(f.content(), rx('Invalid context guard lifecycle'));
  writeFileSync(f.path, JSON.stringify(f.config));
  assert.match(f.content(), rx('worker p02'));
  rmSync(f.path);
  assert.match(f.content(), rx('not configured'));
  writeFileSync(f.path, JSON.stringify(f.config));
  assert.match(f.content(), rx('worker p02'));
});

test('worker config requires a Herdr agent name; coordinator does not', t => {
  const f = fixture(t);
  const { worker, ...withoutWorker } = f.config;
  writeFileSync(f.path, JSON.stringify(withoutWorker));
  assert.throws(() => loadConfig(f.path), rx('agent name'));
  writeFileSync(f.path, JSON.stringify({ ...withoutWorker, role: 'coordinator' }));
  assert.equal(loadConfig(f.path).role, 'coordinator');
});

test('a live Orca guard is reported as a conflict, not silently ignored', async t => {
  const f = fixture(t, 'coordinator');
  const status = async () => (await f.tools.get('herdr_cook_context_status').execute('test', {}, undefined, undefined, f.ctx)).details;
  assert.equal((await status()).conflict, false);
  assert.doesNotMatch(f.content(), rx('conflicting Orca context guard'));

  const orca = { customType: 'orca-cook-context-guard', role: 'custom', content: 'You are the Orca coordinator' };
  const injected = f.emit('context', { messages: [orca] }).messages;
  assert.equal(injected.length, 2, 'both guards inject; the conflict must be surfaced');
  assert.match(injected.at(-1).content, rx('conflicting Orca context guard'));
  assert.match(injected.at(-1).content, rx('Herdr checkpoint and the session brief are the authority'));
  const conflicted = await status();
  assert.equal(conflicted.conflict, true);
  assert.equal(conflicted.setup, 'conflict');
  assert.equal(f.statuses.at(-1), 'Herdr guard: conflicting guard');

  assert.equal(f.emit('context', { messages: [] }).messages.length, 1);
  assert.equal((await status()).conflict, false);
});

test('only the coordinator is told to update the checkpoint; a worker keeps notes', t => {
  const worker = fixture(t);
  const texts = [worker.content()];                     // recovery pending: session entry
  worker.success();
  texts.push(worker.content());                         // steady state
  worker.success(); worker.tokens(76000);
  texts.push(worker.content());                         // near limit
  for (const text of texts) {
    assert.doesNotMatch(text, rx('update (the )?checkpoints?|checkpoint at a safe', 'i'));
    assert.match(text, rx('notes'));
    assert.match(text, rx('read-only|without writing it'));
    assert.doesNotMatch(text, rx('Recovery: '), 'the coordinator recovery guide is not a worker instruction');
  }
  assert.match(texts[2], rx('ask the coordinator through the mailbox before ending the attempt'));

  const coordinator = fixture(t, 'coordinator');
  coordinator.content(); coordinator.success();
  assert.match(coordinator.content(), rx('Update the checkpoint at work boundaries'));
  coordinator.success(); coordinator.emit('session_compact');
  const recovery = coordinator.content();
  assert.match(recovery, rx('Recovery: '));
  assert.doesNotMatch(recovery, rx('request a bounded handoff'), 'coordinator transfer is not supported');
});

test('a worker brief path is carried after compaction and must be an existing file', t => {
  const f = fixture(t);
  const brief = join(f.path, '..', 'brief-p02-a01.txt');
  writeFileSync(brief, 'brief');
  writeFileSync(f.path, JSON.stringify({ ...f.config, brief }));
  f.content(); f.success(); f.emit('session_compact');
  assert.match(f.content(), rx(String.raw`Brief: ".*brief-p02-a01\.txt"`));
  writeFileSync(f.path, JSON.stringify({ ...f.config, brief: 'relative.txt' }));
  assert.throws(() => loadConfig(f.path), rx('Brief must be'));
  writeFileSync(f.path, JSON.stringify({ ...f.config, role: 'coordinator', brief }));
  assert.throws(() => loadConfig(f.path), rx('Brief must be'));
});

test('deleting a completed config stays silent; deleting an active one still reports', async t => {
  const f = fixture(t, 'coordinator');
  const status = async () => (await f.tools.get('herdr_cook_context_status').execute('test', {}, undefined, undefined, f.ctx)).details;
  f.content(); f.success();
  writeFileSync(f.path, JSON.stringify({ ...f.config, lifecycle: 'completed' }));
  assert.deepEqual(f.emit('context', { messages: [] }).messages, []);
  rmSync(f.path);                                        // run completion disposes of guard/
  assert.deepEqual(f.emit('context', { messages: [] }).messages, [], 'a finished run must not re-anchor');
  assert.equal((await status()).error, undefined);
  f.emit('session_start');                               // a later session in the same process
  assert.deepEqual(f.emit('context', { messages: [] }).messages, []);
  assert.equal((await status()).setup, 'idle');
  writeFileSync(f.path, JSON.stringify(f.config));       // the path is reused by a new active run
  assert.match(f.content(), rx('You are the coordinator'));
  rmSync(f.path);
  assert.match(f.content(), rx('not configured'));
});

test('completion disposal with no request in between stays silent; a lost active config does not', async t => {
  const status = async f => (await f.tools.get('herdr_cook_context_status').execute('test', {}, undefined, undefined, f.ctx)).details;
  // Run completion in one command: mark completed, move the ledger to the run root, delete guard/.
  const done = fixture(t, 'coordinator');
  done.content(); done.success();
  writeFileSync(done.path, JSON.stringify({ ...done.config, lifecycle: 'completed' }));
  writeFileSync(join(done.path, '..', 'ledger.jsonl'), '{}\n');
  rmSync(done.path);
  assert.deepEqual(done.emit('context', { messages: [] }).messages, [], 'disposal must not re-anchor');
  assert.equal((await status(done)).error, undefined);
  done.emit('session_start');                              // a later session in the same process
  assert.deepEqual(done.emit('context', { messages: [] }).messages, []);

  // Still active and never marked: the ledger move alone is the durable disposal marker.
  const unmarked = fixture(t, 'coordinator');
  unmarked.content(); unmarked.success();
  writeFileSync(join(unmarked.path, '..', 'ledger.jsonl'), '{}\n');
  rmSync(unmarked.path);
  assert.deepEqual(unmarked.emit('context', { messages: [] }).messages, []);
  assert.equal((await status(unmarked)).active, false);

  // Marked completed but deleted before the ledger moved, with no read in between: indistinguishable
  // from a lost active config, so it is reported rather than silently ignored.
  const early = fixture(t, 'coordinator');
  early.content(); early.success();
  writeFileSync(early.path, JSON.stringify({ ...early.config, lifecycle: 'completed' }));
  rmSync(early.path);
  assert.match(early.content(), rx('not configured'));
});
