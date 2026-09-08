"""Offline contract tests for the senior-advisor broker (Python 3.12)."""

import importlib.util
import io
import json
import os
from pathlib import Path
import sys
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
                    ["--allow-narrative", "--json"], packet("my project uses a pool.")
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
        code, stdout, stderr = self.run_main([], text)
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


if __name__ == "__main__":
    unittest.main()
