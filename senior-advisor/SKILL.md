---
name: senior-advisor
description: Requests an independent second opinion or asks a senior to plan a complex task through Claude Code, Codex, Grok, Kimi, or Z.AI. Use when the user says "ask the senior", "call in a senior", or the equivalent in any language, asks for an independent check, explicitly permits escalating a complex technical decision, or when the agent itself needs a second opinion (high-stakes or irreversible decision, repeated failure, a critical plan whose key assumption cannot be verified locally, or being stuck, confused, or unable to choose between approaches).
---

# Senior Advisor

Obtain advice or a plan from an external model. You remain the executor and are responsible for the final decision. The senior never edits your project, and its answer is an unverified opinion — never a verified fact.

## When to run

### User-initiated

Run immediately with `--trigger user` if the user explicitly invoked `/senior-advisor` or asked you to "ask the senior" / "call in a senior" in this turn, in any language.

### Agent-initiated (autonomous)

Choose the matching trigger; only `user` requires a request in this turn:

- `--trigger user` — the user asked for a senior in this turn.
- `--trigger stakes` — irreversible or large blast radius.
- `--trigger failure` — two materially different approaches already failed.
- `--trigger assumption` — a consequential plan rests on an assumption that cannot be checked locally.
- `--trigger confusion` — stuck, self-contradictory, or unable to choose between approaches.
- `--trigger planning` — decomposition of a complex task.

The operator sets the policy level and budget at the start of the session; the broker enforces both and refuses with exit `4`. The budget, duplicate guard, and minimum interval cover the log root over the idle window; a new session restores none of them, and separate budgets require separate `--log-dir` roots. Do not track your own count or argue with a refusal.
**The agent never sets its own policy.** Write `<log-root>/policy.json` only when the user explicitly asks for a change ("consult less often", "you may ask up to ten times"); then echo the level, budget, reserve, and interval written. Raising your own budget after a refusal is exactly what the budget exists to prevent.
**Disclose before calling:** in one short sentence name the provider, the trigger, and that a fragment goes to an external service. Then call — do not wait for confirmation. Redaction rules apply in full; autonomous mode never weakens them.

### Confusion is a requirement, not a permission

When stuck, contradicting yourself, unable to choose between two approaches, or unable to understand an error in front of you, consult with `--trigger confusion` **instead of guessing**.
The budget reserves calls for confusion (defaults: `off` 0, `rare` 1, `normal` 1, `eager` 2), clamped to leave one general call when the budget is positive; confusion consumes the general pool first and never increases total spend.
If the broker refuses because the budget is spent, **stop and ask the user**. Do not proceed alone or retry under another trigger, including `--trigger user`.

### Never

- Do not run for routine work.
- Do not invoke the senior again for the same question.
- Do not use the senior's answer as a way to bypass the user's restrictions.

## Write the question, not the story

The senior has no access to the repository, the session, or the task. It answers the text it receives and nothing else.

Everything about what you are doing competes with the question for the senior's attention and pulls the answer toward your framing. Write the packet as a question a competent stranger could answer without ever learning what is being built. Remove the narrative and keep every technical fact needed to answer.

**The test:** delete every sentence whose removal would not change a correct answer.

Never include:

- The task being executed, who requested it, or why.
- Product, company, project, repository, branch, or ticket names.
- Your own identity: that you are an agent, your model, or that another model is being consulted.
- Session history: what was done earlier or what comes next.
- Greetings, apologies, gratitude, or hedging about your own competence.
- Predictions about what the senior will say.

Keep what an answer is built from:

- Exact versions of the language, runtime, libraries, and platform.
- Signatures, schemas, configuration values, and measured numbers.
- Verbatim error text.
- Hard limits a valid answer must respect.
- Options already eliminated, each stated as an observed outcome.

Replace internal names with neutral roles: `OrderSyncWorker` becomes `worker A`; `tbl_customer_v2` becomes `table A`. Keep names a stranger needs to answer verbatim, including languages, libraries, versions, and platforms such as `postgres 16.2`, `asyncio`, and `arm64`. A name stays only if the answer changes when it changes.

