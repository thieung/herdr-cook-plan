# Runtimes, placement and recovery coverage

Load before dispatch for placement and worker selection, and before claiming any recovery capability.

## Placement: current tree or a separate worktree

Decide placement per phase **while building the wave table, before any dispatch**, and record the
decision with its reason.

| Situation | Placement |
|---|---|
| Default: sequential phases, and independent parallel phases with disjoint write scopes | The current or selected worktree, one pane per worker |
| Two ready phases write the same paths, or depend on each other's uncommitted state | One worktree, and **serialize** them; parallel speed does not justify a merge |
| The user explicitly asked for isolation or parallel worktrees | One worktree per worker, unique branch, known base |
| A phase needs a divergent checkout (different base or branch, preserved dirty state, or a generated artifact that cannot coexist in one tree) | A separate worktree for that phase |
| A phase must run while another rewrites the same files, and the user wants both at once | Separate worktrees, then integrate before downstream phases |

Serialization is the default answer to a write conflict; a worktree is the exception, because it moves
the cost from "wait" to "integrate". Include shared plan status, lockfiles, generated output, the Git
index and test resources in the conflict check.

When a phase runs outside the current tree:

- Pass absolute paths for the plan directory, phase file, checkpoint and mailbox: the plan directory may
  not exist in the other checkout. The coordinator's own cwd stays in the main tree so `ak plan` status
  reconciliation and the checkpoint refer to one tree; a phase's commit runs with `git -C <that worktree>`.
- Per-phase commits land on that worktree's branch. Name the integration owner and the merge order while
  planning the wave, and run the combined validation on the integrated tree before any dependent phase
  starts.
- Record per phase in the checkpoint: worktree path, Herdr workspace id, branch and base.
- Removing a worktree is part of run cleanup — `herdr worktree remove --workspace <id>` after its phases
  are accepted and integrated, and never with `--force` unless the user authorizes it. `--trust-repository`
  is available on all worktree commands for repositories Git refuses to trust.

## Worker runtimes

Select by **Herdr agent kind**, never by model provider. Rows marked *verified* were checked on the
maintainer's host on 2026-09-19; on any other host every row must be discovered before it is dispatched.
Herdr validates the kind list itself (`herdr agent` help) and reports integration versions per kind.

| Kind | Cook availability (maintainer's host) | Cook trigger | State authority |
|---|---|---|---|
| `omp` | **verified** — `OMP_HOME/skills/ak-cook/SKILL.md` | `SLASHskill:ak-cook` | lifecycle hooks; **requires the integration** — without it Herdr has no fallback for OMP and `blocked`/`idle` are not trustworthy |
| `pi` | **verified** — `$HOME/.pi/agent/skills/ak-cook/SKILL.md` | `SLASHskill:ak-cook` | lifecycle hooks when the integration is installed, otherwise screen manifest |
| `cursor` | **verified present** — trigger form not verified here | inspect the installed catalog | screen manifest |
| `claude` | **not found** under `$HOME/.claude/skills` on 2026-09-19 | verify the installed catalog; a Kit may declare `SLASHak:cook` | screen manifest |
| `codex` | **not found** under `$HOME/.codex/skills` on 2026-09-19; also check `.agents/skills` and `$HOME/.agents/skills` | verify the installed catalog; a Kit may declare `$ak:cook` | screen manifest |
| `opencode`, `kilo` | requires discovery | requires discovery | lifecycle plugin when installed, otherwise screen manifest |
| `grok`, `qwen`, `mastracode`, `agy`, `letta` and the rest | requires discovery | requires discovery | consult `herdr agent explain` and `herdr integration status` |

A kind in `herdr agent` help proves only that Herdr can launch and detect it. It never proves that
AgentKit's cook is installed, that the trigger works, or that the model argv is valid.

Preflight before the first dispatch, for every kind the run will use: confirm the kind is in the live
list; confirm integration state with `herdr integration status` and install a missing one (mandatory for
`omp`); resolve the worker's cook entrypoint by reading that runtime's skill directory or the project's
installed Kit; read the kind's own `--help` for the launch argv and model flag; and confirm the worker can
read the plan and skill paths from its launch cwd. Record per kind: kind, cook path or trigger, launch
argv source, and ready or a concrete blocker. Do not change model or permission settings to make preflight
pass.

Pass a model through the kind's native argv after the `--` separator, for example
`herdr agent start p02a01 --kind omp --pane "$PANE" -- --model <id>`. Never invent a flag; read the resolved
executable's help first, and confirm the observed session model before claiming it was used. A fallback
inside the worker runtime keeps the same kind.

## Recovery across runtimes

Checkpoint discipline is runtime-agnostic; automatic context recovery is not.

**Tier 1 — always available, any runtime.** The checkpoint, the per-attempt `brief-<phase>-a<aa>.txt`, the
`mail/` state and the lease are files on disk. The boundary check in `recovery.md` recovers the run
without any runtime hook, in the same session; a crashed run resumes from the same files only on
`resume run <runId>`. This tier is what every runtime relies on; it is not a fallback.

**Tier 2 — runtime-specific pointer.** A runtime may additionally re-inject a short recovery pointer
after compaction or a provider fallback, telling the live session to re-read those files.

| Runtime | Tier-2 mechanism | Status |
|---|---|---|
| `omp` | `extensions/context-guard.mjs` — `session_compact` / `retry_fallback_applied` set a pending flag that a `context` hook turns into an ephemeral pointer naming the checkpoint, Skill and recovery paths | Implemented and unit-tested. Must be launched with `--extension`; without it the coordinator is unmonitored. Live behaviour under a real compaction is not yet observed |
| `claude` | A hooks surface exists — the Herdr integration installs `$HOME/.claude/hooks/herdr-agent-state.sh` | No recovery hook implemented. **Requires discovery** |
| `codex` | A hooks surface exists — `$HOME/.codex/hooks.json` plus `$HOME/.codex/herdr-agent-state.sh` | No recovery hook implemented. **Requires discovery** |
| `pi` | An extension surface exists — `$HOME/.pi/agent/extensions/herdr-agent-state.ts` | No recovery extension implemented. **Requires discovery** |
| `opencode`, `kilo` | A plugin surface exists — `$HOME/.opencode/plugins/herdr-agent-state.js` | No recovery plugin implemented. **Requires discovery** |
| everything else | only the file the Herdr integration installs has been observed | **Requires discovery** |

Rules for a runtime without a verified Tier-2 mechanism: never claim automatic recovery; state which tier
is in effect when reporting monitoring status; keep Tier 1 strict and keep coordinating in the same
session. Only if the coordinator cannot state the `runId`, lease epoch and next action even after reading
the checkpoint, stop dispatching, answering and committing and report that blocker with the checkpoint
path; never ask for a restarted or replacement coordinator. Adding Tier 2 for another runtime is a
discovery task with its own verification: install it, trigger a real compaction, and confirm the pointer
arrives before claiming support.

## Honest status reporting

Report `adapter installed`, `preflight ready` and `smoke passed` as separate states. Never present a
discovered executable, a documented trigger or a green preflight as a completed smoke test. A model
listed in a catalog is not proof of authentication, entitlement or remaining quota. A kind without a
verifiable cook entrypoint is blocked for this run rather than downgraded to generic implementation
instructions.
