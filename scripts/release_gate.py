#!/usr/bin/env python3
"""Compute and enforce the repository release-review gate."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_FILE = Path(".zcode-plugin/plugin.json")
AUDIT_REPORT_RE = re.compile(r"^release-audits/v\d+\.\d+\.\d+\.md$")
REQUIRED_FIELDS = (
    "report-id",
    "role",
    "reviewer",
    "mode",
    "version",
    "package_fingerprint",
    "base_ref",
    "target_ref",
    "changed_files",
    "removed_files",
    "changed_agents",
    "breaking_impact",
    "reviewed_at",
    "verdict",
)
REQUIRED_HEADINGS = (
    "Scope",
    "Evidence",
    "Findings",
    "Agent Links",
    "Improvements",
    "Blockers",
    "Unverified",
    "Migration",
    "Hand-off",
)
NONE_RE = re.compile(r"(?i)^\s*none\s*$")
NO_AGENT_CHANGES_RE = re.compile(
    r"(?i)^\s*(?:[-*]\s*)?none\s*(?:—|–|-)\s*no agent contract changes\s*[.!]?\s*$"
)
PLACEHOLDER_RE = re.compile(r"(?i)(?:\bPENDING\b|\bTODO\b|\bTBD\b|\bplaceholder\b|待补)")
FINDING_ID_RE = re.compile(r"^[A-Z][A-Z0-9_-]*-\d{3,}$")


class GateError(Exception):
    """A release gate input or audit is invalid."""


def package_version(root: Path) -> str:
    manifest = root / PLUGIN_FILE
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError("cannot read plugin version from {}: {}".format(manifest, exc))
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise GateError("plugin version is missing or invalid in {}".format(manifest))
    return version


def excluded(relative: Path) -> bool:
    value = relative.as_posix()
    parts = relative.parts
    if not parts:
        return True
    if parts[0] == ".git" or AUDIT_REPORT_RE.fullmatch(value):
        return True
    if "__pycache__" in parts or ".tony-agents-pack" in parts:
        return True
    name = parts[-1]
    return (
        name.endswith(".pyc")
        or (len(parts) == 1 and name == "SHA256SUMS")
        or name.endswith(".tony-agents-pack.incoming")
        or name.endswith(".tony-agents-pack.restore")
    )


def git_output(root: Path, arguments: Sequence[str]) -> bytes:
    git = shutil.which("git")
    if not git:
        raise GateError("git is required to verify the release payload")
    result = subprocess.run(
        [git, "-C", str(root)] + list(arguments),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", "replace").strip()
        raise GateError("git {} failed: {}".format(" ".join(arguments), error or "exit {}".format(result.returncode)))
    return result.stdout


def is_git_repository(root: Path) -> bool:
    git = shutil.which("git")
    if not git:
        return False
    result = subprocess.run(
        [git, "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == b"true"


def nul_paths(data: bytes) -> List[str]:
    return [item for item in data.decode("utf-8", "surrogateescape").split("\0") if item]


def index_entry_map(root: Path) -> Dict[str, Tuple[str, str]]:
    """Return stage-0 Git index entries as path -> (mode, blob SHA)."""
    result: Dict[str, Tuple[str, str]] = {}
    for record in nul_paths(git_output(root, ["ls-files", "--stage", "-z"])):
        match = re.fullmatch(r"([0-7]{6}) ([0-9a-f]+) ([0-3])\t(.+)", record)
        if not match:
            raise GateError("cannot parse git ls-files --stage record: {!r}".format(record))
        mode, blob_sha, stage_value, path = match.groups()
        if stage_value != "0":
            raise GateError("Git index contains an unresolved merge entry: {} stage {}".format(path, stage_value))
        result[path] = (mode, blob_sha)
    return result


def untracked_payload_paths(root: Path) -> List[Path]:
    if not is_git_repository(root):
        return []
    return sorted(
        (
            Path(value)
            for value in nul_paths(git_output(root, ["ls-files", "--others", "--exclude-standard", "-z"]))
            if not excluded(Path(value))
        ),
        key=lambda item: item.as_posix(),
    )


def unstaged_payload_paths(root: Path) -> List[Path]:
    if not is_git_repository(root):
        return []
    return sorted(
        (Path(value) for value in nul_paths(git_output(root, ["diff", "--name-only", "-z"])) if not excluded(Path(value))),
        key=lambda item: item.as_posix(),
    )


def staged_payload_paths(root: Path) -> List[Path]:
    if not is_git_repository(root):
        return []
    return sorted(
        (
            Path(value)
            for value in nul_paths(git_output(root, ["diff", "--cached", "--name-only", "-z", "HEAD"]))
            if not excluded(Path(value))
        ),
        key=lambda item: item.as_posix(),
    )


def fallback_payload_paths(root: Path) -> List[Path]:
    files: List[Path] = []
    try:
        for directory, dirnames, filenames in os.walk(str(root), topdown=True, followlinks=False):
            current = Path(directory)
            kept_dirs = []
            for name in dirnames:
                path = current / name
                relative = path.relative_to(root)
                if excluded(relative):
                    continue
                if path.is_symlink():
                    files.append(relative)
                else:
                    kept_dirs.append(name)
            dirnames[:] = kept_dirs
            for name in filenames:
                relative = (current / name).relative_to(root)
                if not excluded(relative):
                    files.append(relative)
    except OSError as exc:
        raise GateError("cannot enumerate release files under {}: {}".format(root, exc))
    return sorted(set(files), key=lambda item: item.as_posix())


def payload_paths(root: Path) -> List[Path]:
    root = root.resolve()
    if not root.is_dir():
        raise GateError("repository root is not a directory: {}".format(root))
    if not is_git_repository(root):
        return fallback_payload_paths(root)
    tracked = list(index_entry_map(root))
    untracked = [path.as_posix() for path in untracked_payload_paths(root)]
    return sorted(
        (Path(value) for value in set(tracked) | set(untracked) if not excluded(Path(value))),
        key=lambda item: item.as_posix(),
    )


def package_fingerprint(root: Path) -> str:
    root = root.resolve()
    git_entries = index_entry_map(root) if is_git_repository(root) else {}
    digest = hashlib.sha256()
    for relative in payload_paths(root):
        path = root / relative
        git_entry = git_entries.get(relative.as_posix())
        if git_entry is not None:
            git_mode, blob_sha = git_entry
            executable = b"1" if git_mode == "100755" else b"0"
            payload = git_output(root, ["cat-file", "blob", blob_sha])
            if git_mode == "120000":
                entry_type = b"symlink"
            elif git_mode in {"100644", "100755"}:
                entry_type = b"file"
            else:
                raise GateError("unsupported tracked Git mode {} for {}".format(git_mode, relative.as_posix()))
        else:
            mode = path.lstat().st_mode
            executable = b"1" if mode & 0o111 else b"0"
            if stat.S_ISLNK(mode):
                payload = os.readlink(str(path)).encode("utf-8", "surrogateescape")
                entry_type = b"symlink"
            elif stat.S_ISREG(mode):
                payload = path.read_bytes()
                entry_type = b"file"
            else:
                raise GateError("unsupported release payload entry type: {}".format(relative.as_posix()))
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry_type)
        digest.update(b"\0")
        digest.update(executable)
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def parse_audit(text: str) -> Tuple[Dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise GateError("audit frontmatter must start with ---")
    try:
        closing = lines.index("---", 1)
    except ValueError:
        raise GateError("audit frontmatter closing --- is missing")
    metadata: Dict[str, str] = {}
    for number, line in enumerate(lines[1:closing], start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*", line)
        if not match:
            raise GateError("invalid audit frontmatter line {}: {!r}".format(number, line))
        key, value = match.groups()
        if key in metadata:
            raise GateError("duplicate audit frontmatter field: {}".format(key))
        metadata[key] = value.strip('"\'')
    return metadata, "\n".join(lines[closing + 1 :])


def parse_list_field(metadata: Dict[str, str], field: str) -> List[str]:
    raw = metadata.get(field, "").strip()
    if not raw:
        raise GateError("audit {} must not be empty".format(field))
    if PLACEHOLDER_RE.search(raw):
        raise GateError("audit {} contains a placeholder".format(field))
    if raw.startswith("["):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GateError("audit {} must be a JSON array or comma string: {}".format(field, exc))
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise GateError("audit {} JSON value must be an array of strings".format(field))
        items = [item.strip() for item in value if item.strip()]
    elif raw.lower() == "none":
        items = []
    else:
        items = [item.strip() for item in raw.split(",") if item.strip()]
    if len(items) != len(set(items)):
        raise GateError("audit {} contains duplicate values".format(field))
    for item in items:
        path = Path(item)
        if path.is_absolute() or ".." in path.parts or item.startswith("./"):
            raise GateError("audit {} contains unsafe path: {}".format(field, item))
    return items


def ordered_sections(body: str) -> Dict[str, str]:
    matches = list(re.finditer(r"(?m)^## ([^\n]+)\s*$", body))
    names = [match.group(1).strip() for match in matches]
    if names != list(REQUIRED_HEADINGS):
        raise GateError(
            "audit level-2 headings must exactly match required order: expected={}, found={}".format(
                list(REQUIRED_HEADINGS), names
            )
        )
    result: Dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        result[names[index]] = body[match.end() : end].strip()
    return result


def require_real_text(label: str, text: str, minimum: int = 1) -> None:
    value = text.strip()
    if len(value) < minimum or PLACEHOLDER_RE.search(value):
        raise GateError("audit {} is too short or contains a placeholder".format(label))


def validate_base_ref(root: Path, base_ref: str, version: str) -> None:
    if base_ref in {version, "v" + version}:
        raise GateError("audit base_ref must not equal target version")
    git_output(root, ["rev-parse", "--is-inside-work-tree"])
    git_output(root, ["show-ref", "--verify", "--quiet", "refs/tags/" + base_ref])
    git_output(root, ["rev-parse", "--verify", "refs/tags/{}^{{commit}}".format(base_ref)])


def parse_name_status(data: bytes) -> Tuple[set, set]:
    fields = nul_paths(data)
    changed = set()
    removed = set()
    index = 0
    while index < len(fields):
        status_value = fields[index]
        index += 1
        code = status_value[:1]
        if code in {"R", "C"}:
            if index + 1 >= len(fields):
                raise GateError("git name-status output ended inside a rename/copy record")
            source, target = fields[index], fields[index + 1]
            index += 2
            changed.add(target)
            if code == "R":
                removed.add(source)
        else:
            if index >= len(fields):
                raise GateError("git name-status output ended without a path")
            path = fields[index]
            index += 1
            if code == "D":
                removed.add(path)
            else:
                changed.add(path)
    return changed, removed


def release_diff(root: Path, base_ref: str, target_ref: str, commit: Optional[str]) -> Tuple[List[str], List[str]]:
    worktree = target_ref.startswith("WORKTREE:")
    if worktree:
        if staged_payload_paths(root):
            data = git_output(root, ["diff", "--cached", "--name-status", "-z", "--find-renames", base_ref])
        else:
            data = git_output(root, ["diff", "--name-status", "-z", "--find-renames", "{}..HEAD".format(base_ref)])
        changed, removed = parse_name_status(data)
    else:
        target = commit or target_ref
        if commit and target_ref != commit:
            raise GateError("audit target_ref must equal commit {}".format(commit))
        git_output(root, ["rev-parse", "--verify", "{}^{{commit}}".format(target)])
        data = git_output(root, ["diff", "--name-status", "-z", "--find-renames", "{}..{}".format(base_ref, target)])
        changed, removed = parse_name_status(data)
    return (
        sorted(path for path in changed if not excluded(Path(path))),
        sorted(path for path in removed if not excluded(Path(path))),
    )


def format_set_mismatch(label: str, declared: Sequence[str], actual: Sequence[str]) -> str:
    missing = sorted(set(actual) - set(declared))
    extra = sorted(set(declared) - set(actual))
    return "audit {} does not match git diff: missing={}, extra={}".format(label, missing, extra)


def validate_scope(
    root: Path,
    metadata: Dict[str, str],
    scope: str,
    actual_changed: Sequence[str],
    actual_removed: Sequence[str],
) -> Tuple[List[str], List[str], List[str]]:
    changed_files = parse_list_field(metadata, "changed_files")
    removed_files = parse_list_field(metadata, "removed_files")
    changed_agents = parse_list_field(metadata, "changed_agents")
    require_real_text("Scope", scope)
    if set(changed_files) != set(actual_changed):
        raise GateError(format_set_mismatch("changed_files", changed_files, actual_changed))
    if set(removed_files) != set(actual_removed):
        raise GateError(format_set_mismatch("removed_files", removed_files, actual_removed))
    expected_agents = {
        Path(relative).stem
        for relative in set(actual_changed) | set(actual_removed)
        if relative.startswith("agents/") and relative.endswith(".md")
    }
    if set(changed_agents) != expected_agents:
        raise GateError(
            "audit changed_agents does not match git diff agents: missing={}, extra={}".format(
                sorted(expected_agents - set(changed_agents)), sorted(set(changed_agents) - expected_agents)
            )
        )
    for relative in changed_files:
        try:
            (root / relative).lstat()
        except FileNotFoundError:
            raise GateError("git changed file does not exist in target: {}".format(relative))
    for label, items in (
        ("changed_files", changed_files),
        ("removed_files", removed_files),
        ("changed_agents", changed_agents),
    ):
        if items:
            for item in items:
                if "`{}`".format(item) not in scope:
                    raise GateError("Scope is missing {} item `{}`".format(label, item))
        elif not re.search(r"(?im)^\s*(?:[-*]\s*)?{}\s*:\s*none\s*$".format(label), scope):
            raise GateError("Scope must explicitly say {}: none".format(label))
    return changed_files, removed_files, changed_agents


def parse_markdown_table(section: str, expected_header: Sequence[str], label: str) -> List[List[str]]:
    require_real_text(label, section)
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    if len(lines) < 3 or any(not line.startswith("|") or not line.endswith("|") for line in lines):
        raise GateError("{} must be a Markdown table with at least one data row".format(label))

    def cells(line: str) -> List[str]:
        return [cell.strip() for cell in line.strip("|").split("|")]

    header = cells(lines[0])
    if header != list(expected_header):
        raise GateError("{} table header must be | {} |".format(label, " | ".join(expected_header)))
    separator = cells(lines[1])
    if len(separator) != len(expected_header) or any(not re.fullmatch(r":?-{3,}:?", item) for item in separator):
        raise GateError("{} table separator is invalid".format(label))
    rows = [cells(line) for line in lines[2:]]
    if any(len(row) != len(expected_header) for row in rows):
        raise GateError("{} table row has the wrong number of columns".format(label))
    for row in rows:
        for value in row:
            require_real_text(label + " cell", value)
    return rows


def validate_evidence(section: str) -> set:
    rows = parse_markdown_table(section, ("evidence-id", "check", "result", "evidence"), "Evidence")
    evidence_ids = set()
    passed_checks = set()
    for evidence_id, check, result, evidence in rows:
        if evidence_id in evidence_ids:
            raise GateError("Evidence contains duplicate evidence-id: {}".format(evidence_id))
        evidence_ids.add(evidence_id)
        category = check.strip().lower()
        if category in {"validation", "tests", "fingerprint"} and result.strip().upper() == "PASS":
            passed_checks.add(category)
        require_real_text("Evidence evidence", evidence)
    missing = sorted({"validation", "tests", "fingerprint"} - passed_checks)
    if missing:
        raise GateError("Evidence must contain PASS rows for: {}".format(", ".join(missing)))
    return evidence_ids


def validate_findings(section: str) -> Tuple[set, set]:
    if NONE_RE.fullmatch(section):
        return set(), set()
    rows = parse_markdown_table(section, ("finding-id", "severity", "status", "summary"), "Findings")
    seen = set()
    open_lower = set()
    for finding_id, severity, status_value, summary in rows:
        if not FINDING_ID_RE.fullmatch(finding_id):
            raise GateError("Findings has invalid finding-id: {}".format(finding_id))
        if finding_id in seen:
            raise GateError("Findings contains duplicate finding-id: {}".format(finding_id))
        seen.add(finding_id)
        severity = severity.upper()
        status_value = status_value.upper()
        if severity not in {"P0", "P1", "P2", "P3"}:
            raise GateError("Findings severity must be P0, P1, P2, or P3")
        if status_value not in {"OPEN", "FIXED", "ACCEPTED_RISK"}:
            raise GateError("Findings status must be OPEN, FIXED, or ACCEPTED_RISK")
        require_real_text("Findings summary", summary)
        if severity in {"P0", "P1"} and status_value != "FIXED":
            raise GateError("PASS audit rejects P0/P1 findings unless status is FIXED")
        if severity in {"P2", "P3"} and status_value == "OPEN":
            open_lower.add(finding_id)
    return seen, open_lower


def validate_agent_links(
    root: Path, section: str, agents: Sequence[str], removed_files: Sequence[str], version: str
) -> None:
    if not agents:
        if not NO_AGENT_CHANGES_RE.fullmatch(section):
            raise GateError("Agent Links must say 'none — no agent contract changes' when changed_agents is empty")
        return
    require_real_text("Agent Links", section)
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    if any(not re.fullmatch(r"[-*]\s+https://github\.com/tony-apan/zcode_skills/blob/v[^/]+/agents/[a-z0-9-]+\.md", line) for line in lines):
        raise GateError("Agent Links must contain only exact versioned URL bullets")
    removed_agents = {
        Path(relative).stem
        for relative in removed_files
        if relative.startswith("agents/") and relative.endswith(".md")
    }
    expected_urls = set()
    for agent in agents:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", agent):
            raise GateError("audit changed_agents contains invalid agent name: {}".format(agent))
        if agent not in removed_agents and not (root / "agents" / (agent + ".md")).is_file():
            raise GateError("audit changed agent file is missing: agents/{}.md".format(agent))
        expected_urls.add("https://github.com/tony-apan/zcode_skills/blob/v{}/agents/{}.md".format(version, agent))
    actual_urls = {re.sub(r"^[-*]\s+", "", line) for line in lines}
    if actual_urls != expected_urls:
        raise GateError("Agent Links mismatch: expected={}, found={}".format(sorted(expected_urls), sorted(actual_urls)))


def validate_improvements(section: str, evidence_ids: set) -> set:
    rows = parse_markdown_table(section, ("improvement-id", "user-value", "evidence-ref"), "Improvements")
    improvement_ids = set()
    references = set()
    for improvement_id, user_value, evidence_ref in rows:
        if improvement_id in improvement_ids:
            raise GateError("Improvements contains duplicate improvement-id: {}".format(improvement_id))
        improvement_ids.add(improvement_id)
        require_real_text("Improvements user-value", user_value)
        if evidence_ref not in evidence_ids:
            raise GateError("Improvements evidence-ref does not exist in Evidence: {}".format(evidence_ref))
        references.add(evidence_ref)
    return improvement_ids


def validate_migration(section: str, impact: str) -> None:
    require_real_text("Migration", section)
    expected_keys = ("breaking-impact", "ordinary-users", "maintainers", "upgrade")
    values: Dict[str, str] = {}
    for line in [item for item in section.splitlines() if item.strip()]:
        match = re.fullmatch(r"([a-z-]+):\s*(.+)", line.strip())
        if not match or match.group(1) not in expected_keys or match.group(1) in values:
            raise GateError("Migration must contain only the four fixed key-value lines")
        values[match.group(1)] = match.group(2).strip()
    if tuple(values) != expected_keys:
        raise GateError("Migration keys must be ordered: {}".format(", ".join(expected_keys)))
    if impact not in {"none", "additive", "breaking"} or values["breaking-impact"] != impact:
        raise GateError("Migration breaking-impact must equal frontmatter breaking_impact")
    for key in ("ordinary-users", "maintainers", "upgrade"):
        require_real_text("Migration " + key, values[key], minimum=20)
    if impact == "breaking" and not (
        re.search(r"(?i)README|update", values["upgrade"])
        or re.search(r"(?:^|\s)(?:\./|python(?:3)?\s|py\s+-3\s)", values["upgrade"])
    ):
        raise GateError("breaking Migration upgrade must cite README/update or an explicit command")


def validate_hand_off(section: str) -> set:
    rows = parse_markdown_table(section, ("owner", "action", "status"), "Hand-off")
    finding_refs = set()
    for owner, action, status_value in rows:
        require_real_text("Hand-off owner", owner)
        require_real_text("Hand-off action", action)
        status_value = status_value.upper()
        if status_value not in {"READY", "COMPLETE", "BLOCKED", "UNAFFECTED"}:
            raise GateError("Hand-off status must be READY, COMPLETE, BLOCKED, or UNAFFECTED")
        if status_value == "BLOCKED":
            raise GateError("PASS audit Hand-off must not contain BLOCKED")
        finding_refs.update(re.findall(r"\b[A-Z][A-Z0-9_-]*-\d{3,}\b", action))
    return finding_refs


def check_gate(root: Path, requested_version: Optional[str], commit: Optional[str]) -> str:
    root = root.resolve()
    git_output(root, ["rev-parse", "--is-inside-work-tree"])
    version = package_version(root)
    if requested_version is not None and requested_version != version:
        raise GateError("requested version {} does not match plugin version {}".format(requested_version, version))
    audit_path = root / "release-audits" / ("v" + version + ".md")
    if not audit_path.is_file():
        raise GateError("PASS audit is missing: {}".format(audit_path))
    try:
        metadata, body = parse_audit(audit_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise GateError("cannot read audit {}: {}".format(audit_path, exc))
    missing = [field for field in REQUIRED_FIELDS if not metadata.get(field)]
    if missing:
        raise GateError("audit is missing required field(s): {}".format(", ".join(missing)))
    for field in REQUIRED_FIELDS:
        if PLACEHOLDER_RE.search(metadata[field]):
            raise GateError("audit {} contains a placeholder".format(field))
    validate_base_ref(root, metadata["base_ref"], version)
    untracked = untracked_payload_paths(root)
    if untracked:
        raise GateError(
            "stage release files before audit: {}".format(", ".join(path.as_posix() for path in untracked))
        )
    unstaged = unstaged_payload_paths(root)
    if unstaged:
        raise GateError(
            "stage all release changes before audit: {}".format(", ".join(path.as_posix() for path in unstaged))
        )
    fingerprint = package_fingerprint(root)
    expected = {
        "version": version,
        "verdict": "PASS",
        "package_fingerprint": fingerprint,
        "role": "github",
        "reviewer": "github",
        "mode": "RELEASE_GATE",
    }
    for field, value in expected.items():
        if metadata[field] != value:
            raise GateError("audit {} must be {!r}, found {!r}".format(field, value, metadata[field]))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", metadata["reviewed_at"]):
        raise GateError("audit reviewed_at must be an ISO-8601 timestamp with timezone")
    target_ref = metadata["target_ref"]
    worktree_ref = "WORKTREE:" + fingerprint
    if target_ref.startswith("WORKTREE:") and target_ref != worktree_ref:
        raise GateError("audit WORKTREE target_ref does not match current fingerprint")
    if commit and target_ref not in {commit, worktree_ref}:
        raise GateError("audit target_ref must equal commit {} or {}".format(commit, worktree_ref))
    actual_changed, actual_removed = release_diff(root, metadata["base_ref"], target_ref, commit)

    audit_sections = ordered_sections(body)
    changed_files, removed_files, changed_agents = validate_scope(
        root, metadata, audit_sections["Scope"], actual_changed, actual_removed
    )
    evidence_ids = validate_evidence(audit_sections["Evidence"])
    _, open_lower = validate_findings(audit_sections["Findings"])
    validate_agent_links(root, audit_sections["Agent Links"], changed_agents, removed_files, version)
    improvement_ids = validate_improvements(audit_sections["Improvements"], evidence_ids)
    if not NONE_RE.fullmatch(audit_sections["Blockers"]):
        raise GateError("PASS audit Blockers must explicitly be None")
    if not NONE_RE.fullmatch(audit_sections["Unverified"]):
        raise GateError("PASS audit Unverified must explicitly be None")
    validate_migration(audit_sections["Migration"], metadata["breaking_impact"])
    hand_off_refs = validate_hand_off(audit_sections["Hand-off"])
    follow_up_text = audit_sections["Improvements"] + "\n" + audit_sections["Hand-off"]
    for finding_id in open_lower:
        if finding_id not in follow_up_text:
            raise GateError("open P2/P3 finding {} must appear in Improvements or Hand-off".format(finding_id))
    return "RELEASE GATE PASS: v{} {}".format(version, fingerprint)


def template(root: Path) -> str:
    version = package_version(root)
    fingerprint = package_fingerprint(root)
    return """---
