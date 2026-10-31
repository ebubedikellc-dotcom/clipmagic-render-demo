"""Small, local caption writer. No remote inference and no invented AI success."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading

from fastapi import HTTPException, Request

MODEL_FILE = 'qwen2.5-0.5b-instruct-q4_k_m.gguf'
DEFAULT_STYLE = 'Write a clear, interesting headline describing this clip. Avoid clickbait and invented facts.'
_slot = threading.Lock()


def model_root():
    return Path(os.environ.get('CLIPMAGIC_MODELS_DIR', '/opt/clipmagic-models'))


def memory_limit_mb():
    limits = []
    for name in ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes'):
        try:
            value = int(Path(name).read_text().strip())
            if value < 2**60:
                limits.append(value // (1024 * 1024))
        except (ValueError, OSError):
            pass
    return min(limits) if limits else None


def status():
    root = model_root()
    installed = (root / MODEL_FILE).is_file() and (root / 'speech' / 'model.bin').is_file()
    dependencies = all(importlib.util.find_spec(name) is not None for name in ('llama_cpp', 'faster_whisper'))
    memory = memory_limit_mb()
    if os.environ.get('CLIPMAGIC_NAMING_ENABLED', '1') != '1':
        reason = 'Naming assistant is switched off.'
    elif not installed or not dependencies:
        reason = 'Local naming models or runtime are not installed.'
    elif memory is not None and memory < 1536:
        reason = 'This service needs more memory before local AI naming can run (at least 1.5 GB).'
    else:
        reason = ''
    return {'ready': not reason, 'installed': installed and dependencies,
            'model': 'Qwen2.5-0.5B-Instruct (Q4_K_M)', 'reason': reason}


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS naming_preferences (user_id INTEGER NOT NULL, workflow TEXT NOT NULL, style TEXT NOT NULL, examples TEXT NOT NULL, PRIMARY KEY(user_id,workflow))')


def preferences(service, user_id, workflow):
    if user_id:
        with service.database() as db:
            row = db.execute('SELECT style,examples FROM naming_preferences WHERE user_id=? AND workflow=?', (user_id, workflow)).fetchone()
        if row:
            return {'style': row['style'], 'examples': json.loads(row['examples'])}
    return {'style': DEFAULT_STYLE, 'examples': []}


def clean_metadata(data, evidence=None):
    if not isinstance(data, dict) or not isinstance(data.get('title'), str):
        raise ValueError('No usable title')
    title = re.sub(r'[\x00-\x1f\x7f]+', ' ', data['title']).strip().strip('"')
    if not title or '<think>' in title:
        raise ValueError('No usable title')
    title = title[:80].strip()
    tags = []
    evidence_words = re.sub(r'[^\w]', '', evidence, flags=re.UNICODE).lower() if evidence is not None else None
    values = data.get('hashtags', [])
    if not isinstance(values, list):
        values = []
    for value in values:
        if not isinstance(value, str):
            continue
        tag = re.sub(r'[^\w]', '', value, flags=re.UNICODE)[:30]
        if evidence_words is not None and tag.lower() not in evidence_words:
            continue
        if (tag and not tag.isdigit() and tag.lower() not in {'short','relevant','tags','hashtag','hashtags'}
                and tag.lower() not in {t[1:].lower() for t in tags}):
            tags.append('#' + tag)
        if len(tags) == 5:
            break
    return {'title': title, 'hashtags': ' '.join(tags)}


def worker(action, payload):
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('naming_worker.py')), action],
                            input=json.dumps(payload), text=True, capture_output=True,
                            timeout=180, check=True)
    return json.loads(result.stdout)


def generate(service, video, creator, index=1, user_id=None, workflow='normal', source_caption='', seconds=120):
    fallback = {'title': f'{creator or "Video"} — clip {index}'[:80], 'hashtags': '',
                'source': 'neutral-fallback', 'warning': ''}
    readiness = status()
    if not readiness['ready']:
        fallback['warning'] = readiness['reason']
        return fallback
    # Models live in separate short-lived processes, never concurrently with each other.
    with _slot:
        try:
            transcript = worker('transcribe', {'video': str(video), 'seconds': min(float(seconds), 120),
                                              'models': str(model_root())}).get('text', '').strip()
            evidence_source = 'speech'
            if not transcript:
                transcript = source_caption.strip()
                evidence_source = 'source-caption'
            if len(transcript.split()) < 3:
                fallback['warning'] = 'No usable speech or source caption; a neutral title was used.'
                return fallback
            pref = preferences(service, user_id, workflow)
            data = worker('generate', {'text': transcript[:3200], 'creator': str(creator)[:70],
                                      'style': pref['style'], 'examples': pref['examples'][-2:],
                                      'models': str(model_root())})
            metadata = clean_metadata(data, transcript)
            metadata.update(source='local-qwen-' + evidence_source, warning='')
            return metadata
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            fallback['warning'] = 'Local AI naming could not finish; a neutral title was used.'
            return fallback


def install(service):
    def customer(request):
        user = service.current_customer(request)
        if not user:
            raise HTTPException(401, 'Log in to your ClipMagic workspace first')
        return user

    def workflow_name(value):
        if value not in ('normal', 'combined'):
            raise HTTPException(400, 'Choose normal or combined videos')
        return value

    @service.app.get('/api/naming/settings')
    async def read_settings(request: Request, workflow: str = 'normal'):
        user = customer(request)
        return {**preferences(service, user['id'], workflow_name(workflow)), 'status': status()}

    @service.app.put('/api/naming/settings')
    async def save_settings(request: Request):
        user = customer(request)
        if len(await request.body()) > 16000:
            raise HTTPException(413, 'Please use shorter examples')
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(400, 'Invalid naming settings')
        workflow = workflow_name(data.get('workflow', 'normal'))
        style = str(data.get('style', DEFAULT_STYLE)).strip()[:400] or DEFAULT_STYLE
        examples = data.get('examples', [])
        if not isinstance(examples, list) or len(examples) > 6:
            raise HTTPException(400, 'Save up to six examples per workflow')
        clean = []
        for item in examples:
            if not isinstance(item, dict) or not isinstance(item.get('context'), str) or not isinstance(item.get('title'), str):
                raise HTTPException(400, 'Each example needs a clip description and title')
            context, title = item['context'].strip()[:400], item['title'].strip()[:80]
            if not context or not title:
                raise HTTPException(400, 'Each example needs a clip description and title')
            clean.append({'context': context, 'title': title})
        with service.database() as db:
            db.execute('INSERT INTO naming_preferences VALUES(?,?,?,?) ON CONFLICT(user_id,workflow) DO UPDATE SET style=excluded.style,examples=excluded.examples',
                       (user['id'], workflow, style, json.dumps(clean)))
        return {'saved': True}
