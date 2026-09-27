# Supervising a running worker

Load for every supervision round, and when a worker has to be interrupted or replaced.

## Round loop

Use rolling rounds of at most 60 seconds in total, with a capped round count. Each round:

1. Scan the mailbox and process every `q-*` without a ledger entry.
2. For each worker that is `working`: `herdr agent wait <name> --timeout T`. Without `--until` it matches
   `idle`, `done` or `blocked` in one call, so a worker that blocks mid-wait is seen at once instead of
   after a second wait's timeout. Read `agent get <name>` for the state it matched and branch on it.
3. Handle the result before the next round: `blocked` under **A blocked worker**, `idle`/`done` under the
   acceptance rules in `SKILL.md`.

Wait only on `working` workers that owe you something. A worker already `blocked` or settled is handled,
not waited on — a wait on it returns at once and turns the round into a busy loop. A worker whose phase
has an unanswered `q-*` reads `working` while it polls the mailbox, but it is waiting on you: skip it in
the wait and in stall sampling until its answer file is written. Herdr has no
wait-any, so with N working workers split the round: `T` is at most 60 s / N, and the mailbox scan runs
between workers, so one worker's question never waits behind another's full timeout.

Exit status 1 is data, not noise. Parse the JSON error and branch:

- `timeout` — read `agent get` and `agent read`, then continue. A timeout does not prove work stopped
  and does not justify a duplicate worker.
- `agent_not_running` / `agent_not_found` — the agent is gone. Follow the crash path and count the
  attempt against the phase's cap.
- anything else — abort the round, report the raw error, and do not loop.

`idle` and `done` both mean ready for input; `done` is the same state before the UI has seen it. Never
claim success from an idle terminal or a timed-out wait. Stop repeating an identical failure for the
same decision instead of looping: a phase that exhausts its attempt cap is a blocker, so report it, hold
its dependents, and do not silently start a third attempt.

## Closing a settled worker's pane

An interactive runtime does not exit when its turn ends: the worker settles at its own prompt and stays in
the pane's foreground. That is the normal settled state, so `pane process-info` naming the session is not
a reason to retain the pane — a coordinator that reads it that way strands every worker pane it creates,
and a crash then leaves them for a later resume. Close on the close conditions in `SKILL.md`, per phase:
`idle`/`done` plus a read report is finished work. Retain the pane only while the session is `working`,
its report is unread, its question unanswered, or a foreign process runs in it.

A **foreign** process is one not descended from that agent session — another client, or something the
user started. A process the worker started (dev server, watcher, `nohup` job) is the attempt's own and
does not block closing. Before closing, record every such descendant from `pane process-info --pane
<pane>`; after closing, confirm none survives and the phase scope has stopped changing. That view is
pane-local: a process that detached or was reparented is not in it, so also check each PID the worker
named in its notes, handoff or report (`ps -p <pid>`). A survivor is reported to the user, never killed
on a guess.

## A blocked worker

Read the pane. If it shows a recognized cook review or approval gate whose decision you already hold,
answer it with `pane read` -> `pane send-text <pane> "<answer>"` -> `pane send-keys <pane> enter`, and
record that the mailbox was bypassed. `agent send-keys` moves keys only and cannot carry free text, and
`agent prompt` refuses a blocked agent. If the pane shows a permission, trust or credential prompt, that
is not an in-plan decision: surface it to the user instead of answering.

## A worker that stops progressing

A worker can stall inside a turn: a hung command, a tool call waiting on the network, a sub-shell
holding the turn open. Herdr still reports `working`, so `working` is not progress; only a change is.

Before counting a round as stalled, read the visible frame (`pane read <pane> --source visible`). A
provider fallback, a quota or rate-limit wait, or a retry countdown inside the worker's runtime is the
same attempt making progress, not a stall and not grounds for replacement: keep supervising without
nudging or interrupting, record it in the checkpoint, and report it once it outlasts the round cap. A
phase with an unanswered `q-*` is never sampled: the delay is yours, not the worker's.

