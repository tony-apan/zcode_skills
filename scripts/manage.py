#!/usr/bin/env python3
"""Validate, install, update, roll back, and uninstall tony-agents-pack."""

import argparse
import datetime as dt
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "agents"
PLUGIN_FILE = ROOT / ".zcode-plugin" / "plugin.json"
PACKAGE_NAME = "tony-agents-pack"
EXPECTED_AGENT_COUNT = 20
COLORS = {"red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"}
TOOLS = {"Read", "Glob", "Grep", "Write", "Edit", "Bash", "WebFetch", "WebSearch", "TodoWrite"}
FORBIDDEN_PUBLISHED_KEYS = {"model", "thoughtLevel", "skills"}
PUBLISHED_MODEL_NAME_RE = re.compile(r"(?i)\b(?:glm|gpt|deepseek|kimi|gemini)\b")
INJECTION_DEFENSE_RE = re.compile(r"注入防御|不可信内容(?:与[^\n#]*)?防线|不可信内容|不可信数据|提示注入")
COMMON_ACCEPTANCE_MARKER = "report-id / role / requirement-version / snapshot(commit/source/artifact SHA/build-id) / generated-at"
ACCEPTANCE_AGENTS = {"shencha", "shencha-content", "shencha-ui", "verifier", "shencha-final"}
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(?:\s*(.*))?$")


class PackError(Exception):
    pass


def transactional(operation: str):
    def decorate(method):
        @functools.wraps(method)
        def wrapped(self, *args, **kwargs):
            self._active_snapshot = None
            try:
                return method(self, *args, **kwargs)
            except Exception as original_error:
                snapshot = self._active_snapshot
                if snapshot is None:
                    raise
                try:
                    self.restore_snapshot(snapshot)
                except Exception as rollback_error:
                    raise PackError(
                        "操作失败且自动回滚失败 ({}): 原始错误: {}; 回滚错误: {}".format(
                            operation, original_error, rollback_error
                        )
                    ) from original_error
                raise PackError(
                    "操作失败且已自动回滚 ({}): {}".format(operation, original_error)
                ) from original_error
            finally:
                self._active_snapshot = None

        return wrapped

    return decorate


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def unique_id() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_json(path: Path, value: object) -> None:
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write(path, data.encode("utf-8"))


def parse_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PackError("invalid quoted frontmatter value: {}".format(exc))
        if not isinstance(parsed, str):
            raise PackError("frontmatter scalar must be a string")
        return parsed
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def frontmatter_parts(text: str) -> Tuple[List[str], List[str]]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise PackError("frontmatter must start with an exact --- delimiter")
    closing = None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            closing = index
            break
    if closing is None:
        raise PackError("frontmatter closing --- delimiter is missing")
    return lines[1:closing], lines[closing + 1 :]


def parse_frontmatter(text: str) -> Dict[str, object]:
    frontmatter, _ = frontmatter_parts(text)
    result: Dict[str, object] = {}
    index = 0
    while index < len(frontmatter):
        raw = frontmatter[index].rstrip("\r\n")
        if not raw or raw.lstrip().startswith("#") or raw[:1].isspace():
            index += 1
            continue
        match = KEY_RE.match(raw)
        if not match:
            raise PackError("invalid top-level frontmatter line: {!r}".format(raw))
        key, value = match.group(1), (match.group(2) or "").strip()
        if key in result:
            raise PackError("duplicate top-level frontmatter key: {}".format(key))
        if key in {"tools", "disallowedTools"}:
            if value:
                if not (value.startswith("[") and value.endswith("]")):
                    raise PackError("tools must be an inline array or an indented list")
                inner = value[1:-1].strip()
                result[key] = [] if not inner else [parse_scalar(item.strip()) for item in inner.split(",")]
            else:
                items: List[str] = []
                lookahead = index + 1
                while lookahead < len(frontmatter):
                    candidate = frontmatter[lookahead].rstrip("\r\n")
                    item = re.match(r"^\s+-\s+(.+?)\s*$", candidate)
                    if item:
                        items.append(parse_scalar(item.group(1)))
                        lookahead += 1
                        continue
                    if not candidate.strip() or candidate.lstrip().startswith("#"):
                        lookahead += 1
                        continue
                    break
                result[key] = items
                index = lookahead - 1
        else:
            result[key] = parse_scalar(value)
        index += 1
    return result


