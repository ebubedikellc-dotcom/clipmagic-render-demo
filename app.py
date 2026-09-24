from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.fernet import Fernet
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


BASE_DIR = Path(__file__).resolve().parent
WORK_ROOT = Path(os.environ.get("CLIPMAGIC_WORK_ROOT", "/tmp/clipmagic-jobs"))
MAX_UPLOAD_BYTES = int(os.environ.get("CLIPMAGIC_MAX_UPLOAD_MB", "250")) * 1024 * 1024
JOB_TTL_SECONDS = int(os.environ.get("CLIPMAGIC_JOB_TTL_SECONDS", "3600"))
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
OWNER_EMAIL = os.environ.get("CLIPMAGIC_OWNER_EMAIL", "Ebubedikellc@gmail.com").strip().lower()
SESSION_SECRET = os.environ.get("CLIPMAGIC_SESSION_SECRET") or hashlib.sha256(
    f"clipmagic-session|{OWNER_EMAIL}|owner-only".encode()
).hexdigest()
SESSION_COOKIE = "clipmagic_owner_session"
CUSTOMER_COOKIE = "clipmagic_customer_session"
DATABASE_PATH = Path(os.environ.get("CLIPMAGIC_DATABASE_PATH", str(WORK_ROOT / "clipmagic-users.db")))
API_SECRET = os.environ.get("CLIPMAGIC_API_ENCRYPTION_KEY") or SESSION_SECRET
API_CIPHER = Fernet(base64.urlsafe_b64encode(hashlib.sha256(API_SECRET.encode()).digest()))

WORK_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="ClipMagic Engine", version="1.0.0")
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
processing_slot = threading.Semaphore(max(1, int(os.environ.get("CLIPMAGIC_MAX_ACTIVE_JOBS", "1"))))
publisher_wakeup = threading.Event()


def job_state_path(job_id: str) -> Path:
    return WORK_ROOT / job_id / "job.json"


def save_job_state(job_id: str) -> None:
    job = jobs.get(job_id)
    if not job:
        return
    path = job_state_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(job), encoding="utf-8")
    temporary.replace(path)


