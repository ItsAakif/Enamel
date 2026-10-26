#!/usr/bin/env python
"""Validate the maintained documentation against the working tree.

Checks performed:

1. Every local Markdown link resolves to an existing file/directory.
2. Fenced command blocks reference real files and real commands:
   - ``manage.py <command>`` is a known Django or project management command.
   - ``python scripts/foo.py`` and ``powershell -File deploy/foo.ps1`` exist.
   - ``docker compose -f <file>`` exists.
   - ``pnpm --filter <pkg> run <script>`` resolves to a package script.
   - ``pnpm run <script>`` resolves to a root script.
   - Referenced repository paths (including Windows ``\\`` paths) exist.
3. Retired package names (Express API, Drizzle, ``lib/db``) never appear as
   current documentation. A line may mention them only when it also says they
   are legacy/retired/removed.
4. The public landing copy makes no claim the product cannot back
   (``scripts/check_claims.py``; see that module for the pattern list).

The script is intentionally repository-aware but dependency-free: it runs with
the standard library and is safe in CI before ``pnpm install``.

    python scripts/check_docs.py
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOCS = [
    ROOT / "README.md",
    ROOT / "backend" / "README.md",
    ROOT / "docs" / "deployment" / "DEPLOYMENT_GUIDE.md",
]

#: The files that carry the **current** runtime state of row-level security.
#: The verification reports and the progress records are deliberately not in
#: this list: they quote the old claim as history, which is what a record is
#: for. The guard is about the state the tree ships in. The internal planning
#: documents are not guarded here either: they are development-environment
#: material and are excluded from the public export, so this list holds only
#: files that ship with the tree.
RLS_STATE_FILES = [
    ROOT / "README.md",
    ROOT / "backend" / "README.md",
    ROOT / "docs" / "deployment" / "DEPLOYMENT_GUIDE.md",
    ROOT / "env.example",
    ROOT / "deploy" / "docker-compose.yml",
    ROOT / "deploy" / "env.example",
    ROOT / "deploy" / "db" / "init" / "01-enamel-app-role.sql",
    ROOT / "backend" / "apps" / "core" / "portal_context.py",
]

#: The stale claims the role switch retired. A line that carries one of them
#: describes a state the tree no longer has.
RLS_STALE_CLAIM_PATTERNS = (
    (re.compile(r"schema[- ]level only", re.IGNORECASE), "claims that row-level security is schema-level only"),
    (re.compile(r"\bstaged but\b", re.IGNORECASE), "claims that the application role is staged but not enabled"),
    (re.compile(r"not in force at runtime", re.IGNORECASE), "claims that row-level security is not in force at runtime"),
    (re.compile(r"\b(?:is|are) inert\b", re.IGNORECASE), "claims that row-level security is inert"),
    (re.compile(r"\bruns? as the (?:compose )?superuser\b", re.IGNORECASE), "claims that the application runs as the superuser"),
    (re.compile(r"\bconnects? as the (?:compose )?superuser\b", re.IGNORECASE), "claims that the application connects as the superuser"),
)

#: A line that carries one of these words describes the past, not the state
#: the tree ships in, so the stale-claim rule does not apply to it.
RLS_HISTORICAL_MARKERS = (
    "was ",
    "were ",
    "before ",
    "previously",
    "no longer",
    "used to",
    "had been",
    "until ",
    "closed",
    "rollback",
    "rejected",
    "histor",
)

#: The number of boundary proofs a document may claim. The number itself lives
#: next to the proofs (``backend/apps/core/tests/rls_proof_floor.py``); this is
#: only the *shape* of a claim, so a document cannot phrase its way around it
#: ("86 proofs", "**86** proofs", "the deny-by-default matrix (86 proofs)").
#: A per-module claim ("32 proofs in ``test_rls.py``") is a different number
#: and is exempt.
RLS_PROOF_COUNT_RE = re.compile(
    r"\b(?P<count>\d+)\s+(?:[a-z-]+\s+){0,2}proofs?\b(?!\s+in\b)",
    re.IGNORECASE,
)

#: The figures the documents must agree with (C-5). The numbers live in
#: ``scripts/figures.json``; the proof count lives next to the proofs.
FIGURES_PATH = ROOT / "scripts" / "figures.json"

#: A count of tests ("N passed", "N tests") in a guarded document must be one
#: of the declared counts. It cannot be a single number: the documents quote
#: several suites (the backend suite, the boundary proofs, the end-to-end run,
#: the unit tests, the reference subset).
TEST_COUNT_RE = re.compile(r"\b(?P<count>\d+)\s+(?:passed|tests?)\b", re.IGNORECASE)

#: The smoke total: "20 checks", "9/9 checks", "20/20", and the failed drill
#: "RESULT: FAIL (2/18)".
SMOKE_CHECK_COUNT_RE = re.compile(
    r"\b(?P<count>\d+)(?:/\d+)?\s+checks\b", re.IGNORECASE
)
SMOKE_TOTAL_RE = re.compile(
    r"RESULT:\s*(?:PASS|FAIL)\s*\(?\s*\d+\s*/\s*(?P<count>\d+)", re.IGNORECASE
)

#: The end-to-end total: "30/30" in an end-to-end context. Only a *pair* of
#: numbers is a claim - a port, a date or a version is not.
E2E_COUNT_RE = re.compile(r"\b(?P<count>\d+)/(?P<total>\d+)\b")
E2E_CONTEXT_RE = re.compile(
    r"\bplaywright\b|\bend-to-end\b|\be2e\b", re.IGNORECASE
)

#: The documentation guard's own assertion count.
DOCS_ASSERTION_COUNT_RE = re.compile(
    r"\b(?P<count>\d+)\s+assertions\b", re.IGNORECASE
)

#: The roles the runtime may connect as. The owner is not one of them: a
#: document that names another role as the runtime connection is stale.
RUNTIME_ROLES = frozenset(
    {
        "enamel_app",
        "enamel_worker",
        "enamel_admin",
        "enamel_maint",
        "enamel_portal",
        "enamel_portal_auth",
        "enamel_platform",
    }
)

#: "connects as <role>" - the structural form of a runtime-connection claim.
CONNECT_ROLE_RE = re.compile(
    r"connects?\s+as\s+(?:the\s+)?(?:non-superuser\s+)?(?:role\s+)?`?(?P<role>[a-z_][a-z0-9_]*)`?",
    re.IGNORECASE,
)

#: A document that describes the runtime connection must name a runtime role.
RUNTIME_CONNECTION_RE = re.compile(r"connects?\s+as\b", re.IGNORECASE)

FENCE_LANGUAGES = {
    "powershell",
    "pwsh",
    "bash",
    "sh",
    "shell",
    "console",
    "cmd",
    "text",
}

TEXT_COMMAND_PREFIXES = (
    "cd ",
    "copy ",
    "cp ",
    "docker ",
    "git ",
    "pip ",
    "pnpm ",
    "powershell ",
    "python ",
    "manage.py ",
    ".venv",
    "..\\.venv",
    "../.venv",
)

# Outputs and generated trees are allowed to be absent in a clean checkout.
IGNORED_PATH_PREFIXES = (
    ".venv",
    "node_modules",
    "artifacts/ml",
    "artifacts/cv",
    "artifacts/evals",
    "deploy/backups",
    "enamel.dump",
    "backend/media",
    "playwright-report",
    "test-results",
)

NON_REPO_PATH_PREFIXES = (
    "http://",
    "https://",
    "postgresql://",
    "redis://",
    "pgvector/",
    "postgres/",
    "redis/",
    "nginx/",
)

# ``line`` may contain one of these when it refers to a retired package.
RETIRED_MARKERS = (
    "retired",
    "legacy",
    "removed",
    "no runtime dependency",
    "do not reintroduce",
    "were gone",
)

RETIRED_PATTERNS = {
    "artifacts/api-server": re.compile(r"artifacts[/\\]api-server"),
    "@workspace/api-server": re.compile(r"@workspace/api-server"),
    "lib/db": re.compile(r"(?<![\w-])lib[/\\]db(?![\w-])"),
    "@workspace/db": re.compile(r"@workspace/db"),
    "Drizzle": re.compile(r"\bdrizzle\b", re.IGNORECASE),
    "Express 5": re.compile(r"\bexpress\s*5\b", re.IGNORECASE),
}

BUILTIN_MANAGE_COMMANDS = {
    "check",
    "collectstatic",
    "createsuperuser",
    "dbshell",
    "diffsettings",
    "dumpdata",
    "flush",
    "inspectdb",
    "loaddata",
    "makemigrations",
    "migrate",
    "runserver",
    "shell",
    "showmigrations",
    "sqlflush",
    "sqlmigrate",
    "sqlsequencereset",
    "squashmigrations",
    "startapp",
    "startproject",
    "test",
    "testserver",
}

LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
MANAGE_RE = re.compile(r"manage\.py\s+([a-z][\w-]*)")
DOCKER_FILE_RE = re.compile(r"docker\s+compose\s+-f\s+(\S+)")
PS_FILE_RE = re.compile(r"powershell(?:\.exe)?\s+-File\s+(\S+)", re.IGNORECASE)
PNPM_FILTER_RE = re.compile(r"pnpm\s+--filter\s+(\S+)\s+run\s+([\w:.-]+)")
PNPM_RUN_RE = re.compile(r"pnpm\s+run\s+([\w:.-]+)")
PY_SCRIPT_RE = re.compile(r"(?:python|py)\s+(\S+\.py)")
PIP_REQ_RE = re.compile(r"pip\s+install\s+-r\s+(\S+)")
COPY_RE = re.compile(r"(?:copy|cp|Copy-Item)\s+(\S+)(?:\s+(\S+))?", re.IGNORECASE)

PATH_TOKEN_RE = re.compile(r"[^\s`'\"]+")
KNOWN_EXTENSIONS = {
    ".conf",
    ".dump",
    ".example",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

#: The module that holds the floor of the boundary proofs.
PROOF_FLOOR_PATH = ROOT / "backend" / "apps" / "core" / "tests" / "rls_proof_floor.py"


def load_proof_floor() -> int:
    """The number of boundary proofs the release gate protects.

    It is read from the module next to the proofs, so the number the
    documents claim is the number the gate enforces.
    """

    spec = importlib.util.spec_from_file_location(
        "enamel_rls_proof_floor", PROOF_FLOOR_PATH
    )
    if spec is None or spec.loader is None:  # pragma: no cover - import guard
        raise RuntimeError(f"the proof floor module is missing: {PROOF_FLOOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.TOTAL_MINIMUM)


def rls_state_claim_errors(path: Path, text: str) -> list[str]:
    """The stale row-level-security claims in one file (F-03, F-08).

    A line that carries a retired claim describes a state the tree no longer
    has: the runtime is off the superuser and the policies are in force. A
    line that describes the past (it carries a historical marker) is a record
    of that state and is allowed.
    """

    errors: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        if any(marker in lowered for marker in RLS_HISTORICAL_MARKERS):
            continue
        for pattern, description in RLS_STALE_CLAIM_PATTERNS:
            if pattern.search(line):
                errors.append(f"{path}:{line_no}: {description}")
    return errors


def rls_proof_count_errors(path: Path, text: str, floor: int) -> list[str]:
    """A document may not claim a proof count the release gate does not hold."""

    errors: list[str] = []
    for match in RLS_PROOF_COUNT_RE.finditer(text):
        count = int(match.group("count"))
        if count != floor:
            line_no = text.count("\n", 0, match.start()) + 1
            errors.append(
                f"{path}:{line_no}: the document claims {count} boundary proofs, "
                f"the release gate protects {floor}"
            )
    return errors


def rls_role_named_errors(path: Path, text: str) -> list[str]:
    """A document about the runtime must name the runtime role."""

    if "enamel_app" in text:
        return []
    return [f"{path}: the document does not name the runtime role `enamel_app`"]


def load_figures() -> dict[str, int]:
    """The figures the documents must agree with (``scripts/figures.json``).

    The number of boundary proofs is **not** here: it lives next to the proofs
    (``backend/apps/core/tests/rls_proof_floor.py``) and the gate reads it
    there, so there is exactly one copy of it.
    """

    if not FIGURES_PATH.exists():
        raise RuntimeError(f"the figures file is missing: {FIGURES_PATH}")
    data = json.loads(FIGURES_PATH.read_text(encoding="utf-8"))
    return {key: int(value) for key, value in data.items() if not key.startswith("_")}


def figure_errors(
    path: Path,
    text: str,
    *,
    floor: int,
    figures: dict[str, int],
) -> list[str]:
    """The numeric claims of one document that are not the current ones.

    The rules are structural, not a phrase list: a claim is recognised by the
    noun it counts ("N proofs", "N checks", "N passed") and by the context it
    appears in (the smoke, the end-to-end run), so it cannot be phrased around.
    """

    errors: list[str] = []
    smoke_total = figures["smoke_checks"]
    e2e_total = figures["e2e_tests"]
    allowed_test_counts = {
        figures["backend_tests"],
        figures["e2e_tests"],
        figures["vitest_tests"],
        figures["prod_security_tests"],
        figures["error_contract_tests"],
        figures["focused_tests"],
        figures["reference_tests"],
        floor,
    }

    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in RLS_PROOF_COUNT_RE.finditer(line):
            count = int(match.group("count"))
            if count != floor:
                errors.append(
                    f"{path}:{line_no}: the document claims {count} boundary "
                    f"proofs, the release gate protects {floor}"
                )

        for match in SMOKE_CHECK_COUNT_RE.finditer(line):
            count = int(match.group("count"))
            if count != smoke_total:
                errors.append(
                    f"{path}:{line_no}: the document claims {count} smoke "
                    f"checks, the smoke runs {smoke_total}"
                )

        for match in SMOKE_TOTAL_RE.finditer(line):
            count = int(match.group("count"))
            if count != smoke_total:
                errors.append(
                    f"{path}:{line_no}: the document quotes a smoke total of "
                    f"{count}, the smoke runs {smoke_total}"
                )

        if E2E_CONTEXT_RE.search(line):
            for match in E2E_COUNT_RE.finditer(line):
                # A pair that counts checks is the smoke's total, not the
                # end-to-end run's.
                if re.match(r"\s+checks\b", line[match.end() :]):
                    continue
                ran = int(match.group("count"))
                total = int(match.group("total"))
                if ran != e2e_total or total != e2e_total:
                    errors.append(
                        f"{path}:{line_no}: the document quotes the end-to-end "
                        f"result {ran}/{total}, the run has {e2e_total}/{e2e_total}"
                    )

        for match in TEST_COUNT_RE.finditer(line):
            count = int(match.group("count"))
            if count not in allowed_test_counts:
                errors.append(
                    f"{path}:{line_no}: the document quotes {count} tests, "
                    "which is no count any suite runs today"
                )
    return errors


def assertion_count_errors(path: Path, text: str, count: int) -> list[str]:
    """The documents must quote the number of assertions the guard performs."""

    errors: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in DOCS_ASSERTION_COUNT_RE.finditer(line):
            quoted = int(match.group("count"))
            if quoted != count:
                errors.append(
                    f"{path}:{line_no}: the document claims {quoted} "
                    f"documentation assertions, the guard performs {count}"
                )
    return errors


def runtime_role_errors(path: Path, text: str) -> list[str]:
    """The runtime-connection claims of one document.

    A document that says which role the runtime connects as must name a
    runtime role; the owner is not one of them. A document that describes the
    runtime connection at all must name a runtime role somewhere.
    """

    errors: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in CONNECT_ROLE_RE.finditer(line):
            role = match.group("role")
            if role.lower() in RUNTIME_ROLES:
                continue
            if role.lower() in {"owner", "superuser", "compose", "roles", "role"}:
                # "connects as the owner" is the stale claim; the phrase rules
                # report it with the reason.
                continue
            if role.lower().startswith("enamel"):
                errors.append(
                    f"{path}:{line_no}: the runtime connects as `{role}`, which is "
                    "not one of the runtime roles"
                )
    if RUNTIME_CONNECTION_RE.search(text) and not any(
        role in text for role in RUNTIME_ROLES
    ):
        errors.append(
            f"{path}: the document describes the runtime connection without "
            "naming a runtime role"
        )
    return errors


class Checker:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.checks = 0
        self.management_commands = self._discover_manage_commands()
        self.packages = self._discover_packages()
        self.root_scripts = self._load_scripts(ROOT / "package.json")
    def _discover_manage_commands(self) -> set[str]:
        commands = set(BUILTIN_MANAGE_COMMANDS)
        for path in (ROOT / "backend" / "apps").glob("*/management/commands/*.py"):
            if path.name != "__init__.py":
                commands.add(path.stem)
        return commands

    def _discover_packages(self) -> dict[str, dict]:
        packages: dict[str, dict] = {}
        for manifest in list((ROOT / "artifacts").glob("*/package.json")) + list(
            (ROOT / "lib").glob("*/package.json")
        ) + [ROOT / "scripts" / "package.json"]:
            if not manifest.exists():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            name = data.get("name")
            if name:
                packages[name] = data
        return packages

    def _load_scripts(self, manifest: Path) -> dict:
        try:
            return json.loads(manifest.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, json.JSONDecodeError):
            return {}

    def _error(self, doc: Path, line: int | None, message: str) -> None:
        location = doc.relative_to(ROOT).as_posix()
        if line is not None:
            location = f"{location}:{line}"
        self.errors.append(f"{location}: {message}")

    def _candidate_paths(self, token: str, cwd: Path) -> list[Path]:
        raw = token.strip().strip("`'\"").rstrip(",;:")
        if not raw or raw.startswith("-"):
            return []
        if raw.startswith("$") or raw.startswith("/") or "=" in raw or "@" in raw:
            return []
        if any(skip in raw for skip in ("<", ">", "*", "?", "|", "%")):
            return []
        if re.match(r"^[A-Za-z]:[\\/]", raw):
            return []
        normalized = raw.replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        lowered = normalized.lower()
        if any(lowered == prefix or lowered.startswith(prefix + "/") for prefix in IGNORED_PATH_PREFIXES):
            return []
        if lowered in {".env", "backend/.env", "deploy/.env"} or "/.venv/" in lowered or lowered.startswith("../.venv/"):
            return []
        if any(normalized.startswith(prefix) for prefix in NON_REPO_PATH_PREFIXES):
            return []
        if re.match(r"^\d+\.\d+\.\d+\.\d+", normalized):
            return []
        if "://" in normalized:
            return []

        has_separator = "/" in normalized
        suffix = Path(normalized).suffix.lower()
        if not has_separator and suffix not in KNOWN_EXTENSIONS:
            return []
        if not has_separator and suffix == ".py" and normalized == "manage.py":
            return []
        return [cwd / normalized, ROOT / normalized]

    def _check_path_token(self, doc: Path, line_no: int, token: str, cwd: Path) -> None:
        candidates = self._candidate_paths(token, cwd)
        if not candidates:
            return
        self.checks += 1
        if any(candidate.exists() for candidate in candidates):
            return
        # A token that names only a directory with a trailing slash is covered by
        # the same check: resolve() drops the marker, but the directory must exist.
        self._error(doc, line_no, f"referenced path does not exist: {token}")

    def _resolve_command_path(self, doc: Path, line_no: int, token: str, cwd: Path) -> None:
        raw = token.strip().strip("`'\"").rstrip(",;:")
        if not raw or any(marker in raw for marker in ("<", ">", "*", "$")):
            return
        self.checks += 1
        normalized = raw.replace("\\", "/")
        candidates = [cwd / normalized, ROOT / normalized]
        if not any(candidate.exists() for candidate in candidates):
            self._error(doc, line_no, f"command references a missing file: {raw}")

    def check_links(self, doc: Path, text: str) -> None:
        for match in LINK_RE.finditer(text):
            target = match.group(1).strip()
            if target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target = target.split("#", 1)[0]
            if not target or any(marker in target for marker in ("<", ">", "*")):
                continue
            self.checks += 1
            if not (doc.parent / target).exists():
                line_no = text.count("\n", 0, match.start()) + 1
                self._error(doc, line_no, f"broken Markdown link: {target}")

    def check_retired_names(self, doc: Path, text: str) -> None:
        for line_no, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            if any(marker in lowered for marker in RETIRED_MARKERS):
                continue
            for label, pattern in RETIRED_PATTERNS.items():
                if pattern.search(line):
                    self._error(
                        doc,
                        line_no,
                        f"retired package/stack mention without a retirement qualifier: {label}",
                    )

    def _check_command_line(self, doc: Path, line_no: int, line: str, cwd: Path) -> None:
        self.checks += 1

        match = MANAGE_RE.search(line)
        if match and match.group(1) not in self.management_commands:
            self._error(doc, line_no, f"unknown management command: {match.group(1)}")

        for match in PY_SCRIPT_RE.finditer(line):
            target = match.group(1)
            # ``python manage.py ...`` is a Django command, validated above.
            if Path(target).name != "manage.py":
                self._resolve_command_path(doc, line_no, target, cwd)

        for match in PS_FILE_RE.finditer(line):
            self._resolve_command_path(doc, line_no, match.group(1), cwd)

        for match in DOCKER_FILE_RE.finditer(line):
            self._resolve_command_path(doc, line_no, match.group(1), cwd)

        for match in PIP_REQ_RE.finditer(line):
            self._resolve_command_path(doc, line_no, match.group(1), cwd)

        for match in COPY_RE.finditer(line):
            source = match.group(1)
            if source.lower() in {"item", "item", "file"}:
                continue
            self._resolve_command_path(doc, line_no, source, cwd)

        for match in PNPM_FILTER_RE.finditer(line):
            package, script = match.group(1), match.group(2)
            package = package.strip("'\"")
            manifest = self.packages.get(package)
            if manifest is None:
                self._error(doc, line_no, f"unknown workspace package: {package}")
            elif script not in manifest.get("scripts", {}):
                self._error(doc, line_no, f"package {package} has no script '{script}'")

        for match in PNPM_RUN_RE.finditer(line):
            script = match.group(1)
            if script not in self.root_scripts:
                self._error(doc, line_no, f"root package has no script '{script}'")

        # Every path-like token on a command line must exist unless it is an
        # output/generated path. The allowlist above keeps this precise.
        for token in PATH_TOKEN_RE.findall(line):
            self._check_path_token(doc, line_no, token, cwd)

    def check_fences(self, doc: Path, text: str) -> None:
        in_fence = False
        language = ""
        cwd = ROOT
        fence_start = 0
        line_no = 0
        pending = ""
        pending_line = 0

        for line_no, line in enumerate(text.splitlines(), start=1):
            if line.startswith("```"):
                if pending:
                    self._check_command_line(doc, pending_line, pending, cwd)
                    pending = ""
                if in_fence:
                    in_fence = False
                    language = ""
                else:
                    in_fence = True
                    language = line[3:].strip().lower()
                    cwd = ROOT
                    fence_start = line_no
                continue

            if not in_fence or language not in FENCE_LANGUAGES:
                continue

            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if language == "text" and not stripped.startswith(TEXT_COMMAND_PREFIXES):
                continue

            # Join PowerShell backtick and POSIX backslash continuations.
            if stripped.endswith("`") or stripped.endswith("\\"):
                pending += stripped[:-1].strip() + " "
                pending_line = pending_line or line_no
                continue
            command = (pending + stripped).strip()
            pending = ""
            pending_line = 0

            if command.startswith("$env:") or command.startswith("export "):
                continue
            command = re.sub(r"^(?:\$|PS>|>)\s+", "", command)

            if command.startswith("cd "):
                target = command[3:].strip().strip("'\"")
                self.checks += 1
                candidates = [cwd / target.replace("\\", "/"), ROOT / target.replace("\\", "/")]
                resolved = next((candidate for candidate in candidates if candidate.is_dir()), None)
                if resolved is None:
                    self._error(doc, line_no, f"cd target does not exist: {target}")
                else:
                    cwd = resolved.resolve()
                continue

            self._check_command_line(doc, line_no, command, cwd)

        if in_fence:
            self._error(doc, fence_start, "unclosed fenced code block")
        if pending:
            self._check_command_line(doc, pending_line, pending, cwd)

    def run(self) -> int:
        if not DOCS:
            self._error(ROOT, None, "no documentation files configured")
        for doc in DOCS:
            if not doc.exists():
                self._error(doc, None, "documentation file is missing")
                continue
            text = doc.read_text(encoding="utf-8")
            self.check_links(doc, text)
            self.check_retired_names(doc, text)
            self.check_fences(doc, text)

        self.check_landing_claims()
        self.check_rls_state()

        if self.errors:
            print(f"FAIL  documentation check ({len(self.errors)} problem(s))")
            for error in self.errors:
                print(f"  - {error}")
            return 1
        print(f"PASS  documentation check ({len(DOCS)} docs, {self.checks} assertions)")
        return 0

    def check_rls_state(self) -> None:
        """The runtime state of row-level security must not regress (F-03, F-08, C-5).

        The rules, one per failure mode the reviews found:

        1. no guarded file may claim that the policies are schema-level only,
           not in force or inert, or that the application runs as the
           superuser - the flip may not silently regress;
        2. no document may claim a count that is not the current one: the
           number of boundary proofs (the floor, read from the module next to
           the proofs), the smoke total, the end-to-end total, any suite's test
           count (the declared figures in ``scripts/figures.json``) and the
           guard's own assertion count. The rules are structural - a claim is
           recognised by the noun it counts and by its context - so they
           cannot be phrased around;
        3. every maintained document must name the runtime role, and a
           document that states which role the runtime connects as must name a
           runtime role, not the owner.
        """

        floor = load_proof_floor()
        figures = load_figures()
        for path in RLS_STATE_FILES:
            if not path.exists():
                self._error(path, None, "the file is missing")
                continue
            text = path.read_text(encoding="utf-8")
            self.checks += 1
            self.errors.extend(rls_state_claim_errors(path, text))
            self.errors.extend(runtime_role_errors(path, text))
            self.errors.extend(figure_errors(path, text, floor=floor, figures=figures))

        for path in DOCS:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            self.checks += 1
            self.errors.extend(rls_role_named_errors(path, text))

        # The assertion count is checked last: it is the number of checks the
        # guard performs, so it is only final here.
        for path in RLS_STATE_FILES:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            self.errors.extend(assertion_count_errors(path, text, self.checks))

    def check_landing_claims(self) -> None:
        """Run the landing-claim guard (F-01) as part of the docs check."""

        checker_path = ROOT / "scripts" / "check_claims.py"
        if not checker_path.exists():
            self._error(checker_path, None, "the landing claim check is missing")
            return
        spec = importlib.util.spec_from_file_location("enamel_check_claims", checker_path)
        if spec is None or spec.loader is None:  # pragma: no cover - import guard
            self._error(checker_path, None, "the landing claim check could not be loaded")
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        sources, errors = module.run()
        if not sources:
            self._error(checker_path, None, "the landing claim check found no source to scan")
            return
        self.checks += sources
        for error in errors:
            self.errors.append(error)


def main() -> int:
    return Checker().run()


if __name__ == "__main__":
    sys.exit(main())
