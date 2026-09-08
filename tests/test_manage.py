import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
import io
import os
import re
import stat
import struct
import zlib


def make_tree_owner_writable(root):
    root = Path(root)
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        current_path.chmod(current_path.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        for name in directories:
            path = current_path / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        for name in files:
            path = current_path / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR)


def png_chunk(chunk_type, data=b""):
    payload = chunk_type + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)


def minimal_png():
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    scanline = b"\x00\x00\x00\x00\x00"
    return (
        manage.PNG_SIGNATURE
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(scanline))
        + png_chunk(b"IEND")
    )


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("tony_agents_manage", ROOT / "scripts" / "manage.py")
manage = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(manage)


class ManageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.temp = Path(self.temporary.name)
        self.package = self.temp / "package"
        shutil.copytree(ROOT / "agents", self.package / "agents")
        shutil.copytree(ROOT / "scripts", self.package / "scripts", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "tests", self.package / "tests", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / ".zcode-plugin", self.package / ".zcode-plugin")
        shutil.copytree(ROOT / ".githooks", self.package / ".githooks")
        shutil.copytree(ROOT / "release-audits", self.package / "release-audits")
        workflow_dir = self.package / ".github" / "workflows"
        workflow_dir.mkdir(parents=True)
        shutil.copy2(ROOT / ".github" / "workflows" / "validate.yml", workflow_dir / "validate.yml")
        shutil.copy2(ROOT / "LICENSE", self.package / "LICENSE")
        shutil.copy2(ROOT / "CHANGELOG.md", self.package / "CHANGELOG.md")
        shutil.copy2(ROOT / "README.md", self.package / "README.md")
        shutil.copy2(ROOT / "MODEL_SETUP.md", self.package / "MODEL_SETUP.md")
        shutil.copy2(ROOT / "INSTALL-FOR-AI.md", self.package / "INSTALL-FOR-AI.md")
        shutil.copytree(ROOT / "docs", self.package / "docs")
        make_tree_owner_writable(self.package)
        self.target = self.temp / "home" / ".zcode" / "agents"
        self.patches = [
            mock.patch.object(manage, "ROOT", self.package),
            mock.patch.object(manage, "AGENTS_DIR", self.package / "agents"),
            mock.patch.object(manage, "PLUGIN_FILE", self.package / ".zcode-plugin" / "plugin.json"),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def manager(self):
        return manage.Manager(self.target)

    def install(self):
        manager = self.manager()
        manager.install(False, None)
        return manager

    def add_source_agent(self, name="new-agent"):
        template = (self.package / "agents" / "coder.md").read_text(encoding="utf-8")
        text = template.replace('name: "coder"', 'name: "{}"'.format(name), 1)
        source = self.package / "agents" / (name + ".md")
        source.write_text(text, encoding="utf-8")
        return source

    def set_package_version(self, version):
        manifest = self.package / ".zcode-plugin" / "plugin.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["version"] = version
        manifest.write_text(json.dumps(data), encoding="utf-8")

    def fail_atomic_write_once_for(self, failed_target):
        original = manage.atomic_write
        failed_target = failed_target.resolve()
        failed = {"value": False}

        def side_effect(path, data):
            if Path(path).resolve() == failed_target and not failed["value"]:
                failed["value"] = True
                raise OSError("injected atomic write failure")
            return original(path, data)

        return mock.patch.object(manage, "atomic_write", side_effect=side_effect)

    def installed_agent_bytes(self):
        return {path.name: path.read_bytes() for path in self.target.glob("*.md")}

    def test_multiline_tools_model_insertion_stays_top_level(self):
        source = (self.package / "agents" / "shencha.md").read_text(encoding="utf-8")
        rendered = manage.render_agent(source, {"model": "custom:test:model", "thoughtLevel": "high"})
        metadata = manage.parse_frontmatter(rendered)
        self.assertEqual(metadata["tools"], ["Read", "Glob", "Grep", "TodoWrite"])
        self.assertEqual(metadata["model"], "custom:test:model")
        self.assertEqual(metadata["thoughtLevel"], "high")
        self.assertRegex(rendered, r"(?m)^model: \"custom:test:model\"$")
        self.assertRegex(rendered, r"(?m)^thoughtLevel: \"high\"$")
        self.assertNotIn("  model:", rendered)
        self.assertLess(rendered.index('model: "custom:test:model"'), rendered.index("\n---", 4))

    def test_install_failure_automatically_restores_preexisting_files(self):
        self.target.mkdir(parents=True)
        originals = {
            "coder-ds.md": b"preexisting coder ds\n",
            "coder-gpt.md": b"preexisting coder gpt\n",
        }
        for name, data in originals.items():
            (self.target / name).write_bytes(data)
        manager = self.manager()

        with self.fail_atomic_write_once_for(self.target / "coder-gpt.md"):
            with self.assertRaisesRegex(manage.PackError, "操作失败且已自动回滚"):
                manager.install(False, None)

        self.assertEqual(self.installed_agent_bytes(), originals)
        self.assertFalse(manager.state_file.exists())

    def test_update_failure_automatically_restores_agents_and_state(self):
        manager = self.install()
        agents_before = self.installed_agent_bytes()
        state_before = manager.state_file.read_bytes()
        source = self.package / "agents" / "coder-gpt.md"
        source.write_text(source.read_text(encoding="utf-8") + "\nPACKAGE UPDATE\n", encoding="utf-8")

        with self.fail_atomic_write_once_for(self.target / "coder-gpt.md"):
            with self.assertRaisesRegex(manage.PackError, "操作失败且已自动回滚"):
                manager.update(False, None)

        self.assertEqual(self.installed_agent_bytes(), agents_before)
        self.assertEqual(manager.state_file.read_bytes(), state_before)

    def test_uninstall_failure_automatically_restores_agents_and_state(self):
        self.target.mkdir(parents=True)
        (self.target / "coder-gpt.md").write_bytes(b"preexisting coder gpt\n")
        manager = self.install()
        agents_before = self.installed_agent_bytes()
        state_before = manager.state_file.read_bytes()

        with self.fail_atomic_write_once_for(self.target / "coder-gpt.md"):
            with self.assertRaisesRegex(manage.PackError, "操作失败且已自动回滚"):
                manager.uninstall(False)

        self.assertEqual(self.installed_agent_bytes(), agents_before)
        self.assertEqual(manager.state_file.read_bytes(), state_before)

    def test_install_backs_up_preexisting_file(self):
        self.target.mkdir(parents=True)
        original = b"preexisting coder\n"
        (self.target / "coder.md").write_bytes(original)
        manager = self.install()
        state = manager.load_state(required=True)
        record = state["files"]["coder"]
        self.assertTrue(record["preexisting"])
        self.assertIsNotNone(record["backup_path"])
        self.assertEqual(Path(record["backup_path"]).read_bytes(), original)
        self.assertNotEqual((self.target / "coder.md").read_bytes(), original)

    def test_force_install_after_partial_uninstall_reinstalls_all_and_snapshots_modified(self):
        self.target.mkdir(parents=True)
        original = b"preexisting writer\n"
        (self.target / "writer.md").write_bytes(original)
        manager = self.install()
        modified = self.target / "writer.md"
        modified.write_text(modified.read_text(encoding="utf-8") + "\nLOCAL CHANGE\n", encoding="utf-8")
        modified_before = modified.read_bytes()
        manager.uninstall(False)
        self.assertEqual(set(manager.load_state(required=True)["files"]), {"writer"})

        output = io.StringIO()
        with redirect_stdout(output):
            manager.install(False, None, force=True)

        self.assertEqual(len(list(self.target.glob("*.md"))), manage.EXPECTED_AGENT_COUNT)
        state = manager.load_state(required=True)
        self.assertEqual(len(state["files"]), manage.EXPECTED_AGENT_COUNT)
        writer_record = state["files"]["writer"]
        self.assertTrue(writer_record["preexisting"])
        self.assertEqual(Path(writer_record["backup_path"]).read_bytes(), original)
        snapshots = sorted(manager.snapshots_dir.iterdir())
        manifest = json.loads((snapshots[-1] / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["operation"], "force-install")
        self.assertEqual((snapshots[-1] / "files" / "writer.md").read_bytes(), modified_before)
        self.assertIn("Force install plan", output.getvalue())

    def test_install_parser_accepts_dry_run_with_force(self):
        args = manage.build_parser().parse_args(["install", "--dry-run", "--force"])
        self.assertTrue(args.dry_run)
        self.assertTrue(args.force)

    def test_install_dry_run_with_state_requires_force_and_force_does_not_write(self):
        manager = self.install()
        state_before = manager.state_file.read_bytes()
        agents_before = self.installed_agent_bytes()
        snapshots_before = sorted(manager.snapshots_dir.iterdir())

        with self.assertRaisesRegex(manage.PackError, "package state already exists"):
            manager.install(True, None)

        output = io.StringIO()
        with redirect_stdout(output):
            manager.install(True, None, force=True)

        self.assertIn("Force install plan", output.getvalue())
        self.assertIn("DRY-RUN: no files changed", output.getvalue())
        self.assertEqual(manager.state_file.read_bytes(), state_before)
        self.assertEqual(self.installed_agent_bytes(), agents_before)
        self.assertEqual(sorted(manager.snapshots_dir.iterdir()), snapshots_before)

    def test_update_dry_run_reports_set_changes_without_writes(self):
        manager = self.install()
        state_before = manager.state_file.read_bytes()
        coder = self.target / "coder.md"
        coder.write_text(coder.read_text(encoding="utf-8") + "\nLOCAL CHANGE\n", encoding="utf-8")
        writer = self.target / "writer.md"
        writer.write_text(writer.read_text(encoding="utf-8") + "\nREMOVED LOCAL CHANGE\n", encoding="utf-8")
        coder_before = coder.read_bytes()
        writer_before = writer.read_bytes()
        self.add_source_agent()
        (self.package / "agents" / "writer.md").unlink()
        snapshots_before = sorted(manager.snapshots_dir.iterdir())
        output = io.StringIO()

        with redirect_stdout(output):
            manager.update(True, None)

        report = output.getvalue()
        self.assertIn("ADDED new-agent", report)
        self.assertIn("REMOVED writer", report)
        self.assertIn("LOCAL CHANGE coder", report)
        self.assertIn("LOCAL CHANGE writer", report)
        self.assertIn("DRY-RUN: no files changed", report)
        self.assertFalse((self.target / "new-agent.md").exists())
        self.assertEqual(coder.read_bytes(), coder_before)
        self.assertEqual(writer.read_bytes(), writer_before)
        self.assertEqual(manager.state_file.read_bytes(), state_before)
        self.assertEqual(sorted(manager.snapshots_dir.iterdir()), snapshots_before)

    def test_update_adds_new_agent_and_updates_state_version(self):
        manager = self.install()
        self.add_source_agent()
        self.set_package_version("1.1.0")

        manager.update(False, None)

        target = self.target / "new-agent.md"
        self.assertTrue(target.is_file())
        state = manager.load_state(required=True)
        self.assertIn("new-agent", state["files"])
        self.assertFalse(state["files"]["new-agent"]["preexisting"])
        self.assertEqual(state["version"], "1.1.0")

    def test_update_removes_unmodified_package_agent(self):
        manager = self.install()
        source = self.package / "agents" / "writer.md"
        source.unlink()

        manager.update(False, None)

        self.assertFalse((self.target / "writer.md").exists())
        state = manager.load_state(required=True)
        self.assertNotIn("writer", state["files"])

    def test_update_removed_modified_agent_is_preserved_and_tracked(self):
        manager = self.install()
        target = self.target / "writer.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nLOCAL CHANGE\n", encoding="utf-8")
        (self.package / "agents" / "writer.md").unlink()

        manager.update(False, None)

        self.assertIn("LOCAL CHANGE", target.read_text(encoding="utf-8"))
        state = manager.load_state(required=True)
        self.assertIn("writer", state["files"])

    def test_update_added_preexisting_agent_backup_is_restored_by_uninstall(self):
        manager = self.install()
        source = self.add_source_agent()
        self.target.mkdir(parents=True, exist_ok=True)
        original = b"preexisting new agent\n"
        (self.target / source.name).write_bytes(original)

        manager.update(False, None)

        state = manager.load_state(required=True)
        record = state["files"]["new-agent"]
        self.assertTrue(record["preexisting"])
        self.assertEqual(Path(record["backup_path"]).read_bytes(), original)
        manager.uninstall(False)
        self.assertEqual((self.target / source.name).read_bytes(), original)

    def test_update_unmodified_file_upgrades_automatically(self):
        manager = self.install()
        source = self.package / "agents" / "coder.md"
        source.write_text(source.read_text(encoding="utf-8") + "\nPACKAGE UPGRADE\n", encoding="utf-8")
        manager.update(False, None)
        installed = (self.target / "coder.md").read_text(encoding="utf-8")
        self.assertIn("PACKAGE UPGRADE", installed)
        state = manager.load_state(required=True)
        self.assertEqual(state["files"]["coder"]["installed_sha"], manage.sha256_file(self.target / "coder.md"))

    def test_update_reinstalls_missing_agent(self):
        model_map = self.temp / "model-map.json"
        model_map.write_text(json.dumps({"coder": {"model": "custom:test:coder", "thoughtLevel": "high"}}), encoding="utf-8")
        manager = self.manager()
        manager.install(False, str(model_map))
        target = self.target / "coder.md"
        target.unlink()
        output = io.StringIO()

        with redirect_stdout(output):
            manager.update(False, None)

        self.assertTrue(target.is_file())
        metadata = manage.parse_frontmatter(target.read_text(encoding="utf-8"))
        self.assertEqual(metadata["model"], "custom:test:coder")
        self.assertEqual(metadata["thoughtLevel"], "high")
        self.assertIn("Reinstalled missing coder", output.getvalue())
        self.assertFalse(target.with_name(target.name + ".tony-agents-pack.incoming").exists())
        state = manager.load_state(required=True)
        self.assertEqual(state["files"]["coder"]["installed_sha"], manage.sha256_file(target))

    @unittest.skipUnless(shutil.which("git"), "git is required for merge-conflict coverage")
    def test_update_conflict_preserves_local_and_writes_incoming(self):
        manager = self.install()
        target = self.target / "coder.md"
        source = self.package / "agents" / "coder.md"
        base_text = target.read_text(encoding="utf-8")
        marker = "你是资深软件工程师，负责把边界清楚的日常开发任务实现成可运行、可验证的代码。"
        self.assertIn(marker, base_text)
        target.write_text(base_text.replace(marker, "LOCAL CUSTOMIZATION"), encoding="utf-8")
        source_text = source.read_text(encoding="utf-8")
        source.write_text(source_text.replace(marker, "REMOTE PACKAGE CHANGE"), encoding="utf-8")

        manager.update(False, None)

        self.assertIn("LOCAL CUSTOMIZATION", target.read_text(encoding="utf-8"))
        incoming = target.with_name("coder.md.tony-agents-pack.incoming")
        self.assertTrue(incoming.is_file())
        self.assertIn("REMOTE PACKAGE CHANGE", incoming.read_text(encoding="utf-8"))

    def test_uninstall_restores_preexisting_and_preserves_modified(self):
        self.target.mkdir(parents=True)
        original = b"original coder\n"
        (self.target / "coder.md").write_bytes(original)
        manager = self.install()
        modified = self.target / "writer.md"
        modified.write_text(modified.read_text(encoding="utf-8") + "\nLOCAL CHANGE\n", encoding="utf-8")

        manager.uninstall(False)

        self.assertEqual((self.target / "coder.md").read_bytes(), original)
        self.assertIn("LOCAL CHANGE", modified.read_text(encoding="utf-8"))
        state = manager.load_state(required=True)
        self.assertEqual(set(state["files"]), {"writer"})

    def test_rollback_restores_pre_update_snapshot(self):
        manager = self.install()
        target = self.target / "coder.md"
        before = target.read_bytes()
        source = self.package / "agents" / "coder.md"
        source.write_text(source.read_text(encoding="utf-8") + "\nNEW RELEASE\n", encoding="utf-8")
        manager.update(False, None)
        self.assertNotEqual(target.read_bytes(), before)

        manager.rollback("latest", False)

        self.assertEqual(target.read_bytes(), before)
        state = manager.load_state(required=True)
        self.assertEqual(state["files"]["coder"]["installed_sha"], manage.sha256_bytes(before))

    def test_make_tree_owner_writable_enables_fixture_mutations(self):
        fixture = self.temp / "readonly-fixture"
        nested = fixture / "nested"
        nested.mkdir(parents=True)
        regular = nested / "fixture.txt"
        regular.write_text("original\n", encoding="utf-8")
        regular.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        nested.chmod(stat.S_IRUSR | stat.S_IXUSR)
        fixture.chmod(stat.S_IRUSR | stat.S_IXUSR)

        make_tree_owner_writable(fixture)

        regular.write_text("updated\n", encoding="utf-8")
        (nested / "created.txt").write_text("created\n", encoding="utf-8")
        (fixture / "created-dir").mkdir()
        regular.unlink()
        self.assertTrue(os.access(fixture, os.W_OK))
        self.assertTrue(os.access(nested, os.W_OK))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unsupported")
    def test_make_tree_owner_writable_does_not_follow_symlinks(self):
        fixture = self.temp / "readonly-links"
        fixture.mkdir()
        outside = self.temp / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        outside.chmod(stat.S_IRUSR)
        link = fixture / "outside-link"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest("cannot create symlink: {}".format(exc))

        make_tree_owner_writable(fixture)

        self.assertEqual(stat.S_IMODE(outside.stat().st_mode) & stat.S_IWUSR, 0)

    def test_validate_passes(self):
        self.assertTrue(manage.validate_package(verbose=False))

    def test_published_agents_strip_local_metadata_and_model_names(self):
        agents = sorted((self.package / "agents").glob("*.md"))
        self.assertEqual(len(agents), manage.EXPECTED_AGENT_COUNT)
        for path in agents:
            text = path.read_text(encoding="utf-8")
            metadata = manage.parse_frontmatter(text)
            self.assertTrue(manage.FORBIDDEN_PUBLISHED_KEYS.isdisjoint(metadata), path.name)
            self.assertIsNone(manage.PUBLISHED_MODEL_NAME_RE.search(metadata["description"]), path.name)
            self.assertRegex(text, manage.INJECTION_DEFENSE_RE, path.name)

    def test_github_metadata_is_hard_read_only(self):
        text = (self.package / "agents" / "github.md").read_text(encoding="utf-8")
        metadata = manage.parse_frontmatter(text)
        forbidden = {"Bash", "Write", "Edit"}
        self.assertTrue(forbidden.isdisjoint(metadata["tools"]))
        self.assertTrue(forbidden.issubset(set(metadata["disallowedTools"])))
        for marker in ("REPO_REVIEW", "README_POLISH", "RELEASE_GATE", "RELEASE_NOTES", "package_fingerprint"):
            self.assertIn(marker, text)

    def test_frontend_has_no_skills_metadata(self):
        metadata = manage.parse_frontmatter((self.package / "agents" / "frontend.md").read_text(encoding="utf-8"))
        self.assertNotIn("skills", metadata)

    def test_mermaid_contract_markers(self):
        text = (self.package / "agents" / "mermaid.md").read_text(encoding="utf-8")
        for marker in (
            "永远只输出一个 `mermaid` 代码块",
            "`graph TD`",
            "禁止任何可执行或外联语法",
            "`click`",
            "DeclaredNodes",
            "EdgeEndpoints",
            "ClassifiedNodes",
            "UsedClasses",
        ):
            self.assertIn(marker, text)

    def test_acceptance_agents_share_verdict_and_report_markers(self):
        for name in manage.ACCEPTANCE_AGENTS:
            text = (self.package / "agents" / (name + ".md")).read_text(encoding="utf-8")
            report_marker = (
                "report-id / role / requirement-version / snapshot(commit|source|artifact SHA|build-id) / generated-at"
                if name == "shencha-content"
                else manage.COMMON_ACCEPTANCE_MARKER
            )
            for marker in ("PASS", "BLOCK", "INCONCLUSIVE", report_marker):
                self.assertIn(marker, text, name)

    def test_content_review_editorial_v4_contract_markers(self):
        text = (self.package / "agents" / "shencha-content.md").read_text(encoding="utf-8")
        marker_groups = {
            "profiles invalid fail closed": (
                "非空去重集合",
                "输入为空，或清洗非法/重复项后集合为空",
                "未知 token",
                "非法组合",
                "case-study、research-report 必须含 `editorial`",
                "CORE UNVERIFIED、INCONCLUSIVE、NO_GO",
            ),
            "QUICK no PASS": (
                "即使全部已查项无缺陷",
                "只能 BLOCK 或 INCONCLUSIVE",
                "绝不得 PASS",
                "`publication_decision=NO_GO`",
            ),
            "deterministic hash sampling": (
                "稳定 section-id 与 claim type 分层",
                'SHA256(snapshot-id + "|" + claim-id)',
                "`ceil(20%)`",
                "令目标数 `K=max(",
                "UTF-8 字节序 `(hash, claim-id)`",
                "先从每个含普通 claim 的实质章节选择该章普通 claim 全序第一项",
                "再按普通 claim 全局全序补到 K",
                "claim-id 必须非空且全局唯一",
                "每条排序 hash",
                "selected claim IDs",
                "未抽范围",
            ),
            "batch Phase A and exact coverage": (
                "`claim_count>40`",
                "`>8000` 词",
                "`>12000` 中文字",
                "Phase A 只输出完整总 claim index",
                "每个 part 最多 20 claims",
                "`report-part-id`",
                "无重复无遗漏",
                "全部 claims 与全部 parts 做 100% 二审",
                "零 finding 也不得豁免",
                "无未达 VERIFIED 的 P0/P1",
                "缺失、截断、无法解析",
            ),
            "status enums": (
                "FINAL_CONTENT | DRAFT_DO_NOT_PUBLISH | DRAFT_COMPLETE_NATIVE_REVIEW_REQUIRED | BLOCKED",
                "CONTENT_STATUS=FINAL_DRAFT | DRAFT_NATIVE_REVIEW_REQUIRED | NEEDS_INPUT | BLOCKED",
                "SEND_STATUS=SEND_BLOCKED | READY_FOR_HUMAN_SEND_REVIEW",
                "`DRAFT_DO_NOT_PUBLISH`、`DRAFT_COMPLETE_NATIVE_REVIEW_REQUIRED`",
                "任一上游 `BLOCKED` => 对应 CORE FAIL、BLOCK、NO_GO",
                "`SEND_BLOCKED` 不等于内容 CORE FAIL",
                "`review_verdict` 与 `publication_decision`",
            ),
            "HIGH_RISK all-document independent retest": (
                "无论长短、是否分批或是否有 finding",
                "对同 snapshot 的全部 claims 做 100% 二审",
                "`single-part/full-claim retest`",
            ),
            "P0/P1 fail closed": (
                "任一 P0/P1 未达 VERIFIED",
                "OPEN、READY_FOR_RETEST、ACCEPTED_RISK",
                "P0/P1 禁止以 ACCEPTED_RISK 换取 GO",
            ),
            "independent retest": (
                "不同全新会话且不得读取初审内部推理或未发布结论",
                "不同审查 agent/model",
                "具名人类编辑",
                "原审查实例不得在同一会话关闭",
                "保持 READY_FOR_RETEST",
            ),
            "report interface enums": (
                "`PUBLISH | REWORK | SUPPLY_EVIDENCE | RUN_STANDARD_REVIEW | RUN_HIGH_RISK_REVIEW | NATIVE_REVIEW | INDEPENDENT_RETEST`",
                "处于 OPEN 状态的 finding 工单 ID 数组",
                "无 OPEN 工单时必须输出 `[]`",
                "`publication_decision=GO` 时必须 `next_action=PUBLISH` 且 `open_ticket_ids=[]`",
                "`next_action=RUN_STANDARD_REVIEW` 或 `RUN_HIGH_RISK_REVIEW`",
            ),
        }
        for contract, markers in marker_groups.items():
            for marker in markers:
                self.assertIn(marker, text, contract)
        self.assertNotIn("review_profile=seo|conversion|social|email|microcopy", text)
        self.assertNotIn("rework_tickets", text)

    def test_validate_rejects_missing_content_review_fail_closed_markers(self):
        path = self.package / "agents" / "shencha-content.md"
        original = path.read_text(encoding="utf-8")
        for marker in (
            "非法组合",
            "绝不得 PASS",
            'SHA256(snapshot-id + "|" + claim-id)',
            "`claim_count>40`",
            "Phase A 只输出完整总 claim index",
            "零 finding 也不得豁免",
            "无论长短、是否分批或是否有 finding",
            "原审查实例不得在同一会话关闭",
            "任一 P0/P1 未达 VERIFIED",
        ):
            with self.subTest(marker=marker):
                path.write_text(original.replace(marker, "REMOVED_MARKER", 1), encoding="utf-8")
                self.assert_validation_fails_with("missing role contract marker: {}".format(marker))
                path.write_text(original, encoding="utf-8")

    def test_validate_rejects_missing_upstream_status_enum_markers(self):
        path = self.package / "agents" / "shencha-content.md"
        original = path.read_text(encoding="utf-8")
        for marker in ("SEND_BLOCKED", "READY_FOR_HUMAN_SEND_REVIEW", "FINAL_CONTENT", "FINAL_DRAFT"):
            with self.subTest(marker=marker):
                path.write_text(original.replace(marker, "REMOVED_MARKER"), encoding="utf-8")
                self.assert_validation_fails_with("missing role contract marker: {}".format(marker))
                path.write_text(original, encoding="utf-8")

    def test_content_review_status_enums_match_upstream_agents(self):
        reviewer = (self.package / "agents" / "shencha-content.md").read_text(encoding="utf-8")
        sheyun = (self.package / "agents" / "sheyun.md").read_text(encoding="utf-8")
        outreach = (self.package / "agents" / "outreach.md").read_text(encoding="utf-8")
        expected_social = {
            "FINAL_CONTENT",
            "DRAFT_DO_NOT_PUBLISH",
            "DRAFT_COMPLETE_NATIVE_REVIEW_REQUIRED",
            "BLOCKED",
        }
        expected_email_content = {
            "FINAL_DRAFT",
            "DRAFT_NATIVE_REVIEW_REQUIRED",
            "NEEDS_INPUT",
            "BLOCKED",
        }
        expected_send = {"SEND_BLOCKED", "READY_FOR_HUMAN_SEND_REVIEW"}
        for status in expected_social:
            self.assertIn("`{}`".format(status), sheyun)
            self.assertIn(status, reviewer)
        for status in expected_email_content | expected_send:
            self.assertIn("`{}`".format(status), outreach)
            self.assertIn(status, reviewer)
        social_contract = re.search(r"sheyun 的 `([^`]+)`", reviewer).group(1)
        email_contract = re.search(r"CONTENT_STATUS=([^`]+)`，以及", reviewer).group(1)
        send_contract = re.search(r"SEND_STATUS=([^`]+)`", reviewer).group(1)
        self.assertEqual(set(social_contract.split(" | ")), expected_social)
        self.assertEqual(set(email_contract.split(" | ")), expected_email_content)
        self.assertEqual(set(send_contract.split(" | ")), expected_send)

    def test_content_review_sampling_fixture_is_deterministic(self):
        import hashlib
        import math

        snapshot = "snapshot-001"
        claims = [
            {"id": "C-01", "section": "S-1", "type": "fact"},
            {"id": "C-02", "section": "S-1", "type": "promise"},
            {"id": "C-03", "section": "S-2", "type": "fact"},
            {"id": "C-04", "section": "S-2", "type": "fact"},
            {"id": "C-05", "section": "S-3", "type": "opinion"},
            {"id": "C-06", "section": "S-3", "type": "fact"},
            {"id": "C-07", "section": "S-1", "type": "fact"},
            {"id": "C-08", "section": "S-2", "type": "promise"},
            {"id": "C-09", "section": "S-3", "type": "fact"},
            {"id": "C-10", "section": "S-1", "type": "opinion"},
        ]

        def select(items):
            ranked = sorted(
                items,
                key=lambda item: (
                    hashlib.sha256((snapshot + "|" + item["id"]).encode("utf-8")).hexdigest().encode("utf-8"),
                    item["id"].encode("utf-8"),
                ),
            )
            sections = sorted({item["section"] for item in items})
            target = max(math.ceil(len(items) * 0.2), min(3, len(items)), len(sections))
            selected = []
            for section in sections:
                selected.append(next(item for item in ranked if item["section"] == section))
            for item in ranked:
                if item not in selected and len(selected) < target:
                    selected.append(item)
            return [item["id"] for item in selected]

        first = select(claims)
        self.assertEqual(first, select(list(reversed(claims))))
        self.assertEqual(len(first), 3)
        self.assertEqual({item["section"] for item in claims if item["id"] in first}, {"S-1", "S-2", "S-3"})

    def test_content_review_metadata_is_hard_read_only(self):
        text = (self.package / "agents" / "shencha-content.md").read_text(encoding="utf-8")
        metadata = manage.parse_frontmatter(text)
        self.assertTrue(manage.HARD_READ_ONLY_FORBIDDEN_TOOLS.isdisjoint(metadata["tools"]))
        self.assertTrue(manage.HARD_READ_ONLY_FORBIDDEN_TOOLS.issubset(set(metadata["disallowedTools"])))

    def test_validate_rejects_each_content_review_write_tool(self):
        path = self.package / "agents" / "shencha-content.md"
        original = path.read_text(encoding="utf-8")
        tools_line = "tools: [Read, Glob, Grep, WebFetch, WebSearch, TodoWrite]"
        for tool in sorted(manage.HARD_READ_ONLY_FORBIDDEN_TOOLS):
            with self.subTest(tool=tool):
                path.write_text(original.replace(tools_line, tools_line[:-1] + ", " + tool + "]", 1), encoding="utf-8")
                self.assert_validation_fails_with("shencha-content tools must be strictly read-only")
                path.write_text(original, encoding="utf-8")

    def test_validate_rejects_content_review_missing_disallowed_write_tool(self):
        path = self.package / "agents" / "shencha-content.md"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("disallowedTools: [Bash, Write, Edit]", "disallowedTools: [Bash, Write]", 1), encoding="utf-8")
        self.assert_validation_fails_with("shencha-content disallowedTools must include Bash, Write, and Edit")

    @staticmethod
    def normalized_agent_body(data):
        normalized = data.replace(b"\r\n", b"\n")
        parts = normalized.split(b"---\n", 2)
        if len(parts) != 3:
            raise AssertionError("agent frontmatter delimiters are invalid")
        return parts[2]

    def test_published_changed_agent_bodies_match_release_fingerprints(self):
        expected_hashes = {
            "shencha-content": "9d41bf674043eaa4cee0f73b9b30f116be08fb873f0236d9291bb132ebf37cec",
            "sheyun": "36550042f633917737df8d4f54d0c5c7c9fb95b66b36153e943fb3c4508375ed",
        }
        for name, expected_hash in expected_hashes.items():
            published = (self.package / "agents" / (name + ".md")).read_bytes()
            published_body = self.normalized_agent_body(published)
            self.assertEqual(manage.sha256_bytes(published_body), expected_hash, name)

    def test_changed_agent_body_parser_is_crlf_stable(self):
        for name in ("shencha-content", "sheyun"):
            published = (self.package / "agents" / (name + ".md")).read_bytes()
            crlf = published.replace(b"\n", b"\r\n")
            self.assertEqual(self.normalized_agent_body(crlf), self.normalized_agent_body(published), name)

    def test_local_changed_agent_sources_match_release_when_present(self):
        for name in ("shencha-content", "sheyun"):
            local_source = Path.home() / ".zcode" / "agents" / (name + ".md")
            if not local_source.is_file():
                self.skipTest("local source directory is unavailable")
            published = (self.package / "agents" / (name + ".md")).read_bytes()
            self.assertEqual(
                self.normalized_agent_body(published),
                self.normalized_agent_body(local_source.read_bytes()),
                name,
            )

    def test_content_review_v4_docs_cover_examples_and_migration(self):
        readme = (self.package / "README.md").read_text(encoding="utf-8")
        protocol = (self.package / "INSTALL-FOR-AI.md").read_text(encoding="utf-8")
        for marker in (
            "review_profiles=[editorial,seo] review_tier=STANDARD",
            "review_profiles=[conversion] review_tier=STANDARD",
            "review_profiles=[editorial,seo] review_tier=HIGH_RISK",
            "review_profiles=[social] review_tier=STANDARD",
            "`QUICK` 永远是 `NO_GO`",
            "agents/shencha-content.md",
            "`sheyun` 与 `shencha-content`",
        ):
            self.assertIn(marker, readme)
        for marker in (
            "单值 `review_profile` 升级为集合 `review_profiles`",
            "旧字段可临时映射为单元素集合",
            "普通安装 state schema 不变",
            "保留当前有效的本地 `model`/`thoughtLevel`",
            "`sheyun` 与 `shencha-content`",
        ):
            self.assertIn(marker, protocol)

    def test_specialized_role_contract_markers(self):
        markers = {
            "outreach": ("SEND_BLOCKED",),
            "huoke": ("evidence_type", "contact_grade", "## 证据分类"),
            "jiankong": ("pending",),
            "tijian": ("ACTIVE_SECURITY",),
            "coder-ds": ("MODE=PARALLEL_ALTERNATIVE", "MODE=OVERFLOW"),
        }
        for name, required in markers.items():
            text = (self.package / "agents" / (name + ".md")).read_text(encoding="utf-8")
            for marker in required:
                self.assertIn(marker, text, name)

    def test_validate_requires_powershell_installer(self):
        (self.package / "scripts" / "install.ps1").unlink()
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertFalse(manage.validate_package(verbose=False))
        self.assertIn("missing required release file: scripts/install.ps1", stderr.getvalue())

    def assert_validation_fails_with(self, marker):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertFalse(manage.validate_package(verbose=False))
        self.assertIn(marker, stderr.getvalue())

    def test_validate_requires_each_release_png(self):
        for relative in manage.RELEASE_PNGS:
            with self.subTest(relative=relative):
                path = self.package / relative
                original = path.read_bytes()
                path.unlink()
                self.assert_validation_fails_with("missing required release file: {}".format(relative))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(original)

    def test_validate_png_structure_accepts_standard_library_minimal_png(self):
        manage.validate_png_structure(minimal_png())

    def test_validate_png_structure_rejects_truncated_png(self):
        with self.assertRaisesRegex(manage.PackError, "truncated|boundary"):
            manage.validate_png_structure(minimal_png()[:-1])

    def test_validate_png_structure_rejects_bad_crc(self):
        data = bytearray(minimal_png())
        data[-5] ^= 0x01
        with self.assertRaisesRegex(manage.PackError, "invalid CRC"):
            manage.validate_png_structure(bytes(data))

    def test_validate_png_structure_rejects_missing_ihdr(self):
        data = manage.PNG_SIGNATURE + png_chunk(b"IDAT", zlib.compress(b"\x00")) + png_chunk(b"IEND")
        with self.assertRaisesRegex(manage.PackError, "must start with a 13-byte IHDR"):
            manage.validate_png_structure(data)

    def test_validate_png_structure_rejects_missing_idat(self):
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
        data = manage.PNG_SIGNATURE + png_chunk(b"IHDR", ihdr) + png_chunk(b"IEND")
        with self.assertRaisesRegex(manage.PackError, "does not contain an IDAT"):
            manage.validate_png_structure(data)

    def test_validate_png_structure_rejects_missing_iend(self):
        data = minimal_png()[:-12]
        with self.assertRaisesRegex(manage.PackError, "does not contain an IEND"):
            manage.validate_png_structure(data)

    def test_validate_png_structure_rejects_trailing_data(self):
        with self.assertRaisesRegex(manage.PackError, "trailing data after IEND"):
            manage.validate_png_structure(minimal_png() + b"trailing")

    def test_validate_png_structure_rejects_oversized_chunk_before_slicing(self):
        data = manage.PNG_SIGNATURE + struct.pack(">I", manage.MAX_PNG_CHUNK_BYTES + 1) + b"IHDR"
        with self.assertRaisesRegex(manage.PackError, "oversized PNG chunk"):
            manage.validate_png_structure(data)

    def test_validate_png_structure_rejects_non_letter_chunk_type(self):
        data = manage.PNG_SIGNATURE + struct.pack(">I", 0) + b"ID1T" + b"\x00\x00\x00\x00"
        with self.assertRaisesRegex(manage.PackError, "not four ASCII letters"):
            manage.validate_png_structure(data)

    def test_validate_rejects_bad_png_signature(self):
        path = self.package / manage.MODEL_SCREENSHOTS[0]
        data = path.read_bytes()
        path.write_bytes(b"NOT-PNG!" + data[len(manage.PNG_SIGNATURE):])
        self.assert_validation_fails_with("does not have a valid PNG signature")

    def test_validate_rejects_undersized_screenshot(self):
        path = self.package / manage.MODEL_SCREENSHOTS[0]
        path.write_bytes(minimal_png())
        self.assert_validation_fails_with("must be larger than 10 KiB")

    def test_validate_rejects_missing_readme_qr_marker(self):
        readme = self.package / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace('width="25%"', 'width="50%"'), encoding="utf-8")
        self.assert_validation_fails_with('width="25%"')

    def test_validate_requires_versioned_qr_markers(self):
        readme = self.package / "README.md"
        original = readme.read_text(encoding="utf-8")
        markers = (
            'src="{}"'.format(manage.QR_IMAGE),
            'href="{}"'.format(manage.QR_IMAGE),
            manage.QR_URL,
            'alt="扫码入群"',
            "仓库内图片固定随版本审计",
        )
        for marker in markers:
            with self.subTest(marker=marker):
                readme.write_text(original.replace(marker, "REMOVED", 1), encoding="utf-8")
                self.assert_validation_fails_with(marker)
        readme.write_text(original, encoding="utf-8")

    def test_readme_maintainer_gate_is_folded_into_details(self):
        readme = (self.package / "README.md").read_text(encoding="utf-8")
        summary = "<summary><strong>维护者专用：GitHub 发布审查与 PR 门禁</strong></summary>"
        heading = "### 每次 push 前必须 GitHub 智能体审查"
        advanced_summary = "<summary><strong>高级安装、兼容性与维护</strong></summary>"
        self.assertIn(summary, readme)
        self.assertIn(heading, readme)
        self.assertIsNone(re.search(r"(?m)^## 每次 push", readme), "maintainer gate must not stay a top-level heading")
        summary_at = readme.index(summary)
        heading_at = readme.index(heading)
        closing_at = readme.index("</details>", summary_at)
        self.assertLess(summary_at, heading_at)
        self.assertLess(heading_at, closing_at)
        folded = readme[summary_at:closing_at]
        for marker in (
            "维护者每次 push 或发布前必须明确调用",
            "MODE=RELEASE_GATE",
            "./scripts/setup-hooks.sh",
            "release_gate.py check",
            "请为当前仓库执行一次真实的 push/发布门禁",
            "#### github 智能体优化路线图",
            "以下项目根据真实使用反馈分期推进",
        ):
            self.assertIn(marker, folded, marker)
        self.assertLess(closing_at, readme.index(advanced_summary), "maintainer details must close before the advanced-install details")

    def test_validate_requires_docs_and_model_setup_in_checksums(self):
        workflow = self.package / ".github" / "workflows" / "validate.yml"
        original = workflow.read_text(encoding="utf-8")
        workflow.write_text(
            original.replace("release-audits docs", "release-audits"), encoding="utf-8"
        )
        self.assert_validation_fails_with("checksum generation must include the docs directory")
        workflow.write_text(original.replace(" MODEL_SETUP.md", ""), encoding="utf-8")
        self.assert_validation_fails_with("checksum generation must include MODEL_SETUP.md")

    def test_validate_rejects_incomplete_model_guide(self):
        guide = self.package / "MODEL_SETUP.md"
        guide.write_text(guide.read_text(encoding="utf-8").replace("硅基流动", "可选聚合平台"), encoding="utf-8")
        self.assert_validation_fails_with("missing vendor keyword: 硅基流动")

    def test_validate_rejects_model_guide_without_api_key_warning(self):
        guide = self.package / "MODEL_SETUP.md"
        text = guide.read_text(encoding="utf-8").replace("API Key", "访问凭证")
        guide.write_text(text, encoding="utf-8")
        self.assert_validation_fails_with("missing an API Key safety warning")

    def test_release_docs_identify_zcode_only_package(self):
        for filename in ("README.md", "INSTALL-FOR-AI.md"):
            text = (self.package / filename).read_text(encoding="utf-8")
            self.assertIn("ZCode 专用", text, filename)

    def test_release_version_matches_latest_changelog(self):
        plugin = json.loads((self.package / ".zcode-plugin" / "plugin.json").read_text(encoding="utf-8"))
        changelog = (self.package / "CHANGELOG.md").read_text(encoding="utf-8")
        readme = (self.package / "README.md").read_text(encoding="utf-8")
        changelog_match = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
        prompt_match = re.search(r"tag=v(\d+\.\d+\.\d+)", readme)
        self.assertIsNotNone(changelog_match)
        self.assertIsNotNone(prompt_match)
        self.assertEqual(plugin["version"], changelog_match.group(1))
        self.assertEqual(plugin["version"], prompt_match.group(1))
        self.assertIn("20 岗逐个完成红队强化", changelog)
        self.assertIn("PR-only", changelog)
        self.assertIn("无智能体契约变更", changelog)

    def test_release_docs_require_pr_only_main_flow(self):
        required = (
            "PR-only",
            "validate (ubuntu-latest, 3.9)",
            "validate (macos-latest, 3.9)",
            "validate (windows-latest, 3.9)",
            "strict",
            "admins enforced",
            "linear history",
            "conversation resolution",
            "required approving review count",
            "禁止 direct push main",
        )
        for filename in ("README.md", "INSTALL-FOR-AI.md"):
            text = (self.package / filename).read_text(encoding="utf-8")
            for marker in required:
                self.assertIn(marker, text, filename)

        readme = (self.package / "README.md").read_text(encoding="utf-8")
        for marker in (
            "commit branch",
            "push branch",
            "required checks",
            "merge main",
            "gate PASS 后在功能分支 commit/push",
            "更新本地 main",
            "release.sh 创建 tag",
            "最终给用户输出",
            "rules config",
        ):
            self.assertIn(marker, readme)

    def test_release_notes_use_none_when_agents_are_unchanged(self):
        for filename in ("README.md", "INSTALL-FOR-AI.md"):
            text = (self.package / filename).read_text(encoding="utf-8")
            self.assertIn("智能体链接", text, filename)
            self.assertIn("none", text, filename)
            self.assertIn("不得生成未来版本的 agent 链接", text, filename)

    def test_readme_first_screen_has_beginner_prerequisites(self):
        readme = (self.package / "README.md").read_text(encoding="utf-8")
        first_screen = "\n".join(readme.splitlines()[:60])
        for marker in (
            "ZCode 专用",
            "外贸 AI 员工团",
            "安全安装整套智能体",
            "必须先安装 ZCode",
            "不是独立软件",
            "不能直接在 ChatGPT 或 Claude 网页中使用",
            "ZCode >= 3.10.2",
            "至少配置一个可用模型/provider",
            "Python >= 3.9",
            "PowerShell 5.1+",
            "~/.zcode/agents",
            "## 3 步自动安装",
        ):
            self.assertIn(marker, first_screen)

    def test_readme_primary_prompt_is_self_contained_and_safe(self):
        readme = (self.package / "README.md").read_text(encoding="utf-8")
        prompt_start = readme.index("请在 ZCode 中自动安装这个智能体包")
        prompt_end = readme.index("\n```", prompt_start)
        prompt = readme[prompt_start:prompt_end]
        for marker in (
            "repo=https://github.com/tony-apan/zcode_skills",
            "tag=v4.0.0",
            "INSTALL-FOR-AI.md",
            "scripts/model_inventory.py",
            "install --dry-run",
            "同为 4.0.0",
            "$env:TEMP",
            "mktemp",
            "以本提示词为准",
            "CONFLICT 或 LOCAL CHANGE",
            "明确确认",
            "删除临时 clone",
        ):
            self.assertIn(marker, prompt)
        self.assertNotIn("先确认当前客户端是 ZCode", prompt)
        self.assertNotIn("确认 ZCode 至少", prompt)

    def test_docs_have_no_legacy_repo_prefix_or_machine_paths(self):
        legacy_repo_prefix = "010_" + "zcode_skills"
        legacy_github_prefix = "github.com/tony-apan/" + "010"
        for filename in ("README.md", "INSTALL-FOR-AI.md"):
            text = (self.package / filename).read_text(encoding="utf-8")
            self.assertNotIn(legacy_repo_prefix, text, filename)
            self.assertNotIn(legacy_github_prefix, text, filename)
            self.assertNotIn("/Users/", text, filename)
            self.assertNotIn("v1.0.1 --", text, filename)

    def test_bootstrap_protocol_covers_required_stages_and_platforms(self):
        protocol = (self.package / "INSTALL-FOR-AI.md").read_text(encoding="utf-8")
        for marker in (
            "## 阶段 0：环境与 state 预检",
            "## 阶段 1：获取并核验固定版本",
            "## 阶段 2：生成脱敏模型映射",
            "## 阶段 3：执行 install、update 或强制重装",
            "## 阶段 4：完成报告与清理",
            "--branch v4.0.0 --single-branch --depth 1",
            "https://github.com/tony-apan/zcode_skills",
            "同为 `4.0.0`",
            "严禁直接 Read/cat ZCode config",
            "macOS / Linux",
            "Windows PowerShell 5.1+",
            "install --dry-run --model-map",
            "update --dry-run",
            "uninstall --dry-run",
            "以本提示词为准",
            "CONFLICT` 或 `LOCAL CHANGE",
            "Reinstalled missing",
            "上下文未知",
            "删除本次创建的临时 clone 目录",
        ):
            self.assertIn(marker, protocol)
        self.assertNotIn("可复用", protocol)
        self.assertNotIn("当前客户端确为 ZCode", protocol)

    def test_workflow_covers_all_supported_script_platforms(self):
        workflow = (self.package / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
        for runner in ("ubuntu-latest", "macos-latest", "windows-latest"):
            self.assertIn(runner, workflow)
        pins = {
            "checkout": "11d5960a326750d5838078e36cf38b85af677262 # v4",
            "setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065 # v5",
            "upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02 # v4",
        }
        for action, pin in pins.items():
            self.assertIn("actions/{}@{}".format(action, pin), workflow)
        self.assertIn("actions/checkout@{}\n        with:\n          fetch-depth: 0".format(pins["checkout"]), workflow)
        self.assertIsNone(re.search(r"uses:\s+actions/[^@]+@v\d+\b", workflow))
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn('release_gate.py check --commit "${{ github.sha }}"', workflow)
        self.assertIn("$Target = Join-Path $env:RUNNER_TEMP 'tony-agents-dry-run'", workflow)
        self.assertIn("scripts/install.ps1 --dry-run --target-dir $Target", workflow)
        self.assertIn('./scripts/install.sh --dry-run --target-dir "$RUNNER_TEMP/tony-agents-sh"', workflow)

    def test_release_and_pre_push_scripts_enforce_gate(self):
        release = (self.package / "scripts" / "release.sh").read_text(encoding="utf-8")
        hook = (self.package / ".githooks" / "pre-push").read_text(encoding="utf-8")
        self.assertIn('release_gate.py" check', release)
        self.assertIn('release_gate.py" check --root "$ROOT" --commit "$HEAD_SHA"', hook)
        self.assertIn("unittest discover", hook)
        self.assertIn('local_sha" = "$ZERO', hook)

    def test_update_model_map_overrides_model_and_removes_old_thought_level(self):
        initial_map = self.temp / "initial-model-map.json"
        initial_map.write_text(json.dumps({"coder": {"model": "custom:test:old", "thoughtLevel": "high"}}), encoding="utf-8")
        manager = self.manager()
        manager.install(False, str(initial_map))
        update_map = self.temp / "update-model-map.json"
        update_map.write_text(json.dumps({"coder": {"model": "custom:test:new"}}), encoding="utf-8")

        manager.update(False, str(update_map))

        metadata = manage.parse_frontmatter((self.target / "coder.md").read_text(encoding="utf-8"))
        self.assertEqual(metadata["model"], "custom:test:new")
        self.assertNotIn("thoughtLevel", metadata)

    def test_update_agent_absent_from_model_map_preserves_model_and_thought_level(self):
        initial_map = self.temp / "initial-model-map.json"
        initial_map.write_text(json.dumps({"writer": {"model": "custom:test:writer", "thoughtLevel": "high"}}), encoding="utf-8")
        manager = self.manager()
        manager.install(False, str(initial_map))
        update_map = self.temp / "update-model-map.json"
        update_map.write_text(json.dumps({"coder": {"model": "custom:test:coder"}}), encoding="utf-8")

        manager.update(False, str(update_map))

        metadata = manage.parse_frontmatter((self.target / "writer.md").read_text(encoding="utf-8"))
        self.assertEqual(metadata["model"], "custom:test:writer")
        self.assertEqual(metadata["thoughtLevel"], "high")

    def test_model_map_round_trip_on_install(self):
        model_map = self.temp / "model-map.json"
        model_map.write_text(json.dumps({"shencha": {"model": "custom:test:model", "thoughtLevel": "high"}}), encoding="utf-8")
        manager = self.manager()
        manager.install(False, str(model_map))
        metadata = manage.validate_agent_text((self.target / "shencha.md").read_text(encoding="utf-8"), "shencha")
        self.assertEqual(metadata["model"], "custom:test:model")
        self.assertEqual(metadata["thoughtLevel"], "high")


if __name__ == "__main__":
    unittest.main()
