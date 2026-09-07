import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
import io
import re


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
        workflow_dir = self.package / ".github" / "workflows"
        workflow_dir.mkdir(parents=True)
        shutil.copy2(ROOT / ".github" / "workflows" / "validate.yml", workflow_dir / "validate.yml")
        shutil.copy2(ROOT / "LICENSE", self.package / "LICENSE")
        shutil.copy2(ROOT / "CHANGELOG.md", self.package / "CHANGELOG.md")
        shutil.copy2(ROOT / "README.md", self.package / "README.md")
        shutil.copy2(ROOT / "INSTALL-FOR-AI.md", self.package / "INSTALL-FOR-AI.md")
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
        marker = "你是资深软件工程师，负责把主智能体交给你的开发任务实现成可运行、可验证的代码。"
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

    def test_validate_passes(self):
        self.assertTrue(manage.validate_package(verbose=False))

    def test_validate_requires_powershell_installer(self):
        (self.package / "scripts" / "install.ps1").unlink()
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertFalse(manage.validate_package(verbose=False))
        self.assertIn("missing required release file: scripts/install.ps1", stderr.getvalue())

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
        self.assertIn("17 个 `agents/*.md` 岗位定义与契约未改动", changelog)

    def test_readme_first_screen_has_beginner_prerequisites(self):
        readme = (self.package / "README.md").read_text(encoding="utf-8")
        first_screen = "\n".join(readme.splitlines()[:60])
        for marker in (
            "# ZCode 专用",
            "安全安装 18 个智能体",
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
            "tag=v1.1.2",
            "INSTALL-FOR-AI.md",
            "scripts/model_inventory.py",
            "install --dry-run",
            "同为 1.1.2",
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
        for filename in ("README.md", "INSTALL-FOR-AI.md"):
            text = (self.package / filename).read_text(encoding="utf-8")
            self.assertNotIn("010_zcode_skills", text, filename)
            self.assertNotIn("github.com/tony-apan/010", text, filename)
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
            "--branch v1.1.2 --single-branch --depth 1",
            "https://github.com/tony-apan/zcode_skills",
            "同为 `1.1.2`",
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
        self.assertIn("actions/setup-python@v5", workflow)
        self.assertIn("$Target = Join-Path $env:RUNNER_TEMP 'tony-agents-dry-run'", workflow)
        self.assertIn("scripts/install.ps1 --dry-run --target-dir $Target", workflow)
        self.assertIn('./scripts/install.sh --dry-run --target-dir "$RUNNER_TEMP/tony-agents-sh"', workflow)

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