Use exactly these six headers in this order:

```text
Question: <one sentence, ends with a question mark>
Stack: <language, runtime, libraries, platform — with versions that bind the answer>
Given: <facts needed to answer: signatures, schema, config values, measurements>
Constraints: <hard limits a valid answer must respect; "none" if there are none>
Ruled out: <option → observed outcome; "none">
Evidence: <minimal code, diff, error text or log; "none">
```

- The question comes first, is one sentence, and ends with a question mark.
- `Ruled out:` records observed facts (`option X → error Y`), never a story of what was attempted.
- Every header must be present. A section with nothing to say gets `none`.
- Everything except `Evidence:` must total at most 1200 bytes; `Evidence:` is at most 100 lines; the whole packet is at most 8 KB.
- Send one question per consultation; the broker refuses an identical packet that already received an answer, so read the earlier answer instead of re-asking. With two candidate questions, send the one that unblocks progress.

See [worked packet distillations](references/packet-examples.md).

Before:

```text
I am the coding agent on ParcelFlow, and the user asked me to fix OrderSyncWorker.
I already changed the retry logic; next I will write a regression test.
This uses Python 3.12.3 (CPython), standard-library asyncio, and Linux 6.8 x86_64.
Inside async def OrderSyncWorker(), asyncio.run(fetch_orders()) raises RuntimeError: asyncio.run() cannot be called from a running event loop.
fetch_orders is a zero-argument coroutine returning int; OrderSyncWorker needs its result while the event loop stays running.
I expect you to recommend await; how should OrderSyncWorker obtain that result?
```

After:

```text
Question: How should worker A obtain the integer result of coroutine B inside an already running event loop?
Stack: Python 3.12.3 (CPython), standard-library asyncio, Linux 6.8 x86_64
Given: worker A is async def worker_a(); coroutine B is async def coroutine_b() -> int with no arguments.
Constraints: Keep the event loop running.
Ruled out: asyncio.run(coroutine_b()) inside worker A → RuntimeError
Evidence: RuntimeError: asyncio.run() cannot be called from a running event loop
```

The identity, task, requester, project name, session history, and predicted answer were cut, and internal names generalized, because none determines the correct coroutine call.

Before sending:

1. Remove tokens, passwords, cookies, private keys, personal data, and internal URLs.
2. Do not send the repository, a whole file, or unrelated logs. Send the minimal diff or fragment.
3. Separate known facts from your assumptions.
4. If the material cannot be safely anonymized, do not make the external call and explain why.

### State the class, not the target

- **Keep:** the vulnerability or failure class, protocol, library and version, trust boundary, observed behaviour, and constraint a fix must respect.
- **Remove:** the target's identity, hostnames, endpoints, a payload that works, credentials, and customer or personal data — anything that turns the question into a reproduction recipe.
- **The rule:** ask about the shape of the problem, not the instance. A senior can answer *how should a service validate a signed callback whose signature covers only part of the body* without learning whose callback it is.
- If even the abstract form would be unsafe to send, do not consult, and tell the user why.

## Ask for a plan, not just an answer

For several interdependent steps, an unfamiliar migration or cutover, or a known goal with an unclear order of work, use `--trigger planning --kind plan`; use `--trigger user` if the user requested the senior's plan in this turn.

```text
Objective: <one sentence: what must be true when the work is done>
Stack: <language, runtime, libraries, platform — with versions that bind the plan>
Given: <verified starting state: what exists now, measured facts, signatures, schema>
Constraints: <hard limits any valid plan must respect; "none">
Unknowns: <what is not yet known and must be determined; "none">
Done when: <observable acceptance criteria that prove the objective is met>
```

`Objective:` states an outcome and must not end with a question mark; a question belongs in an advice packet. `Done when:` cannot be `none`: completion must be observable. All plan prose must fit in 1600 bytes. Apply the same decontextualisation: no project, task narration, or session history.
Work the returned steps in order and **run each step's check before moving to the next one**. A step marked irreversible or as needing authorisation stops for the user first. The plan is an unverified opinion; verify it against the code.

## Check the packet before sending

