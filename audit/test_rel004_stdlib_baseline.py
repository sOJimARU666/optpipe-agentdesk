"""Synthetic controls for the bounded REL-004 fallback; no project dependencies."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import rel004_stdlib_baseline as audit


class AnalysisControls(unittest.TestCase):
    def rules(self, source):
        result = audit.analyze(source.encode(), "tests/synthetic.py")
        return {f["rule"] for f in result["findings"]}

    def test_compiler_rejects_invalid_control_flow(self):
        self.assertIn("compile-error", self.rules("return 1\n"))

    def test_compiler_warning_and_tuple_assert(self):
        rules = self.rules("assert (False, 'broken assertion')\n")
        self.assertIn("compiler-warning", rules)
        self.assertIn("nonempty-tuple-assert", rules)
        self.assertNotIn("nonempty-tuple-assert", self.rules("assert False, 'expected'\n"))

    def test_dictionary_overwrite_including_equal_literal_types(self):
        self.assertIn("duplicate-literal-key", self.rules("d = {True: 1, 1: 2}\n"))
        self.assertNotIn("duplicate-literal-key", self.rules("d = {'1': 1, 1: 2}\n"))

    def test_duplicate_test_and_class_method_detected(self):
        for source in ("def test_a(): pass\ndef test_a(): pass\n",
                       "class C:\n def check(self): pass\n def check(self): pass\n"):
            self.assertIn("duplicate-definition-candidate", self.rules(source))

    def test_missing_global_in_nested_scope(self):
        self.assertIn("unbound-global-candidate", self.rules(
            "def outer():\n def inner():\n  return absent_symbol\n return inner\n"))

    def test_import_closure_builtin_and_implicit_names_are_accepted(self):
        self.assertEqual(self.rules(
            "import math\nx = 3\ndef outer(a):\n def inner():\n  return len(str(a + x + math.pi)), __file__\n return inner\n"), set())

    def test_star_import_explicitly_records_coverage_limit(self):
        result = audit.analyze(b"from sample import *\ndef f(): return external\n", "src/sample.py")
        self.assertEqual(len(result["limitations"]), 1)
        self.assertEqual(result["findings"], [])

    def test_declared_python311_grammar(self):
        expected = "python311-grammar" if sys.version_info >= (3, 12) else "compile-error"
        self.assertIn(expected, self.rules("type Alias = int\n"))

    def test_target_source_is_never_executed_and_encoding_is_respected(self):
        result = audit.analyze(b"# coding: latin-1\nvalue = '\xe9'\nraise RuntimeError('must not execute')\n", "src/sample.py")
        self.assertEqual(result["findings"], [])


class FrozenInputControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.env = os.environ.copy()
        self.env.update(GIT_AUTHOR_NAME="Synthetic Audit", GIT_COMMITTER_NAME="Synthetic Audit",
                        GIT_AUTHOR_EMAIL="audit@example.com", GIT_COMMITTER_EMAIL="audit@example.com",
                        GIT_AUTHOR_DATE="2026-11-06T00:00:00Z", GIT_COMMITTER_DATE="2026-11-06T00:00:00Z")
        self.git("init", "-q")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "sample.py").write_text("value = 1\n")
        self.git("add", "src/sample.py")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "synthetic static scope")
        self.sha = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, env=self.env, stderr=subprocess.DEVNULL).decode()

    def test_frozen_commit_ignores_dirty_and_untracked_sources(self):
        first = audit.scan(self.repo, self.sha)
        (self.repo / "src" / "sample.py").write_text("return invalid\n")
        (self.repo / "src" / "new.py").write_text("return invalid\n")
        self.assertEqual(first, audit.scan(self.repo, self.sha))
        self.assertEqual(first["files"], 1)

    def test_missing_revision_and_size_limit_fail_incomplete(self):
        for revision in ("0" * 40, "HEAD"):
            with self.assertRaises(audit.Incomplete):
                audit.scan(self.repo, revision)
        with self.assertRaises(audit.Incomplete):
            audit.scan(self.repo, self.sha, max_file_bytes=1)

    def test_symlink_python_entry_fails_incomplete(self):
        (self.repo / "src" / "link.py").symlink_to("sample.py")
        self.git("add", "src/link.py")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "synthetic link")
        with self.assertRaises(audit.Incomplete):
            audit.scan(self.repo, self.git("rev-parse", "HEAD").strip())

    def verify_archive(self, baseline):
        receipt = self.repo / "baseline.json"
        receipt.write_text(json.dumps(baseline))
        verifier = Path(__file__).with_name("rel004_verify_archive.py").resolve()
        process = subprocess.run([sys.executable, str(verifier), "--repo", str(self.repo),
                                  "--baseline", str(receipt)], capture_output=True, text=True, check=False)
        self.assertTrue(process.stdout, process.stderr)
        return process.returncode, json.loads(process.stdout)

    def test_independent_verifier_rejects_tampered_inventory(self):
        baseline = audit.scan(self.repo, self.sha)
        self.assertEqual(self.verify_archive(baseline)[0], 0)
        baseline["inventory"][0]["sha256"] = "0" * 64
        code, report = self.verify_archive(baseline)
        self.assertEqual(code, 1)
        self.assertFalse(report["inventory_matches"])

    def test_independent_verifier_rejects_noncompiling_source(self):
        (self.repo / "src" / "sample.py").write_text("return 1\n")
        self.git("add", "src/sample.py")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "synthetic invalid source")
        baseline = audit.scan(self.repo, self.git("rev-parse", "HEAD").strip())
        code, report = self.verify_archive(baseline)
        self.assertEqual(code, 1)
        self.assertTrue(report["inventory_matches"])
        self.assertEqual(report["diagnostics"], [{"path": "src/sample.py", "check": "py_compile", "result": "failed"}])

    def test_cli_exit_codes_distinguish_results_from_incomplete(self):
        for source, revision, expected in (("value = 1\n", None, 0),
                                           ("return 1\n", None, 1),
                                           ("value = 1\n", "0" * 40, 2)):
            (self.repo / "src" / "sample.py").write_text(source)
            self.git("add", "src/sample.py")
            self.git("-c", "commit.gpgsign=false", "commit", "-qm", "control", "--allow-empty")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = audit.main(["--repo", str(self.repo), "--revision", revision or self.git("rev-parse", "HEAD").strip()])
            self.assertEqual(code, expected)
            self.assertEqual(json.loads(output.getvalue())["complete_scope"], expected != 2)


if __name__ == "__main__":
    unittest.main()
