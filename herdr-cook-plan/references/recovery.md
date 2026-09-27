# Checkpoint, lease and recovery

Load on entry, before any mutation, and before recovery or a crash resume. Worker replacement is in
[supervision](supervision.md).

## Durable execution checkpoint

Everything run-local lives under one root, keyed by `runId`, so two runs of the same plan can never
overwrite each other:

```
PLAN_DIR/reports/herdr-cook-runs/RUN_ID/
  .gitignore                    "*"
  checkpoint.md                 coordinator-owned; the run's state
  mail/                         mailbox files (see mailbox.md)
  guard/                        run-local guard configs and the paste overlay
  implementation-summary.md     written at completion, before mail/ is disposed of
  ledger.jsonl                  moved out of mail/ at completion; the only mailbox file kept
```

- `runId`: `<plan-slug>-<UTC>yyyymmddThhmmssZ>-<6 hex>`. Never derive it from Herdr IDs; a moved pane
  receives a new ID and IDs are scoped to one server.
- Create the run root with `chmod 700` and a `.gitignore` containing `*`. Different plans already live in
  different plan directories; the same plan run twice gets two run roots.
- Keep its absolute path in every worker brief and handoff. Workers read the checkpoint and never write
  it.

Keep two zones in `checkpoint.md`, because the hot section is read on every boundary and the detail is
not:

- **Hot section, first ~40 lines**: `runId`, lease `owner`/`epoch`/`heartbeat`, `coordinator` (its own
  pane ID and agent kind, the liveness anchor below), the phase table (phase, status, current attempt
  as two digits (`01`), agent and pane, placement, commit or evidence), every open question (id and
  phase), every in-flight mutation, and the next action. Keep in-flight mutations as one line each,
  so a parallel wave lists every one: `op <phase> <action> <target>: intent` rewritten to `: <receipt>`
  or `: unknown`, and dropped once reconciled. An `unknown` outcome is reconciled against live state
  before the mutation is retried. This is the only part read on the normal path.
