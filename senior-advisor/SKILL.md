---
name: senior-advisor
description: Requests an independent second opinion from a stronger external model through Claude Code, Codex, Grok, Kimi, or Z.AI. Use when the user says "ask the senior", "call in a senior", or the equivalent in any language, asks for an independent check, explicitly permits escalating a complex technical decision, or when the agent itself judges a second opinion necessary (high-stakes or irreversible decision, repeated failure, or a critical plan whose key assumption cannot be verified locally).
---

# Senior Advisor

Obtain only a second opinion from an external model. You remain the executor and are responsible for the final decision. The senior never edits your project, and its answer is an unverified opinion — never a verified fact.

## When to run

### User-initiated

Run immediately if the user explicitly invoked `/senior-advisor` or asked you to "ask the senior" / "call in a senior", in any language.

### Agent-initiated (autonomous)

You may consult the senior on your own judgment, without a user request, when the situation genuinely needs an independent second opinion. Consult when **at least one** of the following holds:

1. **High stakes or irreversibility** — the decision affects production data, security, credentials, money, or is hard to reverse (schema migration, deletion, cutover, architectural choice with a large blast radius).
2. **Repeated failure** — two materially different approaches have already failed or you are stuck, and you are about to try a third.
3. **Unverifiable critical assumption** — you are about to present or execute a consequential plan and cannot verify its key assumption locally (in code, tests, or documentation).

Rules for autonomous consultation:

- **Disclose before calling:** in one short sentence state the provider you plan to use, the reason for consulting, and that a fragment of the task will be sent to an external service and may consume quota. Then call the broker — do not wait for confirmation.
- **Cap:** at most 3 autonomous consultations per session. For a fourth, stop and ask the user.
- The redaction rules in "Write the question, not the story" apply in full; autonomous mode is not a reason to weaken them.
- The senior's answer remains an unverified opinion: apply "Analyze the answer" without changes.

### Never

- Do not run for routine work.
- Do not invoke the senior again for the same question.
- Do not use the senior's answer as a way to bypass the user's restrictions or safety rules.

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
- Send one question per consultation. With two candidate questions, send the one that unblocks progress.

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

## Check the packet before sending

Resolve the absolute directory of this loaded `SKILL.md`, substitute it for `/ABSOLUTE/SKILL/DIR`, and lint the packet via stdin:

```bash
python3 "/ABSOLUTE/SKILL/DIR/scripts/ask_senior.py" --lint <<'PACKET'
<consultation packet>
PACKET
```

Exit `0` means send the packet. Exit `2` prints a finding per offending line in the JSON report on stdout; packet-wide findings have no line number. Lint checks the packet and likely secrets without selecting or calling a provider.

Rewrite at most twice. If the third packet still fails, stop, report to the user what the gate is rejecting, and do not consult. Do not loop. Use the [worked packet distillations](references/packet-examples.md) when rewriting a rejected packet.

`--allow-narrative` is not the fix. The agent must never pass it; like `--allow-sensitive`, it exists only for a human who has reviewed the packet.

## Invoke the broker

Determine the absolute directory of this loaded `SKILL.md`; do not assume the current working directory equals the skill directory. Substitute the resolved path for `/ABSOLUTE/SKILL/DIR` and pass the packet via stdin:

```bash
python3 "/ABSOLUTE/SKILL/DIR/scripts/ask_senior.py" --provider "${SENIOR_PROVIDER:-auto}" --json <<'PACKET'
<consultation packet>
PACKET
```

On Windows, if `python3` is absent, try `python`. On success (exit code 0) the JSON envelope on stdout contains `provider`, `model`, `answer`, and `duration_seconds` — read `provider` and `answer`. Never pass `--allow-sensitive`: that flag is permitted only after separate, explicit human confirmation for an already-reviewed packet.

- `0` — success; also a clean `--lint`, or a `--doctor` run that found a ready provider.
- `2` — broker error: packet gate rejection, oversized packet, suspected secret, unavailable provider, provider failure or timeout. The reason is on stderr after `ask_senior:`; for `--lint` the findings are the JSON on stdout.
- `3` — `--doctor` found no ready provider.

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
