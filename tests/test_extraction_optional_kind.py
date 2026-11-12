import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app.save_pipeline.extraction.extractor import ExtractionError, extract_atomic_memories


class Adapter:
    def __init__(self, records):
        self.records = records

    def chat(self, *args, **kwargs):
        return json.dumps({'memories': self.records})


def proposal(**changes):
    return {
        'text': 'The user prefers direct answers.',
        'type': 'preference', 'source': 'user', 'stream': 'learnings',
        'speaker_focus': 'user', 'evidence_refs': ['u1'], **changes,
    }


def extract(records):
    with patch('app.retrieval_pipeline.config.load_settings', return_value={'source_reliability': {'user': .9}}):
        return extract_atomic_memories('I prefer direct answers.', 'I will keep answers direct.', Adapter(records))


class OptionalKindTests(unittest.TestCase):
    def test_unsupported_string_matches_omitted_kind_and_preserves_other_records(self):
        valid = proposal(text='The user decided to use SQLite.', type='decision', memory_kind='decision', evidence_refs=['u2'])
        expected = extract([proposal(), valid])
        with self.assertLogs('app.save_pipeline.extraction.extractor', level='WARNING') as logs:
            result = extract([proposal(memory_kind='preference'), valid])
        self.assertEqual(result, expected)
        self.assertEqual(result[0]['memory_kind'], 'user_preference')
        self.assertEqual(result[0]['evidence_refs'], ['u1'])
        self.assertIn('1', logs.output[0])

    def test_all_valid_kinds_are_preserved(self):
        kinds = ['user_fact', 'user_preference', 'task', 'decision', 'commitment', 'outcome', 'relationship', 'workflow', 'issue']
        result = extract([proposal(memory_kind=k) for k in kinds])
        self.assertEqual([r['memory_kind'] for r in result], kinds)

    def test_non_string_kind_and_other_invalid_contract_fields_still_fail(self):
        invalid = [dict(memory_kind=123), dict(memory_kind=['fact']), dict(text=''), dict(text=23),
                   dict(evidence_refs='u1'), dict(evidence_refs=[1]), dict(stream='other'), dict(speaker_focus='other'), dict(source=2)]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ExtractionError):
                extract([proposal(**changes)])

    def test_invalid_json_still_fails(self):
        class Broken:
            def chat(self, *args, **kwargs):
                return '```json\n{}\n```'
        with self.assertRaises(ExtractionError):
            extract_atomic_memories('User data', 'Assistant data', Broken())

    def test_unknown_kind_survives_native_save_storage_and_semantic_recall(self):
        from app.save_pipeline import pipeline
        from app.storage.memories import SqliteMemoryRepository
        from app.storage.models import Memory, Scene, SceneMessage
        from app.retrieval_pipeline.retriever import retrieve_memories

        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            env = {'TITAN_HOME': str(home), 'TITAN_BASE_DIR': str(home),
                   'TITAN_SHARED_HOME': str(home), 'TITAN_AGENT_NAME': 'benchmark'}
            repo = SqliteMemoryRepository(home / 'memories.db')
            scene = Scene(scene_id='scene', session_id='session', turn=0,
                          kind='message_exchange', ts='2026-11-11T10:00:00+00:00',
                          source_event_ids=['u1'], anchor_event_id='u1',
                          extraction_user_text='I prefer direct answers.',
                          extraction_assistant_text='I will keep answers direct.',
                          messages=[SceneMessage(role='user', content='I prefer direct answers.', event_id='u1')])
            with patch.dict(os.environ, env), \
                 patch('app.storage.memories.get_memory_repository', return_value=repo), \
                 patch.object(pipeline, 'get_extraction_adapter', return_value=Adapter([proposal(memory_kind='preference', evidence_refs=['m1'])])), \
                 patch.object(pipeline, 'embed', return_value=[np.array([1., 0.], dtype=np.float32)]):
                saved = pipeline.run_memory_pipeline_outcome(
                    session_id='session', turn=0, user_text='I prefer direct answers.',
                    assistant_text='I will keep answers direct.', scene=scene, persist_scene=False)
            record = repo.query_by_ids([saved['records'][0]['id']])[saved['records'][0]['id']]
            self.assertEqual(Memory(**record).memory_kind, 'user_preference')
            self.assertEqual(record['source_event_ids'], ['u1'])
            with patch('app.retrieval_pipeline.config.load_settings', return_value={}), \
                 patch('app.retrieval_pipeline.retriever.embed', return_value=[np.array([1., 0.], dtype=np.float32)]):
                hits = retrieve_memories('direct answers', repository=repo, include_source_passages=False,
                                         min_similarity=0., min_reliability=0.)
            self.assertEqual(hits[0]['memory']['id'], record['id'])
            self.assertEqual(hits[0]['memory']['scene_id'], 'scene')
