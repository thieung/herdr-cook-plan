#!/usr/bin/env python3
"""Static contract checks for herdr-cook-plan.

The package is instructions, so its contract can only drift silently: a dropped
gate clause, a reintroduced 0.7-era command, or a mailbox rule lost in an edit.
These checks pin the behavioural clauses and the package structure, and they name
the owning file for each clause so a rule that moves without its test fails here.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8")
DISPATCH = (ROOT / "references/dispatch.md").read_text(encoding="utf-8")
SUPERVISION = (ROOT / "references/supervision.md").read_text(encoding="utf-8")
MAILBOX = (ROOT / "references/mailbox.md").read_text(encoding="utf-8")
RECOVERY = (ROOT / "references/recovery.md").read_text(encoding="utf-8")
RUNTIMES = (ROOT / "references/runtimes.md").read_text(encoding="utf-8")
OMP = (ROOT / "references/omp.md").read_text(encoding="utf-8")
SELECTION = (ROOT / "references/selection.md").read_text(encoding="utf-8")

FILES = {
    "SKILL.md": SKILL,
    "references/dispatch.md": DISPATCH,
    "references/supervision.md": SUPERVISION,
    "references/mailbox.md": MAILBOX,
    "references/recovery.md": RECOVERY,
    "references/runtimes.md": RUNTIMES,
    "references/omp.md": OMP,
    "references/selection.md": SELECTION,
}
REFERENCES = [name for name in FILES if name != "SKILL.md"]
FRONT = SKILL.split("---", 2)[1]
# The canonical source keeps its full frontmatter; a subscriber copy carries the flat one the
# publisher accepts (no nested YAML), so frontmatter-only clauses are pinned on the source alone.
CANONICAL_FRONTMATTER = re.search(r"(?m)^metadata:\s*$", FRONT) is not None
CHECK = ROOT / "scripts/check-run-closed.py"


def flat(text):
    """Collapse whitespace so assertions do not depend on line wrapping."""
    return re.sub(r"\s+", " ", text)


class Gates(unittest.TestCase):
    def test_requires_a_herdr_pane(self):
        self.assertIn('test "$HERDR_ENV" = 1', SKILL)
        self.assertIn("not running inside a Herdr-managed pane and stop", flat(SKILL))

    def test_operational_commands_are_shell_portable(self):
        # fish rejects ${VAR:-default} and $() assignment, and the gate must not fail for that reason.
        bash_only = ("${HERDR_ENV:-", "${", "$(herdr", "= $(herdr")
        for name, text in FILES.items():
            for token in bash_only:
                self.assertNotIn(token, text, f"{name} contains bash-only syntax {token}")

    def test_requires_the_0_9_surface(self):
        self.assertIn("must be 0.9.1 or newer", flat(SKILL))

    def test_requires_integration_coverage_before_dispatch(self):
        self.assertIn("herdr integration status", SKILL)
        self.assertIn("herdr integration install <kind>", SKILL)
        self.assertIn("no screen-manifest fallback", flat(SKILL))
        self.assertIn("herdr integration status", RUNTIMES)


class FlagSurface(unittest.TestCase):
    def test_wrapper_flag_is_runtime_and_maps_to_herdr_kind(self):
        self.assertIn("--runtime <kind>", SKILL)
        self.assertIn("SLASHherdr-cook-plan <plan.md> --runtime omp --omp-advisor all --auto", flat(SKILL))
        self.assertIn("maps to Herdr's own `--kind` argument on `agent start`", flat(SKILL))
        self.assertIn("`--kind` is not accepted as a wrapper flag here", flat(SKILL))

    def test_herdr_own_flag_is_untouched_in_command_examples(self):
        self.assertIn("herdr agent start <agent-name> --kind <kind> --pane <pane_id>", DISPATCH)
        self.assertIn("herdr agent start p02a01 --kind omp --pane", RUNTIMES)

    def test_runtime_and_cook_flag_handling(self):
        self.assertIn("An explicit `--runtime` skips the runtime question", flat(SKILL))
        self.assertIn("Wrapper options are `--runtime`, `--model`, `--select-agent`, `--omp-advisor` and", flat(SKILL))
        self.assertIn("do not forward it into workers", flat(SKILL))


class DispatchContract(unittest.TestCase):
    def test_launch_is_split_then_start_then_prompt(self):
        self.assertIn('herdr pane split --current --direction right --cwd "$PWD" --no-focus', DISPATCH)
        self.assertIn(
            "herdr agent start <agent-name> --kind <kind> --pane <pane_id> [--timeout MS] -- <native argv>",
            DISPATCH,
        )
        self.assertIn("The pane target is mandatory", flat(DISPATCH))
        self.assertIn("does not create layout and has no `--cwd`", flat(DISPATCH))

    def test_delivery_failure_modes_are_handled(self):
        for token in ("agent_blocked", "agent_prompt_stalled", "agent_not_ready"):
            self.assertIn(token, DISPATCH)
        self.assertIn("`agent_blocked` **without writing any input**", flat(DISPATCH))
        self.assertIn("the receipt does not prove the input was not delivered", flat(DISPATCH))

    def test_ak_plan_commands_are_named(self):
        self.assertIn("ak plan status PLAN_DIR --json", DISPATCH)
        self.assertIn("ak plan check  <phase-file>", DISPATCH)
        self.assertIn("so a fresh coordinator does not spend calls discovering it", flat(DISPATCH))
        self.assertIn("the exact commands are in", flat(SKILL))

    def test_brief_defers_plan_state_and_commits(self):
        self.assertIn("Leave changes uncommitted", flat(DISPATCH))
        self.assertIn("Leave other phases and shared plan status to the coordinator", flat(DISPATCH))
        self.assertIn("explicitly defer shared plan and index status writes and commits", flat(DISPATCH))
        self.assertIn("Workers never stage or commit", flat(DISPATCH))

    def test_brief_file_is_the_worker_recovery_artifact(self):
        self.assertIn("That file is the worker-side recovery path", flat(DISPATCH))
        self.assertIn("re-read that brief, the checkpoint and your notes file", flat(DISPATCH))

    def test_paste_overlay_is_mandatory_for_omp(self):
        self.assertIn("largeMenuThreshold", DISPATCH)
        self.assertIn('"paste": { "largeMenuThreshold": 0 }', OMP)
        self.assertIn("collapsed paste chip", flat(OMP))

    def test_project_execution_guide_is_owned_here(self):
        self.assertIn("Project execution guide", DISPATCH)
        self.assertIn("This preference does not ban ordinary editing and search tools", flat(DISPATCH))


class SupervisionContract(unittest.TestCase):
    def test_round_loop_waits_once_per_working_worker(self):
        # Herdr 0.9.1 `agent wait --help`: "Without --until, matches idle, done, or blocked." A settled
        # wait therefore needs no --until, and chaining a blocked wait before an idle wait doubles the
        # worst-case latency of every round.
        waits = [flat(line) for line in re.findall(r"herdr agent wait [^`\n]*", "\n".join(FILES.values()))]
        self.assertTrue(waits)
        for wait in waits:
            self.assertNotIn("--until", wait, wait)
        self.assertIn("Wait only on `working` workers", flat(SUPERVISION))
        self.assertIn("`T` is at most 60 s / N", flat(SUPERVISION))
        self.assertIn("Exit status 1 is data, not noise", flat(SUPERVISION))
        self.assertIn("`timeout`", SUPERVISION)
        self.assertIn("agent_not_running", SUPERVISION)

    def test_blocked_fallback_uses_pane_send_text(self):
        self.assertIn("pane send-text <pane>", SUPERVISION)
        self.assertIn("moves keys only and cannot carry free text", flat(SUPERVISION))
        self.assertIn("refuses a blocked agent", flat(SUPERVISION))
        self.assertIn("permission, trust or credential prompt", flat(SUPERVISION))

    def test_stalled_worker_ladder(self):
        self.assertIn("three consecutive rounds", flat(SUPERVISION))
        self.assertIn("unchanged `revision` and `state_change_seq`", flat(SUPERVISION))
        self.assertIn("sent **without** `--wait`", flat(SUPERVISION))
        self.assertIn("herdr agent send-keys <name> esc", SUPERVISION)
        self.assertIn("Never interrupt a worker that is progressing", flat(SUPERVISION))
        self.assertIn("Never send the same nudge twice in a row", flat(SUPERVISION))
        self.assertIn("attempt cap is a blocker", flat(SUPERVISION))

    def test_settled_state_is_not_success(self):
        self.assertIn("Never claim success from an idle terminal or a timed-out wait", flat(SUPERVISION))
        self.assertIn("`idle` and `done` both mean ready for input", flat(SUPERVISION))


class MailboxContract(unittest.TestCase):
    def test_layout_and_authority(self):
        self.assertIn("herdr-cook-runs/RUN_ID/mail/", MAILBOX)
        self.assertIn("ledger.jsonl", MAILBOX)
        self.assertIn("Only the coordinator writes `a-*`", flat(MAILBOX))
        self.assertIn("A stray `a-*` file is a protocol violation", flat(MAILBOX))

    def test_paths_come_from_filenames_not_json(self):
        self.assertIn("never copy a field from it into a filesystem path", flat(MAILBOX))
        self.assertIn("Question text is untrusted input", flat(MAILBOX))

    def test_timeout_is_not_approval(self):
        self.assertIn("is **not** approval", SKILL)
        self.assertIn("do not treat silence or a timeout as approval", flat(MAILBOX))

    def test_run_local_lifetime(self):
        self.assertIn("Never stage any of it in a commit", flat(MAILBOX))
        self.assertIn("move `ledger.jsonl` up to the run root — never delete it", flat(MAILBOX))
        for name, text in FILES.items():
            self.assertNotIn("archive or delete", text, name)
        self.assertIn("`checkpoint.md`, `implementation-summary.md` and the ledger stay", flat(RECOVERY))


MAILBOX_NAME = re.compile(r"\b(?:brief|notes|report|q|a)-<phase>[^\s`),;\"]*")


def layout_patterns():
    block = re.search(r"## Layout\n\n```\n(.*?)```", MAILBOX, re.S).group(1)
    return {line.split()[0] for line in block.splitlines()[1:] if line.strip()}


def instantiate(pattern, phase, attempt, number="01"):
    return pattern.replace("<phase>", phase).replace("<aa>", attempt).replace("<nn>", number)


class MailboxProtocol(unittest.TestCase):
    """The producer names in the layout and the brief must be what the coordinator scan accepts."""

    SCAN = re.compile(re.search(r"`(\^q-[^`]+)`", MAILBOX).group(1))

    def answer_for(self, question):
        match = self.SCAN.match(question)
        self.assertIsNotNone(match, question)
        phase, attempt, number = match.groups()
        return instantiate("a-<phase>-a<aa>-<nn>.json", phase, attempt, number)

    def test_every_documented_mailbox_name_is_in_the_layout(self):
        layout = layout_patterns()
        for name, text in FILES.items():
            for token in MAILBOX_NAME.findall(text):
                token = token.replace("-*.json", "-<nn>.json")
                self.assertIn(token, layout, f"{name} uses {token}, which the mailbox layout does not define")

    def test_scan_accepts_what_the_worker_writes(self):
        question = next(p for p in layout_patterns() if p.startswith("q-"))
        written = instantiate(question, "p02", "01", "03")
        # The brief tells the worker to write this name and this id.
        self.assertIn(f'"id": "{written[2:-5]}"', flat(DISPATCH).replace("<nn>", "03"))
        self.assertEqual(self.answer_for(written), "a-p02-a01-03.json")

    def test_a_replacement_can_never_read_an_earlier_attempt_answer(self):
        # Attempt 01 asked q ... 01 and got an answer; attempt 02 starts its own numbering at 01.
        first = self.answer_for("q-p02-a01-01.json")
        second = self.answer_for("q-p02-a02-01.json")
        self.assertNotEqual(first, second)
        reports = {instantiate("report-<phase>-a<aa>.md", "p02", a) for a in ("01", "02")}
        self.assertEqual(len(reports), 2, "an ended attempt's report must not share the current one's name")

    def test_scan_rejects_names_that_could_escape_or_collide(self):
        for name in ("q-../../x-a01-01.json", "q-p02-a01-01.json/../../x", "q-phase-02-api-01.json",
                     "q-p02-01.json", "q-P02-a01-01.json", "q-p02-a01-1.json", "q-p02-a01-01.json.bak"):
            self.assertIsNone(self.SCAN.match(name), name)

    def test_answer_identity_is_checked_beyond_the_file_name(self):
        record = re.search(r"- Answer: `\{([^}]*)\}`", MAILBOX).group(1)
        for field in ("runId", "question_id", "phase", "attempt"):
            self.assertIn(f'"{field}"', record)
        self.assertIn("its `question_id` equals the `id`", flat(MAILBOX))
        self.assertIn("whose attempt is not the phase's current attempt", flat(MAILBOX))

    def test_attempt_and_ts_representation(self):
        # A live worker wrote "attempt": "a01" and a local-time "created"; the ledger needs two digits.
        self.assertIn('`"attempt": "01"` (two digits, never `a01`)', flat(DISPATCH))
        self.assertIn('`"ts"` from `date -u +%Y-%m-%dT%H:%M:%SZ` (never `created`)', flat(DISPATCH))
        brief = flat(re.search(r"Ask through the mailbox:(.*?)question_id", DISPATCH, re.S).group(1))
        question = re.search(r"- Question: `\{([^}]*)\}`", flat(MAILBOX)).group(1)
        for field in re.findall(r'"(\w+)"', question):
            self.assertIn(f'"{field}"', brief, f"the brief does not name question field {field}")
        self.assertIn("carry the attempt as that same two-digit string", flat(MAILBOX))
        self.assertIn("the `a<aa>` spelling above is a deviation, not a mismatch", flat(MAILBOX))
        self.assertIn("mirrors the worker's own spelling", flat(MAILBOX))
        self.assertIn("`p02a01` included, is a mismatch", flat(MAILBOX))
        self.assertIn("time the question's bound from when the coordinator first sees the file", flat(MAILBOX))
        self.assertIn("current attempt as two digits", flat(RECOVERY))


class PromptShellSafety(unittest.TestCase):
    """Run the documented prompt commands in real shells with a stub herdr.

    Shells start without their rc files, with HOME and PATH confined to a temporary directory, so a
    login config can never put the real herdr back on PATH and send a prompt to a live session. The
    mailbox path contains a space, as a plan directory may.
    """

    NO_CONFIG = {"bash": ["--noprofile", "--norc"], "zsh": ["-f"], "fish": ["--no-config"]}
    # chr(47) keeps the real leading slash without a literal path in this file.
    BRIEF = chr(47) + "skill:ak-cook x\n$ak:cook keeps its dollar, `ticks`, \"quotes\" and $(not run)\n"
    LOAD = re.compile(r"(?:brief=|set brief )[^\n`]*herdr agent prompt <agent-name> [^\n`]*")

    def run_in(self, shell, command, brief=BRIEF, readable=True):
        """Return (exit status, prompt text the stub received or None when herdr was never called)."""
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp, "herdr")
            stub.write_text(f'#!{shutil.which("sh")}\nprintf %s "$4" > "$OUT"\n')
            stub.chmod(0o755)
            mailbox = Path(tmp, "mail box")
            mailbox.mkdir()
            if brief is not None:
                path = Path(mailbox, "brief-p02-a01.txt")
                path.write_text(brief)
                if not readable:
                    path.chmod(0)
            line = (command.replace("<agent-name>", "p02a01").replace("MAILBOX", str(mailbox))
                    .replace("<phase>-a<aa>", "p02-a01").replace("<ms>", "1000"))
            out = Path(tmp, "out")
            env = {"PATH": f"{tmp}:/usr/bin:/bin", "HOME": tmp, "OUT": str(out)}
            done = subprocess.run([shutil.which(shell), *self.NO_CONFIG[shell], "-c", line], env=env, cwd=tmp,
                                  capture_output=True, text=True)
            return done.returncode, out.read_text() if out.exists() else None

    def documented(self):
        commands = set(self.LOAD.findall(SKILL + DISPATCH + OMP.replace("p02a01", "<agent-name>")
                                         .replace("brief-p02-a01.txt", "brief-<phase>-a<aa>.txt")))
        self.assertTrue(any(c.startswith("set brief") for c in commands), "no fish form documented")
        self.assertTrue(any(c.startswith("brief=") for c in commands), "no bash/zsh form documented")
        for command in commands:
            shells = ("fish",) if command.startswith("set brief") else ("bash", "zsh")
            for shell in shells:
                if shutil.which(shell):
                    yield shell, command

    def test_documented_prompt_keeps_the_brief_literal(self):
        for shell, command in self.documented():
            self.assertEqual(self.run_in(shell, command), (0, self.BRIEF.rstrip("\n")), f"{shell}: {command}")

    def test_a_brief_that_cannot_be_read_sends_nothing(self):
        cases = {"missing": {"brief": None}, "empty": {"brief": ""}}
        if os.geteuid() != 0:
            cases["unreadable"] = {"readable": False}
        for shell, command in self.documented():
            for case, kwargs in cases.items():
                status, sent = self.run_in(shell, command, **kwargs)
                self.assertIsNone(sent, f"{shell} {case}: herdr was called")
                self.assertNotEqual(status, 0, f"{shell} {case}: the failure was not reported")

    def test_the_forms_these_replace_fail_silently(self):
        # Reproduced rather than asserted: an inline brief loses the trigger, and an unquoted path inside
        # "$(cat ...)" sends an empty prompt with exit status 0.
        self.assertEqual(self.run_in("bash", 'herdr agent prompt p02a01 "$ak:cook go" --wait --timeout 1000'),
                         (0, ":cook go"))
        old = 'herdr agent prompt <agent-name> "$(cat MAILBOX/brief-<phase>-a<aa>.txt)" --wait --timeout <ms>'
        self.assertEqual(self.run_in("bash", old), (0, ""))

    def test_every_waiting_prompt_uses_a_checked_load(self):
        for name, text in FILES.items():
            for line in text.splitlines():
                if "herdr agent prompt" in line and "--wait" in line:
                    self.assertIn('test -n "$brief"', line, f"{name}: {line}")
                    self.assertIn('"$brief" --wait', line, f"{name}: {line}")


class RecoveryContract(unittest.TestCase):
    def test_run_artifacts_are_namespaced_by_run_id(self):
        self.assertIn("reports/herdr-cook-runs/RUN_ID/", RECOVERY)
        self.assertIn("checkpoint.md", RECOVERY)
        self.assertIn("two runs of the same plan can never", flat(RECOVERY))

    def test_existing_run_causes_a_refusal(self):
        self.assertIn("Existing runs: refuse by default", RECOVERY)
        self.assertIn("**refuse to proceed**", RECOVERY)
        self.assertIn("whether it looks live or stale", flat(RECOVERY))
        self.assertIn("Sibling plans in the same plans root", flat(RECOVERY))
        self.assertIn("never a whole-filesystem scan", flat(RECOVERY))
        self.assertIn("resume run <runId>", RECOVERY)
        self.assertIn("abandon run <runId>", RECOVERY)
        self.assertIn("Resuming is crash recovery of a dead run, not a handoff from a live coordinator", flat(RECOVERY))
        self.assertIn("Never adopt a foreign run by rewriting its lease", flat(RECOVERY))
        self.assertNotIn("ask the user which one to continue", flat(RECOVERY))

    def test_an_abandoned_run_is_never_reported_as_accepted(self):
        finished = flat(re.search(r"For a finished candidate(.*?)\n\n", RECOVERY, re.S).group(0))
        self.assertNotIn("all phases accepted", finished)
        self.assertIn("never as accepted phases", finished)
        self.assertIn("Neither is evidence that the requested work is done", finished)

    def test_finished_run_does_not_block_the_next_invocation(self):
        self.assertIn("**finished** when its ledger holds a terminal event", flat(RECOVERY))
        self.assertIn("followed by nothing but `lease-claimed` — and its checkpoint shows the lease released", flat(RECOVERY))
        self.assertIn("it is not a blocker", flat(RECOVERY))
        self.assertIn("For a finished candidate, say so in one line", flat(RECOVERY))
        self.assertIn("This paragraph governs live and stale candidates only", flat(RECOVERY))
        self.assertIn("at `RUN_ROOT/ledger.jsonl` after completion", flat(RECOVERY))
        self.assertIn("a finished run is reported in one line and does not block", flat(SKILL))

    def test_discovery_cannot_fail_open_on_gitignored_run_roots(self):
        self.assertIn("so a glob that honours ignore files reports nothing even when a run exists", flat(RECOVERY))
        self.assertIn("never conclude an absence from one", flat(RECOVERY))
        self.assertIn("with ignore filtering off", flat(RECOVERY))
        self.assertIn("Run roots are gitignored (`*`), so list the directory instead of trusting a filtered glob", flat(SKILL))

    def test_no_work_remains_mints_nothing(self):
        self.assertIn("stop without minting a run root", flat(SKILL))
        self.assertIn("A run root is minted only for work that will actually run", flat(RECOVERY))
        self.assertIn("an empty one would look like a stale run to the next invocation", flat(RECOVERY))

    def test_worker_panes_close_per_phase(self):
        self.assertIn("Close that phase's worker pane under **Close what you created** before dispatching the next phase", flat(SKILL))
        self.assertIn("per phase, not at completion", flat(SKILL))
        self.assertIn("Close any pane this run created whose worker settled and whose phase is already accepted", flat(RECOVERY))

    def test_hot_section_and_boundary_check(self):
        self.assertIn("Hot section, first ~40 lines", flat(RECOVERY))
        self.assertIn("the hot section is read on every boundary and the detail is not", flat(RECOVERY))
        self.assertIn("confirm three things before dispatching", flat(RECOVERY))
        self.assertIn("Escalate to a **full** checkpoint read", flat(RECOVERY))
        self.assertIn("Never read the same file twice in one turn", flat(RECOVERY))
        self.assertIn("Record the **intended** mutation in the checkpoint before issuing it", flat(RECOVERY))

    def test_ledger_timestamps_are_wall_clock_at_write_time(self):
        self.assertIn("**immediately before each write** and stamp that value", flat(RECOVERY))
        self.assertIn("a ledger whose `ts` is not non-decreasing is a defect", flat(RECOVERY))
        self.assertIn("never take one from the heartbeat schedule", flat(RECOVERY))

    def test_lease_and_ledger_vocabulary(self):
        table = set(re.findall(r"(?m)^\| `([a-z-]+)` \|", RECOVERY)) - {"type"}
        checker = re.search(r"TYPES = \{([^}]*)\}", CHECK.read_text()).group(1)
        self.assertEqual(table, set(re.findall(r'"([a-z-]+)"', checker)), "check-run-closed.py and the ledger table differ")
        self.assertIn("run-abandoned", table)
        self.assertIn("previousOwner", RECOVERY)
        self.assertIn("Never invent a new event type", flat(RECOVERY))
        self.assertIn("A coordinator that observes a different owner stops all mutations", flat(RECOVERY))
        self.assertIn("If two coordinators appear live at the same epoch", flat(RECOVERY))

    def test_after_compaction(self):
        self.assertIn("re-injects a short pointer on the next request after compaction", flat(RECOVERY))
        self.assertIn("Treat that pointer as an order, not as information", flat(RECOVERY))
        self.assertIn("**no automatic recovery**", RECOVERY)
        self.assertIn("the post-compaction pointer still works", flat(RECOVERY))

    def test_run_completion_ordering(self):
        self.assertIn("A final checkpoint that still describes a phase as pending is a defect", flat(RECOVERY))
        self.assertIn("implementation-summary.md", RECOVERY)

    def test_completion_disposal_is_verified_not_assumed(self):
        self.assertIn("Disposal is not a prose rule", flat(SKILL))
        self.assertIn("a failure is unfinished work", flat(SKILL))
        self.assertIn("Set `lifecycle: \"completed\"` (or `\"cancelled\"`) in every guard", flat(RECOVERY))

    def test_closing_steps_are_ordered_before_run_completed(self):
        # The lease is the owner's right to mutate, so it is released after the last owner-only write,
        # and run-completed is appended only after the check has passed.
        section = re.search(r"## Run completion\n(.*?)\n## ", RECOVERY, re.S).group(1)
        steps = [flat(s) for s in re.findall(r"(?m)^\d+\. (.*(?:\n   .*)*)", section)]
        def index(fragment):
            found = [i for i, step in enumerate(steps) if fragment in step]
            self.assertEqual(len(found), 1, fragment)
            return found[0]
        close, summary, lifecycle, dispose = (index("Close every run-created worker pane"),
            index("Write `PLAN_DIR/reports"), index('Set `lifecycle: "completed"`'), index("move `mail/ledger.jsonl`"))
        check, completed, release = (index("check-run-closed.py"), index("Append `run-completed`"),
            index("Release the lease"))
        self.assertEqual([close, summary, lifecycle, dispose, check, completed, release],
                         sorted([close, summary, lifecycle, dispose, check, completed, release]))
        self.assertEqual(release, len(steps) - 1, "releasing the lease is the final write")
        order = flat(re.search(r"## Completion\n(.*?)\n## ", SKILL, re.S).group(1))
        self.assertLess(order.index("run the check"), order.index("append `run-completed`"))
        self.assertLess(order.index("append `run-completed`"), order.index("release the lease as the last write"))

    def test_a_settled_session_can_be_closed(self):
        self.assertIn("An interactive runtime waits at its own prompt, so that presence is **not** a reason", flat(SKILL))
        self.assertIn("a coordinator that reads it that way strands every worker pane it creates", flat(SUPERVISION))

    def test_replacement_is_bounded(self):
        self.assertIn("at most one context-driven replacement per phase", flat(SUPERVISION))
        self.assertIn("it never launches its own replacement", flat(SUPERVISION))


class AcceptanceContract(unittest.TestCase):
    def test_commit_policy(self):
        self.assertIn("never the whole worktree, never `git add -A`, never the mailbox", flat(SKILL))
        self.assertIn("verify the resulting SHA", flat(SKILL))
        self.assertIn("do not ask for commit approval again", flat(SKILL))

    def test_progress_is_reported_only_after_its_proof_lands(self):
        self.assertIn("Report a dispatch or an acceptance only once its proof has landed", flat(SKILL))
        self.assertIn("a progress claim the disk does not support yet is a defect", flat(SKILL))

    def test_intent_goes_to_the_checkpoint_and_the_sha_to_the_ledger(self):
        self.assertIn("Record the intended acceptance in the checkpoint hot section **before** committing", flat(SKILL))
        self.assertIn("The ledger entry must always carry the SHA or the evidence pointer", flat(SKILL))

    def test_no_git_fallback_is_explicit(self):
        self.assertIn("initialize nothing", flat(SKILL))
        self.assertIn("no-SHA limitation", flat(SKILL))

    def test_pane_close_conditions(self):
        self.assertIn("herdr pane close <pane_id>", SKILL)
        self.assertIn("re-read live with `pane get`/`agent get`", flat(SKILL))
        self.assertIn("no other client has taken it over", flat(SKILL))
        self.assertIn("never extend this authority to its tab, workspace, session, server or worktree", flat(SKILL))

    def test_completion_requires_disposal_and_release(self):
        self.assertIn("the lease released", flat(SKILL))
        self.assertIn("no unresolved question, repair or pane cleanup", flat(SKILL))


class SummaryAndLanguageContract(unittest.TestCase):
    def test_summary_is_required_and_durable(self):
        self.assertIn("`RUN_ROOT/implementation-summary.md`", SKILL)
        self.assertIn("| `RUN_ROOT` | `PLAN_DIR/reports/herdr-cook-runs/RUN_ID` |", DISPATCH)
        self.assertIn("outside `mail/`", flat(SKILL))
        # The template lives in recovery.md; the entrypoint keeps the durable path and the rules.
        for section in ("Outcome", "Run placement", "Phases", "What changed", "Decisions",
                        "Integration", "Not delivered or not verified", "Follow-ups"):
            self.assertIn(section, RECOVERY, section)
        self.assertIn("Its section template is in [recovery](references/recovery.md)", flat(SKILL))
        self.assertIn("Never summarize a phase without acceptance evidence", flat(SKILL))
        self.assertIn("Pair a SHA with its worktree: alone it cannot locate work in a multi-worktree run", flat(RECOVERY))

    def test_output_language_follows_the_user_request(self):
        self.assertIn("## Output language", SKILL)
        self.assertIn("language of the user's own request", flat(SKILL))
        self.assertIn("Never translate command names, flags, IDs, file paths", flat(SKILL))
        self.assertIn('"Tiếp theo"', SKILL)
        self.assertNotIn("Report in Vietnamese unless requested otherwise", SKILL)


class RuntimesAndOmpContract(unittest.TestCase):
    def test_placement_decision_is_owned_here(self):
        self.assertIn("The current or selected worktree, one pane per worker", flat(RUNTIMES))
        self.assertIn("parallel speed does not justify a merge", flat(RUNTIMES))
        self.assertIn("Serialization is the default answer to a write conflict", flat(RUNTIMES))
        self.assertIn("may not exist in the other checkout", flat(RUNTIMES))
        self.assertIn("The coordinator's own cwd stays in the main tree", flat(RUNTIMES))
        self.assertIn("worktree path, Herdr workspace id, branch and base", flat(RUNTIMES))
        self.assertIn("herdr worktree remove --workspace", RUNTIMES)
        self.assertIn("never with `--force` unless the user authorizes it", flat(RUNTIMES))

    def test_kind_rows_are_evidence_backed(self):
        self.assertIn("requires discovery", RUNTIMES)
        self.assertIn("**verified**", RUNTIMES)
        self.assertIn("adapter installed`, `preflight ready` and `smoke passed", flat(RUNTIMES))
        self.assertIn("Do not change model or permission settings to make preflight pass", flat(RUNTIMES))

    def test_two_tier_recovery_is_documented(self):
        self.assertIn("Tier 1 — always available, any runtime", RUNTIMES)
        self.assertIn("Tier 2 — runtime-specific pointer", RUNTIMES)
        self.assertIn("never claim automatic recovery", flat(RUNTIMES))
        self.assertIn("it is not a fallback", flat(RUNTIMES))

    def test_guard_launch_and_coexistence(self):
        self.assertIn("--herdr-cook-context", OMP)
        self.assertIn("herdr_cook_context_status", OMP)
        self.assertIn("does not own `OMP_HOME/extensions`", flat(OMP))
        self.assertIn("subscriber-orca-cook-plan.js", OMP)
        self.assertIn("reports `conflict`", flat(OMP))
        self.assertIn("Never pass `--no-extensions`", flat(OMP))

    def test_selection_does_not_invent_models(self):
        self.assertIn("Do not invent IDs", flat(SELECTION))
        self.assertIn("`--auto` does **not** answer these selection questions", flat(SELECTION))


class RestoredClauses(unittest.TestCase):
    """Clauses a previous restructure dropped.

    Each one was present before the skill was split across owner files and was lost
    while the prose was tightened, because the suite pinned rules but not this set.
    Pinning them here is what makes the next restructure safe.
    """

    def test_role_boundary(self):
        self.assertIn("Never switch to implementing a phase or to spawning an in-session coding team", flat(SKILL))

    def test_no_manifest_ceremony(self):
        self.assertIn("Do not introduce a separate manifest-approval ceremony", flat(SKILL))

    def test_execution_is_verified_only_when_it_ran(self):
        self.assertIn("only claim execution verified when it actually ran", flat(DISPATCH))

    def test_no_tool_substitution(self):
        self.assertIn("Do not guess flags, install external alternatives, or rebuild an existing project tool",
                      flat(DISPATCH))

    def test_terminology_consistency(self):
        self.assertIn("Keep terminology consistent within a run", flat(SKILL))

    def test_summary_does_not_restate_the_plan(self):
        self.assertIn("Summarize the delivery; do not restate the whole plan", flat(SKILL))
        self.assertIn("never upgrade a reported result into a verified one", flat(SKILL))

    def test_mailbox_path_rule(self):
        self.assertIn("Never build a path from a JSON field", flat(MAILBOX))

    def test_hot_section_is_the_normal_path(self):
        self.assertIn("This is the only part read on the normal path", flat(RECOVERY))



class CoordinatorTransferContract(unittest.TestCase):
    def test_no_coordinator_transfer(self):
        for name, text in FILES.items():
            for token in ("take over run", "Coordinator replacement", "launch a successor",
                          "restart the coordinator or"):
                self.assertNotIn(token, text, name)
        self.assertIn("Coordinator transfer is not supported", RECOVERY)
        self.assertIn("never replace this session to install the guard", flat(RECOVERY))
        self.assertIn("Never claim the lease of a live run", flat(RECOVERY))


class PromptDeliveryContract(unittest.TestCase):
    def test_brief_is_passed_from_its_file(self):
        self.assertIn("Never inline the brief in double quotes", flat(SKILL))
        self.assertIn("Never `eval` the brief", flat(DISPATCH))
        for name, text in FILES.items():
            self.assertNotIn('"<brief>"', text, name)

    def test_submission_is_read_from_the_visible_frame(self):
        self.assertIn("herdr pane read <pane> --source visible", DISPATCH)
        self.assertIn("This applies to every kind", DISPATCH)
        self.assertIn("A menu or a chip does not by itself prove the overlay was omitted", flat(OMP))
        # One key per observed frame: a pre-planned second Enter submits whatever the first one left.
        self.assertNotIn("then a second to submit", flat(DISPATCH))
        self.assertIn("Never send a key blind or a pre-planned sequence of keys", flat(DISPATCH))
        self.assertIn("the runtime's own version and the launch argv", flat(DISPATCH))

    def test_dispatch_timeout_is_a_started_turn_not_a_failure(self):
        # Live run: both dispatches returned exit 1 `timeout` at 60 s while the turns were running.
        self.assertIn("is a started turn rather than a failed dispatch", flat(DISPATCH))
        self.assertIn("Keep the dispatch timeout short", flat(DISPATCH))
        prompts = [line for line in re.findall(r"herdr agent prompt [^`\n]*--wait[^`\n]*", "\n".join(FILES.values()))]
        self.assertTrue(prompts)
        for prompt in prompts:
            timeout = re.search(r"--timeout (\S+)", prompt).group(1)
            self.assertTrue(timeout == "<ms>" or int(timeout) <= 60000, prompt)


class WorkerContinuationContract(unittest.TestCase):
    def test_brief_carries_notes_context_and_replacement_rules(self):
        brief = flat(DISPATCH)
        self.assertIn("Update it at every meaningful edit or test boundary", brief)
        self.assertIn("Never write a final report for unfinished work", brief)
        self.assertIn("Never launch your own replacement", brief)
        self.assertIn("`status: complete` or `status: incomplete`", brief)
        self.assertIn("verified tooling correction", brief)
        self.assertIn("name the acceptance criterion it serves and the concrete failure", brief)

    def test_report_is_ready_for_acceptance_not_accepted(self):
        self.assertIn("**ready for acceptance** — not accepted", MAILBOX)
        self.assertIn("ends the attempt without making the phase ready", flat(MAILBOX))
        self.assertNotIn("A phase is complete when", MAILBOX)
        self.assertIn("a `status: complete` report", flat(SKILL))

    def test_replacement_proves_processes_stopped(self):
        self.assertIn("confirm none survives and the phase scope has stopped changing", flat(SUPERVISION))
        self.assertIn("A **foreign** process is one not descended from that agent session", flat(SUPERVISION))
        self.assertIn("shows no foreign process (one not descended from that agent session)", flat(SKILL))
        self.assertIn("never killed on a guess", flat(SUPERVISION))
        self.assertIn("No process or pending operation from that attempt may still write the phase scope", flat(SUPERVISION))
        self.assertIn("pane-local: a process that detached or was reparented is not in it", flat(SUPERVISION))

    def test_runtime_fallback_is_not_a_stall(self):
        self.assertIn("not a stall and not grounds for replacement", flat(SUPERVISION))
        self.assertIn("keeps the same attempt and never counts as a replacement", flat(SUPERVISION))


class LivenessAndPollingContract(unittest.TestCase):
    def test_liveness_follows_the_coordinator_pane(self):
        self.assertIn("classify it by its **coordinator pane**, not by its workers", flat(RECOVERY))
        self.assertIn("a live worker never makes the coordinator live", flat(RECOVERY))
        self.assertIn("never classify a run stale from an old heartbeat while its coordinator pane is live", flat(RECOVERY))
        self.assertIn("adopting and supervising any worker still running", flat(RECOVERY))

    def test_polling_worker_is_not_a_stall(self):
        self.assertIn("skip it in the wait and in stall sampling until its answer file is written", flat(SUPERVISION))
        self.assertIn("A phase with an unanswered `q-*` is never sampled", flat(SUPERVISION))

    def test_stalled_worker_replacement(self):
        self.assertIn("A stalled worker cannot write its own report", flat(SUPERVISION))
        self.assertIn("is not force-closed: report it as a blocker", flat(SUPERVISION))

    def test_guard_failure_never_blocks_the_coordinator(self):
        self.assertIn("a failed guard never blocks the run or calls for a new coordinator", flat(OMP))


class LedgerAndOwnerContract(unittest.TestCase):
    def test_completion_check_is_the_shipped_script(self):
        self.assertIn("python3 SKILL_DIR/scripts/check-run-closed.py RUN_ROOT", SKILL)
        self.assertTrue(CHECK.is_file())
        self.assertNotIn("grep", re.search(r"## Completion\n(.*?)\n## ", SKILL, re.S).group(1))
        self.assertNotIn("mail-archive", SKILL + MAILBOX + RECOVERY)

    def test_owner_is_the_coordinator_pane(self):
        self.assertIn("`owner = <pane>@<UTC>`", RECOVERY)
        self.assertIn("$HERDR_PANE_ID", RECOVERY)
        self.assertNotIn("coordinator session id", RECOVERY)


class DecisionScopeContract(unittest.TestCase):
    def test_only_dependents_wait(self):
        self.assertIn("Only the asking phase and its dependents wait on an open question", flat(SKILL))

    def test_necessary_change_names_its_criterion(self):
        self.assertIn("must name the acceptance criterion and the concrete failure", flat(SKILL))

    def test_checkpoint_tracks_all_pending_work(self):
        self.assertIn("every open question (id and phase)", flat(RECOVERY))
        self.assertIn("every in-flight mutation", flat(RECOVERY))
        self.assertIn("out-of-scope findings", flat(RECOVERY))

    def test_inputs(self):
        self.assertIn("`--runtime=<kind>` and `--runtime=current`", flat(SKILL))
        self.assertIn("applies to those phases and to their repairs", flat(SKILL))


class CompletionCheck(unittest.TestCase):
    """Run scripts/check-run-closed.py against closed and not-quite-closed run roots."""

    RUN = "plan-20260925T101500Z-a1b2c3"

    def ledger(self, *extra, accepted=None):
        base = {"runId": self.RUN, "epoch": 1}
        rows = [{"ts": "2026-09-25T10:15:00Z", "type": "run-created", "owner": "w1:p3@x", "plan": "p", "flags": ""},
                accepted or {"ts": "2026-09-25T10:40:00Z", "type": "phase-accepted", "phase": "p01", "attempt": "01",
                             "agent": "p01a01", "pane": "w1:p5", "commit": "88fe0b5"}, *extra]
        return "".join(json.dumps({**base, **row}, separators=(",", ":")) + "\n" for row in rows)

    def run_check(self, mutate=lambda root: None, *configs):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, self.RUN)
            root.mkdir()
            for name in (".gitignore", "checkpoint.md", "implementation-summary.md"):
                Path(root, name).write_text("x")
            Path(root, "ledger.jsonl").write_text(self.ledger())
            mutate(root)
            paths = []
            for index, config in enumerate(configs):
                path = Path(tmp, f"outside-{index}.json")
                path.write_text(json.dumps(config, separators=(",", ":")))
                paths.append(str(path))
            done = subprocess.run(["python3", str(CHECK), str(root), *paths], capture_output=True, text=True)
            return done.returncode, done.stdout

    def assertFails(self, mutate, needle, *configs):
        code, out = self.run_check(mutate, *configs)
        self.assertEqual(code, 1, out)
        self.assertIn(needle, out)

    def test_a_closed_run_passes(self):
        self.assertEqual(self.run_check()[0], 0)
        self.assertEqual(self.run_check(lambda r: None, {"lifecycle": "completed"})[0], 0)

    def test_compact_active_guard_config_fails(self):
        def leave_guard(root):
            Path(root, "guard").mkdir()
            Path(root, "guard/c.json").write_text('{"lifecycle":"active"}')
        self.assertFails(leave_guard, "lifecycle is 'active'")
        self.assertFails(lambda r: None, "lifecycle is 'active'", {"role": "coordinator"})

    def test_null_commit_or_empty_evidence_fails(self):
        for bad in ({"commit": None}, {"evidence": ""}, {"commit": "not-a-sha"}):
            row = {"ts": "2026-09-25T10:40:00Z", "type": "phase-accepted", "phase": "p01", **bad}
            self.assertFails(lambda r, row=row: Path(r, "ledger.jsonl").write_text(self.ledger(accepted=row)),
                             "without a commit SHA or evidence pointer")

    def test_left_mailbox_or_missing_ledger_fails(self):
        self.assertFails(lambda r: Path(r, "mail").mkdir(), "mail: left in the run root")
        self.assertFails(lambda r: Path(r, "ledger.jsonl").unlink(), "ledger.jsonl: missing")

    def test_ledger_order_and_vocabulary(self):
        answer = {"type": "answer", "file": "a-p01-a01-01.json", "decision": "yes", "decided_by": "auto"}
        early = {"ts": "2026-09-25T09:00:00Z", "question_id": "p01-a01-01", **answer}
        self.assertFails(lambda r: Path(r, "ledger.jsonl").write_text(self.ledger(early)), "ts goes backwards")
        odd = {"ts": "2026-09-25T11:00:00Z", "type": "phase-done"}
        self.assertFails(lambda r: Path(r, "ledger.jsonl").write_text(self.ledger(odd)), "unknown type")
        completed = {"ts": "2026-09-25T11:00:00Z", "type": "run-completed", "owner": "w1:p3@x"}
        late = {"ts": "2026-09-25T11:01:00Z", "question_id": "p01-a01-02", **answer}
        self.assertFails(lambda r: Path(r, "ledger.jsonl").write_text(self.ledger(completed, late)),
                         "run-completed must be recorded once")
        resumed = {"ts": "2026-09-25T11:01:00Z", "type": "lease-claimed", "owner": "w1:p9@y", "previousOwner": "w1:p3@x"}
        Path_ok = lambda r: Path(r, "ledger.jsonl").write_text(self.ledger(completed, resumed))
        self.assertEqual(self.run_check(Path_ok)[0], 0, "a resume after a crash before the lease release")

    def test_repeated_crash_and_resume_after_the_terminal_event(self):
        completed = {"ts": "2026-09-25T11:00:00Z", "type": "run-completed", "owner": "w1:p3@x"}
        claims = [{"ts": f"2026-09-25T11:0{i}:00Z", "type": "lease-claimed", "owner": f"w1:p{i}@y",
                   "previousOwner": "w1:p3@x", "epoch": 1 + i} for i in (1, 2)]
        self.assertEqual(self.run_check(lambda r: Path(r, "ledger.jsonl").write_text(self.ledger(completed, *claims)))[0], 0)
        again = {**completed, "ts": "2026-09-25T11:03:00Z"}
        self.assertFails(lambda r: Path(r, "ledger.jsonl").write_text(self.ledger(completed, *claims, again)),
                         "run-completed must be recorded once")
        abandoned = {"ts": "2026-09-25T11:03:00Z", "type": "run-abandoned", "owner": "w1:p2@y", "reason": "x"}
        self.assertFails(lambda r: Path(r, "ledger.jsonl").write_text(self.ledger(completed, abandoned)),
                         "abandoned run is not closed through completion")

    def test_acceptance_identity_is_required(self):
        base = {"ts": "2026-09-25T10:40:00Z", "type": "phase-accepted", "agent": "p01a01", "pane": "w1:p5",
                "commit": "abcdef0"}
        self.assertFails(lambda r: Path(r, "ledger.jsonl").write_text(self.ledger(accepted=base)),
                         "phase-accepted is missing phase, attempt")
        for bad, needle in (({"phase": "phase-01", "attempt": "01"}, "not a phase key"),
                            ({"phase": "p01", "attempt": "1"}, "not two digits")):
            row = {**base, **bad}
            self.assertFails(lambda r, row=row: Path(r, "ledger.jsonl").write_text(self.ledger(accepted=row)), needle)

    def test_an_impossible_date_is_reported_not_raised(self):
        row = {"ts": "2026-99-25T10:40:00Z", "type": "phase-accepted", "phase": "p01", "attempt": "01",
               "agent": "p01a01", "pane": "w1:p5", "commit": "abcdef0"}
        code, out = self.run_check(lambda r: Path(r, "ledger.jsonl").write_text(self.ledger(accepted=row)))
        self.assertEqual(code, 1)
        self.assertIn("FAIL ledger.jsonl:2: ts '2026-99-25T10:40:00Z' is not a valid", out)
        self.assertNotIn("Traceback", out)


class SubscriberPortability(unittest.TestCase):
    """The package ships to subscribers, whose publisher rejects literal absolute paths."""

    # Same shape as the subscriber publisher's posix-absolute-path rule.
    ABSOLUTE = re.compile(r"(?<![A-Za-z0-9:._/-])/(?!/)[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
    SHEBANG = "#!" + chr(47) + "usr/bin/env python3\n"  # stripped as the publisher does

    def test_no_file_carries_a_literal_absolute_path(self):
        for path in sorted(p for p in ROOT.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
            text = path.read_text(encoding="utf-8").removeprefix(self.SHEBANG)
            if path.name == "SKILL.md":
                text = text.split("\n---\n", 1)[1]
            found = self.ABSOLUTE.findall(text)
            self.assertEqual(found, [], f"{path.relative_to(ROOT)}: use SLASH or an uppercase placeholder")

    def test_placeholders_and_triggers_are_defined(self):
        self.assertIn("`SLASH` stands for the leading slash character (U+002F)", flat(DISPATCH))
        for name in ("PLAN_DIR", "RUN_ID", "RUN_ROOT", "MAILBOX", "CHECKPOINT", "SKILL_DIR", "OMP_HOME",
                     "PLANS_ROOT", "PHASE_ABSOLUTE_PATH"):
            self.assertIn(f"| `{name}` |", DISPATCH)
        for trigger in ("SLASHherdr-cook-plan <plan.md>", "SLASHskill:herdr-cook-plan <plan.md>",
                        "$herdr-cook-plan <plan.md>"):
            self.assertIn(trigger, SKILL)

    def test_codex_discovery_metadata(self):
        text = (ROOT / "agents/openai.yaml").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("interface:\n"))
        for key in ("display_name", "short_description", "default_prompt"):
            self.assertRegex(text, rf'(?m)^  {key}: "[^"\n]+"$')
        self.assertIn("$herdr-cook-plan", text)


class PackageStructure(unittest.TestCase):
    def test_entrypoint_stays_within_the_load_budget(self):
        lines = SKILL.count("\n") + 1
        self.assertLess(lines, 300, f"SKILL.md is {lines} lines; the load target is under 300")

    def test_each_reference_stays_within_the_load_budget(self):
        for name in REFERENCES:
            lines = FILES[name].count("\n") + 1
            self.assertLess(lines, 300, f"{name} is {lines} lines")

    def test_every_reference_is_linked_from_the_entrypoint(self):
        for name in ("dispatch", "supervision", "mailbox", "recovery", "runtimes", "omp", "selection"):
            self.assertIn(f"references/{name}.md", SKILL, name)

    def test_no_human_documentation_ships_inside_the_skill(self):
        self.assertFalse((ROOT / "references/workflow.md").exists())
        self.assertFalse((ROOT / "docs").exists())
        for name, text in FILES.items():
            self.assertNotIn("landing page", text.lower(), name)

    def test_description_stays_within_the_metadata_budget(self):
        folded = re.search(r"description: >-\n((?:  .*\n)+)", FRONT)
        description = folded.group(1) if folded else json.loads(re.search(r"(?m)^description: (.*)$", FRONT).group(1))
        self.assertLessEqual(len(" ".join(description.split())), 1024)

    @unittest.skipUnless(CANONICAL_FRONTMATTER, "subscriber copy: frontmatter is generated for the publisher")
    def test_canonical_frontmatter(self):
        self.assertTrue(SKILL.startswith("---\n"), "SKILL.md must start with front matter")
        self.assertIn("name: herdr-cook-plan", FRONT)
        self.assertRegex(FRONT, r"(?m)^description:")
        self.assertIn("[--runtime <kind>]", FRONT)
        self.assertRegex(FRONT, r'(?m)^  version: "\d+\.\d+\.\d+"\s*$')
        self.assertNotRegex(FRONT, r"(?m)^metadata\.version:")
        self.assertIn("author: Thieu Nguyen", FRONT)

    @unittest.skipIf(CANONICAL_FRONTMATTER, "canonical source: frontmatter is checked above")
    def test_subscriber_frontmatter_is_flat(self):
        self.assertRegex(FRONT, r"(?m)^name: herdr-cook-plan$")
        self.assertRegex(FRONT, r'(?m)^description: "[^"\n]+"$')
        for line in FRONT.strip().splitlines():
            self.assertFalse(line[:1].isspace(), f"nested frontmatter line: {line!r}")

    def test_no_dropped_scripts_are_referenced(self):
        for name, text in FILES.items():
            self.assertNotIn("discover-agents.py", text, name)
            self.assertNotIn("sync-runtimes.py", text, name)

    def test_no_0_7_era_command_survives(self):
        stale = ("herdr wait agent-status", "herdr wait output", "herdr agent send ")
        for name, text in FILES.items():
            for token in stale:
                self.assertNotIn(token, text, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
