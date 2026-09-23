"""Safety checks for offline review packet handling; no model or Titan store access."""
import unittest

from jev_layered_probe import packets, validate_proposal


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


if __name__ == "__main__":
    unittest.main()