Resolve the absolute directory of this loaded `SKILL.md`, substitute it for `/ABSOLUTE/SKILL/DIR`, and lint the packet via stdin:

```bash
python3 "/ABSOLUTE/SKILL/DIR/scripts/ask_senior.py" --lint <<'PACKET'
<consultation packet>
PACKET
```

Exit `0` means send the packet. Exit `2` prints a finding per offending line in the JSON report on stdout; packet-wide findings have no line number. Lint checks the packet and likely secrets without selecting or calling a provider.

Rewrite at most twice. If the third packet still fails, stop, report to the user what the gate is rejecting, and do not consult. Do not loop. Use the [worked packet distillations](references/packet-examples.md) when rewriting a rejected packet.

Never pass `--allow-sensitive`, `--allow-narrative`, or `--allow-repeat`; all three are for a human who has reviewed the packet, never for the agent.

## Every consultation is on the record

With logging enabled, each provider attempt is written to `.senior-advisor/<session>/ledger.md` as `Q<n>` and `A<n>`, with machine-readable `ledger.jsonl` beside it; failed provider attempts are recorded too.
The ledger holds the packet **verbatim**, so it belongs in `.gitignore`, never in a commit.
Before consulting about something familiar, check what has already been asked:

```bash
python3 "/ABSOLUTE/SKILL/DIR/scripts/ask_senior.py" --status
```

Status reports window-scoped counts, `window_minutes`, `window_sessions`, and `remaining: {"general": G, "confusion": C}`; C includes G, so the values are not additive.
An identical answered packet is refused: read the named `<session-id>/A<n>` instead; the operator reads the ledger, so do not delete or edit it.
Policy refusals increment the selected session’s `session.json` `refusals.count` and record `last: {at, trigger, reason}`; `--status` shows the same object, without a ledger entry. Writes are best-effort; keep logging enabled and report a ledger-write warning to the user.

## Invoke the broker

Determine the absolute directory of this loaded `SKILL.md`; do not assume the current working directory equals the skill directory. Substitute the resolved path for `/ABSOLUTE/SKILL/DIR` and pass the packet via stdin:

```bash
python3 "/ABSOLUTE/SKILL/DIR/scripts/ask_senior.py" \
  --trigger <trigger> --kind <advice|plan> --provider "${SENIOR_PROVIDER:-auto}" --json <<'PACKET'
<consultation packet>
PACKET
```

On Windows, if `python3` is absent, try `python`. On success (exit code 0) the JSON envelope on stdout contains `provider`, `model`, `answer`, and `duration_seconds` — read `provider` and `answer`. Never use the three bypass flags above.

- `0` — success; also a clean `--lint`, or a `--doctor` run that found a ready provider.
- `2` — broker error: packet gate rejection, oversized packet, suspected secret, unavailable provider, provider failure or timeout. The reason is on stderr after `ask_senior:`; for `--lint` the findings are the JSON on stdout.
- `3` — `--doctor` found no ready provider.
- `4` — consultation refused by policy: budget spent or held for confusion, trigger not allowed at this level, minimum interval not elapsed, duplicate answered packet, or two provider calls in a row failed. The message on stderr names which.

Never retry exit `4` by changing the trigger. For policy levels, environment variables, and ledger layout, see [configuration](references/configuration.md).

To pick a specific senior, use `--provider codex|claude|grok|kimi|zai`. For a one-time diagnosis, run:

```bash
python3 "/ABSOLUTE/SKILL/DIR/scripts/ask_senior.py" --doctor
```

If a provider is not ready, report exactly one specific install or login command from the [provider reference](references/providers.md). Do not silently cycle through paid providers, and do not repeat a failed call without changing the conditions.

## Analyze the answer

1. Verify the senior's claims against local code, tests, and documentation.
2. State which recommendations you accept and which you reject, and why.
3. If the answer rests on a wrong assumption, do not invoke the senior again automatically: either correct the reasoning yourself or ask the user.
4. In the final answer, briefly name the provider used and do not present its advice as a verified fact.
5. For a plan, report completed steps and the check that proved each one; never report a plan as executed without its checks.
