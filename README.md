# Herdr Cook Plan

English | [Tiếng Việt](README.vi.md)

![Herdr Cook Plan: one coordinator, a fresh worker pane per phase, ledger and context guard](assets/cover.webp)

A coding-agent skill that runs an existing [AgentKit](https://agentkit.best/?ref=OMG49S8R) plan phase by
phase through [Herdr](https://herdr.dev). Every phase gets a fresh worker agent in its own Herdr pane,
sequentially or in parallel depending on dependencies. The coordinator supervises from its own pane: it
answers worker questions through a file mailbox, verifies each phase's evidence, commits it, and closes
the panes it opened. It never implements a phase itself, so it survives compaction, quota exhaustion
and provider fallback without carrying the work.

Skill version 1.0.0. MIT licensed.

> **Public edition, provided as-is.** Full support is available in the edition for members of
> [Thieu Nguyen's Facebook Subscribers group](https://www.facebook.com/groups/1312173340952529).
> Issues and pull requests here are welcome but may not get a response.

## Requirements

- Herdr 0.9.1 or newer, and the skill invoked **inside a Herdr pane** (`HERDR_ENV=1`).
- A Herdr integration at `current` for every worker runtime you use:
  `herdr integration status`, then `herdr integration install <kind>` for any that is missing.
- AgentKit's Engineer Kit in each worker runtime, because workers run `ak:cook`:
  `ak kit init engineer --target <runtime> --yes`
- Python 3 (for the completion check). Git is strongly recommended: each accepted phase becomes one commit.

## Install

Clone the repository and copy the `herdr-cook-plan/` directory into your runtime's skills directory:

```bash
git clone --depth 1 https://github.com/thieung/herdr-cook-plan.git
cp -R herdr-cook-plan/herdr-cook-plan ~/.claude/skills/
```

| Runtime | Project | Global |
|---|---|---|
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| Codex | `.agents/skills/` | `~/.agents/skills/` |
| OMP | `.omp/skills/` | `~/.omp/agent/skills/` |
| Pi | `.pi/skills/` | `~/.pi/agent/skills/` |
| Cursor | `.cursor/skills/` | `~/.cursor/skills/` |
| Grok CLI | `.grok/skills/` | `~/.grok/skills/` |

To update, pull and copy again.

### OMP: start the coordinator with the context guard

The guard is an OMP extension. Nothing registers it automatically, so launch the coordinator with it:

```bash
omp --extension ~/.omp/agent/skills/herdr-cook-plan/extensions/context-guard.mjs
```

The coordinator adds the same flag to every OMP worker it starts. Do not use `--no-extensions`: it also
disables Herdr's own OMP integration, and worker `blocked`/`idle` states stop being trustworthy.

## Use

Open a Herdr pane in your project and invoke the skill with a plan path (or a directory containing
`plan.md`):

```text
/herdr-cook-plan plan.md --auto                          # Claude Code, Cursor, Grok CLI
/skill:herdr-cook-plan plan.md --auto                    # OMP, Pi
$herdr-cook-plan plan.md --select-agent --auto --advice  # Codex
/skill:herdr-cook-plan plan.md --runtime omp --omp-advisor all --auto
```

| Flag | Effect |
|---|---|
| `--runtime <kind>` | Worker runtime by Herdr agent kind. Defaults to the coordinator's own kind. |
| `--model <id>` | Primary worker model, passed through the kind's native argv. |
| `--select-agent` | Ask once for worker runtime and model before the first dispatch. |
| `--auto` | The coordinator decides in-plan approvals and records why. Never covers scope expansion, publishing, deployment or destructive operations. |
| `--omp-advisor all\|none\|<phase,...>` | OMP's own advisor per worker. OMP workers only. |
| `--parallel` | Prefer parallel scheduling; dependencies and write conflicts still force serial runs. |
| `--advice`, `--tdd`, other cook flags | Forwarded to `ak:cook` after checking the installed cook. |

## How a run works

1. **Gates.** Inside Herdr, Herdr 0.9.1+, integrations current. Any failure stops the run.
2. **Wave table.** Phases, dependencies, write scope, runtime and checks; independent phases run in
   parallel, two workers by default.
3. **Dispatch.** A new pane and a new agent per attempt (`p02a01`, then `p02a02`), prompted from a
   checked brief file.
4. **Supervise.** Each round scans the mailbox and waits on every worker. An idle terminal is not
   success; a timeout is not approval.
5. **Accept and commit.** A phase is accepted on a `status: complete` report, a settled agent, a scoped
   diff and passing checks. Intent goes to the checkpoint first, then a commit of only that phase's
   paths, then a `phase-accepted` ledger line with the SHA. The worker pane closes before the next dispatch.
6. **Close the run.** `implementation-summary.md`, `scripts/check-run-closed.py`, `run-completed`, and
   the lease released last.

Run state lives in files under `<plan-dir>/reports/herdr-cook-runs/<runId>/`, so a compacted or
restarted coordinator recovers in the same session. The full loop, state files and failure map are in
[docs/workflow.md](docs/workflow.md).

## Status and known limits

- Automatic recovery prompts (the context guard) exist only on OMP. Other runtimes recover from the
  checkpoint files.
- The guard is unit-tested; its behaviour under a real compaction has not been observed on 1.0.0.
- Live smoke so far is bounded: two phases accepted and committed, completion check passing.
- A worker may still write `plans/journals/*` outside its phase's write scope.
- Cook availability for Claude Code and Codex must be discovered on your machine before the first dispatch.

Run the tests:

```bash
python3 herdr-cook-plan/tests/instruction-contract.test.py
node --test herdr-cook-plan/tests/context-guard.test.mjs
```

## Using Orca instead of Herdr?

[Orca Cook Plan](https://cookplan.slopengineer.dev) is the Orca edition of this workflow, available to
members of Thieu Nguyen's Facebook Subscribers group. It installs and updates with one command and
registers its OMP context guard for you. [Join the group](https://www.facebook.com/groups/1312173340952529)
to get access.

## License

MIT. See [LICENSE](LICENSE). Herdr and AgentKit are separate projects with their own terms.
