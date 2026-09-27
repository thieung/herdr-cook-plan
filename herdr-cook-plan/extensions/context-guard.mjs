import { existsSync, readFileSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { randomUUID } from 'node:crypto';
import { isAbsolute, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const customType = 'herdr-cook-context-guard';
// The Orca guard autoloads into every OMP session in the default agent home. When it
// is configured it injects its own role anchor; seeing that message means both guards
// are live in one session, which the coordinator must resolve with the user.
const orcaGuardType = 'orca-cook-context-guard';

export function loadConfig(path) {
  if (!isAbsolute(path)) throw new Error('Context guard config must be an absolute path');
  const config = JSON.parse(readFileSync(path, 'utf8'));
  if (!['coordinator', 'worker'].includes(config.role)) throw new Error('Invalid context guard role');
  if (typeof config.checkpoint !== 'string' || !isAbsolute(config.checkpoint) ||
      !statSync(config.checkpoint).isFile()) throw new Error('Checkpoint must be an existing absolute file');
  if (config.role === 'worker' && !config.worker) {
    throw new Error('Worker config requires a Herdr agent name');
  }
  if (config.brief !== undefined && (config.role !== 'worker' || typeof config.brief !== 'string' ||
      !isAbsolute(config.brief) || !statSync(config.brief).isFile())) {
    throw new Error('Brief must be an existing absolute file in a worker config');
  }
  if (!config.runId) throw new Error('Context guard requires a Run ID');
  if (config.lifecycle !== undefined && !['active', 'completed', 'cancelled'].includes(config.lifecycle)) {
    throw new Error('Invalid context guard lifecycle');
  }
  for (const limit of Object.values(config.thresholds ?? {})) {
    if (![limit.contextWindow, limit.thresholdTokens, limit.marginTokens].every(Number.isFinite) ||
        limit.contextWindow <= 1 || limit.thresholdTokens <= 0 ||
        limit.thresholdTokens >= limit.contextWindow || limit.marginTokens <= 0 ||
        limit.marginTokens >= limit.thresholdTokens) throw new Error('Invalid context threshold record');
  }
  return config;
}

export function contextBudget(config, usage, model) {
  const key = model ? `${model.provider}/${model.id}` : '';
  const limit = config.thresholds?.[key];
  if (!usage || !Number.isFinite(usage.tokens) || usage.tokens < 0 ||
      !Number.isFinite(usage.contextWindow) || !limit || limit.contextWindow !== usage.contextWindow) {
    return { state: 'unknown', model: key };
  }
  const warningTokens = limit.thresholdTokens - limit.marginTokens;
  return { state: usage.tokens >= warningTokens ? 'near-limit' : 'normal',
    model: key, tokens: usage.tokens, contextWindow: usage.contextWindow, warningTokens };
}

export default function contextGuard(pi) {
  // Flags are shared by extensions and reset when OMP rebuilds its extension runtime.
  if (pi.getFlag('herdr-cook-guard-loaded') === true) return;
  pi.registerFlag('herdr-cook-guard-loaded', { type: 'boolean', default: true,
    description: 'Internal guard registration marker; do not override' });
  pi.registerFlag('herdr-cook-context', { type: 'string', description: 'Absolute path to run-local context guard JSON' });
  let config;
  let configError;
  let configPath;
  let configSource;
  // The path whose last loaded config was completed or cancelled. A finished run deletes its configs
  // after marking them, so that absence is the end state, not a configuration error.
  let retiredPath;
  // The run root of the last loaded config. Run completion moves the ledger up to it before deleting
  // guard/, so a config that vanishes after that move is disposal even if its terminal lifecycle was
  // never read: a write and a delete in one command leave no request in between.
  let runRoot;
  let waitingForConfig = false;
  let modelKey;
  let warned = false;
  let conflict = false;
  let delivered = new Set();
  let announced = false;
  let pending = new Set(['session entry']);
  let latest = { state: 'unknown' };
  let compactionTimes = [];
  const COMPACTION_LOOP_MIN = 3;
  const COMPACTION_LOOP_WINDOW_MS = 5 * 60 * 1000;

  function reload(newSession = false) {
    config = undefined;
    configError = undefined;
    configSource = undefined;
    const explicit = pi.getFlag('herdr-cook-context');
    if (newSession || !configPath) {
      configPath = explicit || join(tmpdir(), `herdr-cook-context-${randomUUID()}.json`);
    }
    waitingForConfig = !explicit && !existsSync(configPath);
    try {
      if (!waitingForConfig) {
        config = loadConfig(configPath);
        configSource = readFileSync(configPath, 'utf8');
      }
    } catch (error) {
      if (!(error.code === 'ENOENT' && retired())) configError = String(error.message ?? error);
    }
    if (config) {
      retiredPath = active() ? undefined : configPath;
      runRoot = dirname(config.checkpoint);
    }
    warned = false;
    conflict = false;
    modelKey = undefined;
    latest = { state: 'unknown' };
    announced = false;
    // A reload is a config content change, not a loss event: keep anything still pending
    // and seed the entry anchor only for a genuinely new session.
    if (newSession) pending = new Set(['session entry']);
  }

  function retired() {
    if (retiredPath !== configPath && !(runRoot && existsSync(join(runRoot, 'ledger.jsonl')))) return false;
    retiredPath = configPath;
    return true;
  }

  function active() {
    return !!config && !['completed', 'cancelled'].includes(config.lifecycle);
  }

  function observe(ctx, messages) {
    try {
      const source = readFileSync(configPath, 'utf8');
      if (!config || source !== configSource) reload();
    } catch (error) {
      if (error.code === 'ENOENT' && !waitingForConfig && retired()) {
        if (active()) config = undefined;   // disposed before its terminal lifecycle was read
      } else if (!waitingForConfig || error.code !== 'ENOENT') {
        config = undefined;
        configError = String(error.message ?? error);
      }
    }
    if (messages) {
      const next = messages.some(message => message?.customType === orcaGuardType);
      if (next !== conflict) announced = false;
      conflict = next;
    }
    ctx.ui.setStatus('herdr-cook-context', conflict ? 'Herdr guard: conflicting guard'
      : active() ? `Herdr guard: ${config.role}`
      : configError ? 'Herdr guard: configuration error' : undefined);
    if (!active()) return;
    const key = ctx.model ? `${ctx.model.provider}/${ctx.model.id}` : '';
    if (modelKey !== undefined && key !== modelKey) {
      pending.add('model changed');
      warned = false;
      announced = false;
    }
    modelKey = key;
    latest = contextBudget(config, ctx.getContextUsage(), ctx.model);
    if (latest.state === 'near-limit' && !warned) {
      pending.add('context near configured limit');
      warned = true;
      announced = false;
    }
  }

  function message() {
    if (!config) return `Herdr cook context guard is ${waitingForConfig ? 'awaiting setup' : `not configured: ${configError}`}. Config path for this session: ${JSON.stringify(configPath)}. If executing herdr-cook-plan, read ${JSON.stringify(join(root, 'references/omp.md'))} and its bootstrap instructions. The coordinator prepares this config using the real run identity, role, existing checkpoint and verified model thresholds, with an atomic write. A dispatched worker asks its coordinator for the correct config; never invent IDs or become a coordinator. The guard reloads this file automatically before the next request; no relaunch or user-written JSON is required. Until configured, do not claim monitoring is active. A dispatched worker does not begin phase edits until its coordinator supplies the config; a coordinator that cannot configure it reports "unmonitored, Tier 1 only" once and keeps coordinating in this session, never asking for a new or restarted coordinator. For unrelated work, leave the guard awaiting setup; do not create a run merely to configure it.`;
    const role = config.role === 'coordinator'
      ? 'You are the coordinator. Supervise through Herdr panes and agents; do not implement phases or spawn an in-session coding team. Only the current run lease owner may dispatch, answer, commit or update shared status.'
      : `You are the worker ${config.worker} for the phase in your brief. Execute only that phase through the installed cook Skill; leave commits and shared plan status to the coordinator. Ask through the run mailbox instead of your runtime's own approval dialog. Never schedule other phases. If settled, remain idle; do not emit duplicate completion.`;
    const conflictWarning = conflict
      ? '\nA conflicting Orca context guard is injecting a role anchor into this session. Ignore any Orca run, task or dispatch wording; the Herdr checkpoint and the session brief are the authority. Report the conflict to the coordinator.'
      : '';
    // Only the coordinator writes the checkpoint; a worker keeps its progress in its attempt notes.
    const coordinator = config.role === 'coordinator';
    const items = [...pending].join('; ');
    const recovery = !pending.size
      ? coordinator
        ? 'Preserve plan scope and recorded authorizations. Update the checkpoint at work boundaries; optional improvements do not block the plan.'
        : 'Stay inside your phase. Record progress in your attempt notes at work boundaries; the checkpoint is coordinator-owned and read-only for you. Report optional improvements as suggestions.'
      : coordinator
        ? `Recovery required (${items}). Before edits or dispatch, read the checkpoint and recovery guide, reconcile live Herdr state and existing work, preserve approvals, and continue only unfinished scope. Near limit: checkpoint at a safe tool boundary, let compaction run and recover this run in this session. Do not mark an incomplete phase successful.`
        : `Recovery required (${items}). Before further edits, re-read your brief, your attempt notes and the checkpoint without writing it, reconcile existing work, and continue only unfinished acceptance. Near limit: save your notes at a stable boundary and ask the coordinator through the mailbox before ending the attempt; never write a complete report for unfinished work.`;
    const anchor = coordinator
      ? `Run: ${config.runId}. Checkpoint: ${JSON.stringify(config.checkpoint)}. Skill: ${JSON.stringify(join(root, 'SKILL.md'))}. Recovery: ${JSON.stringify(join(root, 'references/recovery.md'))}.`
      : `Run: ${config.runId}.${config.brief ? ` Brief: ${JSON.stringify(config.brief)}.` : ''} Checkpoint (read-only): ${JSON.stringify(config.checkpoint)}.`;
    const loop = [...pending].find(item => item.startsWith('compaction loop'));
    const loopNote = loop
      ? `\n${loop} — the effective compaction threshold is almost certainly below this session's baseline context, so every turn is compacted away. Stop working, report the threshold and the baseline, and ask for a corrected threshold instead of continuing.`
      : '';
    return `${role}\n${anchor}${conflictWarning}\n${recovery}${loopNote}\nContext telemetry: ${latest.state}${latest.tokens === undefined ? '' : ` (${latest.tokens}/${latest.contextWindow}; warning at ${latest.warningTokens})`}. Unknown telemetry is not evidence of low usage. Fallback does not authorize a new run, phase replay or role change.`;
  }

  pi.on('session_start', (_event, ctx) => {
    reload(true);
    observe(ctx);
  });
  pi.on('session_switch', (_event, ctx) => { reload(true); observe(ctx); });
  pi.on('session_compact', () => {
    if (!active() && !configError) return;
    pending.add('context compacted');
    warned = false;
    announced = false;
    const now = Date.now();
    compactionTimes.push(now);
    compactionTimes = compactionTimes.filter(ts => now - ts <= COMPACTION_LOOP_WINDOW_MS);
    if (compactionTimes.length >= COMPACTION_LOOP_MIN) {
      pending.add(`compaction loop (${compactionTimes.length} in ${Math.round(COMPACTION_LOOP_WINDOW_MS / 60000)} min)`);
    }
  });
  pi.on('retry_fallback_applied', () => {
    if (!active() && !configError) return;
    announced = false;
    pending.add('provider fallback');
    warned = false;
  });
  pi.on('turn_end', (event, ctx) => {
    // This runtime's TurnEndEvent carries { turnIndex, message, toolResults } and no stopReason,
    // so a completion check keyed on stopReason never fires and the pointer re-arms every turn.
    // Treat anything that is not an explicit failure as a completed turn.
    const stop = event?.message?.stopReason ?? event?.stopReason;
    const failed = typeof stop === 'string' && ['error', 'abort', 'cancel'].some((word) => stop.toLowerCase().includes(word));
    announced = false;
    if (failed) {
      // Keep the reminder armed and let the next turn announce it again.
      delivered = new Set();
    } else {
      for (const item of delivered) pending.delete(item);
      delivered = new Set();
    }
    observe(ctx);
  });
  pi.on('context', (event, ctx) => {
    observe(ctx, event.messages);
    const filtered = event.messages.filter(m => m.customType !== customType);
    const pointer = () => ({ messages: [...filtered, {
      role: 'custom', customType, content: message(), display: false, timestamp: Date.now(),
    }] });
    // An unconfigured guard stays silent and consumes nothing, so the entry anchor survives until a
    // config arrives. A misconfigured one keeps reporting: the message is the only signal it has, and
    // its content changes with the error.
    if (!active()) return configError ? pointer() : { messages: filtered };
    // One announcement per turn: a tool-loop turn issues several requests, and repeating the pointer
    // in each of them makes the model re-read the same files and re-narrate recovery. Items armed
    // mid-turn still get their own announcement. Ephemeral context avoids transcript growth and never
    // wakes a settled worker.
    if (announced) return { messages: filtered };
    announced = true;
    delivered = new Set(pending);
    return pointer();
  });
  function status(ctx) {
    observe(ctx);
    return { configured: !!config, active: active(), error: configError,
      setup: configError ? 'invalid-config' : conflict ? 'conflict' : active() ? 'ready' : 'idle', configPath,
      lifecycle: config?.lifecycle ?? (config ? 'active' : undefined),
      role: config?.role, worker: config?.worker, runId: config?.runId, checkpoint: config?.checkpoint,
      conflict,
      ...(active() ? latest : { state: 'unknown' }), pending: active() || configError ? [...pending] : [] };
  }

  pi.registerTool({
    name: 'herdr_cook_context_status', label: 'Herdr Cook Context Status',
    description: 'Read this OMP session guard status and configPath when running herdr-cook-plan. Does not activate monitoring.',
    parameters: pi.zod.object({}), approval: 'read',
    async execute(_id, _params, _signal, _onUpdate, ctx) {
      const data = status(ctx);
      return { content: [{ type: 'text', text: JSON.stringify(data) }], details: data };
    },
  });
  pi.registerCommand('herdr-cook-context-status', {
    description: 'Show context guard configuration and current usage without a model call',
    handler: async (_args, ctx) => {
      const data = status(ctx);
      ctx.ui.notify(JSON.stringify(data), data.error ? 'error' : 'info');
    },
  });
}