def load_job_state(job_id: str) -> dict | None:
    path = job_state_path(job_id)
    if not path.is_file():
        return None
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
        if job.get("id") != job_id:
            return None
        jobs[job_id] = job
        return job
    except (OSError, ValueError, TypeError):
        return None


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS api_credentials (
                user_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                encrypted_data TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, platform),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS automation_projects (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_platform TEXT NOT NULL,
                streamer_name TEXT NOT NULL,
                page_name TEXT NOT NULL,
                monitor_new INTEGER NOT NULL DEFAULT 1,
                import_history INTEGER NOT NULL DEFAULT 0,
                clip_count INTEGER NOT NULL DEFAULT 3,
                clip_length INTEGER NOT NULL DEFAULT 30,
                sound_choice TEXT NOT NULL DEFAULT 'No added sound',
                status TEXT NOT NULL DEFAULT 'paused',
                last_checked_at INTEGER,
                last_error TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS project_destinations (
                project_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                account_link TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (project_id, platform),
                FOREIGN KEY (project_id) REFERENCES automation_projects(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS publish_queue (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                project_id TEXT,
                job_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                platform TEXT NOT NULL,
                title TEXT NOT NULL,
                hashtags TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at INTEGER NOT NULL,
                remote_id TEXT,
                last_error TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_publish_ready ON publish_queue(status, next_attempt_at);
            CREATE TABLE IF NOT EXISTS project_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (project_id) REFERENCES automation_projects(id) ON DELETE CASCADE
            );
            """
        )
        project_columns = {row[1] for row in connection.execute("PRAGMA table_info(automation_projects)").fetchall()}
        if "source_secret" not in project_columns:
            connection.execute("ALTER TABLE automation_projects ADD COLUMN source_secret TEXT")
        missing_secrets = connection.execute(
            "SELECT id FROM automation_projects WHERE source_secret IS NULL OR source_secret=''"
        ).fetchall()
        for row in missing_secrets:
            connection.execute(
                "UPDATE automation_projects SET source_secret=? WHERE id=?",
                (secrets.token_urlsafe(24), row[0]),
            )


SUPPORTED_DESTINATIONS = {"facebook", "instagram", "youtube", "tiktok"}


def oauth_environment(platform: str) -> tuple[str, str]:
    """Return the developer-app client credentials configured by the site owner."""
    prefix = {"facebook": "META", "instagram": "META", "youtube": "GOOGLE", "tiktok": "TIKTOK"}[platform]
    client_id = os.environ.get(f"{prefix}_CLIENT_ID", "").strip()
    client_secret = os.environ.get(f"{prefix}_CLIENT_SECRET", "").strip()
    return client_id, client_secret


def oauth_state(user_id: int, platform: str) -> str:
    expires = int(time.time()) + 900
    nonce = secrets.token_urlsafe(12)
    payload = f"{user_id}|{platform}|{expires}|{nonce}"
    signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{signature}".encode()).decode()


def parse_oauth_state(state: str, expected_platform: str) -> int:
    try:
        decoded = base64.urlsafe_b64decode(state.encode()).decode()
        user_id, platform, expires, nonce, signature = decoded.rsplit("|", 4)
        payload = f"{user_id}|{platform}|{expires}|{nonce}"
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if platform != expected_platform or int(expires) < int(time.time()) or not hmac.compare_digest(signature, expected):
            raise ValueError
        return int(user_id)
    except Exception as exc:
        raise HTTPException(400, "This connection request expired. Please press Connect again.") from exc


def save_credentials(user_id: int, platform: str, values: dict) -> None:
    clean = {str(key)[:40]: str(value).strip()[:8000] for key, value in values.items() if value is not None and str(value).strip()}
    now = int(time.time())
    with database() as connection:
        existing = connection.execute(
            "SELECT encrypted_data FROM api_credentials WHERE user_id=? AND platform=?", (user_id, platform)
        ).fetchone()
        if existing:
            try:
                previous = json.loads(API_CIPHER.decrypt(existing["encrypted_data"].encode()).decode())
                previous.update(clean)
                clean = previous
            except Exception:
                pass
        encrypted = API_CIPHER.encrypt(json.dumps(clean).encode()).decode()
        connection.execute(
            "INSERT INTO api_credentials (user_id, platform, encrypted_data, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, platform) DO UPDATE SET encrypted_data=excluded.encrypted_data, updated_at=excluded.updated_at",
            (user_id, platform, encrypted, now),
        )
        connection.execute(
            "UPDATE publish_queue SET status='queued', next_attempt_at=?, last_error=NULL, updated_at=? "
            "WHERE user_id=? AND platform=? AND status='blocked'",
            (now, now, user_id, platform),
        )
    publisher_wakeup.set()


def source_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "facebook.com" in host or "fb.watch" in host:
        return "facebook"
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    return "other"


def add_event(project_id: str, message: str, level: str = "info") -> None:
    with database() as connection:
        connection.execute(
            "INSERT INTO project_events (project_id, level, message, created_at) VALUES (?, ?, ?, ?)",
            (project_id, level, message[:500], int(time.time())),
        )


def credentials_for(user_id: int, platform: str) -> dict:
    with database() as connection:
        row = connection.execute(
            "SELECT encrypted_data FROM api_credentials WHERE user_id = ? AND platform = ?",
            (user_id, platform),
        ).fetchone()
    if not row:
        return {}
    try:
        return json.loads(API_CIPHER.decrypt(row["encrypted_data"].encode()).decode())
    except Exception:
        return {}


def active_credentials(user_id: int, platform: str) -> dict:
    """Refresh expiring OAuth tokens before an automatic post."""
    credentials = credentials_for(user_id, platform)
    expires_at = int(credentials.get("expires_at") or 0)
    if not credentials.get("refresh_token") or expires_at > int(time.time()) + 300:
        return credentials
    client_id, client_secret = oauth_environment(platform)
    if not client_id or not client_secret:
        return credentials
    try:
        if platform == "youtube":
            response = httpx.post(
                "https://oauth2.googleapis.com/token",
                data={"client_id": client_id, "client_secret": client_secret,
                      "refresh_token": credentials["refresh_token"], "grant_type": "refresh_token"}, timeout=30,
            )
        elif platform == "tiktok":
            response = httpx.post(
                "https://open.tiktokapis.com/v2/oauth/token/",
                data={"client_key": client_id, "client_secret": client_secret,
                      "refresh_token": credentials["refresh_token"], "grant_type": "refresh_token"},
                headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30,
            )
        else:
            return credentials
        response.raise_for_status()
        refreshed = response.json()
        credentials["access_token"] = refreshed["access_token"]
        credentials["refresh_token"] = refreshed.get("refresh_token", credentials["refresh_token"])
        credentials["expires_at"] = int(time.time()) + int(refreshed.get("expires_in", 3600))
        save_credentials(user_id, platform, credentials)
    except Exception:
        pass
    return credentials


def public_base_url() -> str:
    return os.environ.get("CLIPMAGIC_PUBLIC_URL", "https://clipmagic-engine.onrender.com").rstrip("/")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return f"{base64.urlsafe_b64encode(salt).decode()}:{base64.urlsafe_b64encode(digest).decode()}"


def check_password(password: str, stored: str) -> bool:
    try:
        salt_text, digest_text = stored.split(":", 1)
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, base64.binascii.Error):
        return False


def create_customer_session(user_id: int, email: str) -> str:
    expires = str(int(time.time()) + 60 * 60 * 24 * 30)
    payload = f"{user_id}|{email}|{expires}"
    signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{signature}".encode()).decode()


def current_customer(request: Request) -> dict | None:
    token = request.cookies.get(CUSTOMER_COOKIE, "")
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        user_id, email, expires, signature = decoded.rsplit("|", 3)
        payload = f"{user_id}|{email}|{expires}"
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if int(expires) <= int(time.time()) or not hmac.compare_digest(signature, expected):
            return None
        with database() as connection:
            row = connection.execute("SELECT id, email FROM users WHERE id = ? AND email = ?", (int(user_id), email)).fetchone()
        return dict(row) if row else None
    except (ValueError, TypeError, base64.binascii.Error):
        return None


def create_owner_session() -> str:
    expires = str(int(time.time()) + 60 * 60 * 24 * 7)
    payload = f"{OWNER_EMAIL}|{expires}"
    signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{signature}".encode()).decode()


def valid_owner_session(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE, "")
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        email, expires, signature = decoded.rsplit("|", 2)
        payload = f"{email}|{expires}"
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return (
            email == OWNER_EMAIL
            and int(expires) > int(time.time())
            and hmac.compare_digest(signature, expected)
        )
    except (ValueError, TypeError, base64.binascii.Error):
        return False


def safe_text(value: str, fallback: str) -> str:
    value = re.sub(r"[^\w .,'!&()@+-]", "", value or "", flags=re.UNICODE).strip()
    return (value[:70] or fallback).replace("'", "\\'").replace(":", "\\:")


def run(command: list[str]) -> str:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        message = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "Video processing failed"
        raise RuntimeError(message)
    return completed.stdout.strip()


def probe_duration(path: Path) -> float:
    output = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    duration = float(output)
    if not 1 <= duration <= 21600:
        raise RuntimeError("Video duration must be between 1 second and 6 hours")
    return duration


def highlight_starts(path: Path, duration: float, clip_length: int, clip_count: int) -> tuple[list[float], str]:
    """Prefer high-motion scene changes, then fall back to even coverage."""
    usable = max(0.0, duration - clip_length)
    if clip_count == 1 or usable <= 0:
        return [0.0], "full-video"
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-an",
        "-vf", "select='gt(scene,0.12)',metadata=print", "-f", "null", "-",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    text_output = completed.stdout + "\n" + completed.stderr
    candidates: list[tuple[float, float]] = []
    current_time: float | None = None
    for line in text_output.splitlines():
        time_match = re.search(r"pts_time:([0-9.]+)", line)
        if time_match:
            current_time = float(time_match.group(1))
        score_match = re.search(r"lavfi\.scene_score=([0-9.]+)", line)
        if score_match and current_time is not None:
            candidates.append((float(score_match.group(1)), current_time))
    selected: list[float] = []
    for _, moment in sorted(candidates, reverse=True):
        start = max(0.0, min(usable, moment - clip_length * 0.25))
        if all(abs(start - existing) >= clip_length * 0.65 for existing in selected):
            selected.append(start)
        if len(selected) >= clip_count:
            break
    if selected:
        selected.sort()
        while len(selected) < clip_count:
            fallback = usable * (len(selected) + 1) / (clip_count + 1)
            if all(abs(fallback - existing) >= max(1, clip_length * 0.25) for existing in selected):
                selected.append(fallback)
            else:
                break
        return sorted(selected[:clip_count]), "scene-change"
    return [usable * (index + 1) / (clip_count + 1) for index in range(clip_count)], "even-coverage"


def update_job(job_id: str, **changes) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(changes)
            save_job_state(job_id)


def generate_post_title(video_path: Path, streamer_name: str, index: int, user_id: int | None = None) -> tuple[str, str]:
    """Use speech understanding when configured; always retain a safe offline fallback."""
    fallbacks = [
        f"{streamer_name} could not believe this moment",
        f"{streamer_name}'s reaction says everything",
        f"Wait for {streamer_name}'s unexpected ending",
        f"{streamer_name} delivered an unforgettable moment",
        f"This {streamer_name} highlight deserves a replay",
    ]
    title = fallbacks[(index - 1) % len(fallbacks)]
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if user_id and not api_key:
        ai_credentials = credentials_for(user_id, "ai")
        if (ai_credentials.get("provider") or "").lower() == "openai":
            api_key = (ai_credentials.get("api_key") or "").strip()
    if not api_key:
        return title, "offline-template"
    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        with video_path.open("rb") as media:
            transcript_response = httpx.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers=headers,
                files={"file": (video_path.name, media, "video/mp4")},
                data={"model": os.environ.get("CLIPMAGIC_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")},
                timeout=90,
            )
        transcript_response.raise_for_status()
        transcript = transcript_response.json().get("text", "").strip()
        if not transcript:
            return title, "offline-template"
        prompt = (
            "Write one accurate, exciting social-video title under 80 characters. "
            f"Include the creator name {streamer_name}. Do not invent facts. "
            "Return only the title. Transcript: " + transcript[:5000]
        )
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={**headers, "Content-Type": "application/json"},
            json={"model": os.environ.get("CLIPMAGIC_AI_MODEL", "gpt-4.1-mini"), "input": prompt},
            timeout=60,
        )
        response.raise_for_status()
        generated = response.json().get("output_text", "").strip().strip('"')
        if generated:
            return generated[:100], "speech-ai"
    except Exception:
        pass
    return title, "offline-template"


def enqueue_outputs(user_id: int, project_id: str, job_id: str, outputs: list[dict]) -> None:
    now = int(time.time())
    with database() as connection:
        destinations = connection.execute(
            "SELECT platform FROM project_destinations WHERE project_id = ? AND enabled = 1",
            (project_id,),
        ).fetchall()
        for output in outputs:
            for destination in destinations:
                connection.execute(
                    "INSERT INTO publish_queue (id, user_id, project_id, job_id, filename, platform, title, hashtags, status, attempts, next_attempt_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?)",
                    (uuid.uuid4().hex, user_id, project_id, job_id, output["name"], destination["platform"],
                     output["post_title"], output["hashtags"], now, now, now),
                )
    add_event(project_id, f"{len(outputs)} clips created and added to the publishing queue")
    publisher_wakeup.set()


def remove_fully_published_job(job_id: str) -> None:
    with database() as connection:
        statuses = [row[0] for row in connection.execute(
            "SELECT status FROM publish_queue WHERE job_id = ?", (job_id,)
        ).fetchall()]
    if statuses and all(status == "posted" for status in statuses):
        jobs.pop(job_id, None)
        shutil.rmtree(WORK_ROOT / job_id, ignore_errors=True)


def process_job(job_id: str, input_path: Path, page_name: str, streamer_name: str,
                clip_count: int, clip_length: int, user_id: int | None = None,
                project_id: str | None = None) -> None:
    job_dir = input_path.parent
    try:
        update_job(job_id, status="queued", progress=2, message="Waiting for the video processor")
        with processing_slot:
            update_job(job_id, status="processing", progress=5, message="Reading the video")
            duration = probe_duration(input_path)
            actual_length = max(3, min(clip_length, int(duration)))
            starts, selection_source = highlight_starts(input_path, duration, actual_length, clip_count)

            outputs = []
            streamer_tag = re.sub(r"[^A-Za-z0-9]", "", streamer_name)[:32] or "Highlights"
            page_tag = re.sub(r"[^A-Za-z0-9]", "", page_name)[:32] or "Clips"
            for index, start in enumerate(starts, 1):
                output_name = f"clip-{index}.mp4"
                output_path = job_dir / output_name
                brand = safe_text(page_name, "My Clips Page")
                avatar = safe_text((page_name.strip()[:1] or "M").upper(), "M")
                vf = (
                    "scale=540:960:force_original_aspect_ratio=decrease,"
                    "pad=540:960:(ow-iw)/2:(oh-ih)/2:color=black,"
                    "drawbox=x=22:y=ih-122:w=496:h=94:color=0x071321@0.92:t=fill,"
                    "drawbox=x=36:y=ih-104:w=58:h=58:color=0x2cb4f3@1:t=fill,"
                    f"drawtext=fontfile={FONT_PATH}:text='{avatar}':"
                    "fontcolor=0x071321:fontsize=28:x=55:y=h-98,"
                    f"drawtext=fontfile={FONT_PATH}:text='{brand}':"
                    "fontcolor=white:fontsize=22:x=108:y=h-101,"
                    "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
                    "text='Fresh clips and highlights':fontcolor=0x9fb2ca:fontsize=14:x=108:y=h-70,"
                    "drawbox=x=iw-169:y=ih-104:w=133:h=58:color=0x2cb4f3@1:t=fill,"
                    f"drawtext=fontfile={FONT_PATH}:text='FOLLOW  >':"
                    "fontcolor=0x06121f:fontsize=19:x=w-153:y=h-86"
                )
                run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-threads", "1", "-filter_threads", "1", "-filter_complex_threads", "1",
                    "-ss", f"{start:.3f}", "-i", str(input_path), "-t", str(actual_length),
                    "-vf", vf, "-c:v", "libx264", "-threads", "1", "-preset", "ultrafast",
                    "-tune", "zerolatency", "-crf", "26", "-c:a", "aac", "-b:a", "96k",
                    "-movflags", "+faststart", str(output_path),
                ])
                post_title, title_source = generate_post_title(output_path, streamer_name, index, user_id)
                outputs.append({
                    "name": output_name,
                    "title": f"{streamer_name} highlight {index}",
                    "post_title": post_title,
                    "title_source": title_source,
                    "hashtags": f"#{streamer_tag} #{page_tag} #Highlights #TrendingClips",
                    "seconds": actual_length,
                    "selection_source": selection_source,
                    "url": f"/api/jobs/{job_id}/files/{output_name}",
                })
                update_job(
                    job_id,
                    progress=5 + int(90 * index / len(starts)),
                    message=f"Created clip {index} of {len(starts)}",
                )

            input_path.unlink(missing_ok=True)
            update_job(
                job_id,
                status="completed",
                progress=100,
                message=f"{len(outputs)} real clips are ready",
                outputs=outputs,
                expires_at=int(time.time() + JOB_TTL_SECONDS),
            )
            if user_id and project_id:
                enqueue_outputs(user_id, project_id, job_id, outputs)
    except Exception as exc:
        input_path.unlink(missing_ok=True)
        update_job(job_id, status="failed", progress=100, message=str(exc)[:300])


def cleanup_expired() -> None:
    while True:
        now = time.time()
        expired: list[str] = []
        with jobs_lock:
            for job_id, job in jobs.items():
                if job.get("expires_at", job.get("created_at", now) + JOB_TTL_SECONDS) <= now:
                    expired.append(job_id)
            for job_id in expired:
                jobs.pop(job_id, None)
        for job_id in expired:
            shutil.rmtree(WORK_ROOT / job_id, ignore_errors=True)
        time.sleep(60)


def resolve_meta_account(platform: str, credentials: dict) -> tuple[str, str]:
    token = credentials.get("access_token") or credentials.get("api_token")
    if not token:
        raise RuntimeError("Connect this Meta account before automatic posting")
    graph_version = os.environ.get("META_GRAPH_VERSION", "v23.0")
    page_id = credentials.get("page_id") or credentials.get("account_id")
    page_token = token
    if not page_id:
        response = httpx.get(
            f"https://graph.facebook.com/{graph_version}/me/accounts",
            params={"fields": "id,name,link,access_token,instagram_business_account{id,username}", "access_token": token},
            timeout=30,
        )
        response.raise_for_status()
        accounts = response.json().get("data", [])
        if not accounts:
            raise RuntimeError("No authorized Facebook Page was found")
        account = accounts[0]
        page_token = account.get("access_token") or token
        if platform == "instagram":
            page_id = (account.get("instagram_business_account") or {}).get("id")
            if not page_id:
                raise RuntimeError("The authorized Page has no connected Instagram professional account")
        else:
            page_id = account.get("id")
    return str(page_id), str(page_token)


def publish_facebook(path: Path, title: str, hashtags: str, credentials: dict) -> str:
    page_id, token = resolve_meta_account("facebook", credentials)
    version = os.environ.get("META_GRAPH_VERSION", "v23.0")
    with path.open("rb") as video:
        response = httpx.post(
            f"https://graph-video.facebook.com/{version}/{page_id}/videos",
            data={"access_token": token, "description": f"{title}\n\n{hashtags}".strip()},
            files={"source": (path.name, video, "video/mp4")},
            timeout=300,
        )
    response.raise_for_status()
    return str(response.json().get("id") or "uploaded")


def publish_instagram(job_id: str, filename: str, title: str, hashtags: str, credentials: dict) -> str:
    account_id, token = resolve_meta_account("instagram", credentials)
    version = os.environ.get("META_GRAPH_VERSION", "v23.0")
    create = httpx.post(
        f"https://graph.facebook.com/{version}/{account_id}/media",
        data={
            "media_type": "REELS",
            "video_url": f"{public_base_url()}/api/jobs/{job_id}/files/{filename}",
            "caption": f"{title}\n\n{hashtags}".strip(),
            "share_to_feed": "true",
            "access_token": token,
        },
        timeout=60,
    )
    create.raise_for_status()
    container_id = create.json().get("id")
    if not container_id:
        raise RuntimeError("Instagram did not create a Reel container")
    for _ in range(18):
        time.sleep(5)
        status = httpx.get(
            f"https://graph.facebook.com/{version}/{container_id}",
            params={"fields": "status_code,status", "access_token": token}, timeout=30,
        )
        status.raise_for_status()
        code = status.json().get("status_code")
        if code == "FINISHED":
            break
        if code in {"ERROR", "EXPIRED"}:
            raise RuntimeError(status.json().get("status") or "Instagram could not process the Reel")
    else:
        raise RuntimeError("Instagram is still processing the Reel; it will be retried")
    publish = httpx.post(
        f"https://graph.facebook.com/{version}/{account_id}/media_publish",
        data={"creation_id": container_id, "access_token": token}, timeout=60,
    )
    publish.raise_for_status()
    return str(publish.json().get("id") or container_id)


def publish_youtube(path: Path, title: str, hashtags: str, credentials: dict) -> str:
    token = credentials.get("access_token") or credentials.get("api_token")
    if not token:
        raise RuntimeError("Connect YouTube before automatic posting")
    metadata = {
        "snippet": {"title": title[:100], "description": hashtags, "categoryId": "22"},
        "status": {"privacyStatus": credentials.get("privacy_status", "public"), "selfDeclaredMadeForKids": False},
    }
    with path.open("rb") as video:
        response = httpx.post(
            "https://www.googleapis.com/upload/youtube/v3/videos",
            params={"uploadType": "multipart", "part": "snippet,status"},
            headers={"Authorization": f"Bearer {token}"},
            files={
                "metadata": (None, json.dumps(metadata), "application/json; charset=UTF-8"),
                "media": (path.name, video, "video/mp4"),
            },
            timeout=300,
        )
    response.raise_for_status()
    return str(response.json().get("id") or "uploaded")


def publish_tiktok(path: Path, title: str, hashtags: str, credentials: dict) -> str:
    token = credentials.get("access_token") or credentials.get("api_token")
    if not token:
        raise RuntimeError("Connect TikTok before automatic posting")
    size = path.stat().st_size
    caption = f"{title} {hashtags}".strip()[:2200]
    initialize = httpx.post(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
        json={
            "post_info": {"title": caption, "privacy_level": credentials.get("privacy_level", "PUBLIC_TO_EVERYONE"),
                          "disable_duet": False, "disable_comment": False, "disable_stitch": False},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1},
        },
        timeout=60,
    )
    initialize.raise_for_status()
    body = initialize.json()
    if (body.get("error") or {}).get("code") not in {None, "ok"}:
        raise RuntimeError((body.get("error") or {}).get("message") or "TikTok rejected the upload")
    data = body.get("data") or {}
    upload_url, publish_id = data.get("upload_url"), data.get("publish_id")
    if not upload_url or not publish_id:
        raise RuntimeError("TikTok did not provide an upload address")
    with path.open("rb") as video:
        upload = httpx.put(upload_url, headers={"Content-Type": "video/mp4", "Content-Range": f"bytes 0-{size - 1}/{size}"},
                           content=video, timeout=300)
    upload.raise_for_status()
    return str(publish_id)


def publish_one(row: sqlite3.Row) -> str:
    path = WORK_ROOT / row["job_id"] / row["filename"]
    if not path.is_file():
        raise RuntimeError("The temporary clip expired before it could be posted")
    credentials = active_credentials(row["user_id"], row["platform"])
    if not (credentials.get("access_token") or credentials.get("api_token")):
        raise PermissionError(f"Connect {row['platform'].title()} before automatic posting")
    if row["platform"] == "facebook":
        return publish_facebook(path, row["title"], row["hashtags"], credentials)
    if row["platform"] == "instagram":
        return publish_instagram(row["job_id"], row["filename"], row["title"], row["hashtags"], credentials)
    if row["platform"] == "youtube":
        return publish_youtube(path, row["title"], row["hashtags"], credentials)
    if row["platform"] == "tiktok":
        return publish_tiktok(path, row["title"], row["hashtags"], credentials)
    raise PermissionError("Automatic posting is not available for this platform yet")


def publisher_loop() -> None:
    while True:
        now = int(time.time())
        with database() as connection:
            row = connection.execute(
                "SELECT * FROM publish_queue WHERE status IN ('queued','retry') AND next_attempt_at <= ? ORDER BY created_at LIMIT 1",
                (now,),
            ).fetchone()
            if row:
                connection.execute("UPDATE publish_queue SET status='posting', updated_at=? WHERE id=?", (now, row["id"]))
        if not row:
            publisher_wakeup.wait(10)
            publisher_wakeup.clear()
            continue
        try:
            remote_id = publish_one(row)
            with database() as connection:
                connection.execute(
                    "UPDATE publish_queue SET status='posted', remote_id=?, last_error=NULL, updated_at=? WHERE id=?",
                    (remote_id, int(time.time()), row["id"]),
                )
            if row["project_id"]:
                add_event(row["project_id"], f"Posted {row['filename']} to {row['platform'].title()}", "success")
            remove_fully_published_job(row["job_id"])
        except PermissionError as exc:
            with database() as connection:
                connection.execute(
                    "UPDATE publish_queue SET status='blocked', last_error=?, updated_at=? WHERE id=?",
                    (str(exc)[:500], int(time.time()), row["id"]),
                )
            if row["project_id"]:
                add_event(row["project_id"], str(exc), "warning")
        except Exception as exc:
            attempts = row["attempts"] + 1
            status = "failed" if attempts >= 5 else "retry"
            retry_at = int(time.time()) + min(3600, 30 * (2 ** attempts))
            with database() as connection:
                connection.execute(
                    "UPDATE publish_queue SET status=?, attempts=?, next_attempt_at=?, last_error=?, updated_at=? WHERE id=?",
                    (status, attempts, retry_at, str(exc)[:500], int(time.time()), row["id"]),
                )
            if row["project_id"]:
                add_event(row["project_id"], f"{row['platform'].title()} post failed: {str(exc)[:180]}", "error")


@app.on_event("startup")
async def startup() -> None:
    initialize_database()
    for path in WORK_ROOT.glob("*/job.json"):
        load_job_state(path.parent.name)
    threading.Thread(target=cleanup_expired, daemon=True).start()
    threading.Thread(target=publisher_loop, daemon=True).start()


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "engine": "ffmpeg",
        "automation_queue": True,
        "automatic_cleanup": True,
        "temporary_storage": True,
        "platforms": sorted(SUPPORTED_DESTINATIONS),
    }


def login_page(message: str = "") -> HTMLResponse:
    template = (BASE_DIR / "owner-login.html").read_text(encoding="utf-8")
    message_html = f'<div class="error">{message}</div>' if message else ""
    return HTMLResponse(template.replace("{{MESSAGE}}", message_html))


@app.get("/owner-login")
async def owner_login_page(request: Request):
    if valid_owner_session(request):
        return RedirectResponse("/control-panel.html", status_code=303)
    return login_page()


@app.post("/owner-login")
async def owner_login(email: str = Form(...)):
    email_ok = hmac.compare_digest(email.strip().lower(), OWNER_EMAIL)
    if not email_ok:
        return login_page("This email is not the registered owner email.")
    response = RedirectResponse("/control-panel.html", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_owner_session(),
        max_age=60 * 60 * 24 * 7,
        httponly=True,
        secure=True,
        samesite="strict",
    )
    return response


@app.post("/owner-logout")
async def owner_logout():
    response = RedirectResponse("/owner-login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/control-panel.html")
async def owner_control_panel(request: Request):
    if not valid_owner_session(request):
        return RedirectResponse("/owner-login", status_code=303)
    return FileResponse(BASE_DIR / "control-panel.html")


def customer_page(filename: str, message: str = "") -> HTMLResponse:
    template = (BASE_DIR / filename).read_text(encoding="utf-8")
    message_html = f'<div class="message">{message}</div>' if message else ""
    return HTMLResponse(template.replace("{{MESSAGE}}", message_html))


@app.get("/register")
async def customer_register_page(request: Request):
    if current_customer(request):
        return RedirectResponse("/my-account", status_code=303)
    return customer_page("customer-register.html")


@app.post("/register")
async def customer_register(email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return customer_page("customer-register.html", "Enter a valid email address.")
    if hmac.compare_digest(email, OWNER_EMAIL):
        return customer_page("customer-register.html", "The owner account is already registered. Please log in.")
    if len(password) < 6:
        return customer_page("customer-register.html", "Password must contain at least 6 characters.")
    try:
        with database() as connection:
            cursor = connection.execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                (email, hash_password(password), int(time.time())),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return customer_page("customer-register.html", "That email is already registered. Please log in.")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(CUSTOMER_COOKIE, create_customer_session(user_id, email), max_age=60 * 60 * 24 * 30,
                        httponly=True, secure=True, samesite="strict")
    return response


@app.get("/login")
async def customer_login_page(request: Request):
    if current_customer(request):
        return RedirectResponse("/my-account", status_code=303)
    return customer_page("customer-login.html")


@app.post("/login")
async def customer_login(email: str = Form(...), password: str = Form("")):
    email = email.strip().lower()
    with database() as connection:
        row = connection.execute("SELECT id, email, password_hash FROM users WHERE email = ?", (email,)).fetchone()
        if hmac.compare_digest(email, OWNER_EMAIL) and not row:
            cursor = connection.execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                (OWNER_EMAIL, hash_password(uuid.uuid4().hex), int(time.time())),
            )
            row = connection.execute(
                "SELECT id, email, password_hash FROM users WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
    owner_login = hmac.compare_digest(email, OWNER_EMAIL)
    if not row or (not owner_login and not check_password(password, row["password_hash"])):
        return customer_page("customer-login.html", "The email or password is incorrect.")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(CUSTOMER_COOKIE, create_customer_session(row["id"], row["email"]), max_age=60 * 60 * 24 * 30,
                        httponly=True, secure=True, samesite="strict")
    return response


@app.post("/logout")
async def customer_logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(CUSTOMER_COOKIE)
    return response


@app.get("/my-account")
async def my_account(request: Request):
    customer = current_customer(request)
    if not customer:
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.get("/api/oauth/status")
async def oauth_status(request: Request):
    customer = current_customer(request)
    if not customer:
        raise HTTPException(401, "Please log in")
    status = {}
    for platform in sorted(SUPPORTED_DESTINATIONS):
        client_id, client_secret = oauth_environment(platform)
        credentials = credentials_for(customer["id"], platform)
        status[platform] = {
            "available": bool(client_id and client_secret),
            "connected": bool(credentials.get("access_token") or credentials.get("api_token")),
            "connected_at": credentials.get("connected_at"),
        }
    return {"platforms": status}


@app.get("/api/oauth/{platform}/start")
async def oauth_start(platform: str, request: Request):
    customer = current_customer(request)
    if not customer:
        return RedirectResponse("/login", status_code=303)
    if platform not in SUPPORTED_DESTINATIONS:
        raise HTTPException(404, "Platform not supported")
    client_id, client_secret = oauth_environment(platform)
    if not client_id or not client_secret:
        return RedirectResponse(f"/?connection={platform}&result=owner_setup_required", status_code=303)
    redirect_uri = f"{public_base_url()}/api/oauth/{platform}/callback"
    state = oauth_state(customer["id"], platform)
    if platform in {"facebook", "instagram"}:
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": "pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish",
        }
        url = f"https://www.facebook.com/{os.environ.get('META_GRAPH_VERSION', 'v23.0')}/dialog/oauth?{urlencode(params)}"
    elif platform == "youtube":
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
        url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    else:
        params = {
            "client_key": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": "user.info.basic,video.publish",
        }
        url = f"https://www.tiktok.com/v2/auth/authorize/?{urlencode(params)}"
    return RedirectResponse(url, status_code=303)


@app.get("/api/oauth/{platform}/callback")
async def oauth_callback(platform: str, request: Request, state: str = "", code: str = "", error: str = ""):
    if platform not in SUPPORTED_DESTINATIONS:
        raise HTTPException(404, "Platform not supported")
    if error or not code:
        return RedirectResponse(f"/?connection={platform}&result=cancelled", status_code=303)
    user_id = parse_oauth_state(state, platform)
    client_id, client_secret = oauth_environment(platform)
    if not client_id or not client_secret:
        raise HTTPException(503, "The developer app configuration is missing")
    redirect_uri = f"{public_base_url()}/api/oauth/{platform}/callback"
    now = int(time.time())
    try:
        if platform in {"facebook", "instagram"}:
            version = os.environ.get("META_GRAPH_VERSION", "v23.0")
            short = httpx.get(
                f"https://graph.facebook.com/{version}/oauth/access_token",
                params={"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri, "code": code},
                timeout=30,
            )
            short.raise_for_status()
            short_token = short.json()["access_token"]
            exchange = httpx.get(
                f"https://graph.facebook.com/{version}/oauth/access_token",
                params={"grant_type": "fb_exchange_token", "client_id": client_id, "client_secret": client_secret,
                        "fb_exchange_token": short_token}, timeout=30,
            )
            exchange.raise_for_status()
            token_data = exchange.json()
            values = {"access_token": token_data.get("access_token", short_token), "expires_in": token_data.get("expires_in"),
                      "connected_at": now, "oauth": True}
        elif platform == "youtube":
            response = httpx.post(
                "https://oauth2.googleapis.com/token",
                data={"client_id": client_id, "client_secret": client_secret, "code": code,
                      "grant_type": "authorization_code", "redirect_uri": redirect_uri}, timeout=30,
            )
            response.raise_for_status()
            token_data = response.json()
            values = {"access_token": token_data.get("access_token"), "refresh_token": token_data.get("refresh_token"),
                      "expires_at": now + int(token_data.get("expires_in", 3600)), "connected_at": now, "oauth": True}
        else:
            response = httpx.post(
                "https://open.tiktokapis.com/v2/oauth/token/",
                data={"client_key": client_id, "client_secret": client_secret, "code": code,
                      "grant_type": "authorization_code", "redirect_uri": redirect_uri},
                headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30,
            )
            response.raise_for_status()
            token_data = response.json()
            values = {"access_token": token_data.get("access_token"), "refresh_token": token_data.get("refresh_token"),
                      "open_id": token_data.get("open_id"), "expires_at": now + int(token_data.get("expires_in", 86400)),
                      "connected_at": now, "oauth": True}
        if not values.get("access_token"):
            raise RuntimeError("The platform did not return posting authorization")
        save_credentials(user_id, platform, values)
    except Exception as exc:
        return RedirectResponse(f"/?connection={platform}&result=failed", status_code=303)
    return RedirectResponse(f"/?connection={platform}&result=connected", status_code=303)


@app.delete("/api/oauth/{platform}")
async def oauth_disconnect(platform: str, request: Request):
    customer = current_customer(request)
    if not customer:
        raise HTTPException(401, "Please log in")
    if platform not in SUPPORTED_DESTINATIONS:
        raise HTTPException(404, "Platform not supported")
    with database() as connection:
        connection.execute("DELETE FROM api_credentials WHERE user_id=? AND platform=?", (customer["id"], platform))
    return {"disconnected": True}


@app.get("/api/customer/apis")
async def get_customer_apis(request: Request):
    customer = current_customer(request)
    if not customer:
        raise HTTPException(401, "Please log in")
    with database() as connection:
        rows = connection.execute("SELECT platform, encrypted_data FROM api_credentials WHERE user_id = ?", (customer["id"],)).fetchall()
    configured = {}
    links = {}
    for row in rows:
        try:
            data = json.loads(API_CIPHER.decrypt(row["encrypted_data"].encode()).decode())
            configured[row["platform"]] = {key: bool(value) for key, value in data.items()}
            if data.get("account_link"):
                links[row["platform"]] = data["account_link"]
        except Exception:
            configured[row["platform"]] = {}
    return {"email": customer["email"], "configured": configured, "links": links}


@app.post("/api/customer/apis")
async def save_customer_apis(request: Request):
    customer = current_customer(request)
    if not customer:
        raise HTTPException(401, "Please log in")
    payload = await request.json()
    allowed = {"facebook", "instagram", "youtube", "tiktok", "snapchat", "x", "rumble", "dailymotion", "ai"}
    saved = []
    with database() as connection:
        for platform, values in payload.items():
            if platform not in allowed or not isinstance(values, dict):
                continue
            clean = {str(key)[:40]: str(value).strip()[:4000] for key, value in values.items() if str(value).strip()}
            if not clean:
                continue
            existing = connection.execute(
                "SELECT encrypted_data FROM api_credentials WHERE user_id = ? AND platform = ?",
                (customer["id"], platform),
            ).fetchone()
            if existing:
                try:
                    previous = json.loads(API_CIPHER.decrypt(existing["encrypted_data"].encode()).decode())
                    previous.update(clean)
                    clean = previous
                except Exception:
                    pass
            encrypted = API_CIPHER.encrypt(json.dumps(clean).encode()).decode()
            connection.execute(
                "INSERT INTO api_credentials (user_id, platform, encrypted_data, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, platform) DO UPDATE SET encrypted_data=excluded.encrypted_data, updated_at=excluded.updated_at",
                (customer["id"], platform, encrypted, int(time.time())),
            )
            saved.append(platform)
            if platform in SUPPORTED_DESTINATIONS:
                connection.execute(
                    "UPDATE publish_queue SET status='queued', next_attempt_at=?, last_error=NULL, updated_at=? "
                    "WHERE user_id=? AND platform=? AND status='blocked'",
                    (int(time.time()), int(time.time()), customer["id"], platform),
                )
                publisher_wakeup.set()
    return {"saved": saved, "message": "Your API details were saved privately."}


def project_payload(row: sqlite3.Row) -> dict:
    with database() as connection:
        destinations = [dict(item) for item in connection.execute(
            "SELECT platform, account_link, enabled FROM project_destinations WHERE project_id = ? ORDER BY platform",
            (row["id"],),
        ).fetchall()]
        counts = {item["status"]: item["count"] for item in connection.execute(
            "SELECT status, COUNT(*) AS count FROM publish_queue WHERE project_id = ? GROUP BY status",
            (row["id"],),
        ).fetchall()}
        events = [dict(item) for item in connection.execute(
            "SELECT level, message, created_at FROM project_events WHERE project_id = ? ORDER BY id DESC LIMIT 8",
            (row["id"],),
        ).fetchall()]
    data = dict(row)
    data["monitor_new"] = bool(data["monitor_new"])
    data["import_history"] = bool(data["import_history"])
    data["destinations"] = destinations
    data["queue"] = counts
    data["events"] = events
    data["automatic_inbox"] = (
        f"{public_base_url()}/api/source-inbox/{data['id']}/{data['source_secret']}"
        if data.get("source_secret") else None
    )
    return data


@app.get("/api/automation/projects")
async def list_projects(request: Request):
    customer = current_customer(request)
    if not customer:
        raise HTTPException(401, "Please log in")
    with database() as connection:
        rows = connection.execute(
            "SELECT * FROM automation_projects WHERE user_id = ? ORDER BY created_at DESC", (customer["id"],)
        ).fetchall()
    return {"projects": [project_payload(row) for row in rows], "maximum": 7}


@app.post("/api/automation/projects")
async def create_project(request: Request):
    customer = current_customer(request)
    if not customer:
        raise HTTPException(401, "Please log in")
    payload = await request.json()
    source_url = str(payload.get("source_url", "")).strip()
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "Enter a valid streamer page link")
    streamer_name = str(payload.get("streamer_name", "")).strip()[:70]
    page_name = str(payload.get("page_name", "")).strip()[:70]
    if not streamer_name or not page_name:
        raise HTTPException(400, "Enter the streamer name and your page name")
    requested = payload.get("destinations") or []
    if not isinstance(requested, list):
        raise HTTPException(400, "Choose at least one destination")
    destinations = []
    for item in requested:
        if not isinstance(item, dict) or item.get("platform") not in SUPPORTED_DESTINATIONS:
            continue
        link = str(item.get("account_link", "")).strip()
        if link:
            destinations.append((item["platform"], link[:1000]))
    if not destinations:
        raise HTTPException(400, "Save and choose at least one supported destination")
    unauthorized = [platform for platform, _ in destinations
                    if not (credentials_for(customer["id"], platform).get("access_token")
                            or credentials_for(customer["id"], platform).get("api_token"))]
    if unauthorized:
        names = ", ".join(platform.title() for platform in unauthorized)
        raise HTTPException(400, f"Add official API authorization for: {names}")
    with database() as connection:
        count = connection.execute("SELECT COUNT(*) FROM automation_projects WHERE user_id = ?", (customer["id"],)).fetchone()[0]
        if count >= 7:
            raise HTTPException(400, "You already have 7 clipping connections")
        project_id = uuid.uuid4().hex
        now = int(time.time())
        clip_count = max(1, min(int(payload.get("clip_count", 3)), 5))
        clip_length = max(3, min(int(payload.get("clip_length", 30)), 90))
        connection.execute(
            "INSERT INTO automation_projects (id,user_id,name,source_url,source_platform,streamer_name,page_name,monitor_new,import_history,clip_count,clip_length,sound_choice,status,created_at,updated_at,source_secret) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, customer["id"], str(payload.get("name") or f"{streamer_name} clips")[:100], source_url,
             source_platform(source_url), streamer_name, page_name, bool(payload.get("monitor_new", True)),
             bool(payload.get("import_history", False)), clip_count, clip_length,
             str(payload.get("sound_choice", "No added sound"))[:100], "active", now, now, secrets.token_urlsafe(24)),
        )
        for platform, link in destinations:
            connection.execute(
                "INSERT INTO project_destinations (project_id,platform,account_link,enabled) VALUES (?,?,?,1)",
                (project_id, platform, link),
            )
        row = connection.execute("SELECT * FROM automation_projects WHERE id = ?", (project_id,)).fetchone()
    add_event(project_id, "Automation connection created. Waiting for an authorized source video.", "success")
    return JSONResponse(project_payload(row), status_code=201)


@app.patch("/api/automation/projects/{project_id}")
async def update_project(project_id: str, request: Request):
    customer = current_customer(request)
    if not customer:
        raise HTTPException(401, "Please log in")
    payload = await request.json()
    status = payload.get("status")
    if status not in {"active", "paused"}:
        raise HTTPException(400, "Status must be active or paused")
    with database() as connection:
        result = connection.execute(
            "UPDATE automation_projects SET status=?, updated_at=? WHERE id=? AND user_id=?",
            (status, int(time.time()), project_id, customer["id"]),
        )
        if not result.rowcount:
            raise HTTPException(404, "Connection not found")
        row = connection.execute("SELECT * FROM automation_projects WHERE id=?", (project_id,)).fetchone()
    add_event(project_id, "Automation resumed" if status == "active" else "Automation paused")
    return project_payload(row)


@app.delete("/api/automation/projects/{project_id}")
async def delete_project(project_id: str, request: Request):
    customer = current_customer(request)
    if not customer:
        raise HTTPException(401, "Please log in")
    with database() as connection:
        result = connection.execute("DELETE FROM automation_projects WHERE id=? AND user_id=?", (project_id, customer["id"]))
    if not result.rowcount:
        raise HTTPException(404, "Connection not found")
    return {"deleted": True}


@app.get("/api/automation/queue")
async def get_publish_queue(request: Request):
    customer = current_customer(request)
    if not customer:
        raise HTTPException(401, "Please log in")
    with database() as connection:
        rows = connection.execute(
            "SELECT id,project_id,platform,title,status,attempts,remote_id,last_error,created_at,updated_at FROM publish_queue "
            "WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (customer["id"],)
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


async def store_upload(video: UploadFile, job_id: str) -> Path:
    if not (video.content_type or "").startswith("video/"):
        raise HTTPException(400, "Please upload a video file")
    job_dir = WORK_ROOT / job_id
    job_dir.mkdir(parents=True)
    suffix = Path(video.filename or "video.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
        suffix = ".mp4"
    input_path = job_dir / f"source{suffix}"
    total = 0
    with input_path.open("wb") as destination:
        while chunk := await video.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                destination.close()
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(413, f"Video is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
            destination.write(chunk)
    await video.close()
    return input_path


@app.post("/api/automation/projects/{project_id}/ingest")
async def ingest_project_video(project_id: str, background_tasks: BackgroundTasks, request: Request,
                               video: UploadFile = File(...)):
    customer = current_customer(request)
    if not customer:
        raise HTTPException(401, "Please log in")
    with database() as connection:
        project = connection.execute(
            "SELECT * FROM automation_projects WHERE id=? AND user_id=?", (project_id, customer["id"])
        ).fetchone()
    if not project:
        raise HTTPException(404, "Connection not found")
    if project["status"] != "active":
        raise HTTPException(409, "Resume this connection before adding a source video")
    job_id = uuid.uuid4().hex
    input_path = await store_upload(video, job_id)
    jobs[job_id] = {"id": job_id, "status": "queued", "progress": 0, "message": "Authorized source video queued",
                    "outputs": [], "created_at": int(time.time()), "expires_at": int(time.time() + max(JOB_TTL_SECONDS, 86400))}
    save_job_state(job_id)
    add_event(project_id, "A new authorized source video entered the clip queue")
    background_tasks.add_task(process_job, job_id, input_path, project["page_name"], project["streamer_name"],
                              project["clip_count"], project["clip_length"], customer["id"], project_id)
    return JSONResponse(jobs[job_id], status_code=202)


@app.post("/api/source-inbox/{project_id}/{source_secret}")
async def automatic_source_inbox(project_id: str, source_secret: str, background_tasks: BackgroundTasks,
                                  video: UploadFile = File(...)):
    """Secure machine-to-machine intake used by an authorized source connector or webhook."""
    with database() as connection:
        project = connection.execute(
            "SELECT * FROM automation_projects WHERE id=? AND source_secret=?", (project_id, source_secret)
        ).fetchone()
    if not project:
        raise HTTPException(404, "Automatic inbox not found")
    if project["status"] != "active" or not project["monitor_new"]:
        raise HTTPException(409, "This clipping connection is paused")
    job_id = uuid.uuid4().hex
    input_path = await store_upload(video, job_id)
    jobs[job_id] = {"id": job_id, "status": "queued", "progress": 0, "message": "New source video received automatically",
                    "outputs": [], "created_at": int(time.time()), "expires_at": int(time.time() + max(JOB_TTL_SECONDS, 86400))}
    save_job_state(job_id)
    add_event(project_id, "A new source video was received automatically", "success")
    background_tasks.add_task(process_job, job_id, input_path, project["page_name"], project["streamer_name"],
                              project["clip_count"], project["clip_length"], project["user_id"], project_id)
    return JSONResponse({"accepted": True, "job_id": job_id}, status_code=202)


@app.post("/api/jobs")
async def create_job(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(...),
    page_name: str = Form("My Clips Page"),
    streamer_name: str = Form("Streamer"),
    clip_count: int = Form(3),
    clip_length: int = Form(30),
) -> JSONResponse:
    if clip_count < 1 or clip_count > 5:
        raise HTTPException(400, "Choose between 1 and 5 clips")
    if clip_length < 3 or clip_length > 90:
        raise HTTPException(400, "Clip length must be between 3 and 90 seconds")
    if not (video.content_type or "").startswith("video/"):
        raise HTTPException(400, "Please upload a video file")

    job_id = uuid.uuid4().hex
    job_dir = WORK_ROOT / job_id
    job_dir.mkdir(parents=True)
    suffix = Path(video.filename or "video.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
        suffix = ".mp4"
    input_path = job_dir / f"source{suffix}"

    total = 0
    with input_path.open("wb") as destination:
        while chunk := await video.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                destination.close()
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(413, f"Video is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
            destination.write(chunk)
    await video.close()

    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "progress": 0,
        "message": "Video uploaded and queued",
        "outputs": [],
        "created_at": int(time.time()),
        "expires_at": int(time.time() + JOB_TTL_SECONDS),
    }
    save_job_state(job_id)
    background_tasks.add_task(
        process_job, job_id, input_path, page_name, streamer_name, clip_count, clip_length
    )
    return JSONResponse(jobs[job_id], status_code=202)


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = jobs.get(job_id) or load_job_state(job_id)
    if not job:
        raise HTTPException(404, "Job not found or its temporary files have been deleted")
    return job


@app.get("/api/jobs/{job_id}/files/{filename}")
async def get_file(job_id: str, filename: str) -> FileResponse:
    if filename not in {f"clip-{index}.mp4" for index in range(1, 6)}:
        raise HTTPException(404, "File not found")
    path = WORK_ROOT / job_id / filename
    if not path.is_file():
        raise HTTPException(404, "File not found or already deleted")
    return FileResponse(path, media_type="video/mp4", filename=filename)


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> dict:
    jobs.pop(job_id, None)
    shutil.rmtree(WORK_ROOT / job_id, ignore_errors=True)
    return {"deleted": True}


app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="site")