report-id: PENDING
role: github
reviewer: github
mode: RELEASE_GATE
version: {version}
package_fingerprint: {fingerprint}
base_ref: PENDING
target_ref: WORKTREE:{fingerprint}
changed_files: ["PENDING"]
removed_files: []
changed_agents: []
breaking_impact: PENDING
reviewed_at: PENDING
verdict: INCONCLUSIVE
---

# Release Gate v{version}

## Scope

- changed_files: `PENDING`
- removed_files: none
- changed_agents: none

## Evidence

| evidence-id | check | result | evidence |
|---|---|---|---|
| PENDING | validation | PENDING | PENDING |
| PENDING | tests | PENDING | PENDING |
| PENDING | fingerprint | PENDING | PENDING |

## Findings

| finding-id | severity | status | summary |
|---|---|---|---|
| PENDING | PENDING | PENDING | PENDING |

## Agent Links

PENDING

## Improvements

| improvement-id | user-value | evidence-ref |
|---|---|---|
| PENDING | PENDING | PENDING |

## Blockers

none

## Unverified

none

## Migration

breaking-impact: PENDING
ordinary-users: PENDING
maintainers: PENDING
upgrade: PENDING

## Hand-off

| owner | action | status |
|---|---|---|
| PENDING | PENDING | PENDING |
""".format(version=version, fingerprint=fingerprint)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("fingerprint", "template"):
        child = subparsers.add_parser(name)
        child.add_argument("--root", type=Path, default=ROOT)
    check = subparsers.add_parser("check")
    check.add_argument("--root", type=Path, default=ROOT)
    check.add_argument("--version")
    check.add_argument("--commit")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "fingerprint":
            print(package_fingerprint(args.root))
        elif args.command == "template":
            print(template(args.root), end="")
        else:
            print(check_gate(args.root, args.version, args.commit))
        return 0
    except (GateError, OSError, UnicodeError) as exc:
        print("RELEASE GATE FAILED: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
