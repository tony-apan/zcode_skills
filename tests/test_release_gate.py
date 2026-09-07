import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("tony_release_gate", ROOT / "scripts" / "release_gate.py")
release_gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(release_gate)


@unittest.skipUnless(shutil.which("git"), "git is required for release gate tests")
class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write(".zcode-plugin/plugin.json", json.dumps({"version": "3.0.0"}))
        self.write(".gitignore", ".env*\n*.log\n")
        self.write("agents/github.md", "base contract\n")
        self.write("release-audits/README.md", "governance base\n")
        self.write("removed.txt", "remove me\n")
        self.git("init", "-q")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "Release Gate Tests")
        self.git("add", ".")
        self.git("commit", "-qm", "base fixture")
        self.git("tag", "v2.0.0")
        self.write("agents/github.md", "v3 contract\n")
        self.write("new.txt", "untracked release file\n")
        (self.root / "removed.txt").unlink()

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def git(self, *arguments):
        result = subprocess.run(
            [shutil.which("git"), "-C", str(self.root)] + list(arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.fail("git {} failed: {}".format(" ".join(arguments), result.stderr))
        return result.stdout.strip()

    def fields(self):
        fingerprint = release_gate.package_fingerprint(self.root)
        return {
            "report-id": "github-20260907-001",
            "role": "github",
            "reviewer": "github",
            "mode": "RELEASE_GATE",
            "version": "3.0.0",
            "package_fingerprint": fingerprint,
            "base_ref": "v2.0.0",
            "target_ref": "WORKTREE:" + fingerprint,
            "changed_files": '["agents/github.md", "new.txt"]',
            "removed_files": '["removed.txt"]',
            "changed_agents": '["github"]',
            "breaking_impact": "breaking",
            "reviewed_at": "2026-09-07T12:00:00Z",
            "verdict": "PASS",
        }

    def sections(self):
        return {
            "Scope": "- changed_files: `agents/github.md`, `new.txt`\n- removed_files: `removed.txt`\n- changed_agents: `github`",
            "Evidence": "| evidence-id | check | result | evidence |\n|---|---|---|---|\n| EV-001 | validation | PASS | validate completed successfully |\n| EV-002 | tests | PASS | complete unittest suite passed |\n| EV-003 | fingerprint | PASS | fingerprint matches reviewed payload |",
            "Findings": "none",
            "Agent Links": "- https://github.com/tony-apan/zcode_skills/blob/v3.0.0/agents/github.md",
            "Improvements": "| improvement-id | user-value | evidence-ref |\n|---|---|---|\n| IMP-001 | Users receive only payloads independently reconciled with Git. | EV-003 |",
            "Blockers": "none",
            "Unverified": "none",
            "Migration": "breaking-impact: breaking\nordinary-users: Ordinary installs remain compatible with the existing state schema.\nmaintainers: Maintainers must use the structured v3 release audit before every push.\nupgrade: Follow the README update flow and run ./scripts/setup-hooks.sh before pushing.",
            "Hand-off": "| owner | action | status |\n|---|---|---|\n| main AI | run release gate and prepare reviewed audit | READY |",
        }

    def write_audit(self, field_overrides=None, section_overrides=None, heading_order=None):
        fields = self.fields()
        fields.update(field_overrides or {})
        sections = self.sections()
        sections.update(section_overrides or {})
        order = heading_order or list(release_gate.REQUIRED_HEADINGS)
        frontmatter = "\n".join("{}: {}".format(key, value) for key, value in fields.items())
        body = "\n\n".join("## {}\n\n{}".format(name, sections[name]) for name in order)
        self.write("release-audits/v3.0.0.md", "---\n{}\n---\n\n# Release Gate v3.0.0\n\n{}\n".format(frontmatter, body))
        return fields["package_fingerprint"]

    def test_valid_worktree_report_passes_and_audit_report_is_excluded(self):
        fingerprint = self.write_audit()
        changed, removed = release_gate.release_diff(self.root, "v2.0.0", "WORKTREE:" + fingerprint, None)
        self.assertEqual(changed, ["agents/github.md", "new.txt"])
        self.assertEqual(removed, ["removed.txt"])
        self.assertNotIn("release-audits/v3.0.0.md", release_gate.payload_paths(self.root))
        self.assertIn("PASS", release_gate.check_gate(self.root, "3.0.0", None))

    def test_audit_readme_changes_fingerprint_and_enters_diff(self):
        before = release_gate.package_fingerprint(self.root)
        self.write("release-audits/README.md", "governance changed\n")
        after = release_gate.package_fingerprint(self.root)
        self.assertNotEqual(before, after)
        changed, _ = release_gate.release_diff(self.root, "v2.0.0", "WORKTREE:" + after, None)
        self.assertIn("release-audits/README.md", changed)
        self.write("release-audits/v3.0.0.md", "audit one\n")
        audit_fingerprint = release_gate.package_fingerprint(self.root)
        self.write("release-audits/v3.0.0.md", "audit two\n")
        self.assertEqual(audit_fingerprint, release_gate.package_fingerprint(self.root))

    def test_tracked_ignored_env_and_log_are_payload_but_untracked_ignored_are_not(self):
        self.write(".env.tracked", "base\n")
        self.write("tracked.log", "base\n")
        self.git("add", "-f", ".env.tracked", "tracked.log")
        self.git("commit", "-qm", "tracked ignored files")
        self.git("tag", "-f", "v2.0.0")
        before = release_gate.package_fingerprint(self.root)
        self.write(".env.tracked", "changed\n")
        self.write("tracked.log", "changed\n")
        tracked = release_gate.package_fingerprint(self.root)
        self.assertNotEqual(before, tracked)
        self.write(".env.untracked", "ignored\n")
        self.write("debug.log", "ignored\n")
        self.assertEqual(tracked, release_gate.package_fingerprint(self.root))
        changed, _ = release_gate.release_diff(self.root, "v2.0.0", "WORKTREE:" + tracked, None)
        self.assertIn(".env.tracked", changed)
        self.assertIn("tracked.log", changed)
        self.assertNotIn(".env.untracked", changed)
        self.assertNotIn("debug.log", changed)

    def test_executable_bit_changes_fingerprint(self):
        path = self.write("script.sh", "#!/bin/sh\nexit 0\n")
        before = release_gate.package_fingerprint(self.root)
        path.chmod(path.stat().st_mode | stat_exec())
        self.assertNotEqual(before, release_gate.package_fingerprint(self.root))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unsupported")
    def test_symlink_target_and_executable_marker_are_hashed_without_following(self):
        outside = Path(self.temporary.name).parent / (Path(self.temporary.name).name + "-outside")
        outside.write_text("one\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink() if outside.exists() else None)
        link = self.root / "link"
        link.symlink_to(outside)
        first = release_gate.package_fingerprint(self.root)
        outside.write_text("two\n", encoding="utf-8")
        self.assertEqual(first, release_gate.package_fingerprint(self.root))
        link.unlink()
        link.symlink_to("other-target")
        self.assertNotEqual(first, release_gate.package_fingerprint(self.root))

    def test_fallback_non_git_directory_uses_same_exclusions(self):
        other = Path(self.temporary.name) / "fallback"
        other.mkdir()
        (other / "release-audits").mkdir()
        (other / "release-audits" / "README.md").write_text("rules\n", encoding="utf-8")
        (other / "release-audits" / "v3.0.0.md").write_text("audit\n", encoding="utf-8")
        paths = [path.as_posix() for path in release_gate.payload_paths(other)]
        self.assertIn("release-audits/README.md", paths)
        self.assertNotIn("release-audits/v3.0.0.md", paths)

    def test_diff_declaration_missing_extra_removed_and_agent_mismatch_fail(self):
        cases = (
            ({"changed_files": '["agents/github.md"]'}, r"changed_files.*missing=.*new.txt"),
            ({"changed_files": '["agents/github.md", "new.txt", "fake"]'}, r"changed_files.*extra=.*fake"),
            ({"removed_files": "none"}, r"removed_files.*missing=.*removed.txt"),
            ({"changed_agents": "none"}, r"changed_agents.*missing=.*github"),
        )
        for fields, error in cases:
            with self.subTest(fields=fields):
                self.write_audit(fields)
                with self.assertRaisesRegex(release_gate.GateError, error):
                    release_gate.check_gate(self.root, None, None)

    def test_scope_requires_every_code_formatted_item(self):
        self.write_audit(section_overrides={"Scope": "- changed_files: `agents/github.md`\n- removed_files: `removed.txt`\n- changed_agents: `github`"})
        with self.assertRaisesRegex(release_gate.GateError, "new.txt"):
            release_gate.check_gate(self.root, None, None)

    def test_heading_missing_reordered_and_extra_level_two_fail(self):
        required = list(release_gate.REQUIRED_HEADINGS)
        for order in (required[:-1], [required[1], required[0]] + required[2:], required + ["Appendix"]):
            with self.subTest(order=order):
                sections = {"Appendix": "extra"}
                self.write_audit(section_overrides=sections, heading_order=order)
                with self.assertRaisesRegex(release_gate.GateError, "headings must exactly match"):
                    release_gate.check_gate(self.root, None, None)

    def test_findings_table_rejects_multiline_p1_open_and_accepted_risk(self):
        for status_value in ("OPEN", "ACCEPTED_RISK"):
            table = "| finding-id | severity | status | summary |\n|---|---|---|---|\n| RG-201 | P1 | {} | release blocker on a separate table row |".format(status_value)
            self.write_audit(section_overrides={"Findings": table})
            with self.assertRaisesRegex(release_gate.GateError, "P0/P1"):
                release_gate.check_gate(self.root, None, None)
        self.write_audit(section_overrides={"Findings": "- none"})
        with self.assertRaisesRegex(release_gate.GateError, "Markdown table"):
            release_gate.check_gate(self.root, None, None)

    def test_findings_table_schema_and_status_are_strict(self):
        cases = (
            ("| id | severity | status | summary |\n|---|---|---|---|\n| RG-1 | P1 | FIXED | fixed |", "header"),
            ("| finding-id | severity | status | summary |\n|---|---|---|---|\n| RG-201 | P4 | FIXED | fixed issue |", "severity"),
            ("| finding-id | severity | status | summary |\n|---|---|---|---|\n| RG-201 | P2 | BLOCKED | blocked issue |", "status"),
        )
        for findings, error in cases:
            self.write_audit(section_overrides={"Findings": findings})
            with self.assertRaisesRegex(release_gate.GateError, error):
                release_gate.check_gate(self.root, None, None)

    def test_open_p2_must_be_referenced_in_improvements_or_hand_off(self):
        findings = "| finding-id | severity | status | summary |\n|---|---|---|---|\n| RG-202 | P2 | OPEN | follow-up remains |"
        self.write_audit(section_overrides={"Findings": findings})
        with self.assertRaisesRegex(release_gate.GateError, "RG-202"):
            release_gate.check_gate(self.root, None, None)
        improvements = "| improvement-id | user-value | evidence-ref |\n|---|---|---|\n| RG-202 | Follow-up is visible to maintainers and users. | EV-003 |"
        self.write_audit(section_overrides={"Findings": findings, "Improvements": improvements})
        self.assertIn("PASS", release_gate.check_gate(self.root, None, None))

    def test_evidence_table_requires_three_pass_rows_and_nonempty_evidence(self):
        cases = (
            ("| evidence-id | check | result | evidence |\n|---|---|---|---|\n| EV-1 | validation | PASS | valid |", "tests"),
            ("| evidence-id | check | result | evidence |\n|---|---|---|---|\n| EV-1 | validation | PASS |  |", "cell"),
        )
        for evidence, error in cases:
            self.write_audit(section_overrides={"Evidence": evidence})
            with self.assertRaisesRegex(release_gate.GateError, error):
                release_gate.check_gate(self.root, None, None)

    def test_improvements_require_rows_and_existing_evidence_ref(self):
        cases = (
            ("| improvement-id | user-value | evidence-ref |\n|---|---|---|", "data row"),
            ("| improvement-id | user-value | evidence-ref |\n|---|---|---|\n| IMP-1 | useful release behavior | EV-999 |", "does not exist"),
            ("| improvement-id | user-value | evidence-ref |\n|---|---|---|\n| IMP-1 | TODO | EV-001 |", "placeholder"),
        )
        for improvements, error in cases:
            self.write_audit(section_overrides={"Improvements": improvements})
            with self.assertRaisesRegex(release_gate.GateError, error):
                release_gate.check_gate(self.root, None, None)

    def test_migration_requires_fixed_order_long_values_and_breaking_upgrade(self):
        short = "breaking-impact: breaking\nordinary-users: short\nmaintainers: Maintainers use the new audit contract for release pushes.\nupgrade: Follow README update instructions before the next repository push."
        self.write_audit(section_overrides={"Migration": short})
        with self.assertRaisesRegex(release_gate.GateError, "ordinary-users"):
            release_gate.check_gate(self.root, None, None)
        no_command = "breaking-impact: breaking\nordinary-users: Ordinary installs remain compatible with the current state schema.\nmaintainers: Maintainers use the new audit contract for every repository push.\nupgrade: Maintainers should adopt the new process before their next release."
        self.write_audit(section_overrides={"Migration": no_command})
        with self.assertRaisesRegex(release_gate.GateError, "README/update"):
            release_gate.check_gate(self.root, None, None)

    def test_hand_off_table_rejects_empty_invalid_and_blocked(self):
        cases = (
            ("| owner | action | status |\n|---|---|---|\n|  | publish release | READY |", "cell"),
            ("| owner | action | status |\n|---|---|---|\n| main AI | publish release | WAITING |", "status"),
            ("| owner | action | status |\n|---|---|---|\n| main AI | publish release | BLOCKED |", "must not contain BLOCKED"),
        )
        for handoff, error in cases:
            self.write_audit(section_overrides={"Hand-off": handoff})
            with self.assertRaisesRegex(release_gate.GateError, error):
                release_gate.check_gate(self.root, None, None)

    def test_blockers_and_unverified_require_standalone_none(self):
        for heading in ("Blockers", "Unverified"):
            self.write_audit(section_overrides={heading: "- none"})
            with self.assertRaisesRegex(release_gate.GateError, heading):
                release_gate.check_gate(self.root, None, None)

    def test_reviewer_is_required_and_must_be_github(self):
        self.write_audit({"reviewer": "human"})
        with self.assertRaisesRegex(release_gate.GateError, "reviewer must be 'github'"):
            release_gate.check_gate(self.root, None, None)
        fields = self.fields()
        fields.pop("reviewer")
        self.write_audit(fields)
        audit = self.root / "release-audits" / "v3.0.0.md"
        audit.write_text(audit.read_text(encoding="utf-8").replace("reviewer: github\n", ""), encoding="utf-8")
        with self.assertRaisesRegex(release_gate.GateError, "reviewer"):
            release_gate.check_gate(self.root, None, None)

    def test_commit_mode_and_missing_git_fail_closed(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "target")
        target = self.git("rev-parse", "HEAD")
        self.write_audit({"target_ref": target})
        self.assertIn("PASS", release_gate.check_gate(self.root, None, target))
        with mock.patch.object(release_gate.shutil, "which", return_value=None):
            with self.assertRaisesRegex(release_gate.GateError, "git is required"):
                release_gate.check_gate(self.root, None, target)

    def test_template_contains_structured_schema(self):
        output = release_gate.template(self.root)
        for marker in ("reviewer: github", "| evidence-id | check | result | evidence |", "| finding-id | severity | status | summary |", "| improvement-id | user-value | evidence-ref |", "breaking-impact:", "| owner | action | status |"):
            self.assertIn(marker, output)


def stat_exec():
    return 0o100


if __name__ == "__main__":
    unittest.main()
