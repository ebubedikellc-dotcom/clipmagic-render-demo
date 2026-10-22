import os
import tempfile
import unittest
import subprocess
import json
from pathlib import Path
from unittest.mock import patch

sandbox=tempfile.TemporaryDirectory()
os.environ['CLIPMAGIC_WORK_ROOT']=sandbox.name
os.environ['CLIPMAGIC_DATABASE_PATH']=sandbox.name+'/users.db'
os.environ['CLIPMAGIC_SESSION_SECRET']='local-test-only'
import app as service
import combined
from fastapi.testclient import TestClient

class CombinedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        service.initialize_database()
        with service.database() as db:
            db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'test@example.com','test',0)")
            db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(2,'other@example.com','test',0)")
        cls.client=TestClient(service.app)
        cls.client.cookies.set(service.CUSTOMER_COOKIE, service.create_customer_session(1,'test@example.com'))
        cls.top=Path(sandbox.name)/'top.mp4';cls.bottom=Path(sandbox.name)/'bottom.mp4'
        for path,color,seconds in [(cls.top,'red',3),(cls.bottom,'blue',1)]:
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=c={color}:s=160x90:r=15',
                            '-t',str(seconds),'-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(path)],check=True)
        r=cls.client.post('/api/combined/assets',files={'video':('lower.mp4',cls.bottom.read_bytes(),'video/mp4')})
        assert r.status_code==200,r.text
        cls.asset=r.json()['id']

    def options(self):
        return {'asset_id':self.asset,'audio':'source','signature':'Our signature','addition':'What do you think?',
                'profile_url':'https://instagram.com/example'}

    def test_real_video_preview_and_private_job(self):
        r=self.client.post('/api/combined/preview',files={'video':('top.mp4',self.top.read_bytes(),'video/mp4')},
                           data={'options':json.dumps(self.options()),'page_name':"My : page % ' [test]",'seconds':'3',
                                 'source_caption':'A red sample.'})
        self.assertEqual(r.status_code,202,r.text)
        job=self.client.get('/api/jobs/'+r.json()['id']).json()
        self.assertEqual(job['status'],'completed',job)
        output=job['outputs'][0];self.assertIn('Our signature',output['post_title'])
        self.assertIn('A red sample.',output['post_title'])
        self.assertEqual(output['profile_url'],'https://instagram.com/example')
        path=service.WORK_ROOT/job['id']/'clip-1.mp4'
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]))
        self.assertEqual((probe['streams'][0]['width'],probe['streams'][0]['height']),(540,960))
        self.assertAlmostEqual(float(probe['format']['duration']),3,delta=.2)
        # Sample central pixels to verify source above and lower video below, including its loop.
        raw=subprocess.check_output(['ffmpeg','-v','error','-ss','2','-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
        def pixel(x,y):return raw[(y*540+x)*3:(y*540+x)*3+3]
        self.assertGreater(pixel(270,240)[0],200);self.assertGreater(pixel(270,720)[2],200)
        stranger=TestClient(service.app)
        self.assertEqual(stranger.get('/api/jobs/'+job['id']).status_code,404)
        self.assertEqual(stranger.delete('/api/jobs/'+job['id']).status_code,404)
        self.assertEqual(self.client.delete('/api/jobs/'+job['id']).status_code,200)
        self.assertTrue(combined.asset_path(service,1,self.asset).exists())

    def test_anonymous_and_other_user_asset_access(self):
        anon=TestClient(service.app)
        self.assertEqual(anon.get('/api/combined/assets').status_code,401)
        with self.assertRaises(service.HTTPException):combined.settings(service,2,self.options())
        self.assertEqual(self.client.delete('/api/combined/assets/not-a-uuid').status_code,400)

    def test_profile_rejects_injection(self):
        for url in ['javascript:alert(1)','https://instagram.com.evil.example/a','http://instagram.com/a','https://user@instagram.com/a']:
            with self.assertRaises(service.HTTPException):combined.profile_url(url)

    def test_caption_no_invented_action(self):
        text=combined.caption('Original caption','','Signature')
        self.assertTrue(text.startswith('Original caption'))
        self.assertTrue(text.endswith('Signature'))
        self.assertIn('?',text)

    def test_combined_project_and_automatic_intake(self):
        service.save_credentials(1,'youtube',{'access_token':'test-only'})
        payload={'mode':'combined','combined':self.options(),'source_url':'https://youtube.com/@example',
                 'streamer_name':'Example','page_name':'My page','clip_count':1,'clip_length':3,
                 'destinations':[{'platform':'youtube','account_link':'https://youtube.com/@mypage'}]}
        r=self.client.post('/api/automation/projects',json=payload)
        self.assertEqual(r.status_code,201,r.text); project=r.json()
        self.assertTrue(project['combined_config'])
        self.assertEqual(self.client.delete('/api/combined/assets/'+self.asset).status_code,409)
        with patch.object(combined,'render') as render:
            result=self.client.post('/api/source-inbox/'+project['id']+'/'+project['source_secret'],
                files={'video':('top.mp4',self.top.read_bytes(),'video/mp4')},data={'source_caption':'New source caption'})
            self.assertEqual(result.status_code,202,result.text)
            self.assertEqual(render.call_args.args[-2],'New source caption')
            self.assertEqual(render.call_args.args[-1],project['id'])
        self.client.delete('/api/automation/projects/'+project['id'])

    def test_preview_never_enqueues(self):
        with patch.object(service,'enqueue_outputs') as enqueue:
            r=self.client.post('/api/combined/preview',files={'video':('bad.mp4',b'not video','video/mp4')},
                              data={'options':json.dumps(self.options()),'seconds':'3'})
            self.assertEqual(r.status_code,202)
            job=self.client.get('/api/jobs/'+r.json()['id']).json()
            self.assertEqual(job['status'],'failed')
            enqueue.assert_not_called()

    def test_normal_mode_still_dispatches(self):
        with patch.object(service,'process_job') as process:
            r=self.client.post('/api/jobs',files={'video':('top.mp4',self.top.read_bytes(),'video/mp4')},data={'clip_count':1,'clip_length':3})
            self.assertEqual(r.status_code,202,r.text);process.assert_called_once()

if __name__=='__main__':unittest.main()
