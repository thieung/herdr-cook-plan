# Dispatching a phase worker

Load before the first dispatch and whenever a worker is launched or replaced.

## Placeholders

In command examples, `SLASH` stands for the leading slash character (U+002F); a brief or a prompt you send
carries the real slash. Uppercase names are path placeholders, resolved to absolute paths before use:

| Placeholder | Meaning |
|---|---|
| `PLAN_DIR` | The plan's directory |
| `RUN_ID` | The run's `runId` |
| `RUN_ROOT` | `PLAN_DIR/reports/herdr-cook-runs/RUN_ID` |
| `MAILBOX` | `RUN_ROOT/mail` |
| `CHECKPOINT` | `RUN_ROOT/checkpoint.md` |
| `SKILL_DIR` | This Skill's installed directory |
| `OMP_HOME` | The OMP agent home |
| `PLANS_ROOT` | The directory that holds sibling plans |
| `PHASE_ABSOLUTE_PATH` | The phase file |

## One fresh session per phase

Always launch a new pane and a new agent for a phase or a repair. Never reuse a previous phase's pane,
and never resume its session.

1. `herdr pane split --current --direction right --cwd "$PWD" --no-focus` — split right for a wide
   caller pane, down for a narrow or tall one. Read `.result.pane.pane_id` from the response.
2. `herdr agent start <agent-name> --kind <kind> --pane <pane_id> [--timeout MS] -- <native argv>`, where
   `<agent-name>` is `<phase>a<aa>` (`p02a01`, then `p02a02` for a replacement; see
   [mailbox](mailbox.md) for the three identities).
   The pane target is mandatory: `agent start` does not create layout and has no `--cwd`. An
   `agent_not_ready` result still leaves the name usable — wait for `idle` before prompting.
3. Load the brief, and prompt only when the load succeeded and is not empty:
   - bash or zsh:
     `brief="$(cat -- "MAILBOX/brief-<phase>-a<aa>.txt")" && test -n "$brief" && herdr agent prompt <agent-name> "$brief" --wait --timeout <ms>`
   - fish:
     `set brief (string collect < "MAILBOX/brief-<phase>-a<aa>.txt"); and test -n "$brief"; and herdr agent prompt <agent-name> "$brief" --wait --timeout <ms>`

Pass the brief from its file, never inline. Inside double quotes the shell expands `$`, so Codex's
`$ak:cook` trigger arrives empty; command output and a quoted variable holding it are not re-expanded.
Quote the path too: in `"$(cat <path>)"` the outer quotes protect only the output, so a path with a
space makes `cat` fail and an empty prompt goes out with exit status 0. A failed or empty load sends
nothing: report it and fix the brief file. A tool that passes argv without a shell reads the file and,
only if that read succeeded, passes its contents as the one `<TEXT>` argument. Never `eval` the brief or
paste it into a shell command line. Write the file with the
file tool or a quoted heredoc (`<<'EOF'`), and confirm it still contains the literal trigger before
prompting.

Delivery is not proof of a running turn: confirm it under **Confirm the prompt was submitted** below.

## Confirm the prompt was submitted

This applies to every kind.

`agent prompt` writes text plus an encoded Enter and honors the pane's live bracketed-paste mode.
Treat its result as evidence, not as proof that a turn is running:

- It refuses an agent that is already `blocked` with `agent_blocked` **without writing any input**.
  Read the pane and handle the dialog as a question instead of retrying blindly.
- A prompt sent from a non-working state must produce an observed lifecycle change within five
  seconds, or Herdr returns `agent_prompt_stalled`. If a timeout or a stall is returned, read
  `agent get` and `agent read` **before** sending again: the receipt does not prove the input was
  not delivered, and re-sending duplicates the brief.
- `--wait` holds until the turn settles, so on a dispatch the caller timeout normally expires before a
  long worker turn ends. Exit status 1 with `{"error":{"code":"timeout"}}` and no stall code, followed
  by the started-turn evidence below, is a started turn rather than a failed dispatch. Keep the dispatch
  timeout short (60000 ms worked in a live run) so the round returns to supervision instead of
  blocking on the turn.
