import unittest

from app.retrieval_pipeline.event_time import (
    filter_records_by_event_time, normalize_event_time, parse_temporal_query,
)


class EventTimeTests(unittest.TestCase):
    def test_day_and_month_boundaries(self):
        self.assertEqual(normalize_event_time('2024-02-29'), {
            'event_date': '2024-02-29', 'event_start': '2024-02-29', 'event_end': '2024-03-01'})
        self.assertEqual(normalize_event_time('2024-02')['event_end'], '2024-03-01')
        self.assertEqual(normalize_event_time('2024-12')['event_end'], '2025-01-01')
        for value in ('2023-02-29', '2024-2-01', '2024-02-30', '2024-13', '2024-02-01T10:00:00Z'):
            self.assertIsNone(normalize_event_time(value)['event_start'])

    def test_relative_requires_scene_anchor(self):
        self.assertIsNone(normalize_event_time('yesterday')['event_start'])
        self.assertEqual(normalize_event_time('yesterday', source_time='2024-03-01T00:30:00+05:30'), {
            'event_date': '2024-02-29', 'event_start': '2024-02-29', 'event_end': '2024-03-01'})
        self.assertIsNone(parse_temporal_query('today'))
        self.assertEqual(parse_temporal_query('yesterday', reference_time='2024-03-01T00:30:00+05:30'), ('2024-02-29', '2024-03-01'))

    def test_explicit_bounds_and_conflicts(self):
        self.assertEqual(normalize_event_time(event_start='2024-01-01', event_end='2024-02-01')['event_end'], '2024-02-01')
        self.assertIsNone(normalize_event_time('2024-01', '2024-01-01', '2024-01-31')['event_start'])
        self.assertIsNone(normalize_event_time(event_start='2024-01-01')['event_start'])
        self.assertIsNone(normalize_event_time(event_start='2024-02-01', event_end='2024-01-01')['event_start'])

    def test_query_inclusive_day_and_month_end(self):
        self.assertEqual(parse_temporal_query('2024-02-29'), ('2024-02-29', '2024-03-01'))
        self.assertEqual(parse_temporal_query('work [2024-02-28 to 2024-02-29]'), ('2024-02-28', '2024-03-01'))
        self.assertEqual(parse_temporal_query('[2024-02 through 2024-03]'), ('2024-02-01', '2024-04-01'))
        for text in ('before 2024-02-29', 'except [2024-02-29]', 'work in 2024-02', '[2023-02-29]', '[2024-03 to 2024-02]', '[2024-01] [2024-02]'):
            self.assertIsNone(parse_temporal_query(text), text)

    def test_filter_uses_occurrence_and_keeps_unknowns(self):
        records = [
            {'id': 'backfill', 'ts': '2025-01-01', 'event_date': '2024-02-29'},
            {'id': 'wrong', 'ts': '2024-02-29', 'event_date': '2024-03-01'},
            {'id': 'month', 'metadata': {'event_date': '2024-02'}},
            {'id': 'unknown', 'ts': '2024-02-29'},
            {'id': 'invalid', 'event_date': '2024-02-30'},
        ]
        interval = parse_temporal_query('2024-02-29')
        self.assertEqual([r['id'] for r in filter_records_by_event_time(records, interval)], ['backfill', 'month', 'unknown', 'invalid'])
        self.assertEqual([r['id'] for r in filter_records_by_event_time(records, interval, retain_unknown=False)], ['backfill', 'month'])
        self.assertEqual(filter_records_by_event_time(records, None), records)
        with self.assertRaises(ValueError):
            filter_records_by_event_time(records, ('2024-02-29', '2024-02-29'))


if __name__ == '__main__':
    unittest.main()
