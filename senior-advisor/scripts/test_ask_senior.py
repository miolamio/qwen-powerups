"""Offline contract tests for the senior-advisor broker (Python 3.12)."""

import importlib.util
import datetime
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock


NAME = "ask_senior_under_test"
PATH = Path(__file__).resolve().with_name("ask_senior.py")
spec = importlib.util.spec_from_file_location(NAME, PATH)
senior = importlib.util.module_from_spec(spec)
# Dataclasses look up their defining module while exec_module is running.
sys.modules[NAME] = senior
spec.loader.exec_module(senior)


MUST_CATCH = (
    "I'm implementing a retry loop and the pool leaks connections.",
    "I am working on the sync layer; the pool leaks connections.",
    "I am building a queue consumer.",
    "We are working on the ingestion path.",
    "my goal is to keep the pool under 10 connections.",
    "my project uses a connection pool.",
    "in my codebase the pool is shared.",
    "The user requested a faster endpoint.",
    "The user needs this by Friday.",
    "as an agent I cannot restart the service.",
    "as an LLM I have no shell access.",
    "Hello, I have a pooling question.",
    "To give you context, the pool is shared.",
    "Background: the pool is shared.",
    "я делаю воркер, который держит пул.",
    "я пишу воркер, который держит пул.",
    "я реализую пул соединений.",
    "я пытаюсь ускорить пул.",
    "мне нужно ускорить пул.",
    "моя цель — уменьшить число соединений.",
    "мой проект использует пул.",
    "пользователь требует ускорить это.",
    "пользователь хочет ускорить это.",
    "я ожидаю что ты предложишь пул.",
    "сейчас я отлаживаю пул.",
)

MUST_STAY_CLEAN = (
    "p99 latency must stay under 200 ms; no new dependencies.",
    "the context manager returns None on the second call.",
    "соединение закрывается через 30 секунд простоя.",
    "pgbouncer transaction pooling fails with DuplicatePreparedStatement.",
    "the background worker drains the queue every 5 s.",
    "switching context costs 1.2 us on this platform.",
    "the request context is propagated through contextvars.",
    "клиент закрывает соединение через 30 с.",
    "поиск по индексу занимает 12 мс.",
    "the retry budget is 3 attempts with 100 ms jitter.",
)

# Deliberately synthetic fixtures, never credentials from the environment.
SECRET_SAMPLES = {
    "private key": "-----BEGIN PRIVATE KEY-----",
    "AWS access key": "AKIA" + "A" * 16,
    "OpenAI-style key": "sk-" + "a" * 24,
    "xAI key": "xai-" + "b" * 24,
    "GitHub token": "ghp_" + "c" * 24,
    "Slack token": "xoxb-" + "d" * 16,
    "Google API key": "AIza" + "e" * 35,
    "Stripe live secret/restricted key": "rk_live_" + "f" * 20,
    "JSON Web Token": (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaXh0dXJlIn0.c3ludGhldGlj"
    ),
    "Slack webhook URL": "https://hooks.slack.com/services/TFAKE/BFAKE/fixture",
    "URL with inline credentials": "https://fixture:synthetic-password@example.com",
    "Bearer token": "Authorization: Bearer synthetic-token-value",
    "secret assignment": "API_KEY=synthetic-assignment-value",
}


def packet(given="The pool is shared.", *, question="How should the pool be bounded?",
           stack="Python 3.12.1", constraints="At most 10 connections.",
           ruled_out="none", evidence="none"):
    return (
        f"Question: {question}\n"
        f"Stack: {stack}\n"
        f"Given: {given}\n"
        f"Constraints: {constraints}\n"
        f"Ruled out: {ruled_out}\n"
        f"Evidence: {evidence}\n"
    )


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        self.log_dir = Path(self.enterContext(
            tempfile.TemporaryDirectory(prefix="senior-advisor-test-")
        )).resolve()
        # Host configuration must not affect CLI defaults or model selection.
        self.enterContext(mock.patch.dict(os.environ, {}, clear=True))
        self.enterContext(mock.patch.object(
            senior.subprocess, "Popen", side_effect=AssertionError("No provider process allowed")
        ))
        self.enterContext(mock.patch.object(
            senior.urllib.request, "urlopen", side_effect=AssertionError("No network allowed")
        ))

    def findings(self, text):
        lines = senior.parse_packet(text)
        return senior.sort_findings(
            senior.check_packet(text, lines) + senior.sensitive_findings(text, lines)
        )

    def run_main(self, args, text):
        args = list(args)
        # Real consultations must never inherit the working-directory ledger.
        ledger_options = {"--log-dir", "--no-ledger", "--status", "--lint", "--doctor", "--dry-run"}
        if not any(arg.split("=", 1)[0] in ledger_options for arg in args):
            args = ["--log-dir", str(self.log_dir), *args]
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(text)):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = senior.main(args)
        return code, stdout.getvalue(), stderr.getvalue()


class NarrativeTests(OfflineTestCase):
    def test_must_catch_corpus(self):
        for given in MUST_CATCH:
            with self.subTest(given=given):
                self.assertTrue(any(f.code == "narrative" for f in self.findings(packet(given))))

    def test_must_stay_clean_corpus(self):
        for given in MUST_STAY_CLEAN:
            with self.subTest(given=given):
                self.assertEqual(self.findings(packet(given)), [])

    def test_evidence_is_exempt_from_narrative_gate(self):
        text = packet(evidence="\nI am working on this\nпользователь просит")
        code, stdout, stderr = self.run_main(["--lint"], text)
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["findings"], [])
        self.assertTrue(json.loads(stdout)["ok"])

    def test_finding_shape(self):
        text = packet("The pool is shared; my project uses 8 connections.")
        findings = [f for f in self.findings(text) if f.code == "narrative"]
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.line, 3)
        self.assertEqual(finding.section, "Given")
        self.assertEqual(finding.match, "my project")
        self.assertIn(finding.match, text.splitlines()[finding.line - 1])
        self.assertTrue(finding.fix.strip())
        self.assertEqual(finding.category, "task narration")


