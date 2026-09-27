---
name: herdr-cook-plan
description: >-
  Run an existing AgentKit plan phase by phase through Herdr, using one fresh worker agent per
  phase and sequential or parallel execution based on dependencies. The coordinator supervises from
  a Herdr pane, relays approval questions through a file mailbox, verifies and commits each phase,
  and closes the panes it created. Requires HERDR_ENV=1. Not for creating a plan, and not for a full
  handoff.
user-invocable: true
when_to_use: "Invoke inside a Herdr pane when an existing plan should be executed through Herdr workers."
category: utilities
keywords: [herdr, plan, orchestration, cook, panes, workers]
argument-hint: "<plan.md> [--runtime <kind>] [--select-agent] [--model <id>] [--omp-advisor all|none|<phase,...>] [--auto] [--advice] [cook flags]"
license: MIT
metadata:
  author: Thieu Nguyen
  version: "1.0.0"
---

# Herdr Cook Plan

Run one existing plan through Herdr without accumulating implementation context in the coordinator.
Each phase gets a fresh pane and a fresh agent running `ak:cook`. The coordinator reads the plan,
schedules workers, answers questions, verifies evidence, commits each accepted phase, and reports
progress. Implementation and repairs stay in workers, so the coordinator survives compaction, quota
exhaustion and provider fallback without carrying the work itself. Never switch to implementing a
phase or to spawning an in-session coding team: dispatch implementation and repairs as Herdr workers.

Load each reference before the work it owns:

| Reference | Load it for |
|---|---|
| [dispatch](references/dispatch.md) | Launching or replacing a worker, the worker brief, the project execution guide |
| [supervision](references/supervision.md) | Every supervision round, blocked and stalled workers, worker replacement |
| [mailbox](references/mailbox.md) | Questions, answers, and run-local disposal |
| [recovery](references/recovery.md) | Run root, existing-run refusal, boundary check, lease, compaction, crash resume |
| [runtimes](references/runtimes.md) | Placement, worker selection, preflight, recovery coverage per runtime |
| [omp](references/omp.md) | OMP launch, model roles, advisor, paste overlay, context guard |
| [selection](references/selection.md) | `--select-agent` runtime and model choice |

## Before anything else

Three gates. Failing any of them stops the run, and there is no degraded mode.

1. **Inside Herdr.** `test "$HERDR_ENV" = 1`. If it fails, say you are not running inside a
   Herdr-managed pane and stop. Never inspect or control a Herdr session from outside Herdr.
2. **Version.** `herdr --version` must be 0.9.1 or newer. On older binaries the dispatch and wait verbs
   differ, so refuse and report the observed version instead of improvising.
3. **Integration coverage.** `herdr integration status`: every kind this run will use must be `current`.
   OMP has no screen-manifest fallback, so without its integration `blocked` and `idle` are not
   trustworthy. Install a missing one with `herdr integration install <kind>` and re-check.

Then load the live contract: `herdr --skill` for the release-matched guide, and `herdr <group>` for the
exact syntax of every group you use. The installed binary is the authority; a checked-in copy of a Herdr
skill may be stale. Do not probe a mutating command by omitting its arguments.

Before minting a run root, discover existing runs as [recovery](references/recovery.md) requires: a live
run stops the invocation (coordinator transfer is not supported), a stale run waits for `resume run` or
`abandon run`, and a finished run is reported in one line and does not block. Recovery after compaction,
fallback or a missing guard happens in this session; never ask for a new coordinator. Run roots are
gitignored (`*`), so list the directory instead of trusting a filtered glob — a filtered glob reports no
runs even when one exists.

## Inputs

```text
SLASHherdr-cook-plan <plan.md> --auto                       # Claude Code, Cursor, Grok
SLASHskill:herdr-cook-plan <plan.md> --auto                 # Pi, OMP
$herdr-cook-plan <plan.md> --select-agent --auto --advice   # Codex
SLASHherdr-cook-plan <plan.md> --runtime omp --omp-advisor all --auto
```

- `SLASH` means a leading slash; UPPERCASE names are path placeholders: [dispatch](references/dispatch.md).
- Accept one plan path, absolute or relative, optionally prefixed with `@`, or a directory containing
  `plan.md`.
