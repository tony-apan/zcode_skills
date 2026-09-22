import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
import errno
import io
import os
import re
import stat
import struct
import sys
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
        collisions = [
            path.stem
            for path in self.target.glob("*.md")
            if path.stem in manage.source_agents()
        ]
        if collisions:
            self.install_with_approval(manager, overwrite=collisions)
        else:
            self.install_with_approval(manager)
        return manager

    def install_with_approval(self, manager, model_map_path=None, overwrite=None, force=False):
        plan, _, _ = manager.install_plan(
            model_map_path,
            force=force,
            overwrite=overwrite,
            allow_unverified_model_map=bool(model_map_path),
        )
        manager.install(
            False,
            model_map_path,
            force=force,
            overwrite=overwrite,
            confirm_plan=plan["digest"],
            allow_unverified_model_map=bool(model_map_path),
        )
        return manager

    def install_model_map_unverified(self, manager, model_map_path):
        return self.install_with_approval(manager, model_map_path=str(model_map_path))

    def update_with_approval(
        self,
        manager,
        model_map_path=None,
        inventory_path=None,
        allow_unverified_model_map=False,
        add=None,
        remove=None,
        overwrite=None,
        keep=None,
    ):
        plan = manager.update(
            True,
            str(model_map_path) if model_map_path else None,
            str(inventory_path) if inventory_path else None,
            allow_unverified_model_map,
            add=add,
            remove=remove,
            overwrite=overwrite,
            keep=keep,
        )
        manager.update(
            False,
            str(model_map_path) if model_map_path else None,
            str(inventory_path) if inventory_path else None,
            allow_unverified_model_map,
            plan["digest"],
            add,
            remove,
            overwrite,
            keep,
        )
        return manager

    def rollback_with_approval(self, manager, snapshot_id="latest"):
        plan = manager.rollback(snapshot_id, True)
        manager.rollback(snapshot_id, False, plan["digest"])
        return plan

    def write_inventory(self, providers):
        path = self.temp / "inventory.json"
        path.write_text(
            json.dumps(
                {
                    "generator": "tony-agents-pack/model_inventory",
                    "providers": providers,
                    "schema_version": 1,
                    "verification": "DECLARED_UNVERIFIED",
                }
            ),
            encoding="utf-8",
        )
        return path

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
        original = manage.conditional_atomic_write
        failed_target = failed_target.resolve()
        failed = {"value": False}

        def side_effect(path, data, expected_sha):
            if Path(path).resolve() == failed_target and not failed["value"]:
                failed["value"] = True
                raise OSError("injected atomic write failure")
            return original(path, data, expected_sha)

        return mock.patch.object(manage, "conditional_atomic_write", side_effect=side_effect)

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
            collision_names = [Path(name).stem for name in originals]
            plan, _, _ = manager.install_plan(None, overwrite=collision_names)
            with self.assertRaisesRegex(manage.PackError, "操作失败且已自动回滚"):
                manager.install(
                    False,
                    None,
                    overwrite=collision_names,
                    confirm_plan=plan["digest"],
                )

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
                self.update_with_approval(manager)

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

    def test_scan_classifies_foreign_and_unmanaged_collisions(self):
        self.target.mkdir(parents=True)
        (self.target / "coder.md").write_text("unmanaged coder\n", encoding="utf-8")
        (self.target / "private-agent.md").write_text("private agent\n", encoding="utf-8")
        summary = self.manager().scan()
        statuses = {entry["name"]: entry["status"] for entry in summary["entries"]}
        self.assertEqual(statuses["coder"], "COLLISION_UNMANAGED")
        self.assertEqual(statuses["private-agent"], "FOREIGN")

    def test_operation_lock_rejects_overlapping_process(self):
        manager = self.manager()
        code = (
            "import importlib.util, pathlib; "
            "p=pathlib.Path({!r}); "
            "s=importlib.util.spec_from_file_location('m', p); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "mgr=m.Manager(pathlib.Path({!r})); "
            "ctx=mgr.operation_lock(); ctx.__enter__()"
        ).format(str(self.package / "scripts" / "manage.py"), str(self.target))
        with manager.operation_lock():
            result = subprocess.run(
                [sys.executable, "-c", code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("another package operation is already running", result.stderr)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support is required")
    def test_snapshot_rejects_symlink_before_reading_external_content(self):
        outside = self.temp / "outside-private.md"
        outside.write_text("PRIVATE OUTSIDE CONTENT\n", encoding="utf-8")
        self.target.mkdir(parents=True)
        (self.target / "coder.md").symlink_to(outside)
        manager = self.manager()
        with self.assertRaisesRegex(manage.DecisionRequired, "not a regular file|cannot open .* safely"):
            manager.install_plan(None, only=["coder"], overwrite=["coder"])
        snapshots = list(manager.snapshots_dir.glob("*")) if manager.snapshots_dir.exists() else []
        self.assertEqual(snapshots, [])
        self.assertEqual(outside.read_text(encoding="utf-8"), "PRIVATE OUTSIDE CONTENT\n")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support is required")
    def test_snapshot_rejects_symlink_state_before_reading_external_content(self):
        outside = self.temp / "outside-state.json"
        outside.write_text('{"private":"DO NOT SNAPSHOT"}\n', encoding="utf-8")
        manager = self.manager()
        manager.meta_dir.mkdir(parents=True)
        manager.state_file.symlink_to(outside)

        with self.assertRaisesRegex(manage.DecisionRequired, "state.json is not a regular file|cannot open state.json safely"):
            manager.create_snapshot([], "audit")

        snapshots = list(manager.snapshots_dir.glob("*")) if manager.snapshots_dir.exists() else []
        self.assertEqual(snapshots, [])
        self.assertEqual(outside.read_text(encoding="utf-8"), '{"private":"DO NOT SNAPSHOT"}\n')

    def test_decision_required_after_snapshot_preserves_concurrent_target(self):
        manager = self.manager()
        plan, _, _ = manager.install_plan(None, only=["coder"])
        original_create_snapshot = manager.create_snapshot
        concurrent = b"late user content\n"

        def side_effect(names, operation):
            snapshot = original_create_snapshot(names, operation)
            self.target.mkdir(parents=True, exist_ok=True)
            (self.target / "coder.md").write_bytes(concurrent)
            return snapshot

        with mock.patch.object(manager, "create_snapshot", side_effect=side_effect):
            with self.assertRaisesRegex(manage.PackError, "target changed after plan confirmation"):
                manager.install(False, None, only=["coder"], confirm_plan=plan["digest"])
        self.assertEqual((self.target / "coder.md").read_bytes(), concurrent)
        self.assertFalse(manager.state_file.exists())

    def test_atomic_write_preimage_check_rejects_late_target_change(self):
        path = self.temp / "target.md"
        path.write_text("approved\n", encoding="utf-8")
        approved_sha = manage.sha256_file(path)
        path.write_text("late change\n", encoding="utf-8")
        with self.assertRaisesRegex(manage.DecisionRequired, "ownership claim"):
            manage.atomic_write(
                path,
                b"replacement\n",
                expected_sha=approved_sha,
                verify_preimage=True,
            )
        self.assertEqual(path.read_text(encoding="utf-8"), "late change\n")

    def test_atomic_write_final_publish_race_preserves_both_versions(self):
        path = self.temp / "target.md"
        approved = b"approved\n"
        concurrent = b"concurrent user save\n"
        path.write_bytes(approved)
        expected_sha = manage.sha256_bytes(approved)
        original_exclusive_rename = manage.exclusive_rename
        injected = {"value": False}

        def side_effect(source, target, source_dir_fd=None, target_dir_fd=None):
            if Path(target) == path.resolve() and not injected["value"]:
                injected["value"] = True
                path.write_bytes(concurrent)
            return original_exclusive_rename(source, target, source_dir_fd, target_dir_fd)

        with mock.patch.object(manage, "exclusive_rename", side_effect=side_effect):
            with self.assertRaisesRegex(manage.DecisionRequired, "preserved candidate"):
                manage.conditional_atomic_write(path, b"installer write\n", expected_sha)

        self.assertEqual(path.read_bytes(), concurrent)
        candidates = list(path.parent.glob(path.name + ".tony-agents-pack.concurrent.*"))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].read_bytes(), approved)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support is required")
    def test_metadata_directory_symlink_is_rejected_without_escape(self):
        self.target.mkdir(parents=True)
        outside = self.temp / "outside-metadata"
        outside.mkdir()
        (self.target / ".tony-agents-pack").symlink_to(outside, target_is_directory=True)
        manager = self.manager()

        with self.assertRaisesRegex(manage.DecisionRequired, "metadata path contains a symlink"):
            with manager.operation_lock():
                self.fail("symlinked metadata directory unexpectedly locked")

        self.assertEqual(list(outside.iterdir()), [])

    def test_automatic_rollback_keeps_operation_lock_held(self):
        manager = self.manager()
        plan, _, _ = manager.install_plan(None, only=["coder"])
        original_restore = manager.restore_snapshot
        observed = {}
        code = (
            "import importlib.util, pathlib; "
            "p=pathlib.Path({!r}); "
            "s=importlib.util.spec_from_file_location('m', p); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "mgr=m.Manager(pathlib.Path({!r})); "
            "ctx=mgr.operation_lock(); ctx.__enter__()"
        ).format(str(ROOT / "scripts" / "manage.py"), str(self.target))

        def restore_side_effect(snapshot, *args, **kwargs):
            observed["result"] = subprocess.run(
                [sys.executable, "-c", code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            return original_restore(snapshot, *args, **kwargs)

        with mock.patch.object(manager, "write_target", side_effect=OSError("injected failure")):
            with mock.patch.object(manager, "restore_snapshot", side_effect=restore_side_effect):
                with self.assertRaisesRegex(manage.PackError, "已自动回滚"):
                    manager.install(
                        False,
                        None,
                        only=["coder"],
                        confirm_plan=plan["digest"],
                    )

        self.assertNotEqual(observed["result"].returncode, 0)
        self.assertIn("another package operation is already running", observed["result"].stderr)

    def test_post_write_user_change_is_preserved_and_never_recorded_clean(self):
        manager = self.manager()
        plan, _, _ = manager.install_plan(None, only=["coder"])
        target = manager.target_dir / "coder.md"
        concurrent = b"USER SAVE AFTER INSTALLER WRITE\n"
        original_read = manage.read_required_regular_bytes
        injected = {"value": False}

        def read_side_effect(path, label, error_type=manage.PackError):
            if label == "installed target" and Path(path) == target and not injected["value"]:
                injected["value"] = True
                target.write_bytes(concurrent)
            return original_read(path, label, error_type)

        with mock.patch.object(manage, "read_required_regular_bytes", side_effect=read_side_effect):
            with self.assertRaisesRegex(manage.PackError, "已自动回滚"):
                manager.install(
                    False,
                    None,
                    only=["coder"],
                    confirm_plan=plan["digest"],
                )

        self.assertEqual(target.read_bytes(), concurrent)
        self.assertFalse(manager.state_file.exists())

    def test_keyboard_interrupt_rolls_back_and_main_has_no_traceback(self):
        manager = self.manager()
        plan, _, _ = manager.install_plan(None, only=["coder", "writer"])
        original_write = manager.write_target
        calls = {"count": 0}

        def write_side_effect(path, data, expected_sha):
            calls["count"] += 1
            if calls["count"] == 2:
                raise KeyboardInterrupt()
            return original_write(path, data, expected_sha)

        with mock.patch.object(manager, "write_target", side_effect=write_side_effect):
            with self.assertRaises(KeyboardInterrupt):
                manager.install(
                    False,
                    None,
                    only=["coder", "writer"],
                    confirm_plan=plan["digest"],
                )
        self.assertFalse((self.target / "coder.md").exists())
        self.assertFalse(manager.state_file.exists())

        stderr = io.StringIO()
        with mock.patch.object(manage.Manager, "scan", side_effect=KeyboardInterrupt()):
            with redirect_stderr(stderr):
                result = manage.main(["scan", "--target-dir", str(self.target)])
        self.assertEqual(result, 130)
        self.assertIn("operation interrupted", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_remove_after_claim_preserves_concurrent_recreated_target(self):
        manager = self.manager()
        manager.target_dir.mkdir(parents=True)
        target = manager.target_dir / "coder.md"
        approved = b"approved package content\n"
        concurrent = b"user recreated target\n"
        target.write_bytes(approved)
        original_unlink = manage.unlink_path
        injected = {"value": False}

        def unlink_side_effect(path, directory_fd=None):
            if ".tony-agents-pack.concurrent." in Path(path).name and not injected["value"]:
                injected["value"] = True
                target.write_bytes(concurrent)
            return original_unlink(path, directory_fd)

        with mock.patch.object(manage, "unlink_path", side_effect=unlink_side_effect):
            manager.remove_target(target, manage.sha256_bytes(approved))

        self.assertEqual(target.read_bytes(), concurrent)
        self.assertEqual(list(target.parent.glob(target.name + ".tony-agents-pack.concurrent.*")), [])

    def test_windows_lock_fallback_uses_msvcrt_without_directory_fd(self):
        manager = self.manager()
        fake_msvcrt = mock.MagicMock()
        fake_msvcrt.LK_NBLCK = 1
        fake_msvcrt.LK_UNLCK = 2
        manager.meta_dir.mkdir(parents=True)
        lock_fd = os.open(str(manager.lock_file), os.O_RDWR | os.O_CREAT, 0o600)
        with mock.patch.object(manage, "open_directory_no_symlinks", return_value=(manager.meta_dir, None)):
            with mock.patch.object(manage, "open_windows_lock_file", return_value=lock_fd):
                with mock.patch.object(manage.os, "name", "nt"):
                    with mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                        with manager.operation_lock():
                            pass
        self.assertEqual(fake_msvcrt.locking.call_count, 2)
        self.assertEqual(fake_msvcrt.locking.call_args_list[0].args[1:], (1, 1))
        self.assertEqual(fake_msvcrt.locking.call_args_list[1].args[1:], (2, 1))

    def test_windows_exclusive_rename_rejects_existing_target(self):
        source = self.temp / "rename-source.md"
        target = self.temp / "rename-target.md"
        source.write_bytes(b"source content\n")
        target.write_bytes(b"existing target\n")
        real_rename = manage.os.rename

        def rename_side_effect(src, dst, **kwargs):
            if os.fspath(dst) == os.fspath(target):
                raise FileExistsError(errno.EEXIST, "File exists", str(dst))
            return real_rename(src, dst, **kwargs)

        with mock.patch.object(manage.sys, "platform", "win32"):
            with mock.patch.object(manage.os, "name", "nt"):
                with mock.patch.object(manage.os, "rename", side_effect=rename_side_effect):
                    self.assertFalse(manage.exclusive_rename(source, target))
        self.assertEqual(source.read_bytes(), b"source content\n")
        self.assertEqual(target.read_bytes(), b"existing target\n")

    def test_windows_exclusive_rename_moves_when_target_absent(self):
        source = self.temp / "rename-src.md"
        target = self.temp / "rename-dst.md"
        source.write_bytes(b"payload\n")
        with mock.patch.object(manage.sys, "platform", "win32"):
            with mock.patch.object(manage.os, "name", "nt"):
                self.assertTrue(manage.exclusive_rename(source, target))
        self.assertFalse(source.exists())
        self.assertEqual(target.read_bytes(), b"payload\n")

    @staticmethod
    def fake_kernel32(create_result, attributes, get_information_result=True):
        class FakeFunction:
            def __init__(self, implementation):
                self.implementation = implementation
                self.calls = []

            def __call__(self, *args):
                self.calls.append(args)
                return self.implementation(*args)

        create_file = FakeFunction(lambda *args: create_result)
        close_handle = FakeFunction(lambda handle: True)

        def get_information(handle, pointer):
            if not get_information_result:
                return False
            pointer._obj.file_attributes = attributes
            return True

        kernel32 = type("Kernel32", (), {})()
        kernel32.CreateFileW = create_file
        kernel32.CloseHandle = close_handle
        kernel32.GetFileInformationByHandle = FakeFunction(get_information)
        return kernel32, close_handle

    def test_windows_lock_inspect_failure_closes_handle(self):
        kernel32, close_handle = self.fake_kernel32(123, 0x00000080, get_information_result=False)
        fake_msvcrt = mock.MagicMock()
        with mock.patch.object(manage.ctypes, "WinDLL", return_value=kernel32, create=True):
            with mock.patch.object(manage.ctypes, "get_last_error", return_value=2, create=True):
                with mock.patch.object(manage.ctypes, "FormatError", return_value="mocked error", create=True):
                    with mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                        with self.assertRaisesRegex(manage.DecisionRequired, "cannot inspect operation lock"):
                            manage.open_windows_lock_file(self.temp / "operation.lock")
        self.assertEqual(close_handle.calls, [(123,)])
        fake_msvcrt.open_osfhandle.assert_not_called()

    def test_windows_lock_open_osfhandle_failure_closes_handle(self):
        kernel32, close_handle = self.fake_kernel32(123, 0x00000080)
        fake_msvcrt = mock.MagicMock()
        fake_msvcrt.open_osfhandle.side_effect = OSError("cannot make fd")
        with mock.patch.object(manage.ctypes, "WinDLL", return_value=kernel32, create=True):
            with mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                with self.assertRaisesRegex(OSError, "cannot make fd"):
                    manage.open_windows_lock_file(self.temp / "operation.lock")
        self.assertEqual(close_handle.calls, [(123,)])

    def test_update_empty_selection_values_fail_closed(self):
        manager = self.install()
        for kwargs in ({"add": [""]}, {"remove": [""]}, {"overwrite": [""]}, {"keep": [""]}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(manage.PackError, "selection is empty"):
                    manager.update(True, None, **kwargs)

    def test_windows_directory_guard_holds_no_delete_share_handles(self):
        class FakeFunction:
            def __init__(self, implementation):
                self.implementation = implementation
                self.calls = []

            def __call__(self, *args):
                self.calls.append(args)
                return self.implementation(*args)

        handles = iter(range(100, 200))
        create_file = FakeFunction(lambda *args: next(handles))
        close_handle = FakeFunction(lambda handle: True)

        def get_information(handle, pointer):
            pointer._obj.file_attributes = 0x00000010
            return True

        kernel32 = type("Kernel32", (), {})()
        kernel32.CreateFileW = create_file
        kernel32.CloseHandle = close_handle
        kernel32.GetFileInformationByHandle = FakeFunction(get_information)
        with mock.patch.object(manage.ctypes, "WinDLL", return_value=kernel32, create=True):
            _, guard = manage.open_windows_directory_chain(self.temp, create=False)
        try:
            self.assertGreater(len(create_file.calls), 0)
            for call in create_file.calls:
                self.assertEqual(call[2], 0x00000003)
                self.assertEqual(call[5] & 0x00200000, 0x00200000)
                self.assertEqual(call[5] & 0x02000000, 0x02000000)
        finally:
            guard.close()
        self.assertEqual(len(close_handle.calls), len(create_file.calls))

    def test_windows_missing_metadata_is_absent_before_win32_calls(self):
        missing = self.temp / "missing" / ".tony-agents-pack"
        with self.assertRaises(FileNotFoundError):
            manage.open_windows_directory_chain(missing, create=False)

    def test_invalid_selected_agents_fails_cleanly(self):
        manager = self.install()
        state = json.loads(manager.state_file.read_text(encoding="utf-8"))
        state["selected_agents"] = 5
        manager.state_file.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(manage.PackError, "invalid selected_agents"):
            manager.install_plan(None, force=True)

    def test_automatic_rollback_before_first_write_has_no_concurrency_noise(self):
        manager = self.manager()
        plan, _, _ = manager.install_plan(None, only=["coder", "writer"])
        output = io.StringIO()
        with redirect_stdout(output):
            with mock.patch.object(manager, "write_target", side_effect=OSError("injected before first write")):
                with self.assertRaisesRegex(manage.PackError, "已自动回滚"):
                    manager.install(
                        False,
                        None,
                        only=["coder", "writer"],
                        confirm_plan=plan["digest"],
                    )
        self.assertNotIn("ROLLBACK PRESERVED CONCURRENT CHANGE", output.getvalue())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support is required")
    def test_rollback_dry_run_rejects_symlinked_snapshots_directory(self):
        manager = self.install()
        outside = self.temp / "external-snapshots"
        outside.mkdir()
        shutil.rmtree(manager.snapshots_dir)
        manager.snapshots_dir.symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(manage.DecisionRequired, "symlink|non-directory"):
            manager.rollback("latest", True)
        self.assertEqual(list(outside.iterdir()), [])

    def test_windows_lock_rejects_reparse_handle_without_path_fallback(self):
        class FakeFunction:
            def __init__(self, implementation):
                self.implementation = implementation
                self.calls = []

            def __call__(self, *args):
                self.calls.append(args)
                return self.implementation(*args)

        create_file = FakeFunction(lambda *args: 123)
        close_handle = FakeFunction(lambda handle: True)

        def get_information(handle, pointer):
            pointer._obj.file_attributes = 0x00000400
            return True

        kernel32 = type("Kernel32", (), {})()
        kernel32.CreateFileW = create_file
        kernel32.CloseHandle = close_handle
        kernel32.GetFileInformationByHandle = FakeFunction(get_information)
        fake_msvcrt = mock.MagicMock()
        with mock.patch.object(manage.ctypes, "WinDLL", return_value=kernel32, create=True):
            with mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                with self.assertRaisesRegex(manage.DecisionRequired, "non-reparse"):
                    manage.open_windows_lock_file(self.temp / "operation.lock")
        self.assertEqual(close_handle.calls[0][0], 123)
        fake_msvcrt.open_osfhandle.assert_not_called()

    @unittest.skipUnless(Path("/dev/fd").exists(), "fd accounting requires /dev/fd")
    def test_special_state_reads_do_not_leak_directory_descriptors(self):
        manager = self.manager()
        manager.meta_dir.mkdir(parents=True)
        outside = self.temp / "outside-state.json"
        outside.write_text("{}", encoding="utf-8")
        manager.state_file.symlink_to(outside)
        before = len(list(Path("/dev/fd").iterdir()))
        for _ in range(20):
            with self.assertRaises(manage.PackError):
                manager.load_state(required=True)
        after = len(list(Path("/dev/fd").iterdir()))
        self.assertLessEqual(after, before + 1)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support is required")
    def test_scan_does_not_read_symlink_target(self):
        self.target.mkdir(parents=True)
        outside = self.temp / "outside-secret.md"
        outside.write_bytes(b"SECRET OUTSIDE CONTENT\n")
        (self.target / "coder.md").symlink_to(outside)
        summary = self.manager().scan()
        entry = next(item for item in summary["entries"] if item["name"] == "coder")
        self.assertEqual(entry["status"], "SPECIAL_UNMANAGED")
        self.assertIsNone(entry["sha256"])

    def test_unique_candidate_names_do_not_overwrite_previous_candidates(self):
        target = self.temp / "coder.md"
        with mock.patch.object(
            manage,
            "unique_id",
            side_effect=["20260922T000000.000001Z", "20260922T000000.000002Z"],
        ):
            first = target.with_name(
                target.name + ".tony-agents-pack.incoming." + manage.unique_id()
            )
            second = target.with_name(
                target.name + ".tony-agents-pack.incoming." + manage.unique_id()
            )
        manage.conditional_atomic_write(first, b"first candidate\n", None)
        manage.conditional_atomic_write(second, b"second candidate\n", None)
        self.assertEqual(first.read_bytes(), b"first candidate\n")
        self.assertEqual(second.read_bytes(), b"second candidate\n")

    def test_windows_lock_rejection_releases_directory_guard(self):
        manager = self.manager()
        guard = mock.MagicMock()
        with mock.patch.object(manage, "open_directory_no_symlinks", return_value=(manager.meta_dir, guard)):
            with mock.patch.object(manage, "open_windows_lock_file", side_effect=manage.DecisionRequired("reparse")):
                with mock.patch.object(manage.os, "name", "nt"):
                    with self.assertRaisesRegex(manage.DecisionRequired, "reparse"):
                        with manager.operation_lock():
                            self.fail("reparse lock unexpectedly opened")
        guard.close.assert_called_once_with()

    def test_read_race_to_fifo_fails_without_blocking(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO support is required")
        path = self.temp / "race-target"
        path.write_bytes(b"regular\n")
        original_open = manage.os.open
        injected = {"value": False}

        def open_side_effect(value, flags, *args, **kwargs):
            if Path(value) == path and not injected["value"]:
                injected["value"] = True
                path.unlink()
                os.mkfifo(path)
            return original_open(value, flags, *args, **kwargs)

        with mock.patch.object(manage.os, "open", side_effect=open_side_effect):
            with self.assertRaisesRegex(manage.DecisionRequired, "changed before"):
                manage.read_optional_regular_bytes(path, "race target")

    def test_explicit_empty_selection_fails_closed(self):
        manager = self.manager()
        for value in ("", "   ", ","):
            with self.subTest(value=value):
                with self.assertRaisesRegex(manage.PackError, "agent selection is empty"):
                    manager.install_plan(None, only=[value])

    def test_empty_cli_paths_are_rejected_without_writing_current_directory(self):
        script = self.package / "scripts" / "manage.py"
        for arguments in (
            ["scan", "--target-dir", ""],
            ["install", "--dry-run", "--target-dir", ""],
            ["install", "--dry-run", "--target-dir", str(self.target), "--model-map", ""],
        ):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, str(script)] + arguments,
                    cwd=str(self.temp),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must not be empty", result.stderr)
                self.assertFalse((self.temp / ".tony-agents-pack").exists())

    def test_unknown_user_path_fails_without_traceback(self):
        script = self.package / "scripts" / "manage.py"
        result = subprocess.run(
            [sys.executable, str(script), "scan", "--target-dir", "~tony_agents_no_such_user/agents"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERROR:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_inventory_boolean_schema_is_rejected(self):
        inventory = self.write_inventory([])
        value = json.loads(inventory.read_text(encoding="utf-8"))
        value["schema_version"] = True
        inventory.write_text(json.dumps(value), encoding="utf-8")
        model_map = self.temp / "model-map.json"
        model_map.write_text(
            json.dumps({"coder": {"model": "custom:provider:model"}}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(manage.PackError, "unsupported schema"):
            self.manager().install_plan(
                str(model_map),
                only=["coder"],
                inventory_path=str(inventory),
            )

    @unittest.skipIf(os.name == "nt", "fcntl is only available on Unix")
    def test_unsupported_locking_filesystem_fails_with_specific_error(self):
        import fcntl

        manager = self.manager()
        with mock.patch.object(fcntl, "flock", side_effect=OSError(errno.ENOTSUP, "unsupported")):
            with self.assertRaisesRegex(manage.DecisionRequired, "does not support operation locking"):
                with manager.operation_lock():
                    self.fail("operation lock unexpectedly succeeded")

    def test_unmanaged_collision_requires_explicit_decision_before_writes(self):
        self.target.mkdir(parents=True)
        original = b"unmanaged coder\n"
        (self.target / "coder.md").write_bytes(original)
        manager = self.manager()
        with self.assertRaisesRegex(manage.DecisionRequired, "unmanaged collisions"):
            manager.install(False, None)
        self.assertEqual((self.target / "coder.md").read_bytes(), original)
        self.assertFalse(manager.state_file.exists())

    def test_keep_preserves_unmanaged_collision_and_does_not_track_it(self):
        self.target.mkdir(parents=True)
        original = b"unmanaged coder\n"
        (self.target / "coder.md").write_bytes(original)
        manager = self.manager()
        plan, _, _ = manager.install_plan(None, keep=["coder"])
        manager.install(False, None, keep=["coder"], confirm_plan=plan["digest"])
        self.assertEqual((self.target / "coder.md").read_bytes(), original)
        self.assertNotIn("coder", manager.load_state(required=True)["files"])

    def test_keep_or_overwrite_requires_real_unmanaged_collision(self):
        manager = self.manager()
        with self.assertRaisesRegex(manage.PackError, "require an existing unmanaged collision"):
            manager.install_plan(None, only=["coder"], keep=["coder"])
        with self.assertRaisesRegex(manage.PackError, "require an existing unmanaged collision"):
            manager.install_plan(None, only=["coder"], overwrite=["coder"])

        installed = self.install()
        with self.assertRaisesRegex(manage.PackError, "only to unmanaged collisions"):
            installed.install_plan(None, force=True, keep=["coder"])

    def test_subset_install_writes_only_selected_agents(self):
        manager = self.manager()
        plan, _, _ = manager.install_plan(None, only=["coder", "writer"])
        manager.install(False, None, only=["coder", "writer"], confirm_plan=plan["digest"])
        self.assertEqual(sorted(path.stem for path in self.target.glob("*.md")), ["coder", "writer"])
        self.assertEqual(set(manager.load_state(required=True)["files"]), {"coder", "writer"})

    def test_confirmed_plan_is_invalidated_when_target_changes(self):
        manager = self.manager()
        plan, _, _ = manager.install_plan(None, only=["coder"])
        self.target.mkdir(parents=True)
        (self.target / "coder.md").write_text("appeared after approval\n", encoding="utf-8")
        with self.assertRaisesRegex(manage.DecisionRequired, "unmanaged collisions|confirm-plan"):
            manager.install(False, None, only=["coder"], confirm_plan=plan["digest"])

    def test_subset_update_does_not_silently_install_unselected_agents(self):
        manager = self.manager()
        plan, _, _ = manager.install_plan(None, only=["coder"])
        manager.install(False, None, only=["coder"], confirm_plan=plan["digest"])
        self.assertEqual(set(manager.load_state(required=True)["selected_agents"]), {"coder"})

        output = io.StringIO()
        with redirect_stdout(output):
            self.update_with_approval(manager)
        self.assertEqual(sorted(path.stem for path in self.target.glob("*.md")), ["coder"])
        self.assertIn("AVAILABLE NOT SELECTED writer", output.getvalue())

        self.update_with_approval(manager, add=["writer"])
        self.assertEqual(sorted(path.stem for path in self.target.glob("*.md")), ["coder", "writer"])
        self.assertEqual(set(manager.load_state(required=True)["selected_agents"]), {"coder", "writer"})

    def test_update_add_collision_requires_explicit_decision(self):
        manager = self.manager()
        plan, _, _ = manager.install_plan(None, only=["coder"])
        manager.install(False, None, only=["coder"], confirm_plan=plan["digest"])
        self.target.mkdir(parents=True, exist_ok=True)
        original = b"unmanaged writer\n"
        (self.target / "writer.md").write_bytes(original)
        with self.assertRaisesRegex(manage.DecisionRequired, "added collisions"):
            manager.update(True, None, add=["writer"])
        self.assertEqual((self.target / "writer.md").read_bytes(), original)

        self.update_with_approval(manager, add=["writer"], keep=["writer"])
        self.assertEqual((self.target / "writer.md").read_bytes(), original)
        self.assertNotIn("writer", manager.load_state(required=True)["files"])

        self.update_with_approval(manager, add=["writer"], overwrite=["writer"])
        self.assertNotEqual((self.target / "writer.md").read_bytes(), original)
        record = manager.load_state(required=True)["files"]["writer"]
        self.assertTrue(record["preexisting"])
        self.assertEqual(Path(record["backup_path"]).read_bytes(), original)

    def test_confirmed_update_plan_is_invalidated_by_target_drift(self):
        manager = self.install()
        source = self.package / "agents" / "coder.md"
        source.write_text(source.read_text(encoding="utf-8") + "\nPACKAGE UPDATE\n", encoding="utf-8")
        plan = manager.update(True, None)
        target = self.target / "coder.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nLATE LOCAL CHANGE\n", encoding="utf-8")
        with self.assertRaisesRegex(manage.DecisionRequired, "confirm-plan"):
            manager.update(False, None, confirm_plan=plan["digest"])

    def test_inventory_schema_and_utf16_are_supported_or_rejected_safely(self):
        providers = [
            {
                "enabled": True,
                "id": "provider-a",
                "models": [
                    {
                        "name": "model-a",
                        "limit": {"context": 100000},
                        "modalities": {"input": ["text"]},
                        "reasoning": {"variants": ["high"]},
                    }
                ],
            }
        ]
        inventory = self.write_inventory(providers)
        model_map = self.temp / "model-map.json"
        model_map.write_text(json.dumps({"coder": {"model": "custom:provider-a:model-a"}}), encoding="utf-8")
        value = json.loads(inventory.read_text(encoding="utf-8"))
        inventory.write_bytes(json.dumps(value).encode("utf-16"))
        plan, _, _ = self.manager().install_plan(
            str(model_map), only=["coder"], inventory_path=str(inventory)
        )
        self.assertEqual(plan["model_status"], "DECLARED_UNVERIFIED")

        inventory.write_text(json.dumps({"providers": providers, "verification": "DECLARED_UNVERIFIED"}), encoding="utf-8")
        with self.assertRaisesRegex(manage.PackError, "unsupported schema or generator"):
            self.manager().install_plan(str(model_map), only=["coder"], inventory_path=str(inventory))

    def test_update_missing_target_prefers_explicit_model_map(self):
        initial_map = self.temp / "initial.json"
        initial_map.write_text(
            json.dumps({"coder": {"model": "custom:test:old", "thoughtLevel": "high"}}),
            encoding="utf-8",
        )
        manager = self.manager()
        self.install_model_map_unverified(manager, initial_map)
        (self.target / "coder.md").unlink()
        update_map = self.temp / "update.json"
        update_map.write_text(
            json.dumps({"coder": {"model": "custom:test:new"}}),
            encoding="utf-8",
        )
        self.update_with_approval(manager, update_map, allow_unverified_model_map=True)
        metadata = manage.parse_frontmatter((self.target / "coder.md").read_text(encoding="utf-8"))
        self.assertEqual(metadata["model"], "custom:test:new")
        self.assertNotIn("thoughtLevel", metadata)

    def test_update_rejects_keep_or_overwrite_for_already_managed_agent(self):
        manager = self.install()
        with self.assertRaisesRegex(manage.PackError, "only to unmanaged"):
            manager.update(True, None, add=["coder"], keep=["coder"])
        with self.assertRaisesRegex(manage.PackError, "only to unmanaged"):
            manager.update(True, None, add=["coder"], overwrite=["coder"])
        subset_target = self.temp / "subset-home" / ".zcode" / "agents"
        subset_manager = manage.Manager(subset_target)
        subset_plan, _, _ = subset_manager.install_plan(None, only=["coder"])
        subset_manager.install(False, None, only=["coder"], confirm_plan=subset_plan["digest"])
        with self.assertRaisesRegex(manage.PackError, "require an existing unmanaged added collision"):
            subset_manager.update(True, None, add=["writer"], keep=["writer"])

    def test_force_install_cannot_change_selected_set(self):
        manager = self.manager()
        plan, _, _ = manager.install_plan(None, only=["coder", "writer"])
        manager.install(False, None, only=["coder", "writer"], confirm_plan=plan["digest"])
        with self.assertRaisesRegex(manage.PackError, "cannot change the selected agent set"):
            manager.install_plan(None, force=True, only=["coder"])

    def test_removed_modified_agent_stays_selected(self):
        manager = self.install()
        target = self.target / "writer.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nLOCAL CHANGE\n", encoding="utf-8")
        self.update_with_approval(manager, remove=["writer"])
        state = manager.load_state(required=True)
        self.assertIn("writer", state["files"])
        self.assertIn("writer", state["selected_agents"])

    def test_inconsistent_selected_agents_does_not_duplicate_removed_output(self):
        manager = self.install()
        state = json.loads(manager.state_file.read_text(encoding="utf-8"))
        state["selected_agents"].remove("writer")
        manager.state_file.write_text(json.dumps(state), encoding="utf-8")

        output = io.StringIO()
        with redirect_stdout(output):
            manager.update(True, None)
        report = output.getvalue()
        self.assertIn("REMOVED writer", report)
        self.assertNotIn("AVAILABLE NOT SELECTED writer", report)

    def test_state_requires_matching_internal_base_content(self):
        manager = self.install()
        state = json.loads(manager.state_file.read_text(encoding="utf-8"))
        base = Path(state["files"]["coder"]["base_path"])
        base.write_text("tampered base\n", encoding="utf-8")
        with self.assertRaisesRegex(manage.PackError, "base content does not match base_sha"):
            manager.load_state(required=True)

    def test_rollback_rejects_path_traversal_and_non_object_manifest(self):
        manager = self.install()
        evil = manager.snapshots_dir / "20260921T000000.000000Z"
        evil.mkdir(parents=True)
        (evil / "snapshot.json").write_text(
            json.dumps({"state_existed": False, "files": {"../../../victim": {"existed": False}}}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(manage.PackError, "invalid file entry"):
            manager.rollback(evil.name, False)
        (evil / "snapshot.json").write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(manage.PackError, "JSON object"):
            manager.rollback(evil.name, False)
        with self.assertRaisesRegex(manage.PackError, "invalid snapshot id"):
            manager.rollback("../../../victim", False)

    def downgrade_state_to_legacy(self, manager):
        state = json.loads(manager.state_file.read_text(encoding="utf-8"))
        state.pop("schema_version", None)
        state.pop("selected_agents", None)
        for record in state["files"].values():
            record.pop("base_sha", None)
        manager.state_file.write_text(json.dumps(state), encoding="utf-8")

    def test_legacy_state_remove_modified_migrates_to_readable_schema_v2(self):
        manager = self.install()
        self.downgrade_state_to_legacy(manager)
        target = self.target / "writer.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nLOCAL CHANGE\n", encoding="utf-8")
        self.update_with_approval(manager, remove=["writer"])
        state = manager.load_state(required=True)
        self.assertEqual(state["schema_version"], manage.STATE_SCHEMA_VERSION)
        self.assertIn("base_sha", state["files"]["writer"])
        self.assertIn("writer", state["selected_agents"])
        manager.scan()

    def test_legacy_state_uninstall_modified_migrates_remaining_record(self):
        manager = self.install()
        self.downgrade_state_to_legacy(manager)
        target = self.target / "writer.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nLOCAL CHANGE\n", encoding="utf-8")
        manager.uninstall(False)
        state = manager.load_state(required=True)
        self.assertEqual(state["schema_version"], manage.STATE_SCHEMA_VERSION)
        self.assertIn("base_sha", state["files"]["writer"])
        manager.scan()

    def test_state_package_mismatch_is_rejected(self):
        manager = self.manager()
        manager.meta_dir.mkdir(parents=True)
        manager.state_file.write_text(json.dumps({"package": "other-pack", "files": {}}), encoding="utf-8")
        with self.assertRaisesRegex(manage.PackError, "different package"):
            manager.load_state(required=True)

    def test_state_metadata_path_escape_is_rejected(self):
        manager = self.manager()
        manager.meta_dir.mkdir(parents=True)
        base = manager.bases_dir / "op" / "coder.md"
        base.parent.mkdir(parents=True)
        base.write_text("base\n", encoding="utf-8")
        digest = manage.sha256_file(base)
        manager.state_file.write_text(
            json.dumps(
                {
                    "package": manage.PACKAGE_NAME,
                    "files": {
                        "coder": {
                            "source_sha": "0" * 64,
                            "installed_sha": digest,
                            "backup_path": str(self.temp / "outside.md"),
                            "base_path": str(base),
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(manage.PackError, "escapes"):
            manager.load_state(required=True)

    def test_model_map_requires_inventory_or_explicit_unverified_approval(self):
        model_map = self.temp / "model-map.json"
        model_map.write_text(json.dumps({"coder": {"model": "custom:test:model"}}), encoding="utf-8")
        manager = self.manager()
        with self.assertRaisesRegex(manage.DecisionRequired, "requires --inventory"):
            manager.install_plan(str(model_map))
        plan, _, _ = manager.install_plan(str(model_map), allow_unverified_model_map=True)
        self.assertEqual(plan["model_status"], "UNVERIFIED_USER_ACCEPTED")

    def test_inventory_rejects_unknown_model_and_thought_level(self):
        inventory = self.write_inventory(
            [
                {
                    "enabled": True,
                    "id": "provider-a",
                    "models": [
                        {
                            "name": "model-a",
                            "limit": {"context": 100000},
                            "modalities": {"input": ["text"]},
                            "reasoning": {"variants": ["low", "high"]},
                        }
                    ],
                }
            ]
        )
        model_map = self.temp / "model-map.json"
        model_map.write_text(json.dumps({"coder": {"model": "custom:provider-a:missing"}}), encoding="utf-8")
        with self.assertRaisesRegex(manage.PackError, "not in the enabled inventory"):
            self.manager().install_plan(str(model_map), inventory_path=str(inventory))
        model_map.write_text(
            json.dumps({"coder": {"model": "custom:provider-a:model-a", "thoughtLevel": "max"}}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(manage.PackError, "thoughtLevel.*not declared"):
            self.manager().install_plan(str(model_map), inventory_path=str(inventory))

    def test_model_map_validated_by_inventory_needs_confirmed_plan(self):
        inventory = self.write_inventory(
            [
                {
                    "enabled": True,
                    "id": "provider-a",
                    "models": [
                        {
                            "name": "model-a",
                            "limit": {"context": 100000},
                            "modalities": {"input": ["text"]},
                            "reasoning": {"variants": ["high"]},
                        }
                    ],
                }
            ]
        )
        model_map = self.temp / "model-map.json"
        model_map.write_text(
            json.dumps({"coder": {"model": "custom:provider-a:model-a", "thoughtLevel": "high"}}),
            encoding="utf-8",
        )
        manager = self.manager()
        plan, _, _ = manager.install_plan(str(model_map), only=["coder"], inventory_path=str(inventory))
        self.assertEqual(plan["model_status"], "DECLARED_UNVERIFIED")
        with self.assertRaisesRegex(manage.DecisionRequired, "confirm-plan"):
            manager.install(False, str(model_map), only=["coder"], inventory_path=str(inventory))
        manager.install(
            False,
            str(model_map),
            only=["coder"],
            inventory_path=str(inventory),
            confirm_plan=plan["digest"],
        )
        metadata = manage.parse_frontmatter((self.target / "coder.md").read_text(encoding="utf-8"))
        self.assertEqual(metadata["model"], "custom:provider-a:model-a")
        self.assertEqual(metadata["thoughtLevel"], "high")

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

    def test_force_install_after_partial_uninstall_preserves_selected_set_and_snapshots_modified(self):
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
        plan, _, _ = manager.install_plan(None, force=True)
        with redirect_stdout(output):
            manager.install(False, None, force=True, confirm_plan=plan["digest"])

        self.assertEqual(sorted(path.stem for path in self.target.glob("*.md")), ["writer"])
        state = manager.load_state(required=True)
        self.assertEqual(set(state["files"]), {"writer"})
        self.assertEqual(set(state["selected_agents"]), {"writer"})
        writer_record = state["files"]["writer"]
        self.assertTrue(writer_record["preexisting"])
        self.assertEqual(Path(writer_record["backup_path"]).read_bytes(), original)
        snapshots = sorted(manager.snapshots_dir.iterdir())
        manifest = json.loads((snapshots[-1] / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["operation"], "force-install")
        self.assertEqual((snapshots[-1] / "files" / "writer.md").read_bytes(), modified_before)
        self.assertIn("Force install plan", output.getvalue())

    def test_force_install_ignores_selected_agent_removed_from_package(self):
        manager = self.install()
        (self.package / "agents" / "writer.md").unlink()
        self.update_with_approval(manager)
        state = manager.load_state(required=True)
        self.assertNotIn("writer", state["files"])
        plan, agents, _ = manager.install_plan(None, force=True)
        self.assertNotIn("writer", agents)
        manager.install(False, None, force=True, confirm_plan=plan["digest"])
        self.assertFalse((self.target / "writer.md").exists())

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
        self.assertIn("AVAILABLE NOT SELECTED new-agent", report)
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

        self.update_with_approval(manager, add=["new-agent"])
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

        self.update_with_approval(manager)

        self.assertFalse((self.target / "writer.md").exists())
        state = manager.load_state(required=True)
        self.assertNotIn("writer", state["files"])

    def test_update_removed_modified_agent_is_preserved_and_tracked(self):
        manager = self.install()
        target = self.target / "writer.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nLOCAL CHANGE\n", encoding="utf-8")
        (self.package / "agents" / "writer.md").unlink()

        self.update_with_approval(manager)

        self.assertIn("LOCAL CHANGE", target.read_text(encoding="utf-8"))
        state = manager.load_state(required=True)
        self.assertIn("writer", state["files"])

    def test_update_added_preexisting_agent_backup_is_restored_by_uninstall(self):
        manager = self.install()
        source = self.add_source_agent()
        self.target.mkdir(parents=True, exist_ok=True)
        original = b"preexisting new agent\n"
        (self.target / source.name).write_bytes(original)

        self.update_with_approval(manager, add=["new-agent"], overwrite=["new-agent"])
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
        self.update_with_approval(manager)
        installed = (self.target / "coder.md").read_text(encoding="utf-8")
        self.assertIn("PACKAGE UPGRADE", installed)
        state = manager.load_state(required=True)
        self.assertEqual(state["files"]["coder"]["installed_sha"], manage.sha256_file(self.target / "coder.md"))

    def test_update_reinstalls_missing_agent(self):
        model_map = self.temp / "model-map.json"
        model_map.write_text(json.dumps({"coder": {"model": "custom:test:coder", "thoughtLevel": "high"}}), encoding="utf-8")
        manager = self.manager()
        self.install_model_map_unverified(manager, model_map)
        target = self.target / "coder.md"
        target.unlink()
        output = io.StringIO()

        with redirect_stdout(output):
            self.update_with_approval(manager)

        self.assertTrue(target.is_file())
        metadata = manage.parse_frontmatter(target.read_text(encoding="utf-8"))
        self.assertEqual(metadata["model"], "custom:test:coder")
        self.assertEqual(metadata["thoughtLevel"], "high")
        self.assertIn("Reinstalled missing coder", output.getvalue())
        self.assertEqual(list(target.parent.glob(target.name + ".tony-agents-pack.incoming*")), [])
        state = manager.load_state(required=True)
        self.assertEqual(state["files"]["coder"]["installed_sha"], manage.sha256_file(target))

    @unittest.skipUnless(shutil.which("git"), "git is required for merge coverage")
    def test_clean_three_way_merge_keeps_state_loadable(self):
        manager = self.install()
        target = self.target / "coder.md"
        source = self.package / "agents" / "coder.md"
        marker = "你是资深软件工程师，负责把边界清楚的日常开发任务实现成可运行、可验证的代码。"
        self.assertIn(marker, target.read_text(encoding="utf-8"))
        local_text = target.read_text(encoding="utf-8").replace(marker, "LOCAL CUSTOMIZATION", 1)
        target.write_text(local_text, encoding="utf-8")
        source.write_text(source.read_text(encoding="utf-8") + "\nREMOTE PACKAGE CHANGE\n", encoding="utf-8")

        self.update_with_approval(manager)

        merged = target.read_text(encoding="utf-8")
        self.assertIn("LOCAL CUSTOMIZATION", merged)
        self.assertIn("REMOTE PACKAGE CHANGE", merged)
        state = manager.load_state(required=True)
        record = state["files"]["coder"]
        self.assertEqual(manage.sha256_file(Path(record["base_path"])), record["base_sha"])
        next_plan = manager.update(True, None)
        self.assertIn("digest", next_plan)

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

        self.update_with_approval(manager)

        self.assertIn("LOCAL CUSTOMIZATION", target.read_text(encoding="utf-8"))
        incoming_candidates = list(target.parent.glob("coder.md.tony-agents-pack.incoming.*"))
        self.assertEqual(len(incoming_candidates), 1)
        incoming = incoming_candidates[0]
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
        self.update_with_approval(manager)
        self.assertNotEqual(target.read_bytes(), before)

        plan = self.rollback_with_approval(manager)

        self.assertEqual(target.read_bytes(), before)
        self.assertRegex(plan["snapshot_id"], r"^[0-9]{8}T[0-9]{6}\.[0-9]{6}Z$")
        state = manager.load_state(required=True)
        self.assertEqual(state["files"]["coder"]["installed_sha"], manage.sha256_bytes(before))

    def test_rollback_dry_run_reports_actions_and_requires_matching_digest(self):
        manager = self.install()
        source = self.package / "agents" / "coder.md"
        source.write_text(source.read_text(encoding="utf-8") + "\nNEW RELEASE\n", encoding="utf-8")
        self.update_with_approval(manager)

        output = io.StringIO()
        with redirect_stdout(output):
            plan = manager.rollback("latest", True)
        report = output.getvalue()
        self.assertIn("RESTORE", report)
        self.assertIn(str(manager.state_file), report)
        self.assertIn("PLAN_DIGEST {}".format(plan["digest"]), report)
        with self.assertRaisesRegex(manage.DecisionRequired, "requires --confirm-plan"):
            manager.rollback("latest", False, "0" * 64)

    def test_rollback_rejects_target_drift_after_dry_run_without_writes(self):
        manager = self.install()
        source = self.package / "agents" / "coder.md"
        source.write_text(source.read_text(encoding="utf-8") + "\nNEW RELEASE\n", encoding="utf-8")
        self.update_with_approval(manager)
        plan = manager.rollback("latest", True)
        target = self.target / "coder.md"
        concurrent = b"USER CHANGE AFTER DRY RUN\n"
        target.write_bytes(concurrent)
        state_before = manager.state_file.read_bytes()

        with self.assertRaisesRegex(manage.DecisionRequired, "requires --confirm-plan"):
            manager.rollback("latest", False, plan["digest"])

        self.assertEqual(target.read_bytes(), concurrent)
        self.assertEqual(manager.state_file.read_bytes(), state_before)

    def test_rollback_preserves_late_change_and_writes_candidate(self):
        manager = self.install()
        target = self.target / "coder.md"
        expected_rollback = target.read_bytes()
        source = self.package / "agents" / "coder.md"
        source.write_text(source.read_text(encoding="utf-8") + "\nNEW RELEASE\n", encoding="utf-8")
        self.update_with_approval(manager)
        plan = manager.rollback("latest", True)
        state_before = manager.state_file.read_bytes()
        original_create_snapshot = manager.create_snapshot
        concurrent = b"USER CHANGE INSIDE LOCK\n"

        def side_effect(names, operation):
            snapshot = original_create_snapshot(names, operation)
            target.write_bytes(concurrent)
            return snapshot

        with mock.patch.object(manager, "create_snapshot", side_effect=side_effect):
            with self.assertRaisesRegex(manage.DecisionRequired, "rollback target changed"):
                manager.rollback("latest", False, plan["digest"])

        self.assertEqual(target.read_bytes(), concurrent)
        self.assertEqual(manager.state_file.read_bytes(), state_before)
        candidates = list(self.target.glob("coder.md.tony-agents-pack.rollback.*"))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].read_bytes(), expected_rollback)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support is required")
    def test_rollback_rejects_symlink_target_after_confirmation(self):
        manager = self.install()
        target = self.target / "coder.md"
        source = self.package / "agents" / "coder.md"
        source.write_text(source.read_text(encoding="utf-8") + "\nNEW RELEASE\n", encoding="utf-8")
        self.update_with_approval(manager)
        plan = manager.rollback("latest", True)
        outside = self.temp / "outside-user-file.md"
        outside.write_bytes(b"EXTERNAL USER CONTENT\n")
        target.unlink()
        target.symlink_to(outside)

        with self.assertRaisesRegex(manage.DecisionRequired, "not a regular file|cannot open .* safely"):
            manager.rollback(plan["snapshot_id"], False, plan["digest"])

        self.assertTrue(target.is_symlink())
        self.assertEqual(outside.read_bytes(), b"EXTERNAL USER CONTENT\n")

    def test_snapshot_schema_v1_compatibility_and_v2_integrity(self):
        manager = self.install()
        target = self.target / "coder.md"
        legacy_data = b"legacy snapshot content\n"
        legacy = manager.snapshots_dir / "20260921T000000.000001Z"
        (legacy / "files").mkdir(parents=True)
        (legacy / "files" / "coder.md").write_bytes(legacy_data)
        state_before = manager.state_file.read_bytes()
        (legacy / "state.json").write_bytes(state_before)
        (legacy / "snapshot.json").write_text(
            json.dumps(
                {
                    "created_at": "2026-09-21T00:00:00+00:00",
                    "operation": "legacy",
                    "files": {"coder": {"existed": True}},
                    "state_existed": True,
                }
            ),
            encoding="utf-8",
        )
        manager.restore_snapshot(legacy)
        self.assertEqual(target.read_bytes(), legacy_data)
        self.assertEqual(manager.state_file.read_bytes(), state_before)

        snapshot = manager.create_snapshot(["coder"], "integrity")
        manifest_path = snapshot / "snapshot.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], manage.SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(manifest["files"]["coder"]["sha256"], manage.sha256_bytes(legacy_data))
        self.assertEqual(manifest["state_sha256"], manage.sha256_file(manager.state_file))

        (snapshot / "files" / "coder.md").write_bytes(b"tampered\n")
        with self.assertRaisesRegex(manage.PackError, "does not match its manifest"):
            manager.read_snapshot(snapshot)

    def test_snapshot_v2_requires_file_and_state_digests(self):
        manager = self.install()
        snapshot = manager.create_snapshot(["coder"], "integrity")
        manifest_path = snapshot / "snapshot.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["coder"].pop("sha256")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(manage.PackError, "invalid file sha256"):
            manager.read_snapshot(snapshot)

        snapshot = manager.create_snapshot(["coder"], "integrity")
        manifest_path = snapshot / "snapshot.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.pop("state_sha256")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(manage.PackError, "invalid state_sha256"):
            manager.read_snapshot(snapshot)

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
        self.assertIn("`gonghao`（公众号长文）", reviewer)
        self.assertIn("upstream_agent", reviewer)
        social_contract = re.search(r"同一组状态值 `([^`]+)`", reviewer).group(1)
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
            "dongcha": "5c1aa2b7b7a8f736334241cafa2fcac7e34592e108908753d6c0552437b881d2",
            "shencha-content": "ab2ceacdc7910c5f1178d489fa80deba0f4ecdd083a47b1fc26ca876ed66660c",
            "sheyun": "cd5649dacb0832515e328d0513d68a1c7b777988e78420d523d8fe75745eeeb3",
        }
        for name, expected_hash in expected_hashes.items():
            published = (self.package / "agents" / (name + ".md")).read_bytes()
            published_body = self.normalized_agent_body(published)
            self.assertEqual(manage.sha256_bytes(published_body), expected_hash, name)

    def test_changed_agent_body_parser_is_crlf_stable(self):
        for name in ("dongcha", "shencha-content", "sheyun"):
            published = (self.package / "agents" / (name + ".md")).read_bytes()
            lf = published.replace(b"\r\n", b"\n")
            crlf = lf.replace(b"\n", b"\r\n")
            self.assertEqual(self.normalized_agent_body(crlf), self.normalized_agent_body(lf), name)

    def test_local_agent_bodies_match_release_for_every_agent(self):
        if os.environ.get("TONY_AGENTS_CHECK_LOCAL_SYNC") != "1":
            self.skipTest("set TONY_AGENTS_CHECK_LOCAL_SYNC=1 for maintainer-only local sync audit")
        names = sorted(p.name for p in (self.package / "agents").glob("*.md"))
        self.assertEqual(len(names), manage.EXPECTED_AGENT_COUNT)
        local_dir = Path.home() / ".zcode" / "agents"
        if not local_dir.is_dir():
            self.skipTest("local source directory is unavailable")
        for filename in names:
            with self.subTest(agent=filename):
                local_source = local_dir / filename
                self.assertTrue(local_source.is_file(), "本地缺少 agent: " + filename)
                published = (self.package / "agents" / filename).read_bytes()
                self.assertEqual(
                    self.normalized_agent_body(published),
                    self.normalized_agent_body(local_source.read_bytes()),
                    filename,
                )

    def test_gonghao_ad_identification_and_review_markers(self):
        text = (self.package / "agents" / "gonghao.md").read_text(encoding="utf-8")
        for marker in (
            "广告可识别性",
            "广告审查批准文号",
            "平台规则来源URL",
            "适用法域与行业",
            "资质/审查文号缺口",
            "review_tier=STANDARD|HIGH_RISK",
        ):
            self.assertIn(marker, text, marker)

    def test_shencha_content_accepts_gonghao_as_social_upstream(self):
        text = (self.package / "agents" / "shencha-content.md").read_text(encoding="utf-8")
        self.assertIn("`gonghao`（公众号长文）", text)
        # upstream_agent 必须落在报告接口字段表，而不只是正文提一句
        self.assertIn("deprecated_input / upstream_agent`", text)
        self.assertIn("`upstream_agent` 必填", text)

    def test_no_agent_forces_content_review_for_strategy_only_modes(self):
        sheyun = (self.package / "agents" / "sheyun.md").read_text(encoding="utf-8")
        gonghao = (self.package / "agents" / "gonghao.md").read_text(encoding="utf-8")
        self.assertIn("S4 纯策略无成稿", sheyun)
        self.assertIn("G4 纯策略无成稿", gonghao)
        for text, label in ((sheyun, "sheyun"), (gonghao, "gonghao")):
            idx = text.index("纯策略无成稿")
            tail = text[idx:text.index("\n- 完成末行", idx)]
            self.assertNotIn("必须明确写 `shencha-content", tail, label)

    def test_content_producers_pass_review_tier_and_upstream_agent(self):
        for name, upstream in (("sheyun", "sheyun"), ("gonghao", "gonghao")):
            text = (self.package / "agents" / (name + ".md")).read_text(encoding="utf-8")
            self.assertIn("review_tier=STANDARD|HIGH_RISK", text, name)
            self.assertIn("upstream_agent=" + upstream, text, name)
        frontend = (self.package / "agents" / "frontend.md").read_text(encoding="utf-8")
        self.assertIn("review_profiles=[microcopy] review_tier=STANDARD", frontend)

    def test_changelog_does_not_contradict_strategy_status_rule(self):
        import json as _json
        version = _json.loads((self.package / ".zcode-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
        changelog = (self.package / "CHANGELOG.md").read_text(encoding="utf-8")
        start = changelog.index("## [{}]".format(version))
        rest = changelog[start:]
        nxt = rest.find("\n## [", 1)
        current = rest if nxt == -1 else rest[:nxt]
        self.assertNotIn("G4 无成稿时用 `DRAFT_DO_NOT_PUBLISH`", current)
        self.assertIn("gonghao", changelog)

    def test_strategy_only_modes_do_not_emit_content_status(self):
        sheyun = (self.package / "agents" / "sheyun.md").read_text(encoding="utf-8")
        gonghao = (self.package / "agents" / "gonghao.md").read_text(encoding="utf-8")
        self.assertIn("S4：策略目标", sheyun)
        self.assertIn("不输出 `CONTENT_STATUS`", sheyun)
        self.assertIn("不输出 `CONTENT_STATUS`", gonghao)
        # S2/S3 必须锚定状态
        self.assertIn("批次实验说明 → `CONTENT_STATUS` → 待确认", sheyun)
        self.assertIn("选题雷达 → `CONTENT_STATUS` → 待确认", sheyun)

    def test_gonghao_strategy_route_does_not_misreference_engineering_reviewer(self):
        text = (self.package / "agents" / "gonghao.md").read_text(encoding="utf-8")
        idx = text.index("- **G4 纯策略无成稿**")
        tail = text[idx:text.index("\n- 完成末行", idx)]
        self.assertIn("主智能体决策", tail)
        self.assertNotIn("按需交 `shencha` 做策略审查", tail)

    def test_writer_description_does_not_claim_interface_microcopy(self):
        for name in ("writer", "writer-pro"):
            text = (self.package / "agents" / (name + ".md")).read_text(encoding="utf-8")
            description = manage.parse_frontmatter(text)["description"]
            self.assertNotIn("产品微文案", description, name)
            self.assertNotIn("界面文案", description, name)

    def test_gonghao_g4_does_not_force_content_review(self):
        text = (self.package / "agents" / "gonghao.md").read_text(encoding="utf-8")
        idx = text.index("- **G4 纯策略无成稿**")
        tail = text[idx:text.index("\n- 完成末行", idx)]
        self.assertIn("不进入内容门禁", tail)
        self.assertNotIn("必须明确写 `shencha-content", tail)

    def test_frontend_routes_interface_copy_to_microcopy_profile(self):
        text = (self.package / "agents" / "frontend.md").read_text(encoding="utf-8")
        self.assertIn("review_profiles=[microcopy]", text)

    def test_audit_exclusion_docs_match_gate_regex(self):
        for filename in ("README.md", "release-audits/README.md", "agents/github.md"):
            text = (self.package / filename).read_text(encoding="utf-8")
            self.assertNotIn("release-audits/v*.md", text, filename)

    def test_microcopy_ownership_is_exclusive_between_frontend_and_writer(self):
        fe = (self.package / "agents" / "frontend.md").read_text(encoding="utf-8")
        wr = (self.package / "agents" / "writer.md").read_text(encoding="utf-8")
        self.assertIn("与 writer 的分工（排他）", fe)
        self.assertIn("由你唯一负责", fe)
        self.assertIn("界面内文案", wr)
        self.assertIn("由 `frontend` 唯一负责", wr)

    def test_sheyun_contract_declares_platform_scope(self):
        text = (self.package / "agents" / "sheyun.md").read_text(encoding="utf-8")
        self.assertIn("LinkedIn / Facebook / Instagram", text)
        self.assertIn("`gonghao`", text)

    def test_readme_badge_matches_agent_count(self):
        readme = (self.package / "README.md").read_text(encoding="utf-8")
        self.assertIn("badge/agents-22-", readme)
        self.assertIn("(#22-个岗位)", readme)
        self.assertNotIn("badge/agents-21-", readme)

    def test_gonghao_published_contract_and_tool_boundary(self):
        path = self.package / "agents" / "gonghao.md"
        text = path.read_text(encoding="utf-8")
        metadata = manage.parse_frontmatter(text)
        self.assertEqual(manage.EXPECTED_AGENT_COUNT, 22)
        for key in manage.FORBIDDEN_PUBLISHED_KEYS:
            self.assertNotIn(key, metadata)
        self.assertTrue({"Bash", "Edit"}.isdisjoint(metadata["tools"]))
        self.assertIn("Write", metadata["tools"])
        for marker in (
            "G1 单篇", "G2 系列", "G3 周运营", "G4 纯策略",
            "## 平台规则与合规（公众号特有）",
            "诱导分享", "诱导关注", "绝对化用语",
            "原创声明", "留言区",
            "review_profiles=[editorial,social]",
        ):
            self.assertIn(marker, text, marker)

    def test_validate_rejects_missing_gonghao_contract_markers(self):
        path = self.package / "agents" / "gonghao.md"
        original = path.read_text(encoding="utf-8")
        for marker in (
            "## 平台规则与合规（公众号特有）",
            "诱导分享",
            "诱导关注",
            "绝对化用语",
            "原创声明",
            "review_profiles=[editorial,social]",
            "G4 纯策略",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, original)
                stripped = original.replace(marker, "")
                self.assertNotEqual(stripped, original)
                path.write_text(stripped, encoding="utf-8")
                self.assert_validation_fails_with(
                    "missing role contract marker: {}".format(marker)
                )
                path.write_text(original, encoding="utf-8")

    def test_frontend_microcopy_contract_markers(self):
        path = self.package / "agents" / "frontend.md"
        original = path.read_text(encoding="utf-8")
        for marker in ("## 界面文案（微文案）", "[文案待确认", "与真实状态同源"):
            self.assertIn(marker, original, marker)
        for marker in ("## 界面文案（微文案）", "[文案待确认"):
            with self.subTest(marker=marker):
                stripped = original.replace(marker, "")
                self.assertNotEqual(stripped, original)
                path.write_text(stripped, encoding="utf-8")
                self.assert_validation_fails_with(
                    "missing role contract marker: {}".format(marker)
                )
                path.write_text(original, encoding="utf-8")

    def test_readme_and_changelog_declare_twenty_two_agents(self):
        readme = (self.package / "README.md").read_text(encoding="utf-8")
        changelog = (self.package / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## 22 个岗位", readme)
        self.assertIn("`gonghao`", readme)
        self.assertNotIn("21 个岗位", readme)
        self.assertIn("## [4.2.3] — 2026-09-22", changelog)
        self.assertIn("## [4.2.2] — 2026-09-21", changelog)
        self.assertIn("## [4.2.1] — 2026-09-11", changelog)
        self.assertIn("## [4.2.0] — 2026-09-10", changelog)
        self.assertIn("gonghao", changelog)

    def test_dongcha_published_contract_and_tool_boundary(self):
        text = (self.package / "agents" / "dongcha.md").read_text(encoding="utf-8")
        metadata = manage.parse_frontmatter(text)
        self.assertEqual(manage.EXPECTED_AGENT_COUNT, 22)
        self.assertNotIn("model", metadata)
        self.assertNotIn("thoughtLevel", metadata)
        self.assertNotIn("skills", metadata)
        self.assertTrue({"Bash", "Edit"}.isdisjoint(metadata["tools"]))
        self.assertIn("Write", metadata["tools"])
        for marker in (
            "VALIDATION_BACKLOG",
            "claim_status",
            "production_verdict",
            "PRODUCTION_ELIGIBLE",
            "E0_UNATTRIBUTED",
            "E4_PRIMARY_OR_VERIFIABLE",
            "SEARCH_VALIDATED",
            "SERP_VALIDATED",
            "QUERY_HYPOTHESIS",
            "AI_PROMPT_VALIDATED",
            "usable_as_fact",
            "public_discussion_safety",
            "origin_source_id",
            "snapshot_id",
            "只提议不授予",
            "不可信内容防线",
        ):
            self.assertIn(marker, text)

    def test_dongcha_review_grant_and_fact_consumer_markers(self):
        markers = {
            "shencha-content": (
                "## dongcha claim 复核",
                "review_profiles=[editorial]",
                "APPROVE | CHANGES | REJECT",
                "claim_status=VERIFIED",
                "不得自行置 `production_verdict=PRODUCTION_ELIGIBLE`",
                "不得改写证据等级",
                "本节 `VERIFIED` 指 finding 关闭，不是 claim_status",
            ),
            "shencha-final": (
                "## dongcha 生产资格授予",
                "唯一可将 `production_verdict` 置为 `PRODUCTION_ELIGIBLE`",
                "claim_status=VERIFIED",
                "SEARCH_VALIDATED",
                "SERP_VALIDATED",
                "AI_PROMPT_",
                "run 级重交 <=3",
                "单 claim 审查 <=3",
                "专项重验 <=2",
                "conflict-id",
            ),
            "writer": (
                "## dongcha 事实接口",
                "usable_as_fact=Y",
                "claim_status=VERIFIED",
                "production_verdict=PRODUCTION_ELIGIBLE",
                "usable_as_fact=N",
                "claim_id",
                "evidence_ids",
            ),
            "writer-pro": (
                "## dongcha 事实接口",
                "usable_as_fact=Y",
                "claim_status=VERIFIED",
                "production_verdict=PRODUCTION_ELIGIBLE",
                "usable_as_fact=N",
                "claim_id",
                "evidence_ids",
            ),
        }
        for name, required in markers.items():
            text = (self.package / "agents" / (name + ".md")).read_text(encoding="utf-8")
            for marker in required:
                self.assertIn(marker, text, name)

    def test_dongcha_round_limits_evidence_floor_and_namespace_markers(self):
        text = (self.package / "agents" / "dongcha.md").read_text(encoding="utf-8")
        for marker in (
            "## 审查轮次上限",
            "run 级重交 <=3",
            "单 claim 审查 <=3",
            "专项重验 <=2",
            "差分再审",
            "## claim_type 证据下限表",
            "| claim_type | 允许 source_type 白名单 | 最小独立来源数 | 禁止替代 |",
            "need_jtbd",
            "不得用于搜索/行为类主张",
            "AI_PROMPT_HYPOTHESIS",
            "任何专项 verdict 不得升级 `claim_status` 或证据等级",
            "唯一例外是 `SEARCH_VALIDATED` 仅由一方日志证据置位",
        ):
            self.assertIn(marker, text)

    def test_validate_rejects_missing_demand_insight_contract_markers(self):
        cases = {
            "dongcha": ("## 审查轮次上限", "## claim_type 证据下限表"),
            "shencha-content": ("## dongcha claim 复核",),
            "shencha-final": ("## dongcha 生产资格授予", "production_verdict", "PRODUCTION_ELIGIBLE"),
            "writer": ("## dongcha 事实接口", "usable_as_fact"),
            "writer-pro": ("## dongcha 事实接口", "usable_as_fact"),
        }
        for name, markers in cases.items():
            path = self.package / "agents" / (name + ".md")
            original = path.read_text(encoding="utf-8")
            for marker in markers:
                with self.subTest(name=name, marker=marker):
                    path.write_text(original.replace(marker, "REMOVED_MARKER"), encoding="utf-8")
                    self.assert_validation_fails_with("missing role contract marker: {}".format(marker))
                    path.write_text(original, encoding="utf-8")

    def test_validate_rejects_each_dongcha_claim_type_table_row_removal(self):
        path = self.package / "agents" / "dongcha.md"
        original = path.read_text(encoding="utf-8")
        row_starts = (
            "| `pain` |",
            "| `need_jtbd` |",
            "| `search_behavior` |",
            "| `ai_prompt_behavior` |",
            "| `buying_behavior` |",
            "| `transaction` |",
        )
        for row in row_starts:
            with self.subTest(row=row):
                self.assertIn(row, original)
                stripped = "\n".join(line for line in original.splitlines() if not line.startswith(row))
                self.assertNotEqual(stripped, original.rstrip("\n"))
                path.write_text(stripped + "\n", encoding="utf-8")
                self.assert_validation_fails_with("missing role contract marker: {}".format(row))
                path.write_text(original, encoding="utf-8")

    def test_validate_rejects_dongcha_claim_type_header_and_need_jtbd_substitute_tampering(self):
        path = self.package / "agents" / "dongcha.md"
        original = path.read_text(encoding="utf-8")
        header = "| claim_type | 允许 source_type 白名单 | 最小独立来源数 | 禁止替代 |"
        need_jtbd_substitute = "不得以改标 `need_jtbd` 绕过 `search_behavior`/`buying_behavior` 下限"
        for marker, tampered in (
            (header, "| claim_type | 来源 | 数量 | 备注 |"),
            (need_jtbd_substitute, "允许以改标 `need_jtbd` 绕过证据下限"),
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, original)
                path.write_text(original.replace(marker, tampered, 1), encoding="utf-8")
                self.assert_validation_fails_with("missing role contract marker: {}".format(marker))
                path.write_text(original, encoding="utf-8")

    def test_validate_rejects_dongcha_production_eligible_invariant_removal(self):
        path = self.package / "agents" / "dongcha.md"
        original = path.read_text(encoding="utf-8")
        implication = "=> claim_status=VERIFIED ∧ claim_type 证据下限满足 ∧ 无未决冲突 ∧ freshness 通过"
        invariant_block = "production_verdict=PRODUCTION_ELIGIBLE\n" + implication
        self.assertIn(invariant_block, original)
        for removal in (implication + "\n", invariant_block + "\n"):
            with self.subTest(removal=removal.splitlines()[0]):
                path.write_text(original.replace(removal, "", 1), encoding="utf-8")
                self.assert_validation_fails_with("missing role contract marker: {}".format(implication))
                path.write_text(original, encoding="utf-8")

    def test_readme_marks_updated_agents_and_three_tier_round_limits(self):
        readme = (self.package / "README.md").read_text(encoding="utf-8")
        self.assertIn("model_inventory.py` 可能返回空 `providers`", readme)
        self.assertIn("run 级重交 ≤3", readme)
        self.assertIn("单 claim 审查 ≤3", readme)
        self.assertIn("专项重验 ≤2", readme)

    def test_validate_rejects_production_eligible_in_dongcha_claim_status_enum(self):
        path = self.package / "agents" / "dongcha.md"
        original = path.read_text(encoding="utf-8")
        path.write_text(
            original.replace(
                "claim_status:      DRAFT -> EVIDENCE_COLLECTED -> VERIFIED -> EXPIRED | SUPERSEDED | REJECTED",
                "claim_status:      DRAFT -> EVIDENCE_COLLECTED -> VERIFIED -> PRODUCTION_ELIGIBLE -> EXPIRED | SUPERSEDED | REJECTED",
                1,
            ),
            encoding="utf-8",
        )
        self.assert_validation_fails_with("dongcha claim_status enum must not contain PRODUCTION_ELIGIBLE")

    def test_dongcha_specialized_handoff_markers(self):
        markers = {
            "seoer": ("SERP_CONFIRMED", "SERP_ABSENT", "SERP_INTENT", "CANNIBALIZED", "NO_CONFLICT", "WINNABILITY_A-D", "SERP_VALIDATED", "SEARCH_VALIDATED", "site_asset_inventory"),
            "huoke": ("FIT=True", "N1 边界反例", "N2 匹配但不买", "N3 与 >=E3 来源冲突", "REFUTED"),
            "outreach": ("claim_id", "hypothesis_id", "evidence_ids", "claim_status<VERIFIED", "production_verdict!=PRODUCTION_ELIGIBLE", "usage_scope", "REFUTED"),
            "sheyun": ("public_discussion_safety", "visual_evidence_type", "hook_angle", "interaction_trigger", "lead_magnet", "brand_risk", "PRIVATE_FORBIDDEN"),
        }
        for name, required in markers.items():
            text = (self.package / "agents" / (name + ".md")).read_text(encoding="utf-8")
            for marker in required:
                self.assertIn(marker, text, name)

    def test_validate_rejects_dongcha_forbidden_tools_and_missing_write(self):
        path = self.package / "agents" / "dongcha.md"
        original = path.read_text(encoding="utf-8")
        tools_line = "tools: [Read, Glob, Grep, WebSearch, WebFetch, Write, TodoWrite]"
        for tool in ("Bash", "Edit"):
            with self.subTest(tool=tool):
                path.write_text(original.replace(tools_line, tools_line[:-1] + ", " + tool + "]", 1), encoding="utf-8")
                self.assert_validation_fails_with("dongcha tools must not include Bash or Edit")
                path.write_text(original, encoding="utf-8")
        path.write_text(original.replace(", Write", "", 1), encoding="utf-8")
        self.assert_validation_fails_with("dongcha tools must include Write")

    def test_content_review_docs_cover_examples_and_current_migration(self):
        readme = (self.package / "README.md").read_text(encoding="utf-8")
        protocol = (self.package / "INSTALL-FOR-AI.md").read_text(encoding="utf-8")
        for marker in (
            "review_profiles=[editorial,seo] review_tier=STANDARD",
            "review_profiles=[conversion] review_tier=STANDARD",
            "review_profiles=[editorial,seo] review_tier=HIGH_RISK",
            "review_profiles=[social] review_tier=STANDARD",
            "`QUICK` 永远是 `NO_GO`",
            "agents/shencha-content.md",
            "需求洞察与证据门禁",
            "L1 PUBLIC_EVIDENCE",
            "L2 FIRST_PARTY_RESEARCH",
        ):
            self.assertIn(marker, readme)
        for marker in (
            "只更新 state 中用户已经选择的岗位",
            "不会自动安装",
            "默认保留现有 agent 的本地 `model`/`thoughtLevel`",
            "模型 inventory 与安装计划兼容补丁",
            "PLAN_DIGEST",
            "selected_agents",
        ):
            self.assertIn(marker, protocol)
        for marker in (
            "Windows 与新版 ZCode",
            "严格白名单字段",
            "空 `providers`",
            "不改任何 agent 契约",
            "selected_agents",
            "PLAN_DIGEST",
        ):
            self.assertIn(marker, readme)

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

    def test_validate_scans_release_audits_for_machine_private_paths(self):
        audit = self.package / "release-audits" / "v9.9.9.md"
        audit.write_text("evidence: /Users/tony/private/repo\n", encoding="utf-8")
        self.assert_validation_fails_with("release-audits/v9.9.9.md contains forbidden text /Users/tony")

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

    def test_release_version_is_v4_2_3(self):
        plugin = json.loads((self.package / ".zcode-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], "4.2.3")

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
            "tag=v4.2.3",
            "INSTALL-FOR-AI.md",
            "scripts/model_inventory.py",
            "scripts/manage.py scan --json",
            "DECLARED_UNVERIFIED",
            "PLAN_DIGEST",
            "--confirm-plan",
            "明确授权",
            "FOREIGN",
            "COLLISION_UNMANAGED",
            "同版本就报告已安装",
            "唯一的新临时目录",
            "清理本次临时",
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
            "## 阶段 1.5：只读盘点已有智能体",
            "## 阶段 2：生成脱敏模型映射",
            "## 阶段 2.5：用户确认安装计划",
            "## 阶段 3：执行 install、update 或强制重装",
            "## 阶段 4：完成报告与清理",
            "--branch v4.2.3 --single-branch --depth 1",
            "https://github.com/tony-apan/zcode_skills",
            "同为 `4.2.3`",
            "严禁直接 Read/cat ZCode config",
            "macOS / Linux",
            "Windows PowerShell 5.1+",
            "install --dry-run --model-map",
            "--inventory",
            "PLAN_DIGEST",
            "--confirm-plan",
            "DECLARED_UNVERIFIED",
            "COLLISION_UNMANAGED",
            "FOREIGN",
            "update --dry-run",
            "uninstall --dry-run",
            "以本提示词为准",
            "--overwrite <name>` 或 `--keep <name>",
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

    def test_workflow_avoids_duplicate_release_runs(self):
        workflow = (self.package / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
        # main 推送不再单独触发（合并后的树与 PR head 相同）
        self.assertNotIn("branches: [main]", workflow)
        self.assertNotIn("branches:\n      - main", workflow)
        # PR 并发取消
        self.assertIn("concurrency:", workflow)
        self.assertIn("cancel-in-progress", workflow)
        # 发布标签只跑 Linux
        self.assertIn("validate-tag:", workflow)
        tag_job = workflow[workflow.index("validate-tag:"):]
        self.assertIn("ubuntu-latest", tag_job)
        self.assertNotIn("macos-latest", tag_job)
        self.assertNotIn("windows-latest", tag_job)
        # PR 侧仍保留完整三平台矩阵（required checks 名称不变）
        pr_job = workflow[:workflow.index("validate-tag:")]
        for runner in ("ubuntu-latest", "macos-latest", "windows-latest"):
            self.assertIn(runner, pr_job)
        self.assertIn("validate:", pr_job)

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
        self.install_model_map_unverified(manager, initial_map)
        update_map = self.temp / "update-model-map.json"
        update_map.write_text(json.dumps({"coder": {"model": "custom:test:new"}}), encoding="utf-8")

        self.update_with_approval(manager, update_map, allow_unverified_model_map=True)

        metadata = manage.parse_frontmatter((self.target / "coder.md").read_text(encoding="utf-8"))
        self.assertEqual(metadata["model"], "custom:test:new")
        self.assertNotIn("thoughtLevel", metadata)

    def test_update_agent_absent_from_model_map_preserves_model_and_thought_level(self):
        initial_map = self.temp / "initial-model-map.json"
        initial_map.write_text(json.dumps({"writer": {"model": "custom:test:writer", "thoughtLevel": "high"}}), encoding="utf-8")
        manager = self.manager()
        self.install_model_map_unverified(manager, initial_map)
        update_map = self.temp / "update-model-map.json"
        update_map.write_text(json.dumps({"coder": {"model": "custom:test:coder"}}), encoding="utf-8")

        self.update_with_approval(manager, update_map, allow_unverified_model_map=True)

        metadata = manage.parse_frontmatter((self.target / "writer.md").read_text(encoding="utf-8"))
        self.assertEqual(metadata["model"], "custom:test:writer")
        self.assertEqual(metadata["thoughtLevel"], "high")

    def test_model_map_round_trip_on_install(self):
        model_map = self.temp / "model-map.json"
        model_map.write_text(json.dumps({"shencha": {"model": "custom:test:model", "thoughtLevel": "high"}}), encoding="utf-8")
        manager = self.manager()
        self.install_model_map_unverified(manager, model_map)
        metadata = manage.validate_agent_text((self.target / "shencha.md").read_text(encoding="utf-8"), "shencha")
        self.assertEqual(metadata["model"], "custom:test:model")
        self.assertEqual(metadata["thoughtLevel"], "high")


if __name__ == "__main__":
    unittest.main()
