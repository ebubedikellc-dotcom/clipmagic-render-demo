"""Split-screen rendering and reusable lower-video assets. No third-party scraping."""
import hashlib
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

PROFILE_HOSTS = {'facebook.com','instagram.com','youtube.com','youtu.be','tiktok.com','x.com','twitter.com','snapchat.com'}


def profile_url(value):
    value = str(value or '').strip()
    if not value:
        return ''
    parsed = urlparse(value)
    host = (parsed.hostname or '').lower()
    try:
        port = parsed.port
    except ValueError:
        raise HTTPException(400, 'Invalid profile link')
    if parsed.scheme != 'https' or parsed.username or parsed.password or port not in (None,443) or not any(host == h or host.endswith('.'+h) for h in PROFILE_HOSTS):
        raise HTTPException(400, 'Use an HTTPS link to your social profile')
    return value[:1000]


def settings(service, user_id, data):
    asset_id = str(data.get('asset_id', ''))
    asset = asset_path(service, user_id, asset_id)
    if not asset.is_file():
        raise HTTPException(400, 'Upload your lower video first')
    audio = data.get('audio', 'source')
    if audio not in ('source', 'bottom', 'mute'):
        raise HTTPException(400, 'Choose source, lower-video, or muted audio')
    return {'asset_id': asset_id, 'audio': audio,
            'signature': str(data.get('signature','')).strip()[:180],
            'addition': str(data.get('addition','')).strip()[:400],
            'profile_url': profile_url(data.get('profile_url',''))}


def asset_path(service, user_id, asset_id):
    if not re.fullmatch(r'[a-f0-9]{32}', asset_id):
        raise HTTPException(400, 'Invalid lower-video identifier')
    return service.WORK_ROOT / '_combined' / str(int(user_id)) / asset_id / 'video.mp4'


def caption(original, addition, signature):
    original = str(original or '').strip()[:1200]
    questions = ['What stood out to you in this clip?', 'What is your take on this moment?',
                 'Which detail caught your attention?', 'How would you describe this moment?']
    question = questions[int(hashlib.sha256(original.encode()).hexdigest()[:8],16) % len(questions)]
    parts = [original or 'A moment worth another look.', addition.strip() or question, signature.strip()]
    return '\n\n'.join(p for p in parts if p)


def render(service, job_id, source, user_id, config, page_name, seconds, original='', project_id=None):
    """Produce one 540x960 video. Lower input loops; only selected input audio is used."""
    try:
        with service.processing_slot:
            service.update_job(job_id, status='processing', progress=10, message='Combining the top and lower videos')
            bottom = asset_path(service, user_id, config['asset_id'])
            duration = min(float(seconds), service.probe_duration(source))
            service.probe_duration(bottom)
            # File-based drawtext prevents captions/names from becoming FFmpeg filter syntax.
            brand_file = source.parent / 'brand.txt'
            brand_file.write_text(re.sub(r'[\r\n\x00-\x1f]', '', page_name)[:32] or 'My page', encoding='utf-8')
            filters = (
                '[0:v]setpts=PTS-STARTPTS,scale=540:480:force_original_aspect_ratio=decrease,'
                'pad=540:480:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30[top];'
                '[1:v]setpts=PTS-STARTPTS,scale=540:480:force_original_aspect_ratio=decrease,'
                'pad=540:480:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30[bottom];'
                '[top][bottom]vstack=inputs=2:shortest=1,'
                'drawbox=x=16:y=880:w=508:h=64:color=0x071321@0.9:t=fill,'
                f'drawtext=fontfile={service.FONT_PATH}:textfile={brand_file}:expansion=none:'
                'fontcolor=white:fontsize=20:x=28:y=890,'
                f"drawtext=fontfile={service.FONT_PATH}:text='Tap Follow on our profile':"
                'fontcolor=0x2cb4f3:fontsize=15:x=28:y=920[out]'
            )
            output = source.parent / 'clip-1.mp4'
            command = ['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y',
                       '-threads','1','-filter_complex_threads','1','-i',str(source),
                       '-stream_loop','-1','-i',str(bottom),'-filter_complex',filters,'-map','[out]']
            if config['audio'] != 'mute':
                command += ['-map', ('0:a:0?' if config['audio']=='source' else '1:a:0?'), '-af','apad']
            command += ['-t',str(duration),'-c:v','libx264','-threads','1','-preset','ultrafast',
                        '-crf','26','-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-movflags','+faststart',str(output)]
            service.run(command)
            text = caption(original, config['addition'], config['signature'])
            outputs = [{'name':'clip-1.mp4','title':'Combined video','post_title':text,
                        'title_source':'source-caption-and-original-addition','hashtags':'',
                        'seconds':duration,'profile_url':config['profile_url'],
                        'url':f'/api/jobs/{job_id}/files/clip-1.mp4'}]
            service.update_job(job_id,status='completed',progress=100,message='Your combined video is ready',
                               outputs=outputs,expires_at=int(time.time())+ (86400 if project_id else service.JOB_TTL_SECONDS))
            if project_id:
                service.enqueue_outputs(user_id,project_id,job_id,outputs)
    except Exception as exc:
        service.update_job(job_id,status='failed',progress=100,message=str(exc)[:300])
        if project_id:
            service.add_event(project_id,'Combined video failed: '+str(exc)[:200],'error')
    finally:
        source.unlink(missing_ok=True)
        (source.parent/'brand.txt').unlink(missing_ok=True)


