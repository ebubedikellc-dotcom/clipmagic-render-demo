"""Synthetic media and credentials; no live social posts are sent."""
import subprocess
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from test_combined import service, sandbox


class PostingAccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        service.initialize_database()
        with service.database() as db:
            db.execute("INSERT OR IGNORE INTO users VALUES(1,'test@example.com','unused',0)")
            db.execute("INSERT OR IGNORE INTO users VALUES(2,'other@example.com','unused',0)")
        cls.client=TestClient(service.app)
        cls.client.cookies.set(service.CUSTOMER_COOKIE,service.create_customer_session(1,'test@example.com'))
        cls.video=Path(sandbox.name)/'posting-test.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=green:s=160x90:r=15','-t','3',
                        '-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(cls.video)],check=True)
        response=cls.client.post('/api/combined/assets',files={'video':('lower.mp4',cls.video.read_bytes(),'video/mp4')})
        assert response.status_code==200,response.text
        cls.asset=response.json()['id']

    def setUp(self):
        with service.database() as db:
            for table in ('automation_projects','project_destinations','publish_queue','project_events','api_credentials'):
                db.execute('DELETE FROM '+table)
        service.save_credentials(1,'youtube',{'access_token':'test-only','account_link':'https://youtube.com/@test'})

    def create(self,combined=True,all_accounts=False):
        payload={'source_url':'https://youtube.com/@source','streamer_name':'Source','page_name':'Test page','clip_count':1,'clip_length':3,
                 'destinations':[{'platform':'youtube','account_link':'https://youtube.com/@test'}]}
        if combined:payload.update(mode='combined',combined={'asset_id':self.asset,'audio':'mute','signature':'Test signature'})
        if all_accounts:payload['destination_mode']='all_connected'
        response=self.client.post('/api/automation/projects',json=payload)
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def upload(self,project):
        return self.client.post('/api/automation/projects/'+project['id']+'/ingest',
            files={'video':('source.mp4',self.video.read_bytes(),'video/mp4')},data={'source_caption':'Original caption'})

    def test_each_mode_keeps_its_own_processing_and_sources(self):
        normal=self.create(False);combined=self.create(True,True)
        with patch.object(service.combined,'render') as renderer,patch.object(service,'generate_post_title',return_value=('Normal title','test')):
            response=self.upload(normal)
            self.assertEqual(response.status_code,202,response.text)
            renderer.assert_not_called()
        queue=self.client.get('/api/automation/queue').json()['items']
        self.assertEqual({item['project_id'] for item in queue},{normal['id']})
        response=self.upload(combined)
        self.assertEqual(response.status_code,202,response.text)
        job_id=response.json()['id'];job=self.client.get('/api/jobs/'+job_id).json()
        self.assertEqual(job['status'],'completed',job)
        self.assertEqual(job['mode'],'combined')
        queue=self.client.get('/api/automation/queue').json()['items']
        self.assertEqual(len(queue),2)
        item=next(item for item in queue if item['project_id']==combined['id'])
        self.assertIn('Original caption',item['title']);self.assertIn('Test signature',item['title'])
        self.assertEqual(TestClient(service.app).get('/api/jobs/'+job_id).status_code,404)

    def test_all_six_apis_reuse_existing_and_later_authorizations(self):
        project=self.create(all_accounts=True)
        for platform in service.SUPPORTED_DESTINATIONS-{'youtube'}:
            service.save_credentials(1,platform,{'access_token':'test-only'})
        output={'name':'clip-1.mp4','post_title':'Test caption','hashtags':''}
        service.enqueue_outputs(1,project['id'],uuid.uuid4().hex,[output])
        queue=self.client.get('/api/automation/queue').json()['items']
        self.assertEqual({item['platform'] for item in queue},service.SUPPORTED_DESTINATIONS)
        selected=self.create()
        service.enqueue_outputs(1,selected['id'],uuid.uuid4().hex,[output])
        queue=self.client.get('/api/automation/queue').json()['items']
        self.assertEqual([item['platform'] for item in queue if item['project_id']==selected['id']],['youtube'])

    def test_saving_links_preserves_tokens_without_exposing_them(self):
        response=self.client.post('/api/customer/apis',json={'youtube':{'account_link':'https://youtube.com/@updated'}})
        self.assertEqual(response.status_code,200)
        self.assertEqual(service.credentials_for(1,'youtube')['access_token'],'test-only')
        data=self.client.get('/api/customer/apis').json()
        self.assertEqual(data['links']['youtube'],'https://youtube.com/@updated')
        self.assertIs(data['configured']['youtube']['access_token'],True)
        self.assertNotIn('test-only',str(data))

    def test_another_customers_api_is_not_used(self):
        project=self.create(all_accounts=True)
        service.save_credentials(2,'facebook',{'access_token':'another-user-test-only'})
        service.enqueue_outputs(1,project['id'],uuid.uuid4().hex,[{'name':'clip-1.mp4','post_title':'test','hashtags':''}])
        self.assertEqual([x['platform'] for x in self.client.get('/api/automation/queue').json()['items']],['youtube'])

    def test_existing_project_can_enable_all_accounts(self):
        project=self.create()
        response=self.client.patch('/api/automation/projects/'+project['id'],json={'destination_mode':'all_connected'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['destination_mode'],'all_connected')
        self.assertEqual(response.json()['combined_config'],project['combined_config'])
        self.assertEqual(response.json()['source_url'],project['source_url'])

    def test_pause_stops_queued_posts_and_delete_cancels_them(self):
        project=self.create()
        service.enqueue_outputs(1,project['id'],uuid.uuid4().hex,[{'name':'clip-1.mp4','post_title':'test','hashtags':''}])
        self.client.patch('/api/automation/projects/'+project['id'],json={'status':'paused'})
        with patch.object(service,'publish_one') as publish,patch.object(service.publisher_wakeup,'wait',side_effect=RuntimeError('stop test')):
            with self.assertRaisesRegex(RuntimeError,'stop test'):service.publisher_loop()
            publish.assert_not_called()
        self.client.delete('/api/automation/projects/'+project['id'])
        self.assertEqual(self.client.get('/api/automation/queue').json()['items'][0]['status'],'cancelled')

    def test_oauth_returns_to_correct_section_and_rejects_tampering(self):
        for destination in ('/','/combined.html','https://untrusted.example'):
            state=service.oauth_state(1,'youtube',destination)
            self.assertEqual(service.parse_oauth_state(state,'youtube'),1)
            expected='/combined.html' if destination=='/combined.html' else '/'
            self.assertEqual(service.oauth_return_path(state,'youtube'),expected)
            response=self.client.get('/api/oauth/youtube/callback',params={'state':state,'error':'access_denied'},follow_redirects=False)
            self.assertTrue(response.headers['location'].startswith(expected+'?'))
        with self.assertRaises(service.HTTPException):service.oauth_return_path(state+'bad','youtube')
        with patch.object(service,'oauth_environment',return_value=('','')):
            response=self.client.get('/api/oauth/youtube/start?next=/combined.html',follow_redirects=False)
            self.assertEqual(response.headers['location'],'/combined.html?connection=youtube&result=owner_setup_required')

    def test_migration_preserves_selected_destinations(self):
        project=self.create();service.initialize_database();service.initialize_database()
        current=self.client.get('/api/automation/projects').json()['projects'][0]
        self.assertEqual(current['id'],project['id']);self.assertEqual(current['destination_mode'],'selected')


if __name__=='__main__':unittest.main()