def validate_agent_text(text: str, expected_name: Optional[str] = None, published: bool = False) -> Dict[str, object]:
    metadata = parse_frontmatter(text)
    for key in ("name", "description", "color", "tools"):
        if key not in metadata:
            raise PackError("missing required frontmatter key: {}".format(key))
    name = metadata["name"]
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise PackError("name must use lowercase letters, digits, and single hyphens")
    if expected_name is not None and name != expected_name:
        raise PackError("filename/name mismatch: {} != {}".format(expected_name, name))
    if not isinstance(metadata["description"], str) or not metadata["description"].strip():
        raise PackError("description must be a non-empty string")
    if metadata["color"] not in COLORS:
        raise PackError("invalid color: {}".format(metadata["color"]))
    if not isinstance(metadata["tools"], list) or not metadata["tools"]:
        raise PackError("tools must be a non-empty list")
    invalid_tools = [tool for tool in metadata["tools"] if tool not in TOOLS]
    if invalid_tools:
        raise PackError("invalid tools: {}".format(", ".join(invalid_tools)))
    if published:
        forbidden = sorted(FORBIDDEN_PUBLISHED_KEYS.intersection(metadata))
        if forbidden:
            raise PackError("published agent contains local-only keys: {}".format(", ".join(forbidden)))
        if PUBLISHED_MODEL_NAME_RE.search(metadata["description"]):
            raise PackError("published description contains a concrete model or vendor name")
    model = metadata.get("model")
    if model is not None and (not isinstance(model, str) or not model.startswith("custom:")):
        raise PackError("model must be a custom: model identifier")
    thought = metadata.get("thoughtLevel")
    if thought is not None and (not isinstance(thought, str) or not thought):
        raise PackError("thoughtLevel must be a non-empty string")
    return metadata


def render_agent(source_text: str, model_fields: Dict[str, str]) -> str:
    frontmatter, body = frontmatter_parts(source_text)
    filtered: List[str] = []
    for line in frontmatter:
        match = KEY_RE.match(line.rstrip("\r\n")) if line and not line[:1].isspace() else None
        if match and match.group(1) in {"model", "thoughtLevel"}:
            continue
        filtered.append(line)
    for key in ("model", "thoughtLevel"):
        if key in model_fields:
            filtered.append("{}: {}\n".format(key, json.dumps(model_fields[key], ensure_ascii=False)))
    rendered = "---\n" + "".join(filtered) + "---\n" + "".join(body)
    validate_agent_text(rendered)
    return rendered


def read_model_map(path: Optional[str], known_names: List[str]) -> Dict[str, Dict[str, str]]:
    if not path:
        return {}
    try:
        value = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackError("cannot read model map: {}".format(exc))
    if not isinstance(value, dict):
        raise PackError("model map must be a JSON object")
    unknown = sorted(set(value) - set(known_names))
    if unknown:
        raise PackError("model map contains unknown agents: {}".format(", ".join(unknown)))
    result: Dict[str, Dict[str, str]] = {}
    for name, mapping in value.items():
        if not isinstance(mapping, dict) or set(mapping) - {"model", "thoughtLevel"}:
            raise PackError("model map entry for {} has invalid fields".format(name))
        model = mapping.get("model")
        thought = mapping.get("thoughtLevel")
        if not isinstance(model, str) or not model.startswith("custom:"):
            raise PackError("model map entry for {} requires a custom: model".format(name))
        if thought is not None and (not isinstance(thought, str) or not thought):
            raise PackError("thoughtLevel for {} must be a non-empty string".format(name))
        result[name] = {"model": model}
        if thought is not None:
            result[name]["thoughtLevel"] = thought
    return result


def source_agents() -> Dict[str, Path]:
    return {path.stem: path for path in sorted(AGENTS_DIR.glob("*.md"))}