class StructuralTests(OfflineTestCase):
    def assert_finding(self, text, code, section=None):
        findings = self.findings(text)
        self.assertTrue(
            any(f.code == code and f.section == section for f in findings),
            f"Missing {code!r} in section {section!r}: {findings!r}",
        )

    def test_missing_question_header(self):
        self.assert_finding(packet().split("\n", 1)[1], "missing_header", "Question")

    def test_question_header_is_not_first(self):
        lines = packet().splitlines(keepends=True)
        lines[0], lines[1] = lines[1], lines[0]
        self.assert_finding("".join(lines), "question_not_first", "Question")

    def test_empty_question(self):
        self.assert_finding(packet(question=""), "question_empty", "Question")

    def test_question_has_no_question_mark(self):
        self.assert_finding(packet(question="Bound the pool"), "question_no_mark", "Question")

    def test_question_over_300_bytes(self):
        self.assert_finding(packet(question="x" * 300 + "?"), "question_too_long", "Question")

    def test_question_over_two_lines(self):
        self.assert_finding(
            packet(question="How should\nthe shared pool\nbe bounded?"),
            "question_too_long", "Question",
        )

    def test_missing_stack_header(self):
        text = packet().replace("Stack: Python 3.12.1\n", "")
        self.assert_finding(text, "missing_header", "Stack")

    def test_prose_over_1200_bytes(self):
        self.assert_finding(packet("x" * 1201), "prose_too_long")

    def test_evidence_over_100_lines(self):
        self.assert_finding(packet(evidence="\n".join(["row"] * 101)), "evidence_too_long", "Evidence")

    def test_packet_over_8192_bytes(self):
        self.assert_finding(packet(evidence="x" * 8193), "packet_too_long")


class MultibyteTests(OfflineTestCase):
    def test_cyrillic_prose_limit_is_measured_in_bytes(self):
        kwargs = {
            "question": "Как ограничить пул?",
            "stack": "Python 3.12.1 — стандартная библиотека",
            "constraints": "Не более 10 соединений.",
            "ruled_out": "нет",
            "evidence": "нет",
        }
        base = packet("", **kwargs)
        base_prose = base.split("Evidence:", 1)[0]
        remaining = 1199 - len(base_prose.encode("utf-8"))
        given = "п" * (remaining // 2) + "." * (remaining % 2)
        text = packet(given, **kwargs)
        prose = text.split("Evidence:", 1)[0]
        self.assertEqual(len(prose.encode("utf-8")), 1199)
        self.assertLess(len(prose), 700)
        self.assertEqual(self.findings(text), [])
        code, stdout, stderr = self.run_main(["--lint"], text)
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["prose_bytes"], 1199)

        oversized = packet(given + "п", **kwargs)
        code, stdout, stderr = self.run_main(["--lint"], oversized)
        self.assertEqual((code, stderr), (2, ""))
        report = json.loads(stdout)
        self.assertEqual(report["prose_bytes"], 1201)
        self.assertEqual([f["code"] for f in report["findings"]], ["prose_too_long"])

    def test_findings_keep_line_numbers_after_multibyte_text(self):
        text = packet(
            "соединение — закрыто.\nя пишу воркер, который держит пул.",
            question="Как ограничить пул?",
            stack="Python 3.12.1 — стандартная библиотека",
            evidence="\nсоединение — открыто.\n" + SECRET_SAMPLES["AWS access key"],
        )
        findings = self.findings(text)
        self.assertEqual(
            [(f.code, f.line, f.section) for f in findings],
            [("narrative", 4, "Given"), ("sensitive", 9, "Evidence")],
        )
        self.assertEqual(findings[0].match, "я пишу")
        self.assertIn(findings[0].match, text.splitlines()[3])


