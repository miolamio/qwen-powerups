#!/usr/bin/env python3
"""Ask a configured senior model without giving it the current repository.

The program is intentionally dependency-free. It supports subscription-backed
agent CLIs and Z.AI's HTTP API, rejects likely secrets by default, and executes
CLI providers in a fresh temporary working directory.
"""

from __future__ import annotations

import argparse
import json
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
MAX_QUESTION_BYTES = 300
MAX_QUESTION_LINES = 2
MAX_PROSE_BYTES = 1200
MAX_EVIDENCE_LINES = 100
MAX_PACKET_BYTES = 8192
PACKET_HEADERS = ("Question", "Stack", "Given", "Constraints", "Ruled out", "Evidence")
HEADER_PATTERN = re.compile(
    r"^\s*(Question|Stack|Given|Constraints|Ruled out|Evidence)\s*:", re.IGNORECASE
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


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    available: bool
    reason: str


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


SYSTEM_INSTRUCTION = """You are a senior technical reviewer.
The packet is deliberately decontextualized; missing project background is by design.
Do not ask for the project, the task, or the repository, and do not request more context.
If the answer turns on a missing technical fact, name that fact and answer for each branch.
Give advice only. Do not use tools, access the filesystem, or modify files.
Separate facts from assumptions. Do not merely agree with the asker.
Return these sections in order: Verdict, Assumptions, Reasoning, Risks, Recommended next step, What would change the answer.
Be concrete and concise."""


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def detect_sensitive(text: str) -> List[str]:
    return [label for label, pattern in SECRET_PATTERNS if pattern.search(text)]


def parse_packet(text: str) -> List[PacketLine]:
    lines: List[PacketLine] = []
    section: Optional[str] = None
    offset = 0
    for number, raw in enumerate(text.splitlines(keepends=True), 1):
        header = HEADER_PATTERN.match(raw) if section != "Evidence" else None
        if header:
            section = next(name for name in PACKET_HEADERS if name.lower() == header.group(1).lower())
        content = raw[header.end():] if header else raw
        lines.append(PacketLine(number, offset, section, raw, content, bool(header)))
        offset += len(raw)
    return lines


def prose_text(lines: Sequence[PacketLine]) -> str:
    return "".join(line.text for line in lines if line.section != "Evidence")


def source_line(lines: Sequence[PacketLine], offset: int) -> PacketLine:
    return next(line for line in reversed(lines) if line.offset <= offset)


def sort_findings(findings: Sequence[Finding]) -> List[Finding]:
    return sorted(
        findings,
        key=lambda finding: (finding.line if finding.line is not None else sys.maxsize, finding.code),
    )


def check_packet(text: str, lines: Sequence[PacketLine]) -> List[Finding]:
    findings: List[Finding] = []
    first = next((line for line in lines if line.text.strip()), None)
    if first is None or not (first.is_header and first.section == "Question"):
        findings.append(Finding(
            "question_not_first", first.number if first else None, "Question",
            first.text.rstrip("\r\n") if first else "", "Start the packet with the Question: header.",
        ))
    present = {line.section for line in lines if line.is_header}
    for name in PACKET_HEADERS:
        if name not in present:
            findings.append(Finding("missing_header", None, name, "", f"Add the {name}: header."))

    question_lines = [line for line in lines if line.section == "Question"]
    if question_lines:
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

    prose = prose_text(lines)
    if len(prose.encode("utf-8")) > MAX_PROSE_BYTES:
        findings.append(Finding(
            "prose_too_long", None, None, "", "Reduce everything before Evidence: to at most 1200 bytes.",
        ))
    evidence = [line for line in lines if line.section == "Evidence"]
    if len(evidence) > MAX_EVIDENCE_LINES:
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
        for line in lines if line.section != "Evidence"
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


def build_prompt(question: str) -> str:
    return f"{SYSTEM_INSTRUCTION}\n\nCONSULTATION PACKET\n{question.strip()}\n"


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


def ask_zai(prompt: str, model: Optional[str], timeout: int, _workdir: Path) -> str:
    api_key = os.environ.get("ZAI_API_KEY")
    if not api_key:
        raise SeniorError("ZAI_API_KEY is not set")
    url = os.environ.get("ZAI_CHAT_URL", "https://api.z.ai/api/paas/v4/chat/completions")
    selected_model = model or os.environ.get("ZAI_MODEL", "glm-5.3")
    body = json.dumps(
        {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
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


PROVIDER_RUNNERS: Dict[str, Callable[[str, Optional[str], int, Path], str]] = {
    "codex": ask_codex,
    "claude": ask_claude,
    "grok": ask_grok,
    "kimi": ask_kimi,
    "zai": ask_zai,
}


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
    parser.add_argument("--json", action="store_true", help="Return a machine-readable result envelope")
    parser.add_argument("--doctor", action="store_true", help="Report provider readiness without making a request")
    parser.add_argument("--lint", action="store_true", help="Check the packet and likely secrets without selecting a provider")
    parser.add_argument("--dry-run", action="store_true", help="Validate and show provider selection only")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.lint and args.doctor:
            raise SeniorError("--lint and --doctor cannot be used together")
        if args.timeout <= 0:
            raise SeniorError("Timeout must be a positive number of seconds")
        if args.max_input_bytes <= 0:
            raise SeniorError("Input byte limit must be positive")
        if args.doctor:
            order = parse_order(args.provider_order)
            report = [provider_status(name).__dict__ for name in order]
            print(json.dumps({"providers": report}, ensure_ascii=False, indent=2))
            return 0 if any(item["available"] for item in report) else 3

        question = read_question(args.question)
        size = len(question.encode("utf-8"))
        if size > args.max_input_bytes:
            raise SeniorError(f"Consultation packet is {size} bytes; limit is {args.max_input_bytes}")
        lines = parse_packet(question)
        findings = [] if args.allow_narrative else check_packet(question, lines)
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
                        "prose_bytes": len(prose_text(lines).encode("utf-8")),
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
        order = parse_order(args.provider_order)
        provider = select_provider(args.provider, order)
        model = resolve_model(provider, args.model)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "provider": provider,
                        "model": model,
                        "input_bytes": size,
                        "sensitive_hits": sensitive_hits,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        started = time.monotonic()
        prompt = build_prompt(question)
        with tempfile.TemporaryDirectory(prefix="senior-advisor-") as temp_dir:
            try:
                answer = PROVIDER_RUNNERS[provider](prompt, model, args.timeout, Path(temp_dir))
            except SeniorError as exc:
                raise SeniorError(f"{provider}: {exc}") from exc
        duration = round(time.monotonic() - started, 3)
        if args.json:
            print(
                json.dumps(
                    {
                        "provider": provider,
                        "model": model,
                        "answer": answer,
                        "duration_seconds": duration,
                        "packet_bytes": size,
                        "checks": {
                            "narrative": "bypassed" if args.allow_narrative else "pass",
                            "secrets": "bypassed" if args.allow_sensitive else "pass",
                        },
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(answer)
        return 0
    except SeniorError as exc:
        eprint(f"ask_senior: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
