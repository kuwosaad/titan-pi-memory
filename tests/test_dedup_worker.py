import unittest
from unittest.mock import patch

from app.save_pipeline import dedup_worker


class DedupWorkerTests(unittest.TestCase):
    def test_retired_worker_does_not_read_or_write_existing_pending_entries(self):
        with (
            patch.object(dedup_worker, "load_settings", return_value={"dedup": {"enabled": False}}),
            patch("app.save_pipeline.dedup_buffer.recover_dedup_buffer") as recover,
        ):
            result = dedup_worker._run_dedup_pass()

        self.assertEqual(result, {"status": "dedup_disabled"})
        recover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