class SecretTests(OfflineTestCase):
    def test_positive_sample_for_every_secret_label_and_redacted_lint_match(self):
        self.assertEqual(set(SECRET_SAMPLES), {label for label, _ in senior.SECRET_PATTERNS})
        for label, secret in SECRET_SAMPLES.items():
            with self.subTest(label=label):
                self.assertIn(label, senior.detect_sensitive(secret))
                # A separate evidence line also exercises anchored assignments.
                code, stdout, stderr = self.run_main(["--lint"], packet(evidence="\n" + secret))
                self.assertEqual((code, stderr), (2, ""))
                report = json.loads(stdout)
                self.assertFalse(report["ok"])
                self.assertTrue(any(
                    f["code"] == "sensitive" and f["category"] == label
                    for f in report["findings"]
                ))
                for finding in report["findings"]:
                    self.assertEqual(finding["code"], "sensitive")
                    self.assertEqual(finding["match"], "")
                    self.assertNotIn(secret, finding["match"])
                self.assertNotIn(secret, stdout)

    def test_negative_secret_corpus(self):
        samples = (
            "[documentation](https://example.com/path)",
            "https://example.com/path",
            "The bare word AIza is an example prefix.",
            "docker://image:tag",
            "eyJzdWIiOiJzeW50aGV0aWMgZml4dHVyZSJ9",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(senior.detect_sensitive(sample), [])
                self.assertEqual(self.findings(packet(evidence="\n" + sample)), [])


class ParseOrderTests(OfflineTestCase):
    def test_deduplicates_preserving_order(self):
        self.assertEqual(
            senior.parse_order(" kimi, CODEX, kimi,claude, codex,zai,grok, "),
            ["kimi", "codex", "claude", "zai", "grok"],
        )

    def test_unknown_name_raises(self):
        with self.assertRaises(senior.SeniorError):
            senior.parse_order("codex,unknown")

    def test_empty_order_raises(self):
        with self.assertRaises(senior.SeniorError):
            senior.parse_order("")


class ResolveModelTests(OfflineTestCase):
    def test_explicit_model_takes_precedence(self):
        for provider in senior.DEFAULT_PROVIDER_ORDER.split(","):
            with self.subTest(provider=provider), mock.patch.dict(os.environ, {
                f"SENIOR_{provider.upper()}_MODEL": "environment-fixture",
                "ZAI_MODEL": "lane-fixture",
            }, clear=True):
                self.assertEqual(senior.resolve_model(provider, " explicit-fixture "), "explicit-fixture")

    def test_provider_environment_takes_precedence_over_lane_default(self):
        for provider in senior.DEFAULT_PROVIDER_ORDER.split(","):
            with self.subTest(provider=provider), mock.patch.dict(os.environ, {
                f"SENIOR_{provider.upper()}_MODEL": " environment-fixture ",
                "ZAI_MODEL": "lane-fixture",
            }, clear=True):
                self.assertEqual(senior.resolve_model(provider, None), "environment-fixture")

    def test_zai_model_environment_default(self):
        with mock.patch.dict(os.environ, {"ZAI_MODEL": " lane-fixture "}, clear=True):
            self.assertEqual(senior.resolve_model("zai", None), "lane-fixture")

    def test_zai_hard_coded_default(self):
        for env in ({}, {"ZAI_MODEL": " "}):
            with self.subTest(env=env), mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(senior.resolve_model("zai", None), "glm-5.3")

    def test_other_lanes_default_to_none(self):
        for provider in ("codex", "claude", "grok", "kimi"):
            with self.subTest(provider=provider), mock.patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(senior.resolve_model(provider, None))


class ExtractJsonTextTests(OfflineTestCase):
    def test_claude_json_envelope_returns_result_without_metadata(self):
        raw = json.dumps({
            "type": "result", "subtype": "success", "is_error": False,
            "usage": {"input_tokens": 42, "output_tokens": 7},
            "modelUsage": {"fixture-model": {"inputTokens": 42, "outputTokens": 7}},
            "session_id": "fixture-session", "duration_ms": 123, "num_turns": 1,
            "result": "  Verdict: bound the pool.\nUse a semaphore.  ",
        })
        self.assertEqual(senior.extract_json_text(raw), "Verdict: bound the pool.\nUse a semaphore.")

    def test_non_json_text_passes_through_stripped(self):
        self.assertEqual(senior.extract_json_text(" \nPlain answer.\n "), "Plain answer.")

    def test_nested_data_output(self):
        self.assertEqual(
            senior.extract_json_text(json.dumps({"data": {"output": " Nested answer. "}})),
            "Nested answer.",
        )

    def test_result_content_blocks_join(self):
        raw = json.dumps({"result": {"content": [
            {"type": "text", "text": " First. "},
            {"type": "text", "text": " Second. "},
        ]}})
        self.assertEqual(senior.extract_json_text(raw), "First.\nSecond.")

    def test_top_level_content_blocks_join(self):
        raw = json.dumps([
            {"type": "text", "text": " First. "},
            {"type": "text", "text": " Second. "},
        ])
        self.assertEqual(senior.extract_json_text(raw), "First.\nSecond.")


class ForbiddenRunners:
    """Raise on access, including a mapping lookup or a readiness check."""

    def __getattribute__(self, name):
        raise AssertionError("Lint must not access PROVIDER_RUNNERS")

    def __getitem__(self, key):
        raise AssertionError("Lint must not select a provider runner")

    def __iter__(self):
        raise AssertionError("Lint must not iterate provider runners")

    def __bool__(self):
        raise AssertionError("Lint must not inspect provider runners")


class CliTests(OfflineTestCase):
    def test_lint_clean_packet(self):
        code, stdout, stderr = self.run_main(["--lint"], packet())
        self.assertEqual((code, stderr), (0, ""))
        report = json.loads(stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["findings"], [])

    def test_lint_narrative_packet(self):
        code, stdout, stderr = self.run_main(["--lint"], packet("my project uses a pool."))
        self.assertEqual((code, stderr), (2, ""))
        report = json.loads(stdout)
        self.assertFalse(report["ok"])
        self.assertTrue(any(f["code"] == "narrative" for f in report["findings"]))

    def test_lint_never_selects_or_touches_providers(self):
        with mock.patch.object(senior, "select_provider", side_effect=AssertionError(
            "Lint must not select a provider"
        )) as select, mock.patch.object(senior, "PROVIDER_RUNNERS", ForbiddenRunners()):
            code, stdout, stderr = self.run_main(["--lint"], packet())
        self.assertEqual((code, stderr), (0, ""))
        self.assertTrue(json.loads(stdout)["ok"])
        select.assert_not_called()

    def test_lint_doctor_conflict(self):
        code, stdout, stderr = self.run_main(["--lint", "--doctor"], packet())
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(stderr, "ask_senior: --lint and --doctor cannot be used together\n")

    def test_allow_narrative_bypasses_gate_and_reports_it_in_json(self):
        runner = mock.Mock(return_value="Verdict: bound the pool.")
        with mock.patch.object(senior, "select_provider", return_value="codex"):
            with mock.patch.object(senior, "PROVIDER_RUNNERS", {"codex": runner}):
                code, stdout, stderr = self.run_main(
                    ["--allow-narrative", "--json", "--trigger", "user"], packet("my project uses a pool.")
                )
        self.assertEqual((code, stderr), (0, ""))
        runner.assert_called_once()
        report = json.loads(stdout)
        self.assertEqual(report["checks"]["narrative"], "bypassed")
        self.assertEqual(report["answer"], "Verdict: bound the pool.")
        self.assertEqual(report["provider"], "codex")

    def test_rejection_stderr_names_each_finding_on_its_own_line(self):
        text = packet(
            "my project uses a pool.\nThe user needs this by Friday.",
            question="How should the pool be bounded",
        )
        code, stdout, stderr = self.run_main(["--dry-run"], text)
        self.assertEqual((code, stdout), (2, ""))
        diagnostic_lines = [line for line in stderr.splitlines() if line.startswith("  line ")]
        self.assertEqual(len(diagnostic_lines), 3)
        expected = (
            ("line 1 [question_no_mark]:", "How should the pool be bounded"),
            ("line 3 [narrative/task narration]:", "my project"),
            ("line 4 [narrative/requester]:", "The user needs"),
        )
        for diagnostic, (label, span) in zip(diagnostic_lines, expected):
            self.assertIn(label, diagnostic)
            self.assertIn(json.dumps(span), diagnostic)
            self.assertIn(" -> ", diagnostic)


class LedgerIsolationTests(OfflineTestCase):
    def test_cli_read_write_surface_leaves_no_ledger_in_cwd(self):
        cwd = Path.cwd()
        self.assertNotIn(cwd / ".senior-advisor", cwd.iterdir())
        runner = mock.Mock(return_value="Verdict: bound the pool.")
        self.enterContext(mock.patch.object(senior, "select_provider", return_value="codex"))
        self.enterContext(mock.patch.object(senior, "PROVIDER_RUNNERS", {"codex": runner}))
        self.enterContext(mock.patch.object(senior.shutil, "which", return_value=None))
        args = ["--trigger", "user", "--json"]

        code, stdout, stderr = self.run_main(args, packet())
        self.assertEqual((code, stderr), (0, ""))
        session = Path(json.loads(stdout)["ledger"]["path"]).parent
        self.assertEqual(session.parent, self.log_dir)
        for path in (self.log_dir / "current.json", session / "ledger.jsonl",
                     session / "ledger.md", session / "session.json"):
            self.assertTrue(path.is_file(), str(path))
        self.assertIn(packet(), (session / "ledger.md").read_text(encoding="utf-8"))

        # Repeated calls within one test must read the same temporary history.
        code, stdout, stderr = self.run_main(args, packet())
        self.assertEqual((code, stdout), (4, ""))
        self.assertIn(f"Duplicate question matches entry {session.name}/A1", stderr)
        runner.assert_called_once()

        runner.side_effect = senior.SeniorError("synthetic provider failure")
        code, stdout, stderr = self.run_main(args, packet(question="How should retries be bounded?"))
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(stderr, "ask_senior: codex: synthetic provider failure\n")
        entries = [json.loads(line) for line in (session / "ledger.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()]
        self.assertEqual([entry["status"] for entry in entries], ["ok", "error"])

        code, stdout, stderr = self.run_main(["--status", "--log-dir", str(self.log_dir)], "")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["counts"]["total"], 2)

        runner.side_effect = None
        code, stdout, stderr = self.run_main(
            [*args, "--new-session"], packet(question="How should idle connections expire?")
        )
        self.assertEqual((code, stderr), (0, ""))
        new_session = Path(json.loads(stdout)["ledger"]["path"]).parent
        self.assertEqual(new_session.parent, self.log_dir)
        self.assertNotEqual(new_session, session)
        self.assertEqual(json.loads((self.log_dir / "current.json").read_text(
            encoding="utf-8"
        ))["id"], new_session.name)

        # Both CLI spellings must preserve an explicitly supplied log root.
        for index, spelling in enumerate(("separate", "equals")):
            with self.subTest(log_dir=spelling):
                explicit_root = self.log_dir / f"explicit-{index}"
                flags = (["--log-dir", str(explicit_root)] if spelling == "separate"
                         else [f"--log-dir={explicit_root}"])
                code, stdout, stderr = self.run_main([*args, *flags], packet())
                self.assertEqual((code, stderr), (0, ""))
                self.assertEqual(Path(json.loads(stdout)["ledger"]["path"]).parent.parent,
                                 explicit_root)

        code, stdout, stderr = self.run_main([*args, "--no-ledger", "--new-session"], packet())
        self.assertEqual((code, stderr), (0, ""))
        self.assertIsNone(json.loads(stdout)["ledger"])
        runner.reset_mock()
        for flags, expected_code in ((["--status"], 0), (["--lint"], 0),
                                     (["--doctor"], 3), (["--dry-run"], 0)):
            with self.subTest(mode=flags[0]):
                code, stdout, stderr = self.run_main([*flags, "--new-session"], packet())
                self.assertEqual((code, stderr), (expected_code, ""))
                self.assertIsInstance(json.loads(stdout), dict)
        runner.assert_not_called()
        self.assertEqual(Path.cwd(), cwd)
        self.assertNotIn(cwd / ".senior-advisor", Path.cwd().iterdir())


class PolicyWindowTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.runner = mock.Mock(return_value="Verdict: bound the pool.")
        self.enterContext(mock.patch.object(senior, "select_provider", return_value="codex"))
        self.enterContext(mock.patch.object(senior, "PROVIDER_RUNNERS", {"codex": self.runner}))

    def consult(self, session_id="S", trigger="stakes", number=1, flags=()):
        session_flags = ["--session-id", session_id] if session_id is not None else ["--new-session"]
        return self.run_main([
            "--trigger", trigger, "--min-interval-seconds", "0", *session_flags, *flags,
        ], packet(question=f"How should pool {number} be bounded?"))

    def status(self, session_id="S", flags=()):
        code, stdout, stderr = self.run_main([
            "--status", "--log-dir", str(self.log_dir), "--session-id", session_id, *flags,
        ], "")
        self.assertEqual((code, stderr), (0, ""))
        return json.loads(stdout)

    def history(self, session_id="S"):
        return senior.read_ledger(self.log_dir / session_id / "ledger.jsonl")[0]

    def set_finished(self, session_id, minutes_ago, n=None):
        entries = self.history(session_id)
        finished = (datetime.datetime.now().astimezone()
                    - datetime.timedelta(minutes=minutes_ago)).isoformat()
        for entry in entries:
            if n is None or entry["n"] == n:
                entry["finished"] = finished
        (self.log_dir / session_id / "ledger.jsonl").write_text(
            "".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8",
        )

    def seed_window_history(self, timestamps, last_activity=None):
        path = self.log_dir / "S"
        path.mkdir(exist_ok=True)
        entries = [dict(
            n=n, trigger="stakes", status="ok",
            packet_sha256=senior.packet_sha256(packet(question="How should pool 1 be bounded?")),
            **timestamps,
        ) for n in (1, 2, 3)]
        ledger = path / "ledger.jsonl"
        ledger.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
        if last_activity is not None:
            (path / "session.json").write_text(json.dumps({"last_activity": last_activity}), encoding="utf-8")
        return ledger

    def assert_window_budget_exhausted(self):
        flags = ["--max-consultations", "3", "--confusion-reserve", "0"]
        report = self.status("T", flags=flags)
        self.assertEqual(report["counts"]["autonomous"], 3)
        self.assertEqual(report["window_sessions"], 1)
        self.assertEqual(report["remaining"], {"general": 0, "confusion": 0})
        self.assertNotIn("unreadable_entries", report)
        code, stdout, stderr = self.consult("T", number=4, flags=flags)
        self.assertEqual((code, stdout), (4, ""))
        self.assertIn("budget 3, used 3: budget exhausted", stderr)
        self.runner.assert_not_called()

    def test_window_budget_counts_valid_finished(self):
        self.seed_window_history({"finished": senior.local_timestamp()})
        self.assert_window_budget_exhausted()

    def test_window_budget_counts_missing_finished(self):
        self.seed_window_history({})
        self.assert_window_budget_exhausted()

    def test_window_budget_counts_unparseable_finished(self):
        self.seed_window_history({"finished": "not-a-timestamp"})
        self.assert_window_budget_exhausted()

    def test_window_budget_counts_offset_naive_finished(self):
        self.seed_window_history({"finished": "2026-09-10T12:00:00"})
        self.assert_window_budget_exhausted()

    def test_undatable_entries_age_out_by_session_activity(self):
        old = datetime.datetime.now().astimezone() - datetime.timedelta(minutes=241)
        self.seed_window_history({}, last_activity=old.isoformat())
        report = self.status()
        self.assertEqual(report["window_sessions"], 0)
        self.assertEqual(report["counts"]["autonomous"], 0)
        self.assertEqual(report["next_allowed_in"], 0)
        # A recent file mtime must not override valid, old session metadata.
        self.assertEqual(self.consult(flags=[
            "--max-consultations", "3", "--confusion-reserve", "0", "--min-interval-seconds", "120",
        ])[0], 0)
        self.runner.assert_called_once()

    def test_undatable_entries_age_out_by_ledger_mtime(self):
        ledger = self.seed_window_history({})
        old = (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=241)).timestamp()
        os.utime(ledger, (old, old))
        self.assertFalse((ledger.parent / "session.json").exists())
        report = self.status()
        self.assertEqual(report["window_sessions"], 0)
        self.assertEqual(report["counts"]["autonomous"], 0)
        self.assertEqual(report["next_allowed_in"], 0)
        self.assertEqual(self.consult(flags=[
            "--max-consultations", "3", "--confusion-reserve", "0", "--min-interval-seconds", "120",
        ])[0], 0)
        self.runner.assert_called_once()

    def test_started_fallback_precedes_metadata_and_mtime(self):
        old = datetime.datetime.now().astimezone() - datetime.timedelta(minutes=241)
        for finished in (None, "bad", "2026-09-10T12:00:00", 123):
            with self.subTest(finished=finished):
                ledger = self.seed_window_history(
                    {"finished": finished, "started": senior.local_timestamp()}, old.isoformat(),
                )
                os.utime(ledger, (old.timestamp(), old.timestamp()))
                self.assert_window_budget_exhausted()

    def test_newest_entry_activity_keeps_mixed_history_in_window(self):
        old = datetime.datetime.now().astimezone() - datetime.timedelta(minutes=241)
        ledger = self.seed_window_history({"finished": old.isoformat()}, old.isoformat())
        entries = self.history()
        entries[1]["finished"] = senior.local_timestamp()
        entries[2]["finished"] = "bad"
        ledger.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
        os.utime(ledger, (old.timestamp(), old.timestamp()))
        self.assert_window_budget_exhausted()

    def test_invalid_metadata_falls_back_to_old_mtime(self):
        old = (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=241)).timestamp()
        for activity in ("bad", "2026-09-10T12:00:00", 123):
            with self.subTest(last_activity=activity):
                ledger = self.seed_window_history({}, last_activity=activity)
                os.utime(ledger, (old, old))
                report = self.status()
                self.assertEqual(report["window_sessions"], 0)
                self.assertEqual(report["next_allowed_in"], 0)

    def test_duplicate_and_interval_share_undatable_window_with_budget(self):
        for source in ("mtime", "metadata"):
            with self.subTest(source=source):
                ledger = self.seed_window_history({"finished": "bad"})
                if source == "metadata":
                    (ledger.parent / "session.json").write_text(json.dumps({
                        "last_activity": senior.local_timestamp(),
                    }), encoding="utf-8")
                    old = (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=241)).timestamp()
                    os.utime(ledger, (old, old))
                self.assert_window_budget_exhausted()
                flags = ["--max-consultations", "4", "--confusion-reserve", "0"]
                code, stdout, stderr = self.consult("T", flags=flags)
                self.assertEqual((code, stdout), (4, ""))
                self.assertIn("Duplicate question matches entry S/A1", stderr)
                flags.extend(["--min-interval-seconds", "120"])
                code, stdout, stderr = self.consult("T", number=4, flags=flags)
                self.assertEqual((code, stdout), (4, ""))
                self.assertIn("Minimum consultation interval:", stderr)
                report = self.status("T", flags=flags)
                self.assertEqual(report["counts"]["autonomous"], 3)
                self.assertGreater(report["next_allowed_in"], 0)
                self.assertLessEqual(report["next_allowed_in"], 120)
                self.runner.assert_not_called()

    def test_unavailable_activity_time_keeps_history_in_window(self):
        ledger = self.seed_window_history({})
        original_stat = Path.stat

        def stat_without_ledger(path, *args, **kwargs):
            if path == ledger:
                raise OSError("synthetic unavailable mtime")
            return original_stat(path, *args, **kwargs)

        with mock.patch.object(Path, "stat", stat_without_ledger):
            self.assert_window_budget_exhausted()
            flags = ["--max-consultations", "4", "--confusion-reserve", "0"]
            code, stdout, stderr = self.consult("T", flags=flags)
            self.assertEqual((code, stdout), (4, ""))
            self.assertIn("Duplicate question matches entry S/A1", stderr)
            code, stdout, stderr = self.consult("T", number=4, flags=[
                *flags, "--min-interval-seconds", "120",
            ])
            self.assertEqual((code, stdout), (4, ""))
            self.assertIn("Minimum consultation interval: 120 seconds remaining", stderr)
            self.runner.assert_not_called()

    def test_session_rotation_does_not_restore_budget_but_another_root_does(self):
        flags = ["--max-consultations", "2", "--confusion-reserve", "0"]
        for number in (1, 2):
            self.assertEqual(self.consult(number=number, flags=flags)[0], 0)
        for session_id in ("S", "S-NEW", None):
            with self.subTest(session_id=session_id):
                code, stdout, stderr = self.consult(session_id, number=3, flags=flags)
                self.assertEqual((code, stdout), (4, ""))
                self.assertIn("budget 2, used 2: budget exhausted", stderr)
        other_root = self.log_dir / "separate-root"
        self.assertEqual(self.consult(flags=[*flags, "--log-dir", str(other_root)])[0], 0)
        self.assertEqual(self.runner.call_count, 3)

    def test_idle_session_is_excluded_even_when_explicitly_selected(self):
        flags = ["--max-consultations", "1"]
        self.assertEqual(self.consult(flags=flags)[0], 0)
        self.set_finished("S", 11)
        with mock.patch.dict(os.environ, {"SENIOR_SESSION_IDLE_MINUTES": "10"}):
            report = self.status(flags=flags)
            self.assertEqual(report["window_minutes"], 10)
            self.assertEqual(report["window_sessions"], 0)
            self.assertEqual(report["counts"]["autonomous"], 0)
            # Expiry clears both the window budget and the answered-packet guard.
            self.assertEqual(self.consult(flags=flags)[0], 0)

    def test_active_session_contributes_its_entire_history(self):
        self.assertEqual(self.consult()[0], 0)
        self.assertEqual(self.consult(number=2)[0], 0)
        self.set_finished("S", 241, n=1)
        self.assertEqual(self.status()["counts"]["autonomous"], 2)
        self.assertEqual(self.status()["window_sessions"], 1)

    def test_duplicate_guard_spans_sessions_and_names_answer(self):
        self.assertEqual(self.consult(trigger="user")[0], 0)
        for session_id in ("T", None):
            code, stdout, stderr = self.consult(session_id, trigger="user")
            self.assertEqual((code, stdout), (4, ""))
            self.assertIn("Duplicate question matches entry S/A1", stderr)
            self.assertIn(str(self.log_dir / "S" / "ledger.md"), stderr)
        self.assertEqual(self.status("T")["refusals"]["last"]["reason"], "duplicate")
        self.assertEqual(self.runner.call_count, 1)

    def test_interval_uses_newest_finished_across_window_including_user_and_failure(self):
        self.assertEqual(self.consult(trigger="user")[0], 0)
        self.assertEqual(self.consult(trigger="user", number=2)[0], 0)
        self.set_finished("S", 3)
        self.runner.side_effect = senior.SeniorError("synthetic outage")
        self.assertEqual(self.consult("T", trigger="user", number=3)[0], 2)
        self.runner.side_effect = None
        flags = ["--min-interval-seconds", "120"]
        for session_id in ("S", "U", None):
            code, stdout, stderr = self.consult(session_id, number=4, flags=flags)
            self.assertEqual((code, stdout), (4, ""))
            self.assertIn("Minimum consultation interval:", stderr)
        report = self.status(flags=flags)
        self.assertEqual(report["refusals"]["last"]["reason"], "min_interval")
        self.assertGreater(report["next_allowed_in"], 115)
        self.assertLessEqual(report["next_allowed_in"], 120)
        self.assertEqual(report["window_sessions"], 2)
        self.assertEqual(self.consult("U", trigger="confusion", number=4, flags=flags)[0], 0)

    def test_reserve_protects_confusion_without_increasing_total(self):
        flags = ["--max-consultations", "3", "--confusion-reserve", "1"]
        for number in (1, 2):
            self.assertEqual(self.consult(number=number, flags=flags)[0], 0)
        code, stdout, stderr = self.consult(number=3, flags=flags)
        self.assertEqual((code, stdout), (4, ""))
        self.assertIn("remaining calls are held for confusion", stderr)
        self.assertIn("Stop and ask the user; do not proceed alone.", stderr)
        report = self.status(flags=flags)
        self.assertEqual(report["remaining"], {"general": 0, "confusion": 1})
        self.assertEqual(report["refusals"]["last"]["reason"], "reserve_only")
        self.assertEqual(self.consult(trigger="confusion", number=3, flags=flags)[0], 0)
        code, stdout, stderr = self.consult(trigger="confusion", number=4, flags=flags)
        self.assertEqual((code, stdout), (4, ""))
        self.assertIn("budget 3, used 3: budget exhausted", stderr)
        self.assertEqual(self.runner.call_count, 3)
        self.assertEqual(self.status(flags=flags)["remaining"], {"general": 0, "confusion": 0})
        self.assertEqual(self.status(flags=flags)["refusals"]["last"]["reason"], "budget_exhausted")

    def test_confusion_spends_general_pool_first_and_is_not_an_exemption(self):
        flags = ["--max-consultations", "3", "--confusion-reserve", "1"]
        self.assertEqual(self.consult(trigger="confusion", flags=flags)[0], 0)
        self.assertEqual(self.status(flags=flags)["remaining"], {"general": 1, "confusion": 2})
        self.assertEqual(self.consult(number=2, flags=flags)[0], 0)
        self.assertEqual(self.consult(number=3, flags=flags)[0], 4)
        self.assertEqual(self.consult(trigger="confusion", number=3, flags=flags)[0], 0)
        self.assertEqual(self.consult("T", trigger="confusion", number=4, flags=flags)[0], 4)

    def test_default_reserve_with_budget_two_keeps_confusion_available(self):
        flags = ["--max-consultations", "2"]
        self.assertEqual(self.consult(flags=flags)[0], 0)
        code, stdout, stderr = self.consult(trigger="planning", number=2, flags=flags)
        self.assertEqual((code, stdout), (4, ""))
        self.assertIn("remaining calls are held for confusion", stderr)
        self.assertIn("Proceed without a senior, or ask the user to raise the budget.", stderr)
        self.assertEqual(self.consult(trigger="confusion", number=2, flags=flags)[0], 0)
        self.assertEqual(self.consult(trigger="confusion", number=3, flags=flags)[0], 4)

    def test_reserve_clamp_preserves_one_general_call(self):
        flags = ["--max-consultations", "1", "--confusion-reserve", "1"]
        self.assertEqual(self.status(flags=flags)["policy"]["confusion_reserve"], 0)
        self.assertEqual(self.consult(flags=flags)[0], 0)
        self.assertEqual(self.consult(trigger="confusion", number=2, flags=flags)[0], 4)

    def test_refusals_increment_without_ledger_and_survive_provider_attempt(self):
        flags = ["--policy", "off"]
        for count in (1, 2):
            self.assertEqual(self.consult(flags=flags)[0], 4)
            metadata = json.loads((self.log_dir / "S" / "session.json").read_text())
            self.assertEqual(metadata["refusals"]["count"], count)
            self.assertEqual(metadata["refusals"]["last"]["reason"], "trigger_not_allowed")
            self.assertEqual(metadata["refusals"]["last"]["trigger"], "stakes")
            self.assertIsNotNone(datetime.datetime.fromisoformat(metadata["refusals"]["last"]["at"]).utcoffset())
            self.assertEqual(self.status()["refusals"], metadata["refusals"])
            self.assertEqual(self.status()["window_sessions"], 0)
            self.assertFalse((self.log_dir / "S" / "ledger.jsonl").exists())
            self.assertFalse((self.log_dir / "S" / "ledger.md").exists())
        self.assertEqual(self.consult(trigger="user", flags=flags)[0], 0)
        self.assertEqual(self.status()["refusals"], metadata["refusals"])
        self.assertEqual(len(self.history()), 1)

    def test_refusal_write_failure_preserves_message_and_exit_code(self):
        flags = ["--policy", "off"]
        expected = self.consult(flags=flags)
        with mock.patch.object(senior, "write_json_object", side_effect=OSError("synthetic disk error")):
            self.assertEqual(self.consult(flags=flags), expected)
        self.assertEqual(self.status()["refusals"]["count"], 1)
        self.assertEqual(self.history(), [])

    def test_first_refusal_is_visible_via_auto_detected_status(self):
        code, stdout, stderr = self.run_main(["--trigger", "confusion", "--policy", "off"], packet())
        self.assertEqual((code, stdout), (4, ""))
        code, stdout, stderr = self.run_main(["--status", "--log-dir", str(self.log_dir)], "")
        self.assertEqual((code, stderr), (0, ""))
        report = json.loads(stdout)
        self.assertEqual(report["refusals"]["count"], 1)
        self.assertEqual(report["refusals"]["last"]["reason"], "trigger_not_allowed")
        self.assertEqual(report["counts"]["total"], 0)

    def test_failure_guard_is_per_session_and_new_session_recovers(self):
        self.runner.side_effect = senior.SeniorError("synthetic outage")
        for number in (1, 2):
            self.assertEqual(self.consult(trigger="user", number=number)[0], 2)
        self.runner.side_effect = None
        code, stdout, stderr = self.consult(trigger="user", number=3)
        self.assertEqual((code, stdout), (4, ""))
        self.assertIn("--new-session", stderr)
        self.assertEqual(self.status()["refusals"]["last"]["reason"], "repeated_failure")
        self.assertEqual(len(self.history()), 2)
        self.assertEqual(self.consult(None, trigger="user", number=3)[0], 0)
        self.assertEqual(self.consult("T", trigger="user", number=4)[0], 0)
        self.assertEqual(self.status()["counts"]["total"], 4)
        self.assertEqual(self.status()["window_sessions"], 3)

    def test_unreadable_entries_are_skipped_but_undatable_entries_count(self):
        self.assertEqual(self.consult()[0], 0)
        for name, body in (("bad-json", "{\n"), ("bad-date", '{"n":1,"finished":"bad"}\n'),
                           ("naive-date", '{"n":1,"finished":"2026-09-10T12:00:00"}\n'),
                           ("bad-bytes", None), ("unreadable", "")):
            path = self.log_dir / name
            path.mkdir()
            (path / "ledger.jsonl").write_bytes(body.encode() if body is not None else b"\xff\n")
        original = senior.read_ledger

        def read_with_unreadable(path):
            if path.parent.name == "unreadable":
                raise PermissionError("synthetic unreadable session")
            return original(path)

        with mock.patch.object(senior, "read_ledger", side_effect=read_with_unreadable):
            report = self.status("unreadable")
            self.assertEqual(report["counts"]["autonomous"], 3)
            self.assertEqual(report["window_sessions"], 3)
            self.assertEqual(report["unreadable_entries"], 2)
            self.assertEqual(self.consult("T", number=2)[0], 0)

    def test_local_modes_and_disabled_logging_create_no_directories(self):
        self.enterContext(mock.patch.object(senior.shutil, "which", return_value="/synthetic/codex"))
        for mode in ("--lint", "--doctor", "--dry-run", "--status"):
            root = self.log_dir / mode.removeprefix("--")
            code, stdout, stderr = self.run_main([mode, "--log-dir", str(root), "--new-session"], packet())
            self.assertEqual((code, stderr), (0, ""))
            self.assertFalse(root.exists())
        root = self.log_dir / "disabled"
        self.assertEqual(self.consult(flags=["--log-dir", str(root), "--no-ledger", "--policy", "off"])[0], 4)
        self.assertFalse(root.exists())
        self.runner.assert_not_called()

    def test_reserve_defaults_and_precedence(self):
        for level, reserve in (("off", 0), ("rare", 1), ("normal", 1), ("eager", 2)):
            self.assertEqual(self.status(flags=["--policy", level])["policy"]["confusion_reserve"], reserve)
        config = self.log_dir / "policy.json"
        config.write_text(json.dumps({"max_consultations": 8, "confusion_reserve": "+3"}))
        self.assertEqual(self.status()["policy"]["confusion_reserve"], 3)
        with mock.patch.dict(os.environ, {"SENIOR_CONFUSION_RESERVE": "4"}):
            self.assertEqual(self.status()["policy"]["confusion_reserve"], 4)
            self.assertEqual(self.status(flags=["--confusion-reserve", "5"])["policy"]["confusion_reserve"], 5)
        self.assertEqual(self.status(flags=["--max-consultations", "0"])["policy"]["confusion_reserve"], 0)

    def test_invalid_reserve_is_rejected_at_each_source(self):
        for value in (-1, True, 1.5, "1.5", "nan", "inf", "", [], None):
            for source in ("file", "env", "flag"):
                with self.subTest(value=value, source=source):
                    config = self.log_dir / "policy.json"
                    config.unlink(missing_ok=True)
                    flags = ["--status", "--log-dir", str(self.log_dir)]
                    env = {}
                    if source == "file":
                        config.write_text(json.dumps({"confusion_reserve": value}))
                        flags.extend(["--confusion-reserve", "1"])
                    elif source == "env":
                        env["SENIOR_CONFUSION_RESERVE"] = str(value)
                    else:
                        flags.append(f"--confusion-reserve={value}")
                    with mock.patch.dict(os.environ, env):
                        code, stdout, stderr = self.run_main(flags, "")
                    self.assertEqual((code, stdout), (2, ""))
                    self.assertIn("confusion_reserve must be a non-negative integer", stderr)


if __name__ == "__main__":
    unittest.main()
