# Senior Advisor Providers

The broker selects the first available provider in the order `codex,claude,grok,kimi,zai`. `--doctor` checks only whether each CLI binary is on PATH and whether `ZAI_API_KEY` is set. A binary on PATH does not guarantee that login has been completed: the doctor does not authenticate or make a consultation request.

For settings other than provider readiness, see [references/configuration.md](configuration.md).

Verification on 2026-09-08: this skill was exercised on macOS with Python 3.12.1, codex-cli 0.153.4, and Claude Code 2.1.263; the `codex` and `claude` lanes were run end to end. The `grok` (`~/.grok/bin/grok`) and `kimi` (`~/.kimi-code/bin/kimi`) binaries are present, but their lanes were not exercised. The `zai` lane needs `ZAI_API_KEY` and was not exercised.

| Provider | Check | Login / key | One-shot selection |
|---|---|---|---|
| Codex | `codex --version` | `codex login` or `printenv OPENAI_API_KEY \| codex login --with-api-key` | `SENIOR_PROVIDER=codex` |
| Claude Code | `claude --version` | `claude auth login` | `SENIOR_PROVIDER=claude` |
| Grok | `grok version` | `grok login` or `XAI_API_KEY` | `SENIOR_PROVIDER=grok` |
| Kimi Code | `kimi --version` | `kimi login` | `SENIOR_PROVIDER=kimi` |
| Z.AI API | `test -n "$ZAI_API_KEY"` | `export ZAI_API_KEY='…'` | `SENIOR_PROVIDER=zai` |

Install:

```bash
# Codex
curl -fsSL https://chatgpt.com/codex/install.sh | sh

# Claude Code
curl -fsSL https://claude.ai/install.sh | bash

# Grok Build
curl -fsSL https://x.ai/cli/install.sh | bash

# Kimi Code
curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash
```

Broker settings:

```bash
export SENIOR_PROVIDER=auto
export SENIOR_PROVIDER_ORDER=codex,claude,grok,kimi,zai
export SENIOR_TIMEOUT_SECONDS=300
export SENIOR_MAX_INPUT_BYTES=32768
export SENIOR_CLAUDE_MODEL=opus
export ZAI_MODEL=glm-5.3
```

Every lane accepts a model alias/id via `SENIOR_CODEX_MODEL`, `SENIOR_CLAUDE_MODEL`, `SENIOR_GROK_MODEL`, `SENIOR_KIMI_MODEL`, and `SENIOR_ZAI_MODEL`. For a single request, `--model` can set the model; it has the highest priority. Model identifiers are provider-specific — check `codex --help`, `claude --help`, `grok models`, or the Kimi configuration first.

Model precedence is `--model`, then `SENIOR_<PROVIDER>_MODEL`, then the lane default. Z.AI uses `ZAI_MODEL` as its lane default and falls back to `glm-5.3`. For other lanes, the broker leaves the model unset when neither override is configured, allowing the CLI to choose its default.

## Exit codes

- `0` — success; also a clean `--lint`, or a `--doctor` run that found a ready provider.
- `2` — broker error: packet gate rejection, oversized packet, suspected secret, unavailable provider, provider failure or timeout. The reason is on stderr after `ask_senior:`; for `--lint` the findings are the JSON on stdout.
- `3` — `--doctor` found no ready provider.
- `4` — consultation refused by policy: budget spent or held for confusion, trigger not allowed at this level, minimum interval not elapsed, duplicate answered packet, or two provider calls in a row failed. The message on stderr names which.

## Codex consultation home

`SENIOR_CODEX_HOME` tells the broker to set `CODEX_HOME` for the Codex child process. Use it to keep a persistent consultation home separate from the normal Codex home and its installed skills and plugins.

In the 2026-09-08 measurement, a default Codex consultation inherited roughly 74 local skill descriptions and a plugin catalogue, and printed a skills-context-budget warning even with `--ignore-user-config --ignore-rules`. Those flags alone did not remove the inherited skill context.

One-time setup:

```bash
CODEX_HOME=~/.codex-senior codex login
export SENIOR_CODEX_HOME=~/.codex-senior
```

Keep this directory persistent. Do not point `SENIOR_CODEX_HOME` at a temporary directory: token refresh writes there. Keep the consultation home free of the local skills and plugin catalogue whose context it is intended to avoid.

## Kimi skill isolation

The Kimi lane passes `--skills-dir` pointing at an empty directory created in the consultation's temporary working directory, so it does not inherit local skills.

## Keep the installed copy in sync

`~/.qwen/skills/senior-advisor` is a **separate copy** of this tree, not a symlink, verified by inode on 2026-09-08. Edits here do not reach the running agent until the tree is copied over to that installed location.