- **Detail**: the wave table, the Project execution guide, per-phase history and links, and out-of-scope
  findings (never dispatched without authorization; carried into the summary's follow-ups). Move finished
  detail down as phases land, so the hot section stays bounded at any phase count.

Record only what is needed to resume: role, this Skill path, plan path, cwd or worktree, `runId`, the
original flags and authorization including `--auto`, the resolved runtime, cook path and launch argv;
the lease; the phase table and placement; Herdr-observed state kept separate from acceptance; evidence
paths and commit SHA, or verified no-change, or no-git file-scope evidence; pending question ids and
answers already written; the worker replacement count; and the Project execution guide.

## Existing runs: refuse by default

A second `herdr-cook-plan` must never start while another run is in flight. On entry, before creating
anything, discover existing runs and **refuse to proceed** when any is found — whether it looks live or
stale — except when the run is **finished**, as classified below. Detection scope, cheapest first. Every
run root ships `.gitignore` containing `*`, so a glob that honours ignore files reports nothing even when
a run exists: list the directory, or disable ignore filtering, and never conclude an absence from one.

1. This plan: list `PLAN_DIR/reports/herdr-cook-runs/` and read each `RUN_ID/checkpoint.md`, or glob
   every run's `checkpoint.md` there with ignore filtering off.
2. Sibling plans in the same plans root: the same `reports/herdr-cook-runs/RUN_ID/checkpoint.md` under
   each direct child of `PLANS_ROOT` — one level only, never a whole-filesystem scan — under the same
   no-filtered-glob rule.
3. Legacy single-file `PLAN_DIR/reports/herdr-cook-checkpoint.md`.
4. The same Git worktree and branch, when a discovered run's hot section names them.

Read each candidate's hot section and classify it by its **coordinator pane**, not by its workers:

- **live** when the recorded `coordinator` pane still exists and hosts that agent (`herdr pane get`,
  `herdr agent list`), whatever the heartbeat says: a coordinator waiting for the user writes nothing
  and is still live. If the pane cannot be checked (another server, unreadable), treat it as live.
- **stale** when that pane is gone or does not host the coordinator. Its workers may still run — an
  orphaned run is stale, because a live worker never makes the coordinator live.
- **finished** when its ledger holds a terminal event — `run-completed` or `run-abandoned`, followed by
  nothing but `lease-claimed` — and its checkpoint shows the lease released. A finished run holds no
  lease and no coordinator, so it is not a blocker. A terminal event with the lease still held is stale.

The heartbeat is written at every supervision round and mutation. It is diagnostic only — report its age,
but never classify a run stale from an old heartbeat while its coordinator pane is live.

The ledger sits at `RUN_ROOT/mail/ledger.jsonl` while the run is open and after an abandonment, and
at `RUN_ROOT/ledger.jsonl` after completion. Finished runs accumulate in a plans root; a plan re-run
after its phases were reopened gets a new run root beside them.

A run root is minted only for work that will actually run: an empty one would look like a stale run to
the next invocation and block it until someone typed an abandonment sentence.

For a live or stale candidate, stop and tell the user, in their language: that another session is already
implementing and this invocation will not proceed; the `runId`, plan path, branch and phase progress of
that run; its lease owner, how old the heartbeat is, and the agents and panes actually observed; whether
it is live or stale; and what the user can do about it.

- **Live**: nothing from this session. Coordinator transfer is not supported, so a live run is continued
  only in the session that owns it. Tell the user to return to that pane, or to stop that coordinator
  first if they want this session instead — which makes the run stale.
- **Stale**: its coordinator is gone. The user authorizes one of two sentences: `resume run <runId>` to
  continue that run here from its checkpoint, adopting and supervising any worker still running instead
  of duplicating it, or `abandon run <runId>` to end it and start fresh. Resuming is crash recovery of a
  dead run, not a handoff from a live coordinator. Tell the user that either sentence requires the old
  coordinator to be stopped, not merely unseen: the lease cannot stop a writer that is still running.

On either sentence, before the first mutation: re-check the recorded coordinator pane, and scan
`herdr agent list` for a coordinator still working this run from another pane (a moved pane gets a new
ID); read the hot section and the ledger, then read them again after one supervision round, and treat
any new write as a live coordinator: stop and report. That observation is a safety check, not a timeout
that grants the claim; the sentence remains the authority. Then claim the lease (**Lease and ledger**)
and reconcile every in-flight mutation the hot section lists before issuing a new one.

- **Already terminal**: when the ledger holds a terminal event, either sentence is finalize-only on
  every repeat: never dispatch, answer, move or delete again, and never append a second terminal event.
  After `run-completed`, re-run the check and release the lease; after `run-abandoned`, finish only the
  missing abandon steps (each idempotent) and release it. Two terminal events, or any event but
  `lease-claimed` after one, is an inconsistent ledger: report it and mutate nothing.
- **Resume** otherwise continues under **Recover the same run**.
- **Abandon** otherwise appends `run-abandoned`, then sets every guard config of that run to
  `cancelled`, records the surviving worker panes with their state, marks the checkpoint abandoned and
  releases the lease, keeping `mail/` as evidence and the panes open. The fresh run holds any phase
  whose write scope a surviving old worker could still touch until that worker reads `idle` or `done`
  with no descendant process, or the user closes it.

For a finished candidate, say so in one line and continue into the normal gates, leaving its run root
untouched: a completed run as `runId`, plan, `completed`, lease released; an abandoned run as `runId`,
plan, `abandoned`, lease released, never as accepted phases. Neither is evidence that the requested work
is done: read the plan's phase status in this session before claiming anything.

Do not dispatch, answer a mailbox question, commit, or create a run root until the user gives one of
those instructions in this session. Never adopt a foreign run by rewriting its lease, never start a
second run beside it, and migrate a legacy single-file checkpoint only as part of an authorized resume
or abandon. This paragraph governs live and stale candidates only.

## Before every mutation

Read the hot section, then confirm three things before dispatching, answering, accepting or committing:
the lease is still yours, the phase you are about to touch is not already accepted, and the next action
matches what you are about to do. That is the whole normal-path cost, and it is what makes the run
recoverable on a runtime with no context hook.

Escalate to a **full** checkpoint read plus a live reconcile (`herdr agent list`, `herdr pane list`) when
any of these holds:

- one of the three confirmations fails, or the hot section is missing or unreadable;
- the session was compacted, hit a provider fallback, or restarted;
- a wait ran past one round, or an answer arrived late;
- the next mutation is hard to undo: an acceptance commit, a pane close, or a worktree removal.

Never read the same file twice in one turn, and never re-read detail already in context from that turn.
Record the **intended** mutation in the checkpoint before issuing it, and its returned ID or receipt
afterwards. If interrupted between the two, query live state before repeating the operation.

## Lease and ledger

Herdr has no fencing primitive, so the lease is a cooperative file convention: it stops a coordinator
that re-reads it, and nothing stops one that does not. Owner and epoch never fence a stale writer.

- Creating a run claims `owner = <pane>@<UTC>` and `epoch = 1`: `<pane>` is the coordinator's own pane
  ID (`$HERDR_PANE_ID`, or `herdr pane current`), `<UTC>` the claim time, e.g. `w1:p3@20260925T101500Z`.
  A coordinator is the owner when the pane part matches its own pane ID; the time only tells two claims
  from the same pane apart.
- Every mutation re-reads the lease and proceeds only when `owner` and `epoch` are its own.
- The same coordinator keeps its lease across compaction, fallback and a restart in the same pane: its
  `owner` does not change, so no epoch bump is needed.
- Only `resume run <runId>` or `abandon run <runId>` on a **stale** run changes the owner, after the
  quiescence check above: write the new `owner`, `coordinator` pane and `epoch + 1`, and record it in the
  checkpoint and the ledger. Never claim the lease of a live run.
- A coordinator that observes a different owner stops all mutations immediately, does not try to win
  the lease back, and never rebinds itself.
- If two coordinators appear live at the same epoch, stop, report the ambiguity, and mutate nothing until
  the user resolves it.

`ledger.jsonl` is append-only with a fixed vocabulary, so a later reader can read a run mechanically. One
JSON object per line, each carrying `ts`, `type`, `runId` and `epoch`:

Read `date -u +%Y-%m-%dT%H:%M:%SZ` **immediately before each write** and stamp that value: `ts` is the
wall clock at write time, and a ledger whose `ts` is not non-decreasing is a defect. Never reuse an
earlier value, and never take one from the heartbeat schedule: either mis-orders the run by `ts`.

| `type` | When | Additional required fields |
|---|---|---|
| `run-created` | the coordinator mints the run and claims epoch 1 | `owner`, `plan`, `flags` |
| `lease-claimed` | a stale run is resumed or abandoned on the user's sentence | `owner`, `previousOwner`, incremented `epoch` |
| `answer` | an answer is written to the mailbox | `question_id`, `file`, `decision`, `decided_by` |
| `phase-accepted` | a phase passes acceptance and is checkpointed | `phase`, `attempt`, `agent`, `pane`, `commit` or `evidence` |
| `run-completed` | every phase is accepted and the completion check passed | `owner` |
| `run-abandoned` | `abandon run <runId>` on a stale run, after `lease-claimed` | `owner`, `reason` |

Never invent a new event type. An unknown `type` in an existing ledger is reported and reconciled before
any mutation, not ignored.

## After compaction

Auto-compaction does not preserve this Skill's operating state, so the run must survive without the
coordinator's memory of it. Nothing here relies on recall.

- The context guard re-injects a short pointer on the next request after compaction — and after a
  provider fallback or a session restart — carrying the role anchor, `Run` and the absolute paths to
  read. It is ephemeral and re-derived each request, so a compaction summary cannot silently drop it,
  and it repeats until one successful turn clears the pending state.
- Treat that pointer as an order, not as information: reload this Skill and this guide, read the
  checkpoint, reconcile live Herdr state, and continue only unfinished work. Never rebuild the plan from
  recalled context, and never re-dispatch a phase whose acceptance is recorded.
- The pointer depends on the guard being loaded and configured. If `herdr_cook_context_status` is not
  available — a "tool not available" answer included — there is **no automatic recovery**, a monitoring
  status, not a failure. Report "unmonitored, Tier 1 only" once and keep coordinating in this session:
  checkpoint at every phase boundary, and after context loss run the boundary check and **Recover the
  same run**. Never ask the user to restart the coordinator, never launch or request a new coordinator,
  and never replace this session to install the guard. Only when the checkpoint cannot give the `runId`,
  lease and next action, stop mutating and report that blocker with the checkpoint path.
- A pre-compaction warning additionally needs resolvable thresholds. When telemetry resolves to `unknown`
  the warning is absent, but the post-compaction pointer still works; report that as unknown rather than
  as low usage.

## Recover the same run

If no run exists at all, reconstruct a checkpoint from live Herdr state, the plan files and Git history;
absence of notes is not a fresh start, and an ambiguous match is resolved before dispatch.

After compaction, a late answer, an interruption or an observed model fallback:

1. Restore the coordinator role, flags, authorization and the execution guide, then re-read the lease. A
   provider fallback does not select a new harness, reset the plan, grant new authority or turn the
   coordinator into a worker.
2. Reconcile the checkpoint against live state, pending questions, worker reports, plan status and Git
   evidence. Live state owns lifecycle; scoped checks, diff and commit-or-file evidence own acceptance.
   Neither stale notes nor a missing transcript justifies replaying work. Close any pane this run created
   whose worker settled and whose phase is already accepted — a dead coordinator strands exactly those —
   while leaving an active worker's pane alone.
3. Verified and committed phases: continue to the next eligible phase. A settled result awaiting
   acceptance or commit: finish only that checkpoint. A completed-but-defective result: run a bounded
   linked repair, never a full replay merely because the model changed. Active workers: consume their
   reports and supervise the existing dispatch; do not launch a duplicate.
4. Match a late answer to its saved question id, checking whether it was already answered or superseded.
   If the worker ended, inspect its report and continue only the unfinished scope in a fresh worker.
5. Persist the reconciled state and return to the supervision loop in the same turn. If state or
   authority cannot be recovered, report the blocker with the checkpoint path; do not guess, start a
   replacement run, or implement locally.

Before every dispatch, check for an existing active or completed attempt for that phase and its
acceptance evidence.

## Coordinator recovery boundary

Coordinator transfer is not supported. A context warning calls for a checkpoint at a safe tool boundary,
then normal runtime compaction and **Recover the same run** in this session. Do not launch a new
coordinator, hand the run to another session, or close the coordinator pane as a context-management
action, and do not ask the user to. A missing guard, a compaction, a provider fallback or a failed tool
call is not a reason to hand off. If state or authority cannot be recovered from the checkpoint and live
state, report the blocker with the checkpoint path and stop mutating; the user decides from there.

## Run completion

Close in this order, holding the lease to the end: every step before the last is an owner-only mutation.

1. Close every run-created worker pane under **Close what you created** — per phase, not at the end.
2. Finalize the checkpoint: every phase accepted with its commit SHA or file-scope evidence, every guard
   config path, and any `resume run` history.
3. Write `PLAN_DIR/reports/herdr-cook-runs/RUN_ID/implementation-summary.md` as described in
   `SKILL.md`, before any run-local artifact is disposed of.
4. Set `lifecycle: "completed"` (or `"cancelled"`) in every guard config **before** deleting it.
5. Leave the run auditable and nothing else: move `mail/ledger.jsonl` to `RUN_ROOT/ledger.jsonl` first
   (a live guard reads that move as disposal), then delete the rest of `mail/` and all of `guard/`. `checkpoint.md`, `implementation-summary.md` and the
   ledger stay; the ledger is how a later invocation recognizes the run as finished, so never delete it.
6. Verify it, rather than assuming it: `scripts/check-run-closed.py RUN_ROOT`, plus every guard config
   path outside the run root. It passes only when the run root holds exactly `.gitignore`,
   `checkpoint.md`, `implementation-summary.md` and `ledger.jsonl`; every ledger line parses with a known
   type and non-decreasing `ts`; every `phase-accepted` entry carries a commit SHA or a non-empty
   evidence pointer — a line without one is an incomplete record, not a crashed run; and no guard config
   still reads `lifecycle: active`. A run that reports completion with `mail/` intact, or with an active
   guard config, is not finished — the lingering config keeps re-anchoring a session that has ended.
7. Append `run-completed` to `RUN_ROOT/ledger.jsonl`.
8. Release the lease in the checkpoint as the final write.

A premature `run-completed` cannot be told apart afterwards from a crash mid-closing: a later invocation
classifies the run finished and stops, so every skipped step stays skipped. Record it only after the
check passes. A crash between steps 7 and 8 leaves the run stale, not finished, and its resume is
finalize-only.

A final checkpoint that still describes a phase as pending is a defect to fix at completion, not a
valid record of the run.

## Implementation summary template

Sections, in order:

1. **Outcome**: what the plan delivered and whether every acceptance criterion was met.
2. **Run placement** once: the main tree and branch, and every worktree used — path, branch, base and
   Herdr workspace id.
3. **Phases**: phase, status, agent and pane, where it landed (`current worktree`, or the worktree path
   and branch), commit SHA or the no-git evidence pointer, and the checks that ran. Pair a SHA with its
   worktree: alone it cannot locate work in a multi-worktree run.
4. **What changed**: the modules, files and behaviors that landed, grouped by phase, each with the
   verification that covered it.
5. **Decisions**: relayed approvals and `--auto` decisions, each with its reason.
6. **Integration**: for a multi-worktree run, what merged into which branch in what order, the combined
   validation that ran afterwards, and anything still unmerged.
7. **Not delivered or not verified**: items left out, checks skipped, blockers recorded, and every claim
   that stayed `unknown`.
8. **Follow-ups**: non-blocking suggestions, optional work, leftover worktrees or branches to remove, and
   the smallest next action.

Summarize the delivery; do not restate the whole plan.