- After dispatch, confirm a turn actually started. Read composer state only from the rendered frame,
  `herdr pane read <pane> --source visible`: `recent` and `recent-unwrapped` are scrollback and can
  show the brief even when it never left the composer. A started turn shows the brief as a submitted
  turn with `agent get` reporting `working`, or a report or question file appears.
- If the visible frame still holds the prompt, send one key for what it shows, then re-read
  `--source visible` before deciding on another:
  - an OMP `Pasted N lines` menu: one `enter` accepts its selection; the next frame then shows a chip,
    plain text or a started turn, handled by the rules here;
  - an OMP collapsed paste chip (`#1 … +N lines`) or plain text in the composer: one submitting
    `pane send-keys <pane> enter`.
  Never send a key blind or a pre-planned sequence of keys. If a turn or a reply is already visible,
  send nothing.
- An OMP chip or menu has one more diagnostic rule in [omp](omp.md).
- Record in the attempt's checkpoint entry the `agent prompt` receipt (exit status and JSON),
  `herdr --version`, the runtime's own version and the launch argv, before drawing any conclusion.
- If the turn still did not start, report that record, the visible frame and the blocker. Do not
  launch a duplicate worker.

## Worker brief

Give the kind-correct cook invocation plus only the scope constraints missing from the phase file.
Prefer a phase path over pasting its contents, and include the resolved cook entrypoint path so
embedded command expansion is not a hidden prerequisite. Also include, all absolute: the checkpoint
path, the mailbox directory, the execution-guide pointer, the brief file path, and the phase key, attempt
and agent name; plus exactly one decision sentence. The brief is the worker's whole contract: a worker
outside OMP gets no guard text, so every worker rule lives in it.

- Auto: "The user delegates phase-local approval to the orchestrator. Its answer file is the
  authorized decision within this plan; ask through the mailbox and wait."
- Interactive: "The orchestrator relays the user's approval decisions through the mailbox; ask and
  wait at cook gates."

Write the brief to `brief-<phase>-a<aa>.txt` under the mailbox directory before prompting, and tell
the worker to re-read it. That file is the worker-side recovery path and needs no runtime hook.

Example payload, with the **Placeholders** above resolved before it is written:

```text
SLASHskill:ak-cook PHASE_ABSOLUTE_PATH --advice
Cook entrypoint: <resolved absolute SKILL.md>; read and execute it if this embedded trigger is not
expanded.
The user delegates phase-local approval to the orchestrator. Its answer file is the authorized
decision within this plan; ask through the mailbox and wait.
You are phase p02, attempt 01, Herdr agent p02a01. Mailbox: MAILBOX. Checkpoint: CHECKPOINT; it is
coordinator-owned, so read it and never write it.
Before the first edit, read the checkpoint and any continuation handoff linked under this phase, then
reconcile existing work. This applies to the first attempt and retries; resume only unfinished
acceptance.
Your brief is saved at MAILBOX/brief-p02-a01.txt. After compaction, a provider fallback or a restart,
re-read that brief, the checkpoint and your notes file, then continue only unfinished acceptance; do
not restart the phase from scratch.
Keep your notes file at MAILBOX/notes-p02-a01.md: completed work, remaining acceptance, checks run
with their results, open questions, background processes you started (command and PID) and the next
step. Update it at every meaningful edit or test boundary, not only when context runs high.
If context pressure means you cannot finish reliably: save your notes at a stable boundary, write a
handoff naming the unfinished acceptance and any background process you started, ask through the
mailbox whether to end this attempt, and wait. Never write a final report for unfinished work to make
room. Never launch your own replacement or any other Herdr pane or agent.
Before executing commands, read Project execution guide in the checkpoint and its linked project
docs. Use its applicable project entrypoints first and pass them to cook delegates. Do not guess
flags or substitute external tools for supported operations without a justified fallback.
Ask through the mailbox: list your existing q-p02-a01-*.json, write q-p02-a01-<nn>.json with the next
unused two-digit <nn> and exactly these fields: `"id": "p02-a01-<nn>"`, `"runId"`, `"phase": "p02"`,
`"attempt": "01"` (two digits, never `a01`), `"pane"`, `"agent"`, `"question"`, `"options"`,
`"blocking"` and `"ts"` from `date -u +%Y-%m-%dT%H:%M:%SZ` (never `created`), then poll for
a-p02-a01-<nn>.json in bounded waits and accept it only if its runId, phase and attempt are yours and its
question_id equals your question's id.
Do not open your runtime's own approval dialog when the question is an in-plan approval.
Execute only this phase. Leave other phases and shared plan status to the coordinator. Leave changes
uncommitted; the coordinator owns commits.
Report in MAILBOX/report-p02-a01.md, starting with the line `status: complete` or
`status: incomplete`, then changed files, checks and results, remaining blockers, advice outcome and
any verified tooling correction (command, cwd, what fixed it). Use `status: incomplete` with the reason, handoff path and
unfinished scope when the coordinator ends the attempt early. Write the report once, then settle and
stay idle; do not rewrite or duplicate it unless the coordinator asks.
Do not start another implementation phase or an orchestration team.
Keep optional improvements outside the plan out of this phase; report them as non-blocking
suggestions. If a change outside this phase's text seems necessary, name the acceptance criterion it
serves and the concrete failure it fixes when you ask; without both it is a suggestion.
```