- `--runtime <kind>` selects the worker runtime by Herdr agent kind (`omp`, `claude`, `codex`, ...).
  Default: the kind of the agent the coordinator itself is running as, when that is a Herdr kind.
  Validate against `herdr agent` help; an unknown kind is a preflight failure. The flag maps to Herdr's
  own `--kind` argument on `agent start`; `--kind` is not accepted as a wrapper flag here. Accept the
  forms `--runtime=<kind>` and `--runtime=current` (the coordinator's own kind) as the same flag.
- `--model <id>` passes a model through the kind's own native argv after `--`. Read the kind's `--help`
  first and never invent a flag.
- `--select-agent` asks the user to choose the worker runtime and primary model once per run, before the
  first dispatch. An explicit `--runtime` skips the runtime question; `--auto` does **not** answer these
  questions. Consume the flag here and never forward it to cook.
- `--advice` forwards AgentKit's advisory flag to every phase and repair.
- `--omp-advisor all|none|<phase-id,...>` controls OMP's own advisor per worker. Reject unknown or
  ambiguous IDs and non-OMP kinds; when omitted, inherit the configured OMP advisor setting. A phase
  list applies to those phases and to their repairs and replacement workers.
- `--auto` delegates in-plan approval decisions to the orchestrator; never forward it to cook (see
  **Decisions**).
- Accept cook flags such as `--tdd` alongside wrapper options. Wrapper options are `--runtime`,
  `--model`, `--select-agent`, `--omp-advisor` and `--auto`; validate the rest against the installed
  worker cook trigger. A standalone `--` before cook flags is still accepted, and `--advice` is forwarded
  once if it is repeated across the separator.
- Treat `--parallel` as an outer scheduling preference: do not forward it into workers, and dependency or
  write conflicts still force serial execution.

These are Skill argument conventions, not an executable parser. Resolve conflicting modes before launch,
and never shell-evaluate prompt text. This is a live session, not a scheduler: nothing wakes a closed one.

## Concept mapping

| Earlier term | Herdr primitive used here |
|---|---|
| Run | One `runId` plus its run root; the caller's workspace and tab are context, not identity |
| Task / dispatch | One agent name plus pane ID per attempt (`p02a01`, then `p02a02`, in `w1:p5`) |
| Worker launch | `pane split --current --direction right --cwd "$PWD" --no-focus`, then `agent start <name> --kind <kind> --pane <captured-id>` |
| Prompt injection | `agent prompt <name> "$brief" --wait --timeout <ms>`, after a checked load of the brief file |
| Waiting | Per-round `agent wait <name>` without `--until` (matches `idle`, `done`, `blocked`), plus a mailbox scan |
| Ask and answer | Files under the run root's `mail/`, with the coordinator's ledger as the authority |
| Reading a worker | `agent read <name> --source recent-unwrapped --lines N`, `pane read` |
| Release | `pane close <pane_id>` for panes this run created, under **Close what you created** |
| Placement | Decided per phase before dispatch; the current worktree is the default (see `runtimes.md`) |
| Coordinator transfer | Not supported: the coordinator recovers in its own session; a crashed run is resumed or abandoned only on the user's sentence |

## Plan and execution order

Read the master plan, linked phase files, repository instructions and relevant Git state. Resolve links
relative to the plan directory; if links are absent, match the phase list against `phase-*.md` there. For
inline phases, give the worker the exact section anchor and restrict it to that phase.

Build the wave table — phase, dependencies, write scope, runtime and options, validation, placement — and
show it with a short explanation of the sequential or parallel choices, then execute the authorized
plan. Do not introduce a separate manifest-approval ceremony. Use explicit dependencies first;
otherwise preserve the plan's order unless independence is clear. Run independent, non-conflicting phases
in parallel within user and repository limits, defaulting to at most two workers. Serialize overlapping
writes and shared contract changes; uncertainty about safe concurrency means sequential execution, not an
approval gate. Placement rules are in [runtimes](references/runtimes.md).

When every phase is already accepted, report that and stop without minting a run root: a run root for
zero phases leaves a checkpoint that later invocations must classify.

An audit or dry-run request stops at findings and launches no workers.

## Decisions

Worker questions and approval requests travel through the mailbox; the coordinator answers by writing an
answer file and appending the ledger entry.

- With `--auto`, the orchestrator decides inside the plan's scope, records a short rationale, and
  answers. The worker never receives cook's `--auto`.
- Without `--auto`, the orchestrator relays the question to the user with phase and progress, waits, then
  writes the answer. It may relay an authorization the user already gave; it must not invent one.
- `--auto` covers decisions inside the requested plan. It does not authorize scope expansion,
  publication, deployment or destructive operations.
- A timeout, an expired poll or an empty mailbox is **not** approval: escalate, keep the phase blocked,
  and record the pending question.
- Native permission, trust and credential dialogs are not in-plan decisions; surface them to the user
  instead of answering them.
- Optional improvements outside the plan are non-blocking suggestions: decline or defer them with
  "Continue the approved phase scope". Escalate only a real missing requirement or authority boundary.
  A change the worker calls necessary must name the acceptance criterion and the concrete failure; under
  `--auto` authorize the smallest in-plan fix, and without both treat it as a suggestion.
- Only the asking phase and its dependents wait on an open question; independent workers keep running
  and ready independent phases may still dispatch.

A user-input pause is pending work, not completion: persist the question and the next action before
yielding, and keep coordinating when the answer arrives.

## Dispatch

Launch a new pane and a new agent for a phase or repair; never reuse a pane or resume a session.

```bash
herdr pane split --current --direction right --cwd "$PWD" --no-focus   # read .result.pane.pane_id
herdr agent start <agent-name> --kind <kind> --pane <pane_id> -- <native argv>
brief="$(cat -- "MAILBOX/brief-<phase>-a<aa>.txt")" && test -n "$brief" && herdr agent prompt <agent-name> "$brief" --wait --timeout <ms>
```

`agent_blocked` means no input was sent; `agent_prompt_stalled` or a timeout does not prove nothing was
sent, so read `agent get` and `agent read` before resending. Never inline the brief in double quotes: the
shell expands `$` (Codex's `$ak:cook` becomes empty). A failed or empty load sends nothing. The fish form,
the brief and its contents, the cook trigger, the paste overlay and the execution guide are in
[dispatch](references/dispatch.md).

## Supervise

Each round: scan the mailbox for unprocessed `q-*`, then one `agent wait <name> --timeout T` per `working`
worker — without `--until` it matches `idle`, `done` and `blocked` in one call. Exit status 1 is data —
branch on the JSON error code, and never treat `working` as progress. `idle` and `done` both mean ready
for input; never claim success from an idle terminal or a timed-out wait. Blocked handling, the stall
ladder, attempt caps and worker replacement are in [supervision](references/supervision.md).

## Accept and commit each phase

An accepted phase needs a `status: complete` report from its current attempt, a settled agent, a
verified scoped diff and passing checks.

1. Verify the scoped diff and the required review and check results against the final code. Reuse the
   review and tests cook already ran; dispatch a separate validation worker only for a stated reason
   (a check cook skipped, or evidence that contradicts its report).
2. Reconcile the phase status through the installed `ak plan` interface — the exact commands are in
   [dispatch](references/dispatch.md).
3. Record the intended acceptance in the checkpoint hot section **before** committing, then append the
   `phase-accepted` ledger entry with its SHA once the commit succeeds. The ledger entry must always
   carry the SHA or the evidence pointer; the crash window between the two is covered by the
   checkpoint's intent, not by an incomplete ledger line.
4. In a Git workspace, commit only that phase's owned paths and its status update: never the whole
   worktree, never `git add -A`, never the mailbox. Inspect the staged diff, follow repository commit
   conventions, verify the resulting SHA, and do not bypass hooks. This checkpoint is authorized by
   invoking this Skill, so do not ask for commit approval again.
5. Close that phase's worker pane under **Close what you created** before dispatching the next phase: an
   accepted worker is never needed again, and a pane carried to completion can be stranded by a crash.
6. Not a Git workspace, and the plan or user forbids `git init`: initialize nothing, accept the phase with
   owned file scope plus check and diff evidence, and report the no-SHA limitation as a recorded
   constraint.
7. Report completed and total phases, the result, checks, the SHA or the no-git evidence pointer and the
   next phase, then dispatch workers whose predecessors are verified and checkpointed. A no-change phase
   records that fact instead of an empty commit.
8. Report a dispatch or an acceptance only once its proof has landed — the brief file for a
   dispatch, the ledger entry and the commit for an acceptance. Narrate intent as intent; a progress
   claim the disk does not support yet is a defect.

Serialize commits. Parallel workers must not mutate a shared index or files that commit hooks need; wait
for a stable wave boundary, keeping separate phase commits. A failed check or a failed commit is a
blocker: report it, repair in a fresh worker when appropriate, and hold dependents. Keep Herdr's settled
state separate from this acceptance state.

## Close what you created

Close a worker's pane as soon as it is done — per phase, not at completion: an accepted phase's worker is
done, so close it before dispatching the next phase. Carrying settled panes to the end accumulates them
and lets a crash strand them, which a later resume then has to clean up. Before
`herdr pane close <pane_id>`, prove all of:

1. this run created the pane, and its ID is re-read live with `pane get`/`agent get` just before closing;
2. the worker settled and its output or report was captured and read;
3. the pane is settled — `agent get` reads `idle` or `done`, `pane process-info` shows no foreign
   process (one not descended from that agent session), and no other client has taken it over. An
   interactive runtime waits at its own prompt, so that presence is **not** a reason to retain the pane;
   a `working` session, an unread report or a foreign process is. Record the worker's own descendant
   processes before closing and confirm none survives after, per [supervision](references/supervision.md).

Never close a pane you did not create, never extend this authority to its tab, workspace, session, server
or worktree, and on an ambiguous handle leave it and report instead of force-closing.

## Completion

Completion requires every planned phase accepted and checkpointed, required combined validation resolved,
run-local artifacts disposed of, the lease released, and no unresolved question, repair or pane cleanup.
Combined validation is required when the plan asks for it, when accepted phases change interacting
behavior, or after integrating worktrees; otherwise per-phase checks suffice. Close in this order: worker
panes, summary, guard lifecycles, keep only the ledger from `mail/` and delete `guard/`, run the check
below, append `run-completed`, and release the lease as the last write; [recovery](references/recovery.md)
has the full order and why a premature `run-completed` strands steps nobody will revisit.

Set `lifecycle` in every guard config to `completed` (or `cancelled`), and move the ledger to the run
root before deleting `guard/`: the guard reads that move as disposal, while a config that simply vanishes
looks lost, and one still `active` re-anchors a session that has ended.

Disposal is not a prose rule: run the check and read its output before reporting completion. It parses
the run's records and disposal, not the plan's acceptance; a failure is unfinished work to fix.

```bash
python3 SKILL_DIR/scripts/check-run-closed.py RUN_ROOT [<guard config outside the run root> ...]
```

A phase report, an idle terminal, a model fallback or an answered question is not the end of the run:
continue while ready work exists, keep rolling waits while workers are active, and yield only for required
external input, a concrete blocker or an explicit user stop, saving the next action first.

## Summarize the completed plan

When the last worker's report is accepted and every phase is checkpointed, and before any run-local
artifact is disposed of, write one consolidated implementation summary to
`RUN_ROOT/implementation-summary.md` — inside the run root, outside `mail/`, so it survives disposal and
cannot be overwritten by another run — and present the same content with that path. Phase reports say what
each worker believed it did; the summary is the coordinator's evidence-backed account of what the plan
delivered, built only from accepted reports, verified diffs, check results, commit SHAs or no-git
evidence, and the ledger's decisions. Its section template is in [recovery](references/recovery.md); what
matters here is that it carries the placement, the SHA or evidence pointer per phase and every claim that
stayed `unknown`.

Report the result in the language defined below: completed and total phases, running phases, blockers and
the concrete next action; label reported completion versus verified acceptance when they differ; and
finish with phase outcomes, validation and skipped checks, actual model and advisor evidence, and
unresolved work. Separate a worker's claim from what the coordinator verified, and never upgrade a
reported result into a verified one. Never summarize a phase without acceptance evidence, never claim
completion from an idle terminal, and state explicitly when a phase landed outside the main tree.
Summarize the delivery; do not restate the whole plan.

## Output language

Write every report in the language of the user's own request — the language of the invocation or task
description that started the run.

- Judge from the user's most recent direct instruction; if it is too terse, fall back to the plan's
  language and then to English, and state the choice once when it was not obvious.
- Never translate command names, flags, IDs, file paths, code identifiers, status values, error strings
  or quoted output; keep them verbatim.
- Keep established technical terms in English when a native word would be ambiguous (commit, checkpoint,
  runtime, fallback, pane, lease).
- Label the next action consistently in the chosen language ("Next" in English, "Tiếp theo" in
  Vietnamese), and always state what happens next, what is waiting, or that no planned work remains.
- Keep terminology consistent within a run, and explain an unfamiliar term briefly on first use.
- When the output language is Vietnamese, prefer "đợt thực hiện" over a literal rendering of "wave".
