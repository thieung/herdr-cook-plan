# OMP workers and the context guard

Read this reference when the coordinator or a worker runs OMP. Herdr owns orchestration; OMP owns
model routing.

## Launching an OMP worker

Split the pane, then read the pane ID out of the JSON response and pass it to `agent start`:

```bash
herdr pane split --current --direction right --cwd "$PWD" --no-focus    # .result.pane.pane_id
herdr agent start p02a01 --kind omp --pane <pane_id> -- --config <run-local-overlay.json>
brief="$(cat -- "MAILBOX/brief-p02-a01.txt")" && test -n "$brief" && herdr agent prompt p02a01 "$brief" --wait --timeout 60000
```

The prompt line is the bash/zsh form; fish and argv-only callers use the forms in [dispatch](dispatch.md),
which also send nothing when the brief cannot be read.

`agent start` has no `--cwd`; the directory comes from the pane split. Put every OMP argument after
the `--` separator, and read `omp --help` before adding one.

## Preserve Oh My Pi model roles

Inherit the effective model roles, effort, retry fallback chains and per-agent overrides from the
same configured profile and project context. Inspect only the relevant keys with
`omp config get <key> --json` after confirming live help — `modelRoles`, `retry.fallbackChains`,
`task.agentModelOverrides`, `task.agentAdvisor`, `advisor.enabled`. Do not dump the full config or
credentials.

A fresh top-level OMP session uses its configured default role. Do not force it onto the TASK role
merely because Herdr calls it a worker, do not flatten every role into one `--model` override, and
do not copy the coordinator's model. A configured fallback chain is valid, not an error to block.

When the user selected a primary model with `--select-agent` or `--model`, that selection overrides
only the primary worker model; other role routing and fallbacks stay intact. Report the configured
choice separately from the observed session model, and never claim an unobserved model ran.

## Keep the two advisors independent

| Option | Where it applies | Meaning |
|---|---|---|
| `--advice` | The worker's cook invocation | AgentKit's advisory protocol (currently `kongming` checkpoints) |
| `--omp-advisor` | The OMP process launch | OMP's passive turn-by-turn advisor using its configured ADVISOR role |

Both may be enabled together. Preserve OMP's per-agent model and advisor overrides for `kongming`;
do not substitute ADVISOR for its configured agent model. To enable the OMP advisor, add `--advisor`.
To disable it for one run, pass a run-local config overlay with `advisor: { enabled: false }` through
`--config` and omit a conflicting `--advisor`. Never edit the user's global OMP config. Confirm the
effective state before claiming the option applied.

## Always pass the run-local paste overlay

OMP opens a modal paste menu (`Attach as a wrapped block` / `Attach as a local file` /
`Paste inline`) when a bracketed paste reaches `paste.largeMenuThreshold` lines — 100 by default —
before any of it reaches the composer. Herdr's `agent prompt` writes the text and then a delayed
Enter as a separate write, so the Enter that should submit the prompt can instead accept the menu's
default option; the composer keeps the prompt as a collapsed paste chip and no turn starts.

Every OMP worker launch therefore includes a run-local overlay containing:

```json
{ "paste": { "largeMenuThreshold": 0 } }
```

Pass it with `--config <absolute path>`, keep it outside tracked files, and keep it available until
the worker exits. Confirm the overlay reached the session by checking that a large paste raises no
menu — `omp config get --config <path>` reports the global value either way and does not prove it.

## Confirm the prompt was submitted

Follow **Confirm the prompt was submitted** in [dispatch](dispatch.md) for every OMP dispatch. The paste
chip and the `Pasted N lines` menu it handles are OMP's; one more rule applies here:

- A menu or a chip does not by itself prove the overlay was omitted. Record `herdr --version`, the
  kind, and whether `--config <overlay>` was in the launch argv before drawing a conclusion.

## Run-local context guard

The guard warns the session when context approaches the configured limit and re-anchors its role
after compaction or provider fallback. It never changes models, runs commands, writes the
checkpoint, dispatches workers or forces a turn.

Launch it explicitly for **both** the coordinator and every OMP worker:

```text
--extension SKILL_DIR/extensions/context-guard.mjs
--herdr-cook-context RUN_ROOT/guard/worker-context-p02.json
```

Bootstrap ordering matters. `--herdr-cook-context` must point at a file that already exists; an
explicit path that does not exist is reported as a configuration error, not as "awaiting setup".
So either pre-create the config before launching, or launch the coordinator with only
`--extension <guard>` and let it bootstrap: the guard starts `awaiting setup` (silent), and the
coordinator reads its own `configPath` from `herdr_cook_context_status`, writes the config
atomically, then verifies `configured: true` and the correct role before dispatch. If configuration
fails, report the error and "unmonitored, Tier 1 only" once and keep coordinating; a failed guard never
blocks the run or calls for a new coordinator.