def read_plugin() -> dict:
    try:
        value = json.loads(PLUGIN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackError("cannot read plugin manifest: {}".format(exc))
    if not isinstance(value, dict):
        raise PackError("plugin manifest must be an object")
    return value


def package_version() -> str:
    version = read_plugin().get("version")
    if not isinstance(version, str):
        raise PackError("plugin version is missing")
    return version


def validate_package(verbose: bool = True) -> bool:
    errors: List[str] = []
    required_files = (
        ".zcode-plugin/plugin.json",
        ".github/workflows/validate.yml",
        "LICENSE",
        "README.md",
        "INSTALL-FOR-AI.md",
        "CHANGELOG.md",
        "scripts/manage.py",
        "scripts/model_inventory.py",
        "scripts/install.sh",
        "scripts/install.ps1",
        "scripts/release.sh",
        "scripts/release_gate.py",
        "scripts/setup-hooks.sh",
        ".githooks/pre-push",
        "release-audits/README.md",
        "tests/test_manage.py",
        "tests/test_model_inventory.py",
        "tests/test_release_gate.py",
    )
    for relative in required_files:
        if not (ROOT / relative).is_file():
            errors.append("missing required release file: {}".format(relative))
    agents = source_agents()
    if len(agents) != EXPECTED_AGENT_COUNT:
        errors.append("expected exactly {} agents, found {}".format(EXPECTED_AGENT_COUNT, len(agents)))
    for name, path in agents.items():
        try:
            text = path.read_text(encoding="utf-8")
            metadata = validate_agent_text(text, name, published=True)
            if "# 模型需求：" not in text:
                raise PackError("missing model requirement comment")
            if not INJECTION_DEFENSE_RE.search(text):
                raise PackError("missing prompt-injection defense")
            if name in ACCEPTANCE_AGENTS:
                for marker in ("PASS", "BLOCK", "INCONCLUSIVE", COMMON_ACCEPTANCE_MARKER):
                    if marker not in text:
                        raise PackError("missing acceptance marker: {}".format(marker))
            if name == "github":
                forbidden_tools = {"Bash", "Write", "Edit"}
                if forbidden_tools.intersection(metadata["tools"]):
                    raise PackError("github tools must be strictly read-only")
                if not forbidden_tools.issubset(set(metadata.get("disallowedTools", []))):
                    raise PackError("github disallowedTools must include Bash, Write, and Edit")
                for marker in (
                    "REPO_REVIEW",
                    "README_POLISH",
                    "RELEASE_GATE",
                    "RELEASE_NOTES",
                    "target_version",
                    "package_fingerprint",
                    "base_ref",
                    "target_ref",
                    "changed_files",
                    "removed_files",
                    "changed_agents",
                    "reviewer: github",
                    "| evidence-id | check | result | evidence |",
                    "| finding-id | severity | status | summary |",
                    "| improvement-id | user-value | evidence-ref |",
                    "| owner | action | status |",
                    "breaking_impact",
                    "## Scope",
                    "## Evidence",
                    "## Findings",
                    "## Agent Links",
                    "## Improvements",
                    "## Blockers",
                    "## Unverified",
                    "## Migration",
                    "## Hand-off",
                    "blob/v<target_version>",
                ):
                    if marker not in text:
                        raise PackError("missing github release-gate marker: {}".format(marker))
            role_markers = {
                "frontend": ("## 模式", "可访问性", "截图"),
                "mermaid": ("永远只输出一个 `mermaid` 代码块", "`graph TD`", "`click`", "集合"),
                "shencha-content": ("review_profile=seo|conversion|social|email|microcopy",),
                "outreach": ("SEND_BLOCKED",),
                "huoke": ("## 证据分类",),
                "jiankong": ("pending",),
                "tijian": ("ACTIVE_SECURITY",),
                "coder-ds": ("MODE=PARALLEL_ALTERNATIVE", "MODE=OVERFLOW"),
            }
            for marker in role_markers.get(name, ()):
                if marker not in text:
                    raise PackError("missing role contract marker: {}".format(marker))
            if verbose:
                print("OK agent {}".format(path.name))
        except (OSError, UnicodeError, PackError) as exc:
            errors.append("{}: {}".format(path, exc))
    try:
        plugin = read_plugin()
        if plugin.get("name") != PACKAGE_NAME:
            errors.append("plugin name must be {}".format(PACKAGE_NAME))
        version = plugin.get("version")
        if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
            errors.append("plugin version must be valid SemVer")
        if plugin.get("agents") != "agents":
            errors.append("plugin agents must equal 'agents'")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        if isinstance(version, str) and not re.search(r"^## \[{}\]".format(re.escape(version)), changelog, re.MULTILINE):
            errors.append("CHANGELOG.md does not contain version {}".format(version))
    except (OSError, UnicodeError, PackError) as exc:
        errors.append("plugin/changelog validation failed: {}".format(exc))
    try:
        workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
        for action, major in (("checkout", "v4"), ("setup-python", "v5"), ("upload-artifact", "v4")):
            pattern = r"actions/{}@[0-9a-f]{{40}}\s+#\s*{}\b".format(re.escape(action), major)
            if not re.search(pattern, workflow):
                errors.append("workflow action {} must use a full commit SHA with # {}".format(action, major))
        if "fetch-depth: 0" not in workflow:
            errors.append("workflow checkout must fetch full history and tags with fetch-depth: 0")
        if re.search(r"uses:\s+actions/[^@\s]+@v\d+\b", workflow):
            errors.append("workflow contains a floating official action major tag")
    except (OSError, UnicodeError) as exc:
        errors.append("cannot validate workflow action pins: {}".format(exc))
    for filename in ("README.md", "INSTALL-FOR-AI.md"):
        try:
            text = (ROOT / filename).read_text(encoding="utf-8")
            if "ZCode 专用" not in text and "ZCode-only" not in text:
                errors.append("{} must identify the package as ZCode 专用 or ZCode-only".format(filename))
            for forbidden in ("/Users/tony", "<OWNER>"):
                if forbidden in text:
                    errors.append("{} contains forbidden text {}".format(filename, forbidden))
            direct_read_patterns = (
                r"(?im)^\s*(?:cat|less|more|head|tail)\s+[^\n]*config\.json",
                r"(?im)^\s*(?:请|让 AI|AI 应|AI 先|使用 Read|用 Read)[^\n]*(?:读取|读|Read|cat)[^\n]*config\.json",
            )
            if any(re.search(pattern, text) for pattern in direct_read_patterns):
                errors.append("{} instructs AI to read config.json directly".format(filename))
            if "scripts/model_inventory.py" not in text or "model-inventory" not in text:
                errors.append("{} does not document the sanitized model inventory helper".format(filename))
        except (OSError, UnicodeError) as exc:
            errors.append("cannot read {}: {}".format(filename, exc))
    if errors:
        print("VALIDATION FAILED ({} error(s))".format(len(errors)), file=sys.stderr)
        for error in errors:
            print("ERROR {}".format(error), file=sys.stderr)
        return False
    print("VALIDATION OK: {} agents, plugin {} v{}".format(len(agents), PACKAGE_NAME, package_version()))
    return True


class Manager:
    def __init__(self, target_dir: Path):
        self.target_dir = target_dir.expanduser().resolve()
        self.meta_dir = self.target_dir / ".tony-agents-pack"
        self.state_file = self.meta_dir / "state.json"
        self.snapshots_dir = self.meta_dir / "snapshots"
        self.bases_dir = self.meta_dir / "bases"
        self.backups_dir = self.meta_dir / "backups"
        self._active_snapshot: Optional[Path] = None

    def load_state(self, required: bool = False) -> Optional[dict]:
        if not self.state_file.exists():
            if required:
                raise PackError("no installed package state found in {}".format(self.state_file))
            return None
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PackError("cannot read state: {}".format(exc))
        if not isinstance(state, dict) or not isinstance(state.get("files"), dict):
            raise PackError("state.json has an invalid structure")
        return state

    def create_snapshot(self, names: List[str], operation: str) -> Path:
        snapshot = self.snapshots_dir / unique_id()
        files_dir = snapshot / "files"
        manifest = {"created_at": now_iso(), "operation": operation, "files": {}, "state_existed": self.state_file.exists()}
        for name in sorted(names):
            target = self.target_dir / (name + ".md")
            existed = target.exists()
            manifest["files"][name] = {"existed": existed}
            if existed:
                atomic_write(files_dir / (name + ".md"), target.read_bytes())
        if self.state_file.exists():
            atomic_write(snapshot / "state.json", self.state_file.read_bytes())
        atomic_json(snapshot / "snapshot.json", manifest)
        print("Snapshot: {}".format(snapshot))
        return snapshot

    @transactional("install")
    def install(self, dry_run: bool, model_map_path: Optional[str], force: bool = False) -> None:
        previous_state = self.load_state()
        if previous_state is not None and not force:
            raise PackError("package state already exists; use update instead")
        agents = source_agents()
        model_map = read_model_map(model_map_path, list(agents))
        collisions = [name for name in agents if (self.target_dir / (name + ".md")).exists()]
        prefix = "Force install" if previous_state is not None else "Install"
        print("{} plan: {} agents, {} conflict(s)".format(prefix, len(agents), len(collisions)))
        for name in collisions:
            print("CONFLICT {} (will back up before overwrite)".format(self.target_dir / (name + ".md")))
        if dry_run:
            print("DRY-RUN: no files changed")
            return
        self.target_dir.mkdir(parents=True, exist_ok=True)
        operation_id = unique_id()
        if previous_state is not None:
            snapshot_names = sorted(set(agents) | set(previous_state["files"]))
            self._active_snapshot = self.create_snapshot(snapshot_names, "force-install")
            self.state_file.unlink()
        else:
            self._active_snapshot = self.create_snapshot(list(agents), "install")
        records: Dict[str, dict] = {}
        for name, source in agents.items():
            target = self.target_dir / source.name
            previous_record = previous_state["files"].get(name) if previous_state is not None else None
            preexisting = target.exists() and previous_record is None
            backup_path: Optional[Path] = None
            previous_backup: Optional[Path] = None
            if isinstance(previous_record, dict):
                backup_value = previous_record.get("backup_path")
                if isinstance(backup_value, str) and backup_value:
                    candidate = Path(backup_value)
                    if candidate.is_file():
                        previous_backup = candidate
            if previous_backup is not None:
                backup_path = self.backups_dir / operation_id / source.name
                atomic_write(backup_path, previous_backup.read_bytes())
                preexisting = True
            elif preexisting:
                backup_path = self.backups_dir / operation_id / source.name
                atomic_write(backup_path, target.read_bytes())
            rendered = render_agent(source.read_text(encoding="utf-8"), model_map.get(name, {}))
            atomic_write(target, rendered.encode("utf-8"))
            validate_agent_text(target.read_text(encoding="utf-8"), name)
            base_path = self.bases_dir / operation_id / source.name
            atomic_write(base_path, rendered.encode("utf-8"))
            records[name] = {
                "source_sha": sha256_file(source),
                "installed_sha": sha256_file(target),
                "preexisting": preexisting,
                "backup_path": str(backup_path) if backup_path else None,
                "base_path": str(base_path),
                "operation_time": now_iso(),
            }
            print("Installed {}".format(target))
        state = {"package": PACKAGE_NAME, "version": package_version(), "source": str(ROOT), "operation_time": now_iso(), "files": records}
        atomic_json(self.state_file, state)
        print("Install complete: {}".format(self.state_file))

    @transactional("update")
    def update(self, dry_run: bool, model_map_path: Optional[str]) -> None:
        state = self.load_state(required=True)
        assert state is not None
        agents = source_agents()
        source_names = set(agents)
        state_names = set(state["files"])
        added = sorted(source_names - state_names)
        common = sorted(source_names & state_names)
        removed = sorted(state_names - source_names)
        model_map = read_model_map(model_map_path, sorted(source_names))
        changed = []
        for name in common:
            record = state["files"][name]
            target = self.target_dir / (name + ".md")
            if not target.exists() or sha256_file(target) != record.get("installed_sha"):
                changed.append(name)
        removed_modified = []
        for name in removed:
            record = state["files"][name]
            target = self.target_dir / (name + ".md")
            if not target.exists() or sha256_file(target) != record.get("installed_sha"):
                removed_modified.append(name)
        print(
            "Update plan: {} source agents, {} added, {} removed, {} locally modified/missing".format(
                len(agents), len(added), len(removed), len(changed) + len(removed_modified)
            )
        )
        for name in added:
            target = self.target_dir / (name + ".md")
            suffix = " (preexisting target will be backed up)" if target.exists() else ""
            print("ADDED {}{}".format(name, suffix))
        for name in removed:
            suffix = " (locally modified/missing; will remain tracked)" if name in removed_modified else ""
            print("REMOVED {}{}".format(name, suffix))
        for name in changed:
            print("LOCAL CHANGE {} (three-way merge required)".format(name))
        for name in removed_modified:
            print("LOCAL CHANGE {} (removed from package; will remain tracked)".format(name))
        if dry_run:
            print("DRY-RUN: no files changed")
            return
        update_id = unique_id()
        self._active_snapshot = self.create_snapshot(sorted(source_names | state_names), "update")
        new_records: Dict[str, dict] = {}

        for name in added:
            source = agents[name]
            target = self.target_dir / source.name
            preexisting = target.exists()
            backup_path: Optional[Path] = None
            if preexisting:
                backup_path = self.backups_dir / update_id / source.name
                atomic_write(backup_path, target.read_bytes())
            rendered = render_agent(source.read_text(encoding="utf-8"), model_map.get(name, {}))
            atomic_write(target, rendered.encode("utf-8"))
            validate_agent_text(target.read_text(encoding="utf-8"), name)
            base_path = self.bases_dir / update_id / source.name
            atomic_write(base_path, rendered.encode("utf-8"))
            new_records[name] = {
                "source_sha": sha256_file(source),
                "installed_sha": sha256_file(target),
                "preexisting": preexisting,
                "backup_path": str(backup_path) if backup_path else None,
                "base_path": str(base_path),
                "operation_time": now_iso(),
            }
            print("Added {}".format(target))

        for name in common:
            source = agents[name]
            record = dict(state["files"][name])
            target = self.target_dir / source.name
            current_text = target.read_text(encoding="utf-8") if target.exists() else ""
            current_fields: Dict[str, str] = {}
            if current_text:
                try:
                    current_metadata = parse_frontmatter(current_text)
                    for key in ("model", "thoughtLevel"):
                        value = current_metadata.get(key)
                        if isinstance(value, str):
                            current_fields[key] = value
                except PackError:
                    pass
            if not current_fields:
                base_value = record.get("base_path")
                base_path = Path(base_value) if isinstance(base_value, str) and base_value else None
                if base_path is not None and base_path.is_file():
                    try:
                        base_metadata = parse_frontmatter(base_path.read_text(encoding="utf-8"))
                        for key in ("model", "thoughtLevel"):
                            value = base_metadata.get(key)
                            if isinstance(value, str):
                                current_fields[key] = value
                    except (OSError, UnicodeError, PackError):
                        pass
            if not target.exists():
                fields = current_fields or dict(model_map.get(name, {}))
            else:
                fields = dict(model_map[name]) if name in model_map else current_fields
            remote = render_agent(source.read_text(encoding="utf-8"), fields)
            if not target.exists():
                validate_agent_text(remote, name)
                atomic_write(target, remote.encode("utf-8"))
                base_path = self.bases_dir / update_id / source.name
                atomic_write(base_path, remote.encode("utf-8"))
                record.update({"source_sha": sha256_file(source), "installed_sha": sha256_file(target), "base_path": str(base_path), "operation_time": now_iso()})
                new_records[name] = record
                print("Reinstalled missing {}".format(name))
                continue
            unmodified = sha256_file(target) == record.get("installed_sha")
            installed_text: Optional[str] = None
            if unmodified:
                installed_text = remote
            else:
                base_value = record.get("base_path")
                base_path = Path(base_value) if isinstance(base_value, str) and base_value else None
                if target.exists() and base_path is not None and base_path.is_file():
                    installed_text = self.merge(base_path.read_text(encoding="utf-8"), current_text, remote)
                if installed_text is None:
                    incoming = target.with_name(target.name + ".tony-agents-pack.incoming")
                    atomic_write(incoming, remote.encode("utf-8"))
                    print("CONFLICT {} preserved; candidate {}".format(target, incoming))
                    new_records[name] = record
                    continue
            validate_agent_text(installed_text, name)
            atomic_write(target, installed_text.encode("utf-8"))
            validate_agent_text(target.read_text(encoding="utf-8"), name)
            base_path = self.bases_dir / update_id / source.name
            atomic_write(base_path, remote.encode("utf-8"))
            record.update({"source_sha": sha256_file(source), "installed_sha": sha256_file(target), "base_path": str(base_path), "operation_time": now_iso()})
            new_records[name] = record
            suffix = " (merged local changes)" if not unmodified else ""
            print("Updated {}{}".format(target, suffix))

        for name in removed:
            record = dict(state["files"][name])
            target = self.target_dir / (name + ".md")
            current_matches = target.exists() and sha256_file(target) == record.get("installed_sha")
            if not current_matches:
                new_records[name] = record
                print("REMOVED BUT MODIFIED/MISSING {} preserved and remains tracked".format(target))
                continue
            if record.get("preexisting"):
                backup_value = record.get("backup_path")
                backup = Path(backup_value) if isinstance(backup_value, str) and backup_value else None
                if backup is None or not backup.is_file():
                    new_records[name] = record
                    print("REMOVED BUT BACKUP MISSING {} preserved and remains tracked".format(target))
                    continue
                atomic_write(target, backup.read_bytes())
                print("Removed package ownership and restored {}".format(target))
            else:
                target.unlink()
                print("Removed {}".format(target))

        state.update({"version": package_version(), "source": str(ROOT), "operation_time": now_iso(), "files": new_records})
        atomic_json(self.state_file, state)
        print("Update complete")

    @staticmethod
    def merge(base: str, local: str, remote: str) -> Optional[str]:
        git = shutil.which("git")
        if not git:
            return None
        with tempfile.TemporaryDirectory(prefix="tony-agents-merge-") as temporary:
            directory = Path(temporary)
            paths = {"local": directory / "local", "base": directory / "base", "remote": directory / "remote"}
            for key, value in (("local", local), ("base", base), ("remote", remote)):
                atomic_write(paths[key], value.encode("utf-8"))
            result = subprocess.run(
                [git, "merge-file", "-p", str(paths["local"]), str(paths["base"]), str(paths["remote"])],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                check=False,
            )
            return result.stdout if result.returncode == 0 else None

    @transactional("uninstall")
    def uninstall(self, dry_run: bool) -> None:
        state = self.load_state(required=True)
        assert state is not None
        names = sorted(state["files"])
        modified = []
        for name in names:
            target = self.target_dir / (name + ".md")
            record = state["files"][name]
            if target.exists() and sha256_file(target) != record.get("installed_sha"):
                modified.append(name)
        print("Uninstall plan: {} tracked agents, {} locally modified".format(len(names), len(modified)))
        if dry_run:
            print("DRY-RUN: no files changed")
            return
        self._active_snapshot = self.create_snapshot(names, "uninstall")
        remaining: Dict[str, dict] = {}
        for name in names:
            target = self.target_dir / (name + ".md")
            record = state["files"][name]
            current_matches = target.exists() and sha256_file(target) == record.get("installed_sha")
            if not current_matches:
                backup_value = record.get("backup_path")
                backup = Path(backup_value) if isinstance(backup_value, str) and backup_value else None
                if record.get("preexisting") and backup is not None and backup.is_file():
                    candidate = target.with_name(target.name + ".tony-agents-pack.restore")
                    atomic_write(candidate, backup.read_bytes())
                    print("MODIFIED {} preserved; restore candidate {}".format(target, candidate))
                else:
                    print("MODIFIED/MISSING {} preserved".format(target))
                remaining[name] = record
                continue
            if record.get("preexisting"):
                backup_value = record.get("backup_path")
                backup = Path(backup_value) if isinstance(backup_value, str) and backup_value else None
                if backup is None or not backup.is_file():
                    print("MISSING BACKUP {} preserved".format(target))
                    remaining[name] = record
                    continue
                atomic_write(target, backup.read_bytes())
                print("Restored {}".format(target))
            else:
                target.unlink()
                print("Removed {}".format(target))
        if remaining:
            state.update({"operation_time": now_iso(), "files": remaining})
            atomic_json(self.state_file, state)
            print("Uninstall incomplete: {} modified item(s) remain tracked".format(len(remaining)))
        else:
            try:
                self.state_file.unlink()
            except FileNotFoundError:
                pass
            print("Uninstall complete")

    def restore_snapshot(self, snapshot: Path) -> None:
        snapshot = snapshot.resolve()
        manifest_file = snapshot / "snapshot.json"
        if not manifest_file.is_file():
            raise PackError("snapshot manifest is missing: {}".format(manifest_file))
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PackError("cannot read snapshot manifest {}: {}".format(manifest_file, exc))
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise PackError("snapshot manifest has invalid files data: {}".format(manifest_file))
        saved_files: Dict[str, bytes] = {}
        for name, info in files.items():
            if not isinstance(name, str) or not isinstance(info, dict) or not isinstance(info.get("existed"), bool):
                raise PackError("snapshot manifest has an invalid file entry")
            if info["existed"]:
                saved = snapshot / "files" / (name + ".md")
                if not saved.is_file():
                    raise PackError("snapshot is incomplete: {}".format(saved))
                saved_files[name] = saved.read_bytes()
        state_existed = manifest.get("state_existed")
        if not isinstance(state_existed, bool):
            raise PackError("snapshot manifest has invalid state_existed data")
        state_data: Optional[bytes] = None
        if state_existed:
            saved_state = snapshot / "state.json"
            if not saved_state.is_file():
                raise PackError("snapshot state is missing: {}".format(saved_state))
            state_data = saved_state.read_bytes()

        for name, info in files.items():
            target = self.target_dir / (name + ".md")
            if info["existed"]:
                atomic_write(target, saved_files[name])
            elif target.exists():
                target.unlink()
        if state_data is not None:
            atomic_write(self.state_file, state_data)
        elif self.state_file.exists():
            self.state_file.unlink()

    def rollback(self, snapshot_id: str, dry_run: bool) -> None:
        if not self.snapshots_dir.is_dir():
            raise PackError("no snapshots found")
        snapshots = sorted(path for path in self.snapshots_dir.iterdir() if (path / "snapshot.json").is_file())
        if not snapshots:
            raise PackError("no snapshots found")
        snapshot = snapshots[-1] if snapshot_id == "latest" else self.snapshots_dir / snapshot_id
        manifest_file = snapshot / "snapshot.json"
        if not manifest_file.is_file():
            raise PackError("snapshot not found: {}".format(snapshot_id))
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        print("Rollback plan: {} ({})".format(snapshot.name, manifest.get("operation", "unknown")))
        if dry_run:
            print("DRY-RUN: no files changed")
            return
        self.restore_snapshot(snapshot)
        print("Rollback complete: {}".format(snapshot.name))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate the release package")
    for command in ("install", "update"):
        child = subparsers.add_parser(command, help="{} agents".format(command))
        child.add_argument("--dry-run", action="store_true")
        child.add_argument("--model-map", metavar="PATH")
        child.add_argument("--target-dir", type=Path, default=Path.home() / ".zcode" / "agents")
        if command == "install":
            child.add_argument("--force", action="store_true", help="replace existing package state")
    rollback = subparsers.add_parser("rollback", help="restore a pre-operation snapshot")
    rollback.add_argument("snapshot", nargs="?", default="latest")
    rollback.add_argument("--dry-run", action="store_true")
    rollback.add_argument("--target-dir", type=Path, default=Path.home() / ".zcode" / "agents")
    uninstall = subparsers.add_parser("uninstall", help="remove managed agents")
    uninstall.add_argument("--dry-run", action="store_true")
    uninstall.add_argument("--target-dir", type=Path, default=Path.home() / ".zcode" / "agents")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            return 0 if validate_package() else 1
        manager = Manager(args.target_dir)
        if args.command == "install":
            manager.install(args.dry_run, args.model_map, args.force)
        elif args.command == "update":
            manager.update(args.dry_run, args.model_map)
        elif args.command == "rollback":
            manager.rollback(args.snapshot, args.dry_run)
        elif args.command == "uninstall":
            manager.uninstall(args.dry_run)
        return 0
    except (PackError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