Phase-local testing, review and advisor delegates required by cook are allowed; they do not schedule
other phases. Do not feed workers the coordinator transcript, and do not make phase files conform to a
new manifest schema.

For OMP workers, always launch with the run-local paste overlay `paste: { "largeMenuThreshold": 0 }`
passed through `--config`; without it a long brief opens OMP's paste menu and the injected Enter
accepts that menu instead of submitting the prompt. See [omp](omp.md).

## Shared plan status stays with the coordinator

Cook normally syncs the entire plan during finalize, which would race the coordinator. For parallel
phases, explicitly defer shared plan and index status writes and commits in every worker brief, and
reconcile status once after the wave through the installed `ak plan` interface — in the coordinator or
one bounded sync worker. Keep phase-local validation intact. If that separation is unsupported for a
runtime, run those phases sequentially. Workers never stage or commit.

The interface to use, so a fresh coordinator does not spend calls discovering it:

```bash
ak plan status PLAN_DIR --json      # one-line progress summary
ak plan parse  PLAN_DIR --json      # structured view: per-phase status
ak plan check  <phase-file>           # tick every checkbox in a completed phase file
```

`ak plan check` covers the phase file; the master table row for that phase is a plain edit in `plan.md`.
Always pass the plan directory explicitly: a coordinator whose cwd sits outside the plan tree will
otherwise read the wrong plan.

## Project execution guide

Before the first dispatch, discover the tooling this plan needs from the project's instructions,
relevant README or linked tool docs, package scripts and declared task runners, and follow those
pointers before searching externally. Keep one compact "Project execution guide" section in the
checkpoint; link an existing canonical guide instead of copying its manual. Do not inventory the whole
repository or run broad setup or test suites merely to build this guide.

For each needed operation, record the preferred project entrypoint, the exact known command, the cwd
relative to the worker checkout, required profile or environment variable names (never secret values),
scoped validation, and a source pointer. Distinguish documented, verified and blocked; only claim
execution verified when it actually ran, because a coordinator check on one host is not proof for a
worker environment. Reuse discoveries across phases and revisit only entries affected by tooling changes
or contradictory evidence.

Every phase, repair and continuation reads this guide before execution and prefers project wrappers,
scripts, CLIs and MCP tools for the operations they support. Pass the relevant entrypoints to cook's
own test, review and advisor delegates. Do not guess flags, install external alternatives, or rebuild an
existing project tool because another tool is familiar. This preference does not ban ordinary editing
and search tools, a user-requested tool, or grant new permissions.

When a project command fails, inspect its documented invocation, cwd and prerequisites and make a
targeted correction supported by evidence; do not cycle through guessed commands. Separate a tooling
failure from a real test or application failure. Report a missing capability or unresolved tooling
blocker with the command, cwd, a concise redacted error and the smallest proposed fallback. Record
verified corrections in the shared guide so later workers reuse them.
