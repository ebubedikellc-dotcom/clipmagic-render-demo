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
        body = {'workflow': 'normal', 'style': 'brief',
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
        for body in ({'workflow': 'other'}, {'examples': [{}]}, {'examples': 'bad'}, {'examples': [{}]*201}, {'style':'anything'}):
            self.assertEqual(self.client.put('/api/naming/settings', headers={'x-test-user': '1'}, json=body).status_code, 400)

    def test_taught_title_and_word_weights(self):
        examples=[{'context':'The gardener shows pruning roses.','title':'Pruning roses'}]
        with patch.object(naming,'preferences',return_value={'style':'direct','examples':examples}):
            exact=naming.generate(self.service,None,'Gardener',source_caption='THE gardener shows pruning roses!')
            other=naming.generate(self.service,None,'Gardener',source_caption='The gardener does not prune roses.')
        self.assertEqual(exact['title'],'Pruning roses')
        self.assertEqual(exact['source'],'naming-taught')
        self.assertIn('not',other['title'])
        self.assertNotEqual(other['source'],'naming-taught')
        self.assertIn('#roses',other['hashtags'])

    def test_feedback_changes_sentence_selection_without_copying_facts(self):
        caption='Oranges grow on trees. Roses grow on bushes.'
        before=naming.generate(self.service,None,'',source_caption=caption)
        with patch.object(naming,'preferences',return_value={'style':'direct','examples':[{'context':'Roses bloom on bushes.','title':'Roses bloom'}]}):
            after=naming.generate(self.service,None,'',source_caption=caption)
        self.assertEqual(before['title'],'Oranges grow on trees')
        self.assertEqual(after['title'],'Roses grow on bushes')
        self.assertNotIn('bloom',after['title'])

    def test_no_caption_no_invented_title(self):
        result=naming.generate(self.service,None,'Alice')
        self.assertEqual(result['title'],'Alice — clip 1')
        self.assertEqual(result['source'],'neutral-fallback')
        self.assertEqual(result['hashtags'],'')

    def test_preview_private_and_does_not_train_on_itself(self):
        self.assertEqual(self.client.post('/api/naming/preview',json={'caption':'How to grow tomatoes'}).status_code,401)
        r=self.client.post('/api/naming/preview',headers={'x-test-user':'1'},json={'caption':'How to grow tomatoes'})
        self.assertEqual(r.status_code,200)
        self.assertIn('tomatoes',r.json()['title'])
        self.assertEqual(naming.preferences(self.service,1,'normal')['examples'],[])
        self.assertEqual(self.client.put('/api/naming/settings',headers={'x-test-user':'1'},content='bad').status_code,400)

    def test_brief_and_creator_styles_and_long_input(self):
        for style in ['brief','creator']:
            with patch.object(naming,'preferences',return_value={'style':style,'examples':[]}):
                result=naming.generate(self.service,None,'Alice',source_caption='Tomatoes grow in the garden with plenty of sunshine and regular watering during the summer months.')
            self.assertLessEqual(len(result['title']),55 if style=='brief' else 80)
            if style=='creator':self.assertTrue(result['title'].startswith('Alice:'))
        self.assertNotIn('http',naming.generate(self.service,None,'',source_caption='How to grow tomatoes https://example.com')['title'])

if __name__=='__main__':unittest.main()
