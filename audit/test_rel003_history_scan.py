"""Offline synthetic controls for the bounded history audit; stdlib only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import rel003_history_scan as audit

SOURCE_REPO = Path(__file__).resolve().parents[1]


class HistoryAuditControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector, cls.pin = audit.load_rule(SOURCE_REPO)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rel003-control-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "synthetic"
        self.repo.mkdir()
        self.run_git("init", "-q", "-b", "main")

    def run_git(self, *args, data=None):
        env = os.environ.copy()
        env.update(GIT_AUTHOR_NAME="Audit Control", GIT_COMMITTER_NAME="Audit Control",
                   GIT_AUTHOR_EMAIL="control@example.com", GIT_COMMITTER_EMAIL="control@example.com",
                   GIT_AUTHOR_DATE="2026-11-06T00:00:00Z", GIT_COMMITTER_DATE="2026-11-06T00:00:00Z",
                   GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
        p = subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=self.repo,
                           env=env, input=data, capture_output=True, check=True)
        return p.stdout.decode().strip()

    def commit(self, message="synthetic control"):
        self.run_git("add", ".")
        self.run_git("commit", "-qm", message)
        return self.run_git("rev-parse", "HEAD")

    def refs(self, sha=None):
        return [{"ref": "refs/heads/main", "sha": sha or self.run_git("rev-parse", "HEAD")}]

    @staticmethod
    def fake():
        return "ghp_" + "FAKEFOROFFLINETESTONLY" * 2

    def test_deleted_fake_detected_in_history_but_not_tip(self):
        fake = self.fake()
        p = self.repo / "removed.txt"
        p.write_text(fake + "\n")
        old = self.commit("add known fake fixture")
        p.unlink()
        (self.repo / "clean.txt").write_text("paper-only synthetic control\n")
        new = self.commit("remove known fake fixture")
        self.assertEqual(audit.detect(audit.git(self.repo, "show", f"{new}:clean.txt"), self.detector), [])
        report = audit.scan(self.repo, self.refs(), self.detector)
        self.assertEqual(report["match_count"], 1)
        self.assertEqual(report["findings"][0]["locations"], [{"commit": old, "path": "removed.txt"}])
        self.assertTrue(report["independent_inventory_agrees"])
        self.assertNotIn(fake, json.dumps(report))

    def test_disconnected_branch_in_scope_is_not_missed(self):
        (self.repo / "clean.txt").write_text("clean\n")
        clean = self.commit()
        self.run_git("checkout", "--orphan", "other", "-q")
        (self.repo / "separate.txt").write_text(self.fake())
        other = self.commit()
        self.run_git("checkout", "main", "-q")
        self.assertEqual(audit.scan(self.repo, self.refs(clean), self.detector)["match_count"], 0)
        refs = self.refs(clean) + [{"sha": other, "ref": "refs/heads/other"}]
        self.assertEqual(audit.scan(self.repo, refs, self.detector)["match_count"], 1)

    def test_binary_non_utf8_blob_is_scanned(self):
        (self.repo / "binary.dat").write_bytes(b"\xff\0" + self.fake().encode())
        self.commit()
        report = audit.scan(self.repo, self.refs(), self.detector)
        self.assertEqual(report["match_count"], 1)
        self.assertEqual(report["non_utf8_blobs_scanned"], 1)
        self.assertEqual(report["nul_containing_blobs_scanned"], 1)

    def test_commit_message_is_scanned_without_disclosing_value(self):
        (self.repo / "clean.txt").write_text("clean\n")
        self.commit(self.fake())
        report = audit.scan(self.repo, self.refs(), self.detector)
        self.assertEqual(report["findings"][0]["kind"], "commit")
        self.assertEqual(report["match_count"], 1)
        self.assertNotIn(self.fake(), json.dumps(report))

    def test_path_and_ref_matches_are_redacted(self):
        (self.repo / self.fake()).write_text("clean\n")
        self.commit()
        report = audit.scan(self.repo, [{"sha": self.refs()[0]["sha"], "ref": "refs/heads/" + self.fake()}], self.detector)
        self.assertEqual(report["match_count"], 2)
        self.assertNotIn(self.fake(), json.dumps(report))

    def test_missing_object_is_incomplete(self):
        with self.assertRaises(audit.Incomplete):
            audit.scan(self.repo, self.refs("0" * 40), self.detector)

    def test_size_bound_is_incomplete_not_clean(self):
        (self.repo / "oversized.txt").write_text("x" * 1024)
        self.commit()
        with self.assertRaises(audit.Incomplete):
            audit.scan(self.repo, self.refs(), self.detector, max_object_bytes=1000)

    def test_real_shallow_clone_is_rejected(self):
        (self.repo / "a.txt").write_text(self.fake())
        self.commit()
        (self.repo / "a.txt").write_text("clean\n")
        self.commit()
        shallow = Path(self.temp.name) / "shallow"
        self.run_git("clone", "-q", "--depth=1", self.repo.as_uri(), str(shallow))
        with self.assertRaises(audit.Incomplete):
            audit.scan(shallow, self.refs(), self.detector)

    def test_empty_and_malformed_ref_snapshots_are_rejected(self):
        p = Path(self.temp.name) / "refs.txt"
        for data in ("", "bad refs/heads/main\n", "0" * 40 + " refs/heads/main extra\n"):
            p.write_text(data)
            with self.assertRaises(audit.Incomplete):
                audit.read_refs(p)

    def test_each_existing_detector_category_has_a_synthetic_positive(self):
        examples = {
            "github-classic": "ghp_" + "F" * 24,
            "github-oauth": "gho_" + "F" * 24,
            "github-fine-grained": "github_" + "pat_" + "F" * 24,
            "aws-access-id": "AK" + "IA" + "F" * 16,
            "sk-prefix": "s" + "k-" + "F" * 24,
            "private-key-header": "-----" + "BEGIN RSA " + "PRIVATE KEY",
            "slack-prefix": "xox" + "b-" + "F" * 16,
            "jwt-shaped": "eyJ" + "F" * 24 + "." + "G" * 24 + ".",
            "assigned-credential": "api" + "_key = '" + "FAKEVALUE" + "'",
        }
        for label, fake in examples.items():
            with self.subTest(category=label):
                hits = audit.detect(fake.encode(), self.detector)
                self.assertEqual([h["rule"] for h in hits], [label])
        self.assertEqual(audit.detect(b"ordinary paper-only data\n", self.detector), [])

    def test_rule_pin_unchanged(self):
        self.assertEqual(self.pin["source_sha256"], audit.RULE_SHA256)
        self.assertEqual(hashlib.sha256(self.detector.pattern.encode()).hexdigest(),
                         self.pin["pattern_sha256"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
