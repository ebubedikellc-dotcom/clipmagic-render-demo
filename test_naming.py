import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import naming


class NamingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        def database():
            db = sqlite3.connect(str(Path(self.folder.name) / 'test.db'))
            db.row_factory = sqlite3.Row
            return db
        self.service = SimpleNamespace(database=database, app=FastAPI(),
            current_customer=lambda r: {'id': int(r.headers['x-test-user'])} if 'x-test-user' in r.headers else None)
        with database() as db:
            naming.migrate(db)
        naming.install(self.service)
        self.client = TestClient(self.service.app)

    def test_preferences_isolated_by_owner_and_workflow(self):
        body = {'workflow': 'normal', 'style': 'Brief factual titles',
                'examples': [{'context': 'Planting tomatoes', 'title': 'How to plant tomatoes'}]}
        self.assertEqual(self.client.put('/api/naming/settings', json=body).status_code, 401)
        self.assertEqual(self.client.put('/api/naming/settings', headers={'x-test-user': '1'}, json=body).status_code, 200)
        for user, workflow in [('1', 'combined'), ('2', 'normal')]:
            data = self.client.get('/api/naming/settings?workflow='+workflow, headers={'x-test-user': user}).json()
            self.assertEqual(data['examples'], [])
        data = self.client.get('/api/naming/settings', headers={'x-test-user': '1'}).json()
        self.assertEqual(data['examples'], body['examples'])
        self.assertEqual(data['style'], body['style'])

    def test_settings_validation(self):
        for body in ({'workflow': 'other'}, {'examples': [{}]}, {'examples': 'bad'}, {'examples': [{}]*7}):
            self.assertEqual(self.client.put('/api/naming/settings', headers={'x-test-user': '1'}, json=body).status_code, 400)

    def test_low_memory_does_not_launch_models(self):
        with patch.object(naming, 'status', return_value={'ready': False, 'reason': 'More memory needed'}), patch.object(naming, 'worker') as worker:
            result = naming.generate(self.service, Path('clip.mp4'), 'Alice')
        worker.assert_not_called()
        self.assertEqual(result['source'], 'neutral-fallback')
        self.assertIn('memory', result['warning'])
        self.assertNotIn('unexpected', result['title'])

    def test_clip_evidence_and_saved_style_reach_qwen(self):
        pref = {'style': 'Factual', 'examples': [{'context': 'Old example', 'title': 'Old title'}]}
        calls = []
        def worker(action, payload):
            calls.append((action, payload))
            return {'text': 'Add ginger to the soup.'} if action == 'transcribe' else {'title': 'Adding ginger to soup', 'hashtags': ['Soup','Ginger']}
        with patch.object(naming, 'status', return_value={'ready': True}), patch.object(naming, 'worker', side_effect=worker), patch.object(naming, 'preferences', return_value=pref):
            result = naming.generate(self.service, Path('specific-clip.mp4'), 'Alice', user_id=7, seconds=12)
        self.assertEqual(calls[0][1]['video'], 'specific-clip.mp4')
        self.assertEqual(calls[0][1]['seconds'], 12)
        self.assertEqual(calls[1][1]['text'], 'Add ginger to the soup.')
        self.assertEqual(calls[1][1]['examples'], pref['examples'])
        self.assertEqual(result['source'], 'local-qwen-speech')
        self.assertEqual(result['hashtags'], '#Soup #Ginger')

    def test_silent_clip_requires_evidence(self):
        with patch.object(naming, 'status', return_value={'ready': True}), patch.object(naming, 'worker', return_value={'text': ''}) as worker:
            result = naming.generate(self.service, Path('silent.mp4'), 'Alice')
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(result['source'], 'neutral-fallback')

    def test_source_caption_used_for_silent_clip(self):
        with patch.object(naming, 'status', return_value={'ready': True}), patch.object(naming, 'worker', side_effect=[{'text': ''}, {'title': 'Growing tomatoes', 'hashtags': ['Garden']}]) as worker:
            result = naming.generate(self.service, Path('silent.mp4'), 'Alice', source_caption='How to grow tomatoes')
        self.assertEqual(worker.call_args.args[1]['text'], 'How to grow tomatoes')
        self.assertEqual(result['source'], 'local-qwen-source-caption')

    def test_failure_never_reported_as_ai_success(self):
        for error in [subprocess.TimeoutExpired('worker', 1), ValueError('bad JSON'), OSError('missing runtime')]:
            with patch.object(naming, 'status', return_value={'ready': True}), patch.object(naming, 'worker', side_effect=error):
                result = naming.generate(self.service, Path('clip.mp4'), 'Alice')
            self.assertEqual(result['source'], 'neutral-fallback')
            self.assertTrue(result['warning'])

    def test_metadata_limits_and_hashtag_deduplication(self):
        grounded = naming.clean_metadata({'title':'Goalkeeper saves penalty','hashtags':['Gaming','Goalkeeper','2','relevant','Penalty']},
                                          'The goalkeeper saves the penalty.')
        self.assertEqual(grounded['hashtags'], '#Goalkeeper #Penalty')
        result = naming.clean_metadata({'title': 'A'*100+'\nsecret', 'hashtags': ['#Soup','SOUP','ginger tea','A','B','C','D']})
        self.assertEqual(len(result['title']), 80)
        self.assertEqual(result['hashtags'], '#Soup #gingertea #A #B #C')
        for value in [None, [], {'title': ''}, {'title': '<think>reason'}]:
            with self.assertRaises(ValueError):
                naming.clean_metadata(value)


if __name__ == '__main__':
    unittest.main()