Sample once per rolling round and treat the worker as stalled when **three consecutive rounds** all
show: `agent get <name>` returning an unchanged `revision` and `state_change_seq`; no new lines from
`agent read <name> --source recent-unwrapped`; and no new or modified `q-*`, `report-*` or notes file
for that phase. Then escalate, cheapest first, one step per three rounds:

1. **Nudge (queued).** `herdr agent prompt <name> "<one line asking for its current step and blocker>"` —
   sent **without** `--wait`, because waiting would block on the very turn you suspect is stuck. Herdr
   queues input for a working agent and delivers it when the turn ends. This resolves a worker that is
   merely mid-long-task and costs nothing if it was fine.
2. **Interrupt and resume.** If nothing changes, end the stuck turn with
   `herdr agent send-keys <name> esc` (use `ctrl+c` when `esc` does not return the composer), re-read
   `agent get` and the pane, then prompt a resume that names the brief, the checkpoint and "resume only
   unfinished acceptance".
3. **Bounded repair.** If it still does not progress, treat the attempt as failed. A stalled worker
   cannot write its own report, so record the failure, the evidence and any handoff path in the
   checkpoint yourself, and replace it through steps 3 and 4 of **Worker continuation** below for the
   **same phase**; a partial report from the stalled attempt stays under its own attempt number. A pane that will not settle after the
   interrupt is not force-closed: report it as a blocker. One replacement per phase by default; a second
   stall is an explicit decision for the user.
4. **Blocker.** At the attempt cap, stop that phase, hold its dependents, and report the evidence: agent
   name, pane, rounds sampled, last output line, and what you tried.

Never interrupt a worker that is progressing: a long test run or a slow build is not a stall. Never send
the same nudge twice in a row — if the first nudge produced no change, go to the interrupt step.

## Worker continuation within a phase

A worker saves phase-local progress at a stable boundary and never reports completion because context is
high. If it cannot continue reliably, it writes a phase handoff, asks through the mailbox naming the
exact unfinished acceptance and any live background operation, and waits; it never launches its own
replacement.

The coordinator authorizes a bounded replacement only when necessary, and then:

1. Hold dependents; record the handoff path and replacement count in that phase's checkpoint entry.
2. Answer the worker to end the attempt: it writes its `report-<phase>-a<aa>.md` with
   `status: incomplete`, the reason, the handoff path and the unfinished acceptance, then settles. That
   ends the attempt; it is never phase acceptance, and the checkpoint records it as a failed attempt,
   not as phase progress.
3. Stop every writer of that attempt before another starts: close the old pane under **Closing a
   settled worker's pane** above, including the detached-PID check, and reconcile each of the attempt's
   operations the hot section still lists as in flight or `unknown`. No process or pending operation
   from that attempt may still write the phase scope when the next one starts; if one might, it is a
   blocker to report.
4. Write `brief-<phase>-a<aa>.txt` for the next attempt, linking the handoff, then launch a fresh pane
   and agent `<phase>a<aa>` (for example `p02a02`) for the **same phase** with the same cook invocation.
   It verifies the current diff and prior check evidence, continues only the unfinished work, and reruns
   only affected checks. The ended attempt's files keep their own number; nothing is renamed.

A provider or quota fallback inside the worker's runtime keeps the same attempt and never counts as a
replacement. Allow at most one context-driven replacement per phase; a second failure is an explicit
bounded decision with the user.

## Portable capture with ak:handoff

Resolve the installed handoff entrypoint (`ak:handoff` or `ak-handoff`), use its schema and redaction
rules, and its default `plans/handoffs/` directory unless the project specifies another approved path.
Use a fresh filename. Do not install or invent a missing skill: continue with ordinary checkpoint
recovery and report that portable capture is unavailable. Record role, authorization and scope; `runId`,
checkpoint path, active agents and panes, and the execution-guide pointer; accepted
phases with checks and SHAs or no-git evidence paths; and pending question ids. Capture only unfinished
work in the next actions, and never ask a replacement worker to recook a completed phase. A worker
handoff never transfers the run or the coordinator role.
