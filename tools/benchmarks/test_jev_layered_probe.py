"""Safety checks for offline review packet handling; no model or Titan store access."""
import unittest
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

from jev_layered_probe import packets, validate_proposal
from laya_entailment_probe import live_fingerprints, verify_live_fingerprints


class ReviewBoundaries(unittest.TestCase):
    def setUp(self):
        self.records = [{"text": str(i)} for i in range(6)]
        self.edges = [{"pair_id": "a", "a": 0, "b": 1},
                      {"pair_id": "b", "a": 1, "b": 2},
                      {"pair_id": "c", "a": 3, "b": 4}]
        self.bundles = packets(self.records, self.edges)

    def proposal(self, *groups):
        return {"merge_groups": [{"members": g, "text": "reviewed"} for g in groups]}

    def test_connected_packets_exclude_unflagged_memory(self):
        self.assertEqual([[m["index"] for m in p["memories"]] for p in self.bundles], [[0, 1, 2], [3, 4]])

    def test_reviewer_can_reject_all_or_merge_subset(self):
        validate_proposal(self.proposal(), self.bundles, 6)
        validate_proposal(self.proposal([0, 1]), self.bundles, 6)

    def test_cross_packet_or_unflagged_merge_rejected(self):
        for members in [[0, 3], [0, 5]]:
            with self.subTest(members=members), self.assertRaises(AssertionError):
                validate_proposal(self.proposal(members), self.bundles, 6)

    def test_duplicate_members_or_overlapping_groups_rejected(self):
        for proposal in [self.proposal([0, 0]), self.proposal([0, 1], [1, 2])]:
            with self.assertRaises(AssertionError):
                validate_proposal(proposal, self.bundles, 6)

    def test_singletons_and_boolean_indices_rejected(self):
        for members in [[0], [False, 1]]:
            with self.subTest(members=members), self.assertRaises(AssertionError):
                validate_proposal(self.proposal(members), self.bundles, 6)


class LiveStoreChecks(unittest.TestCase):
    def test_no_live_database_scan_by_default(self):
        with patch.dict(os.environ, {}, clear=True), patch("sqlite3.connect") as connect:
            self.assertIsNone(live_fingerprints())
            connect.assert_not_called()

    def test_explicit_database_is_read_only_and_path_is_not_exported(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "memory_store.db"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, text TEXT)")
                conn.execute("INSERT INTO memories VALUES ('one', 'example')")
            with patch.dict(os.environ, {"TITAN_BENCH_VERIFY_DBS": str(db)}):
                fingerprints = live_fingerprints()
                self.assertEqual(fingerprints[0]["records"], 1)
                self.assertNotIn(str(db), str(fingerprints))
                before = Path(tmp) / "before.json"
                before.write_text(json.dumps(fingerprints), encoding="utf-8")
                self.assertTrue(verify_live_fingerprints(before))
                with sqlite3.connect(db) as conn:
                    conn.execute("INSERT INTO memories VALUES ('two', 'changed')")
                self.assertFalse(verify_live_fingerprints(before))


if __name__ == "__main__":
    unittest.main()