def install(service):
    app = service.app

    def customer(request):
        user = service.current_customer(request)
        if not user:
            raise HTTPException(401,'Log in to your ClipMagic workspace first')
        return user

    @app.get('/api/combined/assets')
    async def list_assets(request: Request):
        user = customer(request)
        folder = service.WORK_ROOT / '_combined' / str(user['id'])
        result=[]
        for path in folder.glob('*/meta.json'):
            try:
                data=json.loads(path.read_text()); result.append(data)
            except (OSError,ValueError):
                continue
        return {'assets':sorted(result,key=lambda x:x['created_at'],reverse=True)}

    @app.post('/api/combined/assets')
    async def upload_asset(request: Request, video: UploadFile = File(...)):
        user = customer(request)
        root = service.WORK_ROOT / '_combined' / str(user['id'])
        if len(list(root.glob('*/meta.json'))) >= 5:
            raise HTTPException(400,'You can keep five lower videos. Delete an unused one first.')
        upload_id=uuid.uuid4().hex
        source=await service.store_upload(video,upload_id)
        try:
            duration=await service.asyncio.to_thread(service.probe_duration,source)
            target=asset_path(service,user['id'],upload_id)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.move(str(source),str(target))
            meta={'id':upload_id,'name':Path(video.filename or 'My lower video').name[:150],
                  'seconds':duration,'created_at':int(time.time())}
            (target.parent/'meta.json').write_text(json.dumps(meta))
            return meta
        except Exception:
            shutil.rmtree(root/upload_id,ignore_errors=True)
            raise HTTPException(400,'This video could not be read. Please use a valid MP4 or MOV.')
        finally:
            shutil.rmtree(service.WORK_ROOT/upload_id,ignore_errors=True)

    @app.delete('/api/combined/assets/{asset_id}')
    async def delete_asset(asset_id: str, request: Request):
        user=customer(request)
        path=asset_path(service,user['id'],asset_id)
        with service.database() as db:
            projects=db.execute('SELECT combined_config FROM automation_projects WHERE user_id=?',(user['id'],)).fetchall()
        if any(json.loads(row['combined_config'] or '{}').get('asset_id')==asset_id for row in projects):
            raise HTTPException(409,'This video belongs to a saved connection. Delete that connection first.')
        if any(job.get('asset_id')==asset_id and job.get('status') in ('queued','processing') for job in service.jobs.values()):
            raise HTTPException(409,'This video is being used by a job. Wait until it finishes.')
        shutil.rmtree(path.parent,ignore_errors=True)
        return {'deleted':True}

    @app.post('/api/combined/preview')
    async def preview(request: Request, background_tasks: BackgroundTasks,
                      video: UploadFile=File(...), options: str=Form(...),
                      page_name: str=Form('My page'), source_caption: str=Form(''), seconds: int=Form(30)):
        user=customer(request)
        if not 3<=seconds<=90:
            raise HTTPException(400,'Choose a duration between 3 and 90 seconds')
        try:
            data=json.loads(options)
            if not isinstance(data,dict): raise ValueError()
        except ValueError:
            raise HTTPException(400,'Invalid combined-video settings')
        config=settings(service,user['id'],data)
        job_id=uuid.uuid4().hex
        source=await service.store_upload(video,job_id)
        service.jobs[job_id]={'id':job_id,'user_id':user['id'],'asset_id':config['asset_id'],
                            'mode':'combined','status':'queued','progress':0,'message':'Waiting for the video processor',
                            'outputs':[],'created_at':int(time.time()),'expires_at':int(time.time())+86400}
        service.save_job_state(job_id)
        background_tasks.add_task(render,service,job_id,source,user['id'],config,page_name,seconds,source_caption)
        return JSONResponse(service.jobs[job_id],status_code=202)
