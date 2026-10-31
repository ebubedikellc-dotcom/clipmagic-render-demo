"""Lightweight extractive naming with explicit customer feedback; no model or network."""
import json
import re
from collections import Counter
from fastapi import HTTPException, Request

DEFAULT_STYLE = 'direct'
STYLES = {'direct', 'brief', 'creator'}
STOP = set('a an the and or but to of in on at for with from by is are was were be been it its this that these those i you we they he she my your our their as so then than how what when where why who can could will would should do does did have has had not no yes just very into about today here there video clip watch follow subscribe like share'.split())


def status():
    return {'ready': True, 'installed': True, 'model': 'ClipMagic lightweight naming memory', 'reason': ''}


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS naming_preferences (user_id INTEGER NOT NULL, workflow TEXT NOT NULL, style TEXT NOT NULL, examples TEXT NOT NULL, PRIMARY KEY(user_id,workflow))')


def preferences(service, user_id, workflow):
    if user_id:
        with service.database() as db:
            row = db.execute('SELECT style,examples FROM naming_preferences WHERE user_id=? AND workflow=?', (user_id, workflow)).fetchone()
        if row:
            return {'style': row['style'] if row['style'] in STYLES else DEFAULT_STYLE, 'examples': json.loads(row['examples'])}
    return {'style': DEFAULT_STYLE, 'examples': []}


def plain(value):
    value = re.sub(r'https?://\S+|www\.\S+', '', str(value))
    value = re.sub(r'<[^>]*>', '', value)
    return re.sub(r'\s+', ' ', value).strip()


def words(text):
    return re.findall(r"[^\W\d_]+", text.casefold(), re.UNICODE)


def key(text):
    # Preserve numbers and negation: similar subjects can describe opposite events.
    return ' '.join(re.findall(r'\w+', plain(text).casefold(), re.UNICODE))


def shorten(text, limit):
    if len(text) <= limit:
        return text
    prefix = text[:limit-1].rsplit(' ', 1)[0]
    return (prefix or text[:limit-1]) + '…'


def generate(service, video, creator, index=1, user_id=None, workflow='normal', source_caption='', seconds=120):
    evidence = plain(source_caption[:8000])
    if len(words(evidence)) < 3:
        return {'title': shorten(f'{plain(creator) or "Video"} — clip {index}',80), 'hashtags': '',
                'source': 'neutral-fallback', 'warning': 'Add a source caption or clip description for a meaningful title. This naming system does not watch or listen to video.'}
    pref = preferences(service, user_id, workflow)
    seen, chosen = Counter(), Counter()
    for example in pref['examples']:
        seen.update(set(words(example['context'])) - STOP)
        chosen.update((set(words(example['title'])) & set(words(example['context']))) - STOP)
    # Learn which evidence words the owner keeps in approved titles. Only choose words
    # from the CURRENT caption, never facts from a related but different example.
    def weight(word):
        return 1 + (chosen[word] / seen[word] if seen[word] else 0)
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+|[\r\n]+', source_caption[:8000]) if len(words(plain(s))) >= 3]
    sentences = [plain(s) for s in sentences] or [evidence]
    def score(sentence):
        tokens = words(sentence)
        return sum(weight(w) for w in set(tokens) - STOP) / max(1,len(tokens))
    title = max(sentences, key=score).strip(' .')
    source = 'naming-caption'
    for example in reversed(pref['examples']):
        if key(example['context']) == key(evidence):
            title = plain(example['title'])
            source = 'naming-taught'
            break
    if pref['style'] == 'creator' and plain(creator):
        title = plain(creator)[:30] + ': ' + title
    title = shorten(title,55 if pref['style'] == 'brief' else 80)
    tokens = [w for w in words(evidence) if len(w)>2 and w not in STOP]
    frequency = Counter(tokens)
    ranked = sorted(dict.fromkeys(tokens), key=lambda w: frequency[w]*weight(w), reverse=True)
    tags = ' '.join('#'+w[:30] for w in ranked[:5])
    return {'title': title, 'hashtags': tags, 'source': source,
            'warning': 'Named from supplied text; check that it describes this clip.'}


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
        if len(await request.body()) > 256000:
            raise HTTPException(413, 'Please use shorter examples')
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(400, 'Invalid naming settings')
        if not isinstance(data, dict):
            raise HTTPException(400, 'Invalid naming settings')
        workflow = workflow_name(data.get('workflow', 'normal'))
        style = data.get('style', DEFAULT_STYLE)
        if not isinstance(style, str) or style not in STYLES:
            raise HTTPException(400, 'Choose direct, brief or creator naming style')
        examples = data.get('examples', [])
        if not isinstance(examples, list) or len(examples) > 200:
            raise HTTPException(400, 'Save up to 200 examples per workflow')
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

    @service.app.post('/api/naming/preview')
    async def preview(request: Request):
        user = customer(request)
        if len(await request.body()) > 32000:
            raise HTTPException(413, 'Please use a shorter caption')
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(400, 'Invalid preview')
        if not isinstance(data, dict) or not isinstance(data.get('caption'), str):
            raise HTTPException(400, 'Enter a caption')
        return generate(service, None, str(data.get('creator', ''))[:70], user_id=user['id'],
                        workflow=workflow_name(data.get('workflow','normal')), source_caption=data['caption'])
