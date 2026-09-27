# Interactive worker kind and model selection

Load when `--select-agent` is present. Select the worker kind and primary model once per run, before
the first dispatch. This does not change the coordinator's own runtime or model.

## Discover candidates on the execution host

First recover any existing run and completed selection; do not scan or ask again on compact,
fallback, repair or continuation. Revalidate only changed host, profile or launcher facts, or a
concrete failure. Do not change an active worker's selection mid-attempt.

Use the live surfaces, not a guessed inventory:

```bash
herdr agent                 # the kind list Herdr can launch and detect
herdr integration status    # which kinds have lifecycle reporting instead of screen detection
```

Verify a candidate kind's cook entrypoint before offering it — see
[runtimes](runtimes.md) for the per-runtime skill directories and the current verified rows. Herdr
confirming a kind proves detection, not that AgentKit's cook is installed, authenticated or able to
run. Present the list with that evidence, and permit a kind the user names even when this run
discovered no cook for it, marked as requiring discovery rather than ready.

Remote execution changes the answer: a kind verified on one host is not verified elsewhere. If the
run targets another machine, that host's evidence is required; report the gap instead of substituting
local results.

## Ask only for missing choices

1. Ask which worker runtime to use, always allowing an explicit different one. An explicit `--runtime`,
   or a runtime already chosen for this run, fills this field; do not ask again. The value is a Herdr
   agent kind; this skill passes it to Herdr's own `--kind` argument on `agent start`.
2. For the selected kind only, inspect its live model catalog and documented launch support. OMP
   exposes `omp models --json`; confirm the installed help first and preserve the effective profile,
   overlays and role routing. For other kinds, discover the supported interface instead of assuming a
   shared command. Read only model metadata, never API keys or tokens. Do not refresh or install
   anything, and do not run paid inference merely to populate a picker.
3. Ask for a model, always including "Keep configured default". Show exact IDs from the selected
   catalog, optionally narrowing a long list. Accept an explicit ID when no listing exists, marked as
   catalog-unvalidated. Do not invent IDs, and do not treat a missing catalog as proof the kind
   cannot run. Do not add an effort or profile questionnaire unless a real ambiguity needs it.

Use the coordinator's own user-facing question mechanism, not a worker-local dialog. `--auto` does
**not** answer these selection questions: wait for the user, persist the pending field, and resume in
the same run. No response is not a choice of default. Once both choices exist, continue preflight and
dispatch without an extra confirmation. If the selection is unusable, explain the specific blocker
and request only the replacement choice; never silently switch kind or model.

## Apply and persist

Store in the checkpoint: selection complete or pending, host, kind, resolved executable and launch
argv, profile source, selected model ID or explicit inherit-default, model evidence, and launch
method. Distinguish requested, effective and observed model, and keep fallback events separate. Reuse
the choice for fresh phases, repairs and continuations, and carry it into any handoff.

For inherit-default, omit the model override and let the runtime resolve its configured default; do
not freeze a guessed ID. Pass an explicit model through the kind's native argv after the `--`
separator of `agent start`, and confirm the observed session model before claiming it was used. An
explicit primary selection overrides only the primary worker model; it does not force every delegate
onto that model or disable the runtime's fallback chain.

## Bounded acceptance cases

- A kind found in the live list but with no verified cook entrypoint appears as "requires discovery",
  not as ready.
- `--select-agent --auto` still waits for the runtime and model answers; an explicit `--runtime` skips
  only the runtime question.
- A compact between the two questions restores the pending model question; a completed selection
  survives repair and handoff without asking again or replaying phases.
- Default selection adds no overrides; an explicit OMP selection changes only the primary model.
- An unsupported model launch cannot silently revert to the default.
- A local catalog is not presented as remote readiness, authentication or quota.

These cases guide behavioral review. Reading a catalog is not proof that a model ran.
