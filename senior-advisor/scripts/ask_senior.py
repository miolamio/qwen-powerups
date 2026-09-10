#!/usr/bin/env python3
"""Ask a configured senior model without giving it the current repository.

The program is intentionally dependency-free. It supports subscription-backed
agent CLIs and Z.AI's HTTP API, rejects likely secrets by default, and executes
CLI providers in a fresh temporary working directory.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple


DEFAULT_PROVIDER_ORDER = "codex,claude,grok,kimi,zai"
DEFAULT_MAX_INPUT_BYTES = 32 * 1024
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_LOG_DIR = ".senior-advisor"
DEFAULT_SESSION_IDLE_MINUTES = 240
LEDGER_LOCK_TIMEOUT_SECONDS = 10
LEDGER_LOCK_STALE_SECONDS = 60
LEDGER_LOCK_RETRY_SECONDS = 0.05
SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}")
TRIGGER_DESCRIPTIONS = {
    "user": "the user asked for a senior in this turn",
    "stakes": "irreversible or high blast radius",
    "failure": "two materially different approaches already failed",
    "assumption": "a consequential plan rests on an assumption that cannot be checked locally",
    "confusion": "stuck, self-contradictory, or unable to choose between approaches",
    "planning": "decomposition of a complex task",
}
POLICY_TRIGGERS = {
    "off": (),
    "rare": ("stakes", "confusion"),
    "normal": ("stakes", "failure", "assumption", "confusion", "planning"),
    "eager": ("stakes", "failure", "assumption", "confusion", "planning"),
}
POLICY_BUDGETS = {"off": 0, "rare": 2, "normal": 5, "eager": 12}
POLICY_INTERVALS = {"off": 0, "rare": 300, "normal": 60, "eager": 0}
POLICY_CONFUSION_RESERVES = {"off": 0, "rare": 1, "normal": 1, "eager": 2}
MAX_QUESTION_BYTES = 300
MAX_QUESTION_LINES = 2
MAX_PROSE_BYTES = 1200
MAX_PLAN_PROSE_BYTES = 1600
MAX_EVIDENCE_LINES = 100
MAX_PACKET_BYTES = 8192
ADVICE_HEADERS = ("Question", "Stack", "Given", "Constraints", "Ruled out", "Evidence")
PLAN_HEADERS = ("Objective", "Stack", "Given", "Constraints", "Unknowns", "Done when")
PACKET_HEADERS = ADVICE_HEADERS
HEADERS_BY_KIND = {"advice": ADVICE_HEADERS, "plan": PLAN_HEADERS}
TERMINAL_SECTIONS = {"advice": "Evidence", "plan": None}
ALL_HEADERS = tuple(dict.fromkeys(ADVICE_HEADERS + PLAN_HEADERS))
HEADER_PATTERN = re.compile(
    r"^\s*(" + "|".join(ALL_HEADERS) + r")\s*:", re.IGNORECASE
)

NARRATIVE_PATTERNS: Sequence[Tuple[str, re.Pattern[str]]] = (
    (
        "task narration",
        re.compile(
            r"""\b(?:
                (?:I(?:['’](?:m|ve|ll|d))?|we(?:['’](?:re|ve|ll|d))?)
                (?:\s+(?:am|are|was|were|have|had|been|be|will|would)){0,3}
                (?:\s+\w+){0,2}\s+
                (?:work(?:s|ed|ing)?|build(?:s|ing)?|built|implement(?:s|ed|ing)?
                    |writes?|writing|written|wrote|try|tries|tried|trying
                    |develop(?:s|ed|ing)?|refactor(?:s|ed|ing)?|debug(?:s|ged|ging)?
                    |migrat(?:e[sd]?|ing)|fix(?:es|ed|ing)?|add(?:s|ed|ing)?
                    |test(?:s|ed|ing)?|creat(?:e[sd]?|ing)|updat(?:e[sd]?|ing)
                    |port(?:s|ed|ing)?)
                |(?:I|we)(?:\s+\w+){0,2}\s+(?:(?:need|want|plan)\s+to|will|tried|already)
                |(?:I|we)['’]ll
                |(?:my|our)\s+(?:task|goal|job|assignment|project|app|repo|repository
                    |codebase|code|service|plan|approach|hypothesis|team|product|case)
                |in\s+(?:my|our)\s+\w+
                |(?:я|мы)(?:\s+\w+){0,2}\s+(?:работа|дела|пиш|реализу|пыта
                    |разрабатыва|рефактор|отлажива|правл|чин|добавля|тестиру|перенош
                    |внедря|интегриру|собира|исправля)\w*
                |я(?:\s+\w+){0,2}\s+(?:попробовал|пытался|сделал|начал|решил
                    |планиру|буду|собираюсь|хочу|должен)\w*
                |мне\s+(?:нужно|надо|требуется)
                |(?:мо[йяеёюи]\w*|наш\w*)\s+(?:задач|цел|проект|код|репозитори
                    |приложени|сервис|план|подход|гипотез|команд|случа)\w*
                |в\s+(?:мо[её]м|нашем)\s+\w+
            )\b""",
            re.IGNORECASE | re.VERBOSE,
        ),
    ),
    (
        "requester",
        re.compile(
            r"""\b(?:
                the\s+(?:user|client|customer|manager|product\s+owner)\s+
                    (?:asked|wanted|wants|requested|requires|needs|told|said|expects)
                |(?:I|we)(?:\s+\w+){0,2}\s+(?:was|were)\s+(?:told|asked|instructed)
                |пользовател\w*\s+(?:прос|попросил|хоч|требу|сказал|жд[её]т|нужн)\w*
                |(?:меня|нас)\s+попросил\w*
            )\b""",
            re.IGNORECASE | re.VERBOSE,
        ),
    ),
    (
        "self-identification",
        re.compile(
            r"""\b(?:
                (?:as\s+an?|I\s+am\s+an?|I['’]m\s+an?)\s+
                    (?:ai|agent|assistant|llm|model|bot|coding\s+agent)
                |как\s+(?:ИИ|агент\w*|ассистент\w*|модел\w*|бот\w*)
                |я\s+(?:[—–-]\s*)?(?:агент|ассистент|модел)\w*
            )\b""",
            re.IGNORECASE | re.VERBOSE,
        ),
    ),
    (
        "session history",
        re.compile(
            r"""\b(?:
                (?:earlier|previously|so\s+far|until\s+now|initially|at\s+first|next|then)
                    \s*,?\s+(?:I|we)
                |(?:I|we)(?:['’](?:m|re|ve|ll|d)|\s+(?:am|are|was|were|have|had|will))?
                    \s+(?:earlier|previously|so\s+far|until\s+now|initially|at\s+first)
                |I\s+will\s+write
                |(?:сейчас|потом|затем|сначала|ранее|уже)\s+(?:я|мы)
                |я\s+(?:планиру|буду)\w*
            )\b""",
            re.IGNORECASE | re.VERBOSE,
        ),
    ),
    (
        "context preamble",
        re.compile(
            r"""\b(?:
                (?:for|some|a\s+bit\s+of|brief)\s+(?:context|background)
                |to\s+give\s+you\s+context|let\s+me\s+explain
                |(?:для|немного)\s+контекст\w*|предыстори\w*|объясню|поясню
            )\b|^[^\S\r\n]*(?:Background|Context|Контекст)\s*:""",
            re.IGNORECASE | re.VERBOSE | re.MULTILINE,
        ),
    ),
    (
        "politeness",
        re.compile(
            r"""\b(?:
                thanks|thank\s+you|please\s+(?:help|advise)|apologies
                |(?:I['’]m|I\s+am)\s+sorry|sorry(?=\s+(?:for|about|to)\b|,)
                |any\s+(?:ideas|thoughts|advice|help|suggestions)
                |спасибо|пожалуйста|извин\w*|привет|здравствуй\w*|подскаж\w*|помоги(?:те)?
            )\b|^[^\S\r\n]*(?:hi|hello|hey|dear)\b""",
            re.IGNORECASE | re.VERBOSE | re.MULTILINE,
        ),
    ),
    (
        "answer prediction",
        re.compile(
            r"""\b(?:
                I\s+(?:expect|assume|guess|suspect|hope)\s*,?\s+
                    (?:you|that\s+you|the\s+(?:senior|reviewer))
                |you(?:\s+will|['’]ll)(?:\s+(?:probably|likely))?\s+
                    (?:say|answer|recommend|suggest)
                |you\s+(?:probably|likely)\s+(?:say|answer|recommend|suggest)
                |я\s+ожида\w*\s*,?\s*что\s+(?:ты|вы)
            )\b""",
            re.IGNORECASE | re.VERBOSE,
        ),
    ),
)


class SeniorError(RuntimeError):
    """A user-actionable broker error."""


class PolicyRefusal(SeniorError):
    """A consultation refused by policy or a history guard."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ConsultationPolicy:
    level: str
    max_consultations: int
    triggers: Sequence[str]
    source: str
    min_interval_seconds: float = 0
    confusion_reserve: int = 0


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    available: bool
    reason: str


@dataclass(frozen=True)
class LedgerSession:
    log_root: Path
    session_id: str
    enabled: bool
    window_minutes: float = DEFAULT_SESSION_IDLE_MINUTES

    @property
    def path(self) -> Path:
        return self.log_root / self.session_id


@dataclass(frozen=True)
class PacketLine:
    number: int
    offset: int
    section: Optional[str]
    text: str
    content: str
    is_header: bool


@dataclass(frozen=True)
class Finding:
    code: str
    line: Optional[int]
    section: Optional[str]
    match: str
    fix: str
    category: Optional[str] = None


SECRET_PATTERNS: Sequence[Tuple[str, re.Pattern[str]]] = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("xAI key", re.compile(r"\bxai-[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"(?<![A-Za-z0-9_-])AIza[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")),
    ("Stripe live secret/restricted key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")),
    (
        "JSON Web Token",
        re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?![A-Za-z0-9_-])"),
    ),
    (
        "Slack webhook URL",
        re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+"),
    ),
    (
        "URL with inline credentials",
        re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@<>'\"]+:[^\s/@<>'\"]+@[A-Za-z0-9][A-Za-z0-9.-]*"),
    ),
    ("Bearer token", re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/-]{12,}")),
    (
        "secret assignment",
        re.compile(
            r"(?im)^\s*(?:export\s+)?(?:[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD))"
            r"\s*[=:]\s*['\"]?[^\s'\"]{12,}"
        ),
    ),
)


ADVICE_INSTRUCTION = """You are a senior technical reviewer.
The packet is deliberately decontextualized; missing project background is by design.
Do not ask for the project, the task, or the repository, and do not request more context.
If the answer turns on a missing technical fact, name that fact and answer for each branch.
Give advice only. Do not use tools, access the filesystem, or modify files.
Separate facts from assumptions. Do not merely agree with the asker.
Return these sections in order: Verdict, Assumptions, Reasoning, Risks, Recommended next step, What would change the answer.
Be concrete and concise."""


PLAN_INSTRUCTION = """You are a senior technical reviewer.
The packet is deliberately decontextualized; missing project background is by design.
Do not ask for the project, the task, or the repository, and do not request more context.
If the answer turns on a missing technical fact, name that fact and answer for each branch.
Give advice only. Do not use tools, access the filesystem, or modify files.
Separate facts from assumptions. Do not merely agree with the asker.
Return these sections in order: Restated objective, Assumptions, Steps, Ordering and risks, Start here, What would change this plan.
Number and order the Steps. Every step must state both the action and the observable check proving it is done; the executor is a smaller model that needs the check, not only the instruction.
Mark any step that is irreversible, destructive, or needs human authorisation before it runs.
Use the fewest steps that reach the objective; do not pad.
If the constraints make the objective unreachable, say so in Restated objective and give the closest reachable objective instead.
Be concrete and concise."""

INSTRUCTIONS_BY_KIND = {"advice": ADVICE_INSTRUCTION, "plan": PLAN_INSTRUCTION}


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def detect_sensitive(text: str) -> List[str]:
    return [label for label, pattern in SECRET_PATTERNS if pattern.search(text)]


def detect_packet_kind(text: str) -> Optional[str]:
    first = next((line for line in text.splitlines() if line.strip()), "")
    header = HEADER_PATTERN.match(first)
    if header:
        opening = header.group(1).casefold()
        for kind, headers in HEADERS_BY_KIND.items():
            if opening == headers[0].casefold():
                return kind
    return None


def parse_packet(text: str, kind: str = "advice") -> List[PacketLine]:
    lines: List[PacketLine] = []
    section: Optional[str] = None
    terminal = TERMINAL_SECTIONS[kind]
    offset = 0
    for number, raw in enumerate(text.splitlines(keepends=True), 1):
        header = HEADER_PATTERN.match(raw) if terminal is None or section != terminal else None
        if header:
            section = next(name for name in ALL_HEADERS if name.lower() == header.group(1).lower())
        content = raw[header.end():] if header else raw
        lines.append(PacketLine(number, offset, section, raw, content, bool(header)))
        offset += len(raw)
    return lines


def prose_text(lines: Sequence[PacketLine], kind: str = "advice") -> str:
    terminal = TERMINAL_SECTIONS[kind]
    return "".join(line.text for line in lines if terminal is None or line.section != terminal)


def source_line(lines: Sequence[PacketLine], offset: int) -> PacketLine:
    return next(line for line in reversed(lines) if line.offset <= offset)


def sort_findings(findings: Sequence[Finding]) -> List[Finding]:
    return sorted(
        findings,
        key=lambda finding: (finding.line if finding.line is not None else sys.maxsize, finding.code),
    )


def check_packet(
    text: str, lines: Sequence[PacketLine], kind: Optional[str] = None,
) -> List[Finding]:
    findings: List[Finding] = []
    detected = detect_packet_kind(text)
    requested = kind
    kind = kind or detected or "advice"
    headers = HEADERS_BY_KIND[kind]
    opening = headers[0]
    terminal = TERMINAL_SECTIONS[kind]
    first = next((line for line in lines if line.text.strip()), None)
    if detected is None:
        findings.append(Finding(
            "unknown_opening", first.number if first else None, first.section if first else None,
            first.text.rstrip("\r\n") if first else "",
            "Start the packet with Question: for advice or Objective: for a plan.",
        ))
    elif requested is not None and requested != detected:
        findings.append(Finding(
            "kind_mismatch", first.number, first.section, first.text.rstrip("\r\n"),
            f"The packet looks like {detected}; use --kind {detected} or rewrite it as {requested}.",
        ))
    if first is None or not (first.is_header and first.section == opening):
        findings.append(Finding(
            f"{opening.lower()}_not_first", first.number if first else None, opening,
            first.text.rstrip("\r\n") if first else "", f"Start the packet with the {opening}: header.",
        ))
    present = {line.section for line in lines if line.is_header}
    for name in headers:
        if name not in present:
            findings.append(Finding("missing_header", None, name, "", f"Add the {name}: header."))

    for line in lines:
        if line.is_header and line.section not in headers:
            if kind == "plan":
                destination = {
                    "Question": "State the outcome in Objective: and move structural facts to Given:.",
                    "Ruled out": "Move eliminated options to Given:.",
                    "Evidence": "Move error text and structural facts to Given:.",
                }[line.section]
            else:
                destination = {
                    "Objective": "Put the question in Question: and structural facts in Given:.",
                    "Unknowns": "Put missing technical facts in Given:.",
                    "Done when": "Put acceptance criteria in Constraints: and structural facts in Given:.",
                }[line.section]
            findings.append(Finding(
                "unexpected_header", line.number, line.section, f"{line.section}:",
                f"{line.section}: is not a {kind} header. {destination}",
            ))

    question_lines = [line for line in lines if line.section == "Question"]
    if kind == "advice" and question_lines:
        question_content = "".join(line.content for line in question_lines)
        question = question_content.strip()
        number = question_lines[0].number
        if not question_lines[0].content.strip():
            findings.append(Finding(
                "question_empty", number, "Question", "", "Put the question text after Question:.",
            ))
        if not question.endswith("?"):
            findings.append(Finding(
                "question_no_mark", number, "Question", question, "End the question with a question mark.",
            ))
        if (
            len(question.encode("utf-8")) > MAX_QUESTION_BYTES
            or len(question_content.rstrip().splitlines()) > MAX_QUESTION_LINES
        ):
            findings.append(Finding(
                "question_too_long", number, "Question", "", "Limit the question to 300 bytes and 2 lines.",
            ))

    if kind == "plan":
        objective_lines = [line for line in lines if line.section == "Objective"]
        if objective_lines:
            objective_content = "".join(line.content for line in objective_lines)
            objective = objective_content.strip()
            number = objective_lines[0].number
            if not objective_lines[0].content.strip():
                findings.append(Finding(
                    "objective_empty", number, "Objective", "", "Put the objective text after Objective:.",
                ))
            if objective.endswith("?"):
                findings.append(Finding(
                    "objective_is_question", number, "Objective", objective,
                    "An objective states the outcome; a question belongs in an advice packet — "
                    "use --kind advice with the Question: header.",
                ))
            if (
                len(objective.encode("utf-8")) > MAX_QUESTION_BYTES
                or len(objective_content.rstrip().splitlines()) > MAX_QUESTION_LINES
            ):
                findings.append(Finding(
                    "objective_too_long", number, "Objective", "", "Limit the objective to 300 bytes and 2 lines.",
                ))
        done_lines = [line for line in lines if line.section == "Done when"]
        if done_lines and "".join(line.content for line in done_lines).strip().casefold() in {
            "", "none", "none.", "n/a", "нет", "нет.",
        }:
            findings.append(Finding(
                "done_when_empty", done_lines[0].number, "Done when", "",
                "State observable acceptance criteria in Done when: so completion can be checked.",
            ))

    prose = prose_text(lines, kind)
    prose_limit = MAX_PLAN_PROSE_BYTES if kind == "plan" else MAX_PROSE_BYTES
    if len(prose.encode("utf-8")) > prose_limit:
        findings.append(Finding(
            "prose_too_long", None, None, "",
            "Reduce all plan prose to at most 1600 bytes." if kind == "plan"
            else "Reduce everything before Evidence: to at most 1200 bytes.",
        ))
    evidence = [line for line in lines if line.section == "Evidence"]
    if kind == "advice" and len(evidence) > MAX_EVIDENCE_LINES:
        findings.append(Finding(
            "evidence_too_long", evidence[0].number, "Evidence", "", "Limit Evidence: and its contents to 100 lines.",
        ))
    if len(text.encode("utf-8")) > MAX_PACKET_BYTES:
        findings.append(Finding(
            "packet_too_long", None, None, "", "Reduce the whole packet to at most 8192 bytes.",
        ))
    # Treat inline section content as line-initial without changing source offsets.
    narrative_prose = "".join(
        " " * (len(line.text) - len(line.content)) + line.content
        for line in lines if terminal is None or line.section != terminal
    )
    for category, pattern in NARRATIVE_PATTERNS:
        fix = (
            "Remove the requester if they gave you the task; if they are an end user of the "
            "system under discussion, rename them to actor A or caller A and keep the technical facts."
            if category == "requester"
            else f"Remove the {category} and keep only technical facts."
        )
        for match in pattern.finditer(narrative_prose):
            line = source_line(lines, match.start())
            findings.append(Finding(
                "narrative", line.number, line.section, match.group().strip(), fix, category,
            ))
    return sort_findings(findings)


def sensitive_findings(text: str, lines: Sequence[PacketLine]) -> List[Finding]:
    findings: List[Finding] = []
    for category, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            line = source_line(lines, match.start())
            # Never echo a suspected secret into the lint report.
            findings.append(Finding(
                "sensitive", line.number, line.section, "", "Redact the suspected secret.", category,
            ))
    return findings


def gate_message(findings: Sequence[Finding]) -> str:
    messages = [f"Packet narrative gate rejected the packet with {len(findings)} finding(s):"]
    for finding in sort_findings(findings):
        label = f"{finding.code}/{finding.category}" if finding.category else finding.code
        number = str(finding.line) if finding.line is not None else "null"
        messages.append(
            f"  line {number} [{label}]: {json.dumps(finding.match, ensure_ascii=False)} -> {finding.fix}"
        )
    messages.append("Follow the six-header contract in SKILL.md and run --lint before sending.")
    return "\n".join(messages)


def provider_status(name: str) -> ProviderStatus:
    if name in {"codex", "claude", "grok", "kimi"}:
        binary = shutil.which(name)
        return ProviderStatus(name, bool(binary), binary or f"{name} not found on PATH")
    if name == "zai":
        ready = bool(os.environ.get("ZAI_API_KEY"))
        return ProviderStatus(name, ready, "ZAI_API_KEY is set" if ready else "ZAI_API_KEY is not set")
    return ProviderStatus(name, False, "unknown provider")


def parse_order(raw: str) -> List[str]:
    names: List[str] = []
    for item in raw.split(","):
        name = item.strip().lower()
        if name and name not in names:
            names.append(name)
    invalid = [name for name in names if name not in {"codex", "claude", "grok", "kimi", "zai"}]
    if invalid:
        raise SeniorError(f"Unknown provider(s) in order: {', '.join(invalid)}")
    if not names:
        raise SeniorError("Provider order is empty")
    return names


def select_provider(requested: str, order: Sequence[str]) -> str:
    if requested != "auto":
        status = provider_status(requested)
        if not status.available:
            raise SeniorError(f"Provider '{requested}' is unavailable: {status.reason}")
        return requested
    for name in order:
        if provider_status(name).available:
            return name
    details = "; ".join(f"{name}: {provider_status(name).reason}" for name in order)
    raise SeniorError(f"No senior provider is ready. {details}")


def resolve_model(provider: str, explicit: Optional[str]) -> Optional[str]:
    if explicit and explicit.strip():
        return explicit.strip()
    configured = os.environ.get(f"SENIOR_{provider.upper()}_MODEL", "").strip()
    if configured:
        return configured
    if provider == "zai":
        return os.environ.get("ZAI_MODEL", "glm-5.3").strip() or "glm-5.3"
    return None


def build_prompt(question: str, kind: str = "advice") -> str:
    return f"{INSTRUCTIONS_BY_KIND[kind]}\n\nCONSULTATION PACKET\n{question.strip()}\n"


def run_process(
    args: Sequence[str], prompt: str, timeout: int, cwd: Path, env: Optional[Dict[str, str]] = None
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.Popen(
            list(args),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cwd),
            env=env,
            start_new_session=os.name == "posix",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
    except OSError as exc:
        raise SeniorError(f"Could not start senior provider: {exc}") from exc
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
        else:
            proc.kill()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            # An escaped descendant may still hold the output pipes open.
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        raise SeniorError(f"Senior provider timed out after {timeout}s") from exc
    completed = subprocess.CompletedProcess(list(args), proc.returncode, stdout, stderr)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "no diagnostic output").strip()[-2000:]
        raise SeniorError(f"Senior provider exited with code {completed.returncode}: {detail}")
    return completed


def extract_json_text(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()

    def content_text(value: object) -> Optional[str]:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for key in ("result", "output", "response", "text", "message", "content"):
                found = content_text(value.get(key))
                if found:
                    return found
        if isinstance(value, list):
            pieces = [piece for item in value if (piece := content_text(item))]
            if pieces:
                return "\n".join(pieces)
        return None

    candidates: List[object] = []
    if isinstance(payload, dict):
        candidates.extend(payload.get(key) for key in ("result", "output", "response", "text", "message"))
        data = payload.get("data")
        if isinstance(data, dict):
            candidates.extend(data.get(key) for key in ("result", "output", "response", "text", "message"))
    elif isinstance(payload, list):
        candidates.append(payload)
    for candidate in candidates:
        answer = content_text(candidate)
        if answer:
            return answer
    return raw.strip()


def ask_codex(prompt: str, model: Optional[str], timeout: int, workdir: Path) -> str:
    output_path = workdir / "final.txt"
    # codex >= 0.151 has no --ask-for-approval flag; the read-only sandbox
    # already makes any mutating command impossible, so no approval flag is needed.
    args = [
        "codex",
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--output-last-message",
        str(output_path),
        "--cd",
        str(workdir),
    ]
    if model:
        args.extend(["--model", model])
    args.append("-")
    env = None
    codex_home = os.environ.get("SENIOR_CODEX_HOME")
    if codex_home:
        env = os.environ.copy()
        env["CODEX_HOME"] = codex_home
    run_process(args, prompt, timeout, workdir, env=env)
    if not output_path.is_file():
        raise SeniorError("Codex finished without writing its final message")
    answer = output_path.read_text(encoding="utf-8").strip()
    if not answer:
        raise SeniorError("Codex returned an empty answer")
    return answer


def ask_claude(prompt: str, model: Optional[str], timeout: int, workdir: Path) -> str:
    args = [
        "claude",
        "-p",
        "--safe-mode",
        "--restricted",
        "--no-session-persistence",
        "--permission-mode",
        "dontAsk",
        "--disallowedTools",
        "*",
        "--max-turns",
        "1",
        "--output-format",
        "json",
    ]
    if model:
        args.extend(["--model", model])
    completed = run_process(args, prompt, timeout, workdir)
    answer = extract_json_text(completed.stdout)
    if not answer:
        raise SeniorError("Claude returned an empty answer")
    return answer


def ask_grok(prompt: str, model: Optional[str], timeout: int, workdir: Path) -> str:
    prompt_path = workdir / "prompt.txt"
    with os.fdopen(
        os.open(prompt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8"
    ) as prompt_file:
        prompt_file.write(prompt)
    args = [
        "grok",
        "--no-auto-update",
        "--prompt-file",
        str(prompt_path),
        "--cwd",
        str(workdir),
        "--output-format",
        "json",
        "--disallowed-tools",
        "*",
        "--max-turns",
        "1",
        "--no-subagents",
        "--no-memory",
        "--disable-web-search",
    ]
    for denied_tool in ("Bash", "Edit", "Read", "Grep", "MCPTool", "WebFetch", "WebSearch"):
        args.extend(["--deny", denied_tool])
    if model:
        args.extend(["--model", model])
    completed = run_process(args, "", timeout, workdir)
    answer = extract_json_text(completed.stdout)
    if not answer:
        raise SeniorError("Grok returned an empty answer")
    return answer


def write_kimi_agent(path: Path) -> None:
    path.write_text(
        """---
name: senior-consultant
description: Consultation-only senior reviewer with no tools
tools: []
disallowedTools:
  - Bash
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Agent
  - WebSearch
  - FetchURL
---
Give advice from the supplied prompt only. Never call tools or access files.
""",
        encoding="utf-8",
    )


def ask_kimi(prompt: str, model: Optional[str], timeout: int, workdir: Path) -> str:
    agent_path = workdir / "senior-consultant.md"
    write_kimi_agent(agent_path)
    skills_path = workdir / "skills"
    skills_path.mkdir()
    # Prompt exposure in argv is a measured Kimi CLI limitation, not an oversight.
    args = [
        "kimi",
        "-p",
        prompt,
        "--agent-file",
        str(agent_path),
        "--skills-dir",
        str(skills_path),
        "--output-format",
        "text",
    ]
    if model:
        args.extend(["--model", model])
    env = os.environ.copy()
    env.setdefault("KIMI_CODE_EXPERIMENTAL_FLAG", "1")
    completed = run_process(args, "", timeout, workdir, env=env)
    answer = completed.stdout.strip()
    if not answer:
        raise SeniorError("Kimi returned an empty answer")
    return answer


def ask_zai(
    prompt: str, model: Optional[str], timeout: int, _workdir: Path, kind: str = "advice",
) -> str:
    api_key = os.environ.get("ZAI_API_KEY")
    if not api_key:
        raise SeniorError("ZAI_API_KEY is not set")
    url = os.environ.get("ZAI_CHAT_URL", "https://api.z.ai/api/paas/v4/chat/completions")
    selected_model = model or os.environ.get("ZAI_MODEL", "glm-5.3")
    body = json.dumps(
        {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": INSTRUCTIONS_BY_KIND[kind]},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "temperature": 1,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-2000:]
        raise SeniorError(f"Z.AI returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SeniorError(f"Z.AI request failed: {exc}") from exc
    try:
        answer = payload["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise SeniorError("Z.AI returned an unexpected response shape") from exc
    if not answer:
        raise SeniorError("Z.AI returned an empty answer")
    return answer


PROVIDER_RUNNERS: Dict[str, Callable[..., str]] = {
    "codex": ask_codex,
    "claude": ask_claude,
    "grok": ask_grok,
    "kimi": ask_kimi,
    "zai": ask_zai,
}


def local_timestamp() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def validate_session_id(session_id: str) -> str:
    if not SESSION_ID_PATTERN.fullmatch(session_id) or session_id == "." or ".." in session_id:
        raise SeniorError("Session id must match [A-Za-z0-9._-]{1,64}, must not be '.', and must not contain '..'")
    return session_id


def read_json_object(path: Path) -> Dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def consultation_budget(value: object, source: str, field: str = "max_consultations") -> int:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"\+?[0-9]+", value.strip()):
        try:
            return int(value)
        except ValueError:
            pass
    raise SeniorError(f"{source}: {field} must be a non-negative integer")


def consultation_interval(value: object, source: str) -> float:
    if type(value) in {int, float, str}:
        try:
            interval = float(value)
            if 0 <= interval < float("inf"):
                return interval
        except (ValueError, OverflowError):
            pass
    raise SeniorError(f"{source}: min_interval_seconds must be a non-negative finite number")


def resolve_policy(args: argparse.Namespace, log_root: Path) -> ConsultationPolicy:
    path = log_root / "policy.json"
    config: Dict[str, object] = {}
    source = "default"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except (OSError, ValueError, RecursionError) as exc:
        raise SeniorError(f"{path}: could not read valid policy JSON: {exc}") from exc
    else:
        if not isinstance(config, dict):
            raise SeniorError(f"{path}: policy must be a JSON object")
        unknown = config.keys() - {
            "level", "max_consultations", "triggers", "min_interval_seconds", "confusion_reserve",
        }
        if unknown:
            raise SeniorError(f"{path}: unknown policy key(s): {', '.join(sorted(unknown))}")
        if "level" in config and (
            not isinstance(config["level"], str) or config["level"] not in POLICY_TRIGGERS
        ):
            raise SeniorError(f"{path}: unknown policy level: {config['level']!r}")
        if "max_consultations" in config:
            config["max_consultations"] = consultation_budget(config["max_consultations"], str(path))
        if "confusion_reserve" in config:
            config["confusion_reserve"] = consultation_budget(
                config["confusion_reserve"], str(path), "confusion_reserve",
            )
        if "min_interval_seconds" in config:
            config["min_interval_seconds"] = consultation_interval(config["min_interval_seconds"], str(path))
        if "triggers" in config:
            if not isinstance(config["triggers"], list):
                raise SeniorError(f"{path}: triggers must be a list of trigger names")
            for trigger in config["triggers"]:
                if not isinstance(trigger, str) or trigger not in TRIGGER_DESCRIPTIONS:
                    raise SeniorError(f"{path}: unknown trigger name: {trigger!r}")

    level = config.get("level", "normal")
    if "level" in config:
        source = "file"
    if args.policy is not None:
        level, source = args.policy, "flag"
    elif "SENIOR_POLICY" in os.environ:
        level, source = os.environ["SENIOR_POLICY"], "env"
    if level not in POLICY_TRIGGERS:
        raise SeniorError(f"SENIOR_POLICY: unknown policy level: {level!r}; choose off, rare, normal, eager")

    budget = config.get("max_consultations", POLICY_BUDGETS[level])
    if args.max_consultations is not None:
        budget = consultation_budget(args.max_consultations, "--max-consultations")
    elif "SENIOR_MAX_CONSULTATIONS" in os.environ:
        budget = consultation_budget(os.environ["SENIOR_MAX_CONSULTATIONS"], "SENIOR_MAX_CONSULTATIONS")
    interval = config.get("min_interval_seconds", POLICY_INTERVALS[level])
    if args.min_interval_seconds is not None:
        interval = consultation_interval(args.min_interval_seconds, "--min-interval-seconds")
    elif "SENIOR_MIN_INTERVAL_SECONDS" in os.environ:
        interval = consultation_interval(os.environ["SENIOR_MIN_INTERVAL_SECONDS"], "SENIOR_MIN_INTERVAL_SECONDS")
    reserve = config.get("confusion_reserve", POLICY_CONFUSION_RESERVES[level])
    if args.confusion_reserve is not None:
        reserve = consultation_budget(args.confusion_reserve, "--confusion-reserve", "confusion_reserve")
    elif "SENIOR_CONFUSION_RESERVE" in os.environ:
        reserve = consultation_budget(
            os.environ["SENIOR_CONFUSION_RESERVE"], "SENIOR_CONFUSION_RESERVE", "confusion_reserve",
        )
    reserve = min(reserve, max(0, budget - 1))
    triggers = POLICY_TRIGGERS[level]
    if source in {"file", "default"}:
        triggers = tuple(dict.fromkeys(config.get("triggers", triggers)))
    return ConsultationPolicy(level, budget, triggers, source, interval, reserve)


def resolve_session(args: argparse.Namespace) -> LedgerSession:
    raw_root = args.log_dir if args.log_dir is not None else os.environ.get("SENIOR_LOG_DIR", DEFAULT_LOG_DIR)
    log_root = Path(raw_root or DEFAULT_LOG_DIR).resolve()
    enabled = not args.no_ledger and raw_root != ""
    explicit_id = args.session_id if args.session_id is not None else os.environ.get("SENIOR_SESSION_ID")
    if explicit_id is not None:
        validate_session_id(explicit_id)
    try:
        idle_minutes = float(os.environ.get("SENIOR_SESSION_IDLE_MINUTES", DEFAULT_SESSION_IDLE_MINUTES))
        if not 0 < idle_minutes < float("inf"):
            raise ValueError
    except ValueError as exc:
        raise SeniorError("SENIOR_SESSION_IDLE_MINUTES must be a positive number") from exc
    if explicit_id is not None and not args.new_session:
        return LedgerSession(log_root, explicit_id, enabled, idle_minutes)

    now = datetime.datetime.now().astimezone()
    current = read_json_object(log_root / "current.json")
    current_id = None
    try:
        candidate = current["id"]
        if isinstance(candidate, str):
            current_id = validate_session_id(candidate)
        last_activity = datetime.datetime.fromisoformat(current["last_activity"])
        if last_activity.utcoffset() is None:
            raise ValueError("Activity timestamp has no UTC offset")
        if not args.new_session and current_id and (now - last_activity).total_seconds() < idle_minutes * 60:
            return LedgerSession(log_root, current_id, enabled, idle_minutes)
    except (KeyError, TypeError, ValueError, SeniorError):
        pass

    # A forced reset in the same clock second must still get a fresh directory.
    while True:
        session_id = now.strftime("%Y%m%d-%H%M%S")
        try:
            exists = (log_root / session_id).exists()
        except OSError:
            # Defer an inaccessible log root to the best-effort write after the consultation.
            exists = False
        if session_id != current_id and not exists:
            break
        now += datetime.timedelta(seconds=1)
    return LedgerSession(log_root, session_id, enabled, idle_minutes)


def create_ledger_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        if not path.is_dir():
            raise
    else:
        if os.name == "posix":
            os.chmod(path, 0o700)


def ledger_file_opener(path: str, flags: int) -> int:
    return os.open(path, flags, 0o600)


def write_json_object(path: Path, value: Dict[str, object]) -> None:
    # Replace metadata atomically so readers never observe a partially written JSON object.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.close()
            if os.name == "posix":
                os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def start_new_session(session: LedgerSession) -> None:
    if not session.enabled:
        return
    try:
        create_ledger_directory(session.log_root)
        write_json_object(session.log_root / "current.json", {
            "id": session.session_id, "last_activity": local_timestamp(),
        })
    except Exception as exc:
        eprint(f"ask_senior: ledger write failed: {exc}")


def read_ledger(path: Path) -> Tuple[List[Dict[str, object]], int]:
    entries: List[Dict[str, object]] = []
    unreadable = 0
    try:
        stream = path.open("rb")
    except FileNotFoundError:
        return entries, unreadable
    with stream:
        for line in stream:
            try:
                entry = json.loads(line.decode("utf-8"))
                if not isinstance(entry, dict) or type(entry.get("n")) is not int or entry["n"] < 1:
                    raise ValueError("Invalid ledger entry number")
                entries.append(entry)
            except (ValueError, RecursionError):
                unreadable += 1
    return entries, unreadable


def ledger_counts(entries: Sequence[Dict[str, object]]) -> Dict[str, int]:
    return {
        "total": len(entries),
        "ok": sum(entry.get("status") == "ok" for entry in entries),
        "error": sum(entry.get("status") == "error" for entry in entries),
        "autonomous": sum(entry.get("trigger") != "user" for entry in entries),
        "user": sum(entry.get("trigger") == "user" for entry in entries),
    }


def session_activity(path: Path, entries: Sequence[Dict[str, object]]) -> Optional[datetime.datetime]:
    timestamps = []
    for entry in entries:
        for key in ("finished", "started"):
            try:
                timestamp = datetime.datetime.fromisoformat(entry[key])
                if timestamp.utcoffset() is not None:
                    timestamps.append(timestamp)
                    break
            except (KeyError, TypeError, ValueError, OverflowError):
                pass
    if timestamps:
        return max(timestamps)
    try:
        timestamp = datetime.datetime.fromisoformat(read_json_object(path / "session.json")["last_activity"])
        if timestamp.utcoffset() is not None:
            return timestamp
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    try:
        return datetime.datetime.fromtimestamp((path / "ledger.jsonl").stat().st_mtime, datetime.timezone.utc)
    except (OSError, ValueError, OverflowError):
        # Undatable history stays in the window and conservatively restarts the interval.
        return None


def read_session_entries(path: Path) -> Tuple[List[Dict[str, object]], int]:
    try:
        entries, unreadable = read_ledger(path / "ledger.jsonl")
    except OSError:
        return [], 0
    return sorted(entries, key=lambda entry: entry["n"]), unreadable


def read_window(session: LedgerSession) -> Tuple[List[Dict[str, object]], int, int]:
    entries: List[Dict[str, object]] = []
    sessions = unreadable = 0
    now = datetime.datetime.now().astimezone()
    try:
        paths = sorted(session.log_root.iterdir())
    except FileNotFoundError:
        return entries, sessions, unreadable
    except OSError as exc:
        raise SeniorError(f"Could not read log root {session.log_root}: {exc}") from exc
    for path in paths:
        try:
            if not path.is_dir() or path.is_symlink():
                continue
            validate_session_id(path.name)
        except (OSError, SeniorError):
            continue
        history, invalid = read_session_entries(path)
        unreadable += invalid
        if not history:
            continue
        newest = session_activity(path, history)
        if newest is not None and (now - newest).total_seconds() >= session.window_minutes * 60:
            continue
        # Membership is by session activity: include its entire history, not just recent rows.
        sessions += 1
        entries.extend(dict(entry, session_id=path.name, _activity=newest or now) for entry in history)
    return entries, sessions, unreadable


def remaining_consultations(entries: Sequence[Dict[str, object]], policy: ConsultationPolicy) -> Dict[str, int]:
    used = ledger_counts(entries)["autonomous"]
    return {
        "general": max(0, policy.max_consultations - policy.confusion_reserve - used),
        "confusion": max(0, policy.max_consultations - used),
    }


def next_allowed_in(
    entries: Sequence[Dict[str, object]], policy: ConsultationPolicy, trigger: Optional[str] = None,
) -> int:
    if not entries or policy.level == "off" or policy.min_interval_seconds == 0 or trigger in {"user", "confusion"}:
        return 0
    activity = max(entry["_activity"] for entry in entries)
    elapsed = (datetime.datetime.now().astimezone() - activity).total_seconds()
    return max(0, math.ceil(policy.min_interval_seconds - elapsed))


def check_consultation(
    session: LedgerSession, policy: ConsultationPolicy, trigger: str, packet: str,
    allow_repeat: bool = False,
) -> None:
    entries, _, _ = read_window(session)
    used = ledger_counts(entries)["autonomous"]
    if trigger != "user":
        reason = None
        reason_code = "trigger_not_allowed"
        if policy.level == "off":
            reason = "the level forbids autonomous consultations entirely; only --trigger user is available"
        elif trigger not in policy.triggers:
            reason = "trigger is not allowed"
        elif used >= policy.max_consultations:
            reason = "budget exhausted"
            reason_code = "budget_exhausted"
        elif trigger != "confusion" and used >= policy.max_consultations - policy.confusion_reserve:
            reason = "remaining calls are held for confusion"
            reason_code = "reserve_only"
        if reason:
            action = (
                "Stop and ask the user; do not proceed alone."
                if trigger in {"confusion", "stakes"}
                else "Proceed without a senior, or ask the user to raise the budget."
            )
            raise PolicyRefusal(
                reason_code,
                f"Policy '{policy.level}', trigger '{trigger}', budget {policy.max_consultations}, "
                f"used {used}: {reason}. {action}"
            )

    remaining = next_allowed_in(entries, policy, trigger)
    if remaining:
        raise PolicyRefusal(
            "min_interval",
            f"Minimum consultation interval: {remaining} seconds remaining; "
            f"interval in force is {policy.min_interval_seconds:g} seconds."
        )

    if not allow_repeat:
        digest = packet_sha256(packet)
        for entry in entries:
            if entry.get("status") == "ok" and entry.get("packet_sha256") == digest:
                raise PolicyRefusal(
                    "duplicate",
                    f"Duplicate question matches entry {entry['session_id']}/A{entry['n']}; "
                    f"read {session.log_root / entry['session_id'] / 'ledger.md'} instead of asking again."
                )
    # Outage recovery is deliberately per session: --new-session clears this guard,
    # while the log-root window still enforces budget, duplicates, and interval.
    entries, _ = read_session_entries(session.path)
    if len(entries) >= 2 and all(entry.get("status") == "error" for entry in entries[-2:]):
        failures = "; ".join(
            f"entry {entry['n']}: {str(entry.get('error', ''))[:200]}" for entry in entries[-2:]
        )
        raise PolicyRefusal(
            "repeated_failure",
            f"Repeated provider failure ({failures}). "
            "Run --doctor or start a fresh session with --new-session or a different --session-id."
        )


def session_status(
    session: LedgerSession, policy: ConsultationPolicy, trigger: Optional[str] = None,
) -> Dict[str, object]:
    entries, window_sessions, unreadable = read_window(session)
    metadata = read_json_object(session.path / "session.json")
    report = {
        "session_id": session.session_id,
        "path": str(session.path),
        "exists": session.path.is_dir(),
        "started": metadata.get("started"),
        "window_minutes": session.window_minutes,
        "window_sessions": window_sessions,
        "counts": ledger_counts(entries),
        "policy": policy.__dict__,
        "next_allowed_in": next_allowed_in(entries, policy, trigger),
        "remaining": remaining_consultations(entries, policy),
        "refusals": session_refusals(metadata),
        "entries": [
            {key: entry.get(key) for key in ("session_id", "n", "kind", "trigger", "status", "question", "finished")}
            for entry in entries
        ],
    }
    if unreadable:
        report["unreadable_entries"] = unreadable
    return report


def session_refusals(metadata: Dict[str, object]) -> Dict[str, object]:
    refusals = metadata.get("refusals")
    if (isinstance(refusals, dict) and type(refusals.get("count")) is int
            and refusals["count"] >= 0 and isinstance(refusals.get("last"), (dict, type(None)))):
        return {"count": refusals["count"], "last": refusals.get("last")}
    return {"count": 0, "last": None}


def write_refusal(session: LedgerSession, trigger: str, reason: str) -> None:
    if not session.enabled:
        return
    try:
        create_ledger_directory(session.log_root)
        create_ledger_directory(session.path)
        lock_path = session.path / ".ledger.lock"
        acquire_ledger_lock(lock_path)
        try:
            metadata = read_json_object(session.path / "session.json")
            entries, _ = read_session_entries(session.path)
            at = local_timestamp()
            refusals = session_refusals(metadata)
            metadata.update({
                "id": session.session_id,
                "started": metadata.get("started", entries[0].get("started") if entries else at),
                "last_activity": at,
                "counts": ledger_counts(entries),
                "refusals": {
                    "count": refusals["count"] + 1,
                    "last": {"at": at, "trigger": trigger, "reason": reason},
                },
            })
            write_json_object(session.path / "session.json", metadata)
            write_json_object(session.log_root / "current.json", {
                "id": session.session_id, "last_activity": at,
            })
        finally:
            lock_path.unlink(missing_ok=True)
    except Exception:
        # Refusal accounting must never alter the refusal's message or exit code.
        pass


def acquire_ledger_lock(path: Path) -> None:
    deadline = time.monotonic() + LEDGER_LOCK_TIMEOUT_SECONDS
    removed_stale = False
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                if not removed_stale and time.time() - path.stat().st_mtime > LEDGER_LOCK_STALE_SECONDS:
                    path.unlink()
                    removed_stale = True
                    continue
            except FileNotFoundError:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SeniorError(f"Timed out after {LEDGER_LOCK_TIMEOUT_SECONDS}s waiting for ledger lock: {path}")
            time.sleep(min(LEDGER_LOCK_RETRY_SECONDS, remaining))
        else:
            os.close(descriptor)
            return


def packet_question(packet: str, kind: str = "advice") -> str:
    content: List[str] = []
    for line in parse_packet(packet, kind):
        if line.is_header and content:
            break
        if line.is_header or content:
            content.append(line.content)
    return "".join(content).strip()[:160]


def packet_sha256(packet: str) -> str:
    return hashlib.sha256(" ".join(packet.split()).casefold().encode("utf-8")).hexdigest()


def write_ledger(
    session: LedgerSession, packet: str, provider: str, model: Optional[str],
    started: str, finished: str, duration: float, answer: str = "", error: Optional[str] = None,
    bypassed: Sequence[str] = (), trigger: str = "user", kind: str = "advice",
) -> Optional[Dict[str, object]]:
    if not session.enabled:
        return None
    try:
        create_ledger_directory(session.log_root)
        create_ledger_directory(session.path)
        lock_path = session.path / ".ledger.lock"
        acquire_ledger_lock(lock_path)
        try:
            jsonl_path = session.path / "ledger.jsonl"
            markdown_path = session.path / "ledger.md"
            entries, _ = read_ledger(jsonl_path)
            n = max((entry["n"] for entry in entries), default=0) + 1
            entry = {
                "n": n,
                "kind": kind,
                "trigger": trigger,
                "started": started,
                "finished": finished,
                "provider": provider,
                "model": model,
                "duration_seconds": duration,
                "packet_bytes": len(packet.encode("utf-8")),
                "packet_sha256": packet_sha256(packet),
                "question": packet_question(packet, kind),
                "answer_chars": len(answer) if error is None else 0,
                "status": "ok" if error is None else "error",
                "bypassed": list(bypassed),
                "error": error,
            }
            session_started = entries[0].get("started", started) if entries else started
            # Keep a torn final line separate from the new record so its number remains readable.
            with open(jsonl_path, "a+b", opener=ledger_file_opener) as stream:
                if stream.tell():
                    stream.seek(-1, os.SEEK_END)
                    if stream.read(1) != b"\n":
                        stream.write(b"\n")
                stream.write((json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            with open(markdown_path, "a", encoding="utf-8", opener=ledger_file_opener) as stream:
                if stream.tell() == 0:
                    stream.write(
                        "# Senior consultation ledger\n\n"
                        f"- Session: {session.session_id}\n"
                        f"- Started: {session_started}\n"
                        f"- Log root: {session.log_root}\n"
                    )
                body = answer if error is None else f"**Consultation failed:** {error}"
                bypass_line = f"Bypassed guards: {', '.join(bypassed)}\n" if bypassed else ""
                stream.write(
                    f"\n## Q{n} — {kind} — trigger: {trigger} — {started}\n\n"
                    f"Provider: {provider} ({model or 'default'}) · {duration} s · packet {entry['packet_bytes']} B\n"
                    f"{bypass_line}\n"
                    f"```text\n{packet}" + ("" if packet.endswith("\n") else "\n")
                    + f"```\n\n### A{n}\n\n{body}\n\n---\n"
                )
            entries.append(entry)
            write_json_object(session.path / "session.json", {
                "id": session.session_id, "started": session_started, "last_activity": finished,
                "counts": ledger_counts(entries),
                "refusals": session_refusals(read_json_object(session.path / "session.json")),
            })
            write_json_object(session.log_root / "current.json", {
                "id": session.session_id, "last_activity": finished,
            })
            return {"session_id": session.session_id, "entry": n, "path": str(markdown_path)}
        finally:
            lock_path.unlink(missing_ok=True)
    except Exception as exc:
        eprint(f"ask_senior: ledger write failed: {exc}")
        return None


def read_question(positional: Optional[str]) -> str:
    if positional is not None:
        return positional
    if sys.stdin.isatty():
        raise SeniorError("Pass a consultation packet as an argument or through stdin")
    return sys.stdin.read()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ask a senior model through an installed CLI or Z.AI API without exposing the repository."
    )
    parser.add_argument("question", nargs="?", help="Consultation packet; reads stdin when omitted")
    parser.add_argument(
        "--provider",
        choices=["auto", "codex", "claude", "grok", "kimi", "zai"],
        default=os.environ.get("SENIOR_PROVIDER", "auto"),
    )
    parser.add_argument("--model", help="Provider-specific model override")
    parser.add_argument(
        "--kind", choices=["advice", "plan"], default=None,
        help="Packet kind (default: detect Question: as advice or Objective: as plan)",
    )
    parser.add_argument(
        "--trigger", choices=list(TRIGGER_DESCRIPTIONS),
        help="Reason for consulting; required for a real consultation",
    )
    parser.add_argument(
        "--policy", choices=list(POLICY_TRIGGERS),
        help="Consultation policy (overrides SENIOR_POLICY, log-root policy.json, then normal)",
    )
    parser.add_argument(
        "--max-consultations",
        help="Non-negative autonomous budget (overrides SENIOR_MAX_CONSULTATIONS, policy.json, then the level default)",
    )
    parser.add_argument(
        "--confusion-reserve",
        help="Non-negative confusion reserve (overrides SENIOR_CONFUSION_RESERVE, policy.json, then the level default)",
    )
    parser.add_argument(
        "--min-interval-seconds",
        help="Non-negative interval (overrides SENIOR_MIN_INTERVAL_SECONDS, policy.json, then the level default)",
    )
    parser.add_argument(
        "--provider-order",
        default=os.environ.get("SENIOR_PROVIDER_ORDER", DEFAULT_PROVIDER_ORDER),
        help="Comma-separated order used by --provider auto",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("SENIOR_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
    )
    parser.add_argument(
        "--max-input-bytes",
        type=int,
        default=int(os.environ.get("SENIOR_MAX_INPUT_BYTES", DEFAULT_MAX_INPUT_BYTES)),
    )
    parser.add_argument("--allow-sensitive", action="store_true", help="Bypass the likely-secret guard")
    parser.add_argument(
        "--allow-narrative",
        action="store_true",
        help="Bypass the whole packet gate; only for a human who has already reviewed the packet",
    )
    parser.add_argument(
        "--allow-repeat", action="store_true",
        help="Bypass only the duplicate guard; for a human, not for the agent",
    )
    parser.add_argument("--json", action="store_true", help="Return a machine-readable result envelope")
    parser.add_argument("--doctor", action="store_true", help="Report provider readiness without making a request")
    parser.add_argument("--lint", action="store_true", help="Check the packet and likely secrets without selecting a provider")
    parser.add_argument("--dry-run", action="store_true", help="Validate and show provider selection only")
    parser.add_argument("--log-dir", help="Ledger root (default: SENIOR_LOG_DIR or .senior-advisor)")
    parser.add_argument("--session-id", help="Explicit ledger session id (default: SENIOR_SESSION_ID or auto)")
    parser.add_argument("--new-session", action="store_true", help="Start a fresh ledger session")
    parser.add_argument("--no-ledger", action="store_true", help="Disable consultation logging")
    parser.add_argument("--status", action="store_true", help="Report the ledger session without making a request")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.lint and args.doctor:
            raise SeniorError("--lint and --doctor cannot be used together")
        if args.status and args.lint:
            raise SeniorError("--status and --lint cannot be used together")
        if args.status and args.doctor:
            raise SeniorError("--status and --doctor cannot be used together")
        if args.new_session and (args.session_id is not None or "SENIOR_SESSION_ID" in os.environ):
            id_source = "--session-id" if args.session_id is not None else "SENIOR_SESSION_ID"
            raise SeniorError(
                f"--new-session mints an id and {id_source} supplies one; only one can be given"
            )
        if args.timeout <= 0:
            raise SeniorError("Timeout must be a positive number of seconds")
        if args.max_input_bytes <= 0:
            raise SeniorError("Input byte limit must be positive")
        if args.doctor:
            order = parse_order(args.provider_order)
            report = [provider_status(name).__dict__ for name in order]
            print(json.dumps({"providers": report}, ensure_ascii=False, indent=2))
            return 0 if any(item["available"] for item in report) else 3
        if args.status:
            session = resolve_session(args)
            policy = resolve_policy(args, session.log_root)
            print(json.dumps(session_status(session, policy, args.trigger), ensure_ascii=False, indent=2))
            return 0

        question = read_question(args.question)
        size = len(question.encode("utf-8"))
        if size > args.max_input_bytes:
            raise SeniorError(f"Consultation packet is {size} bytes; limit is {args.max_input_bytes}")
        if not args.lint and not args.dry_run:
            session = resolve_session(args)
            policy = resolve_policy(args, session.log_root)
            if args.trigger is None:
                choices = "; ".join(f"{name} — {meaning}" for name, meaning in TRIGGER_DESCRIPTIONS.items())
                raise SeniorError(f"--trigger is required for a real consultation; choose: {choices}.")
            check_consultation(session, policy, args.trigger, question, args.allow_repeat)
        kind = args.kind or detect_packet_kind(question) or "advice"
        lines = parse_packet(question, kind)
        findings = [] if args.allow_narrative else check_packet(question, lines, args.kind)
        if findings and not args.lint:
            raise SeniorError(gate_message(findings))
        sensitive_hits = detect_sensitive(question)
        if args.lint:
            if not args.allow_sensitive:
                findings.extend(sensitive_findings(question, lines))
            findings = sort_findings(findings)
            print(
                json.dumps(
                    {
                        "ok": not findings,
                        "packet_bytes": size,
                        "prose_bytes": len(prose_text(lines, kind).encode("utf-8")),
                        "findings": [finding.__dict__ for finding in findings],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2 if findings else 0
        if sensitive_hits and not args.allow_sensitive:
            raise SeniorError(
                "Possible sensitive data detected: "
                + ", ".join(sensitive_hits)
                + ". Redact it or rerun manually with --allow-sensitive after human review."
            )
        if not question.strip():
            raise SeniorError("Consultation packet is empty")
        if args.dry_run:
            session = resolve_session(args)
        if args.new_session and not args.dry_run:
            start_new_session(session)
        order = parse_order(args.provider_order)
        provider = select_provider(args.provider, order)
        model = resolve_model(provider, args.model)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "provider": provider,
                        "model": model,
                        "kind": kind,
                        "input_bytes": size,
                        "sensitive_hits": sensitive_hits,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        checks = {
            "narrative": "bypassed" if args.allow_narrative else "pass",
            "secrets": "bypassed" if args.allow_sensitive else "pass",
        }
        bypassed = [
            guard for guard, check in (("sensitive", "secrets"), ("narrative", "narrative"))
            if checks[check] == "bypassed"
        ]
        if args.allow_repeat:
            bypassed.append("repeat")
        started = time.monotonic()
        started_at = local_timestamp()
        prompt = build_prompt(question, kind)
        with tempfile.TemporaryDirectory(prefix="senior-advisor-") as temp_dir:
            try:
                runner = PROVIDER_RUNNERS[provider]
                if provider == "zai":
                    answer = runner(prompt, model, args.timeout, Path(temp_dir), kind=kind)
                else:
                    answer = runner(prompt, model, args.timeout, Path(temp_dir))
            except SeniorError as exc:
                write_ledger(
                    session, question, provider, model, started_at, local_timestamp(),
                    round(time.monotonic() - started, 3), error=f"{provider}: {exc}", bypassed=bypassed,
                    trigger=args.trigger, kind=kind,
                )
                raise SeniorError(f"{provider}: {exc}") from exc
        duration = round(time.monotonic() - started, 3)
        ledger = write_ledger(
            session, question, provider, model, started_at, local_timestamp(), duration, answer,
            bypassed=bypassed, trigger=args.trigger, kind=kind,
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "provider": provider,
                        "model": model,
                        "kind": kind,
                        "answer": answer,
                        "duration_seconds": duration,
                        "packet_bytes": size,
                        "checks": checks,
                        "ledger": ledger,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(answer)
        return 0
    except PolicyRefusal as exc:
        write_refusal(session, args.trigger, exc.reason)
        eprint(f"ask_senior: {exc}")
        return 4
    except SeniorError as exc:
        eprint(f"ask_senior: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