Relying on ambient autoload is not sufficient: a skill installer manages the skill directory only
and does not own `OMP_HOME/extensions`, so a coordinator launched without `--extension` has no
guard. Do **not** add `--no-extensions` for the coordinator: it would also disable the Herdr OMP
integration's own extension and degrade that pane's state reporting.

Config is a coordinator-owned JSON file outside tracked source. Write it atomically, keep the path
absolute, and make `checkpoint` point at a file that already exists:

```json
{
  "role": "worker",
  "lifecycle": "active",
  "runId": "planslug-20260919T033800Z-a1b2c3",
  "worker": "p02a01",
  "brief": "PLAN_DIR/reports/herdr-cook-runs/RUN_ID/mail/brief-p02-a01.txt",
  "checkpoint": "PLAN_DIR/reports/herdr-cook-runs/RUN_ID/checkpoint.md",
  "thresholds": {
    "provider/model": {
      "contextWindow": 500000,
      "thresholdTokens": 425000,
      "marginTokens": 32000
    }
  }
}
```

For the coordinator use `role: "coordinator"` and omit `worker` and `brief`. A worker's optional `brief`
names its existing brief file, so the pointer can name it after compaction. The worker pointer tells it
to keep progress in its notes and treat the checkpoint as read-only; only the coordinator's pointer asks
for checkpoint updates. Resolve `thresholdTokens` from the
effective compaction settings, the model window and the version-matched threshold contract; read
only the relevant keys (`compaction.enabled`, `thresholdTokens`, `thresholdPercent`,
`reserveTokens`). If a value cannot be resolved, omit that model record and report telemetry as
`unknown` instead of inventing limits. `marginTokens` is a run-local warning policy, normally 12.5%
of the resolved threshold bounded to 8192..32000 tokens.

Inspect the status through the guard's tool (`herdr_cook_context_status`) or the
`SLASHherdr-cook-context-status` command, which make no model call. Unknown telemetry is not evidence of
low usage.

A compaction threshold must sit **above** the session's own baseline footprint. A threshold below it —
for example `compaction: { thresholdTokens: 20000 }` against a session whose system prompt, Skill text
and tool output already exceed 20k — compacts on every turn, and the session makes no progress: observed
once at 30 compactions in nine minutes, with the run never minting and each turn recovering
`artifact://` references. Measure the baseline before choosing a threshold, and prefer a mid-run manual
compaction over a low threshold when the goal is to exercise a single compaction. The guard names that
state as `compaction loop (N in 5 min)` and tells the session to stop and report.

The pointer carries the role anchor (`Run`, checkpoint and guide paths) and, when something is
outstanding, the recovery demand. Its cadence:

- The first request of every turn carries it; later requests in the same turn do not repeat it. A turn
  in a tool loop issues many requests, and repeating the pointer in each of them made the model re-read
  the same files and re-narrate recovery, so it is announced once per turn.
- A compaction, a provider fallback, a session restart or an observed model change arms the recovery
  demand. An item armed mid-turn is announced immediately, in the same turn.
- The next clean turn clears the demand. A failed turn keeps it armed and the following turn announces
  it again.
- A config rewrite is not a loss event, so editing the config file does not arm anything; it does
  refresh the pointer text.

If a session is re-injecting the recovery demand on every turn, that is a guard defect to report, not
normal behaviour.

At verified run completion or cancellation, update the config to `lifecycle: "completed"` or
`"cancelled"` for the coordinator and every retained worker, then dispose of the run-local config
files with the rest of the run artifacts. The guard stays silent when its config disappears after it
read a terminal lifecycle, or after the ledger reached `RUN_ROOT/ledger.jsonl` (completion moves it
there before deleting `guard/`), because a write and a delete in one command leave it no request in
between. Any other disappearance is reported as `not configured`, since it looks like a lost active
config. Delete a worker's config only after that worker's pane is closed.

## Coexisting with the Orca guard

`$HOME/.omp/agent/extensions/subscriber-orca-cook-plan.js` autoloads the Orca guard into every OMP
session in the default agent home. With no `--orca-cook-context` flag the Orca guard stays in
`awaiting setup` and its context hook returns early, so it injects no role anchor; a Herdr run only
ever writes Herdr config, so that state holds. The port depends on this, so:

- The Herdr guard reports `conflict` when it observes the Orca guard's custom type in the session,
  and the worker brief states that the Herdr checkpoint is the authority and any Orca role text must
  be ignored.
- If the Orca guard reports itself **active** (it has a config) in a Herdr run, stop and ask the
  user; do not silently disable or edit their autoload stub.
- Never pass `--no-extensions` to escape the overlap: it also disables the Herdr OMP integration
  (`herdr-omp-agent-state`), which would remove OMP's lifecycle state authority and make `blocked`
  and `idle` untrustworthy for exactly the workers this skill supervises.
