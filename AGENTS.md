# Repository Guidelines

## Project Structure & Module Organization

This project currently contains one skill, `senior-advisor/`, for requesting external model opinions through a Python broker.

- `senior-advisor/SKILL.md`: agent workflow, consultation packet format, and safety rules.
- `senior-advisor/scripts/ask_senior.py`: packet validation, provider selection, CLI adapters, and Z.AI HTTP integration.
- `senior-advisor/scripts/test_ask_senior.py`: offline regression tests, colocated with the broker.
- `senior-advisor/references/`: provider setup and worked packet examples.

Keep new skills in descriptive, kebab-case directories with the same `SKILL.md`, `scripts/`, and `references/` organization where applicable.

## Build, Test, and Development Commands

Run commands from the project root. The broker uses only Python's standard library; no dependency installation or build step is configured. Python 3.12 is the documented verification environment.

- `python3 senior-advisor/scripts/ask_senior.py --help`: inspect CLI options.
- `python3 senior-advisor/scripts/ask_senior.py --doctor`: check provider binaries and key availability without making a consultation request; this does not verify authentication.
- `python3 senior-advisor/scripts/ask_senior.py --lint < packet.txt`: validate a locally prepared packet without contacting providers.

## Coding Style & Naming Conventions

Use four-space indentation, snake_case functions and variables, PascalCase classes, and UPPER_SNAKE_CASE constants. Match existing type annotations and dataclasses in broker code. Keep provider-specific behavior in `ask_<provider>` functions and validation separate from execution. No formatter or source-code linter is configured; preserve surrounding style.

## Testing Guidelines

Run the standard-library `unittest` suite:

```sh
python3 -B -m unittest discover -s senior-advisor/scripts -p 'test_*.py'
```

Name files `test_*.py` and methods `test_<behavior>`. Extend `OfflineTestCase` to isolate environment variables and block real network requests and provider processes. Cover packet boundaries, multilingual input, secret redaction, provider selection, and CLI output when changing those behaviors. No numeric coverage threshold is configured.

## Commit & Pull Request Guidelines

This directory has no Git metadata, so historical commit conventions cannot be verified. Use concise imperative subjects, such as `Fix packet byte-limit validation`. Keep changes focused. PRs should explain the behavior change, link relevant issues, report test results, and update affected skill instructions or provider references.

## Security & Configuration

Keep credentials in environment variables and use synthetic test fixtures. Preserve packet validation and provider isolation. Follow `SKILL.md` before live consultations. The installed `~/.qwen/skills/senior-advisor` is documented as a separate copy; source edits require explicit synchronization to reach that installation.
