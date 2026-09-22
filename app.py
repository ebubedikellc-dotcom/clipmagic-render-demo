from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


BASE_DIR = Path(__file__).resolve().parent
WORK_ROOT = Path(os.environ.get("CLIPMAGIC_WORK_ROOT", "/tmp/clipmagic-jobs"))
MAX_UPLOAD_BYTES = int(os.environ.get("CLIPMAGIC_MAX_UPLOAD_MB", "250")) * 1024 * 1024
JOB_TTL_SECONDS = int(os.environ.get("CLIPMAGIC_JOB_TTL_SECONDS", "3600"))
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
OWNER_EMAIL = os.environ.get("CLIPMAGIC_OWNER_EMAIL", "Ebubedikellc@gmail.com").strip().lower()
OWNER_PASSWORD = os.environ.get("CLIPMAGIC_OWNER_PASSWORD", "")
SESSION_SECRET = os.environ.get("CLIPMAGIC_SESSION_SECRET") or hashlib.sha256(
    f"clipmagic-session|{OWNER_PASSWORD}".encode()
).hexdigest()
SESSION_COOKIE = "clipmagic_owner_session"

WORK_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="ClipMagic Engine", version="1.0.0")
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


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


def update_job(job_id: str, **changes) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(changes)


def process_job(job_id: str, input_path: Path, page_name: str, streamer_name: str,
                clip_count: int, clip_length: int) -> None:
    job_dir = input_path.parent
    try:
        update_job(job_id, status="processing", progress=5, message="Reading the video")
        duration = probe_duration(input_path)
        actual_length = max(3, min(clip_length, int(duration)))
        usable = max(0.0, duration - actual_length)

        if clip_count == 1 or usable <= 0:
            starts = [0.0]
        else:
            starts = [usable * (index + 1) / (clip_count + 1) for index in range(clip_count)]

        outputs = []
        for index, start in enumerate(starts, 1):
            output_name = f"clip-{index}.mp4"
            output_path = job_dir / output_name
            title = safe_text(f"{streamer_name} highlight {index}", "New highlight")
            brand = safe_text(f"{page_name}   FOLLOW", "FOLLOW")
            vf = (
                "scale=720:1280:force_original_aspect_ratio=decrease,"
                "pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"drawtext=fontfile={FONT_PATH}:text='{title}':"
                "fontcolor=white:fontsize=34:borderw=3:bordercolor=black:"
                "x=(w-text_w)/2:y=60,"
                f"drawtext=fontfile={FONT_PATH}:text='{brand}':"
                "fontcolor=white:fontsize=30:borderw=3:bordercolor=black:"
                "box=1:boxcolor=0x0b1626cc:boxborderw=18:"
                "x=(w-text_w)/2:y=h-text_h-80"
            )
            run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{start:.3f}", "-i", str(input_path), "-t", str(actual_length),
                "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output_path),
            ])
            outputs.append({
                "name": output_name,
                "title": f"{streamer_name} highlight {index}",
                "seconds": actual_length,
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


@app.on_event("startup")
async def startup() -> None:
    threading.Thread(target=cleanup_expired, daemon=True).start()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "engine": "ffmpeg", "temporary_storage": True}


def login_page(message: str = "") -> HTMLResponse:
    template = (BASE_DIR / "owner-login.html").read_text(encoding="utf-8")
    message_html = f'<div class="error">{message}</div>' if message else ""
    return HTMLResponse(template.replace("{{MESSAGE}}", message_html))


@app.get("/owner-login")
async def owner_login_page(request: Request):
    if valid_owner_session(request):
        return RedirectResponse("/control-panel.html", status_code=303)
    configured = bool(OWNER_PASSWORD)
    return login_page("Owner password is not configured yet." if not configured else "")


@app.post("/owner-login")
async def owner_login(email: str = Form(...), password: str = Form(...)):
    configured = bool(OWNER_PASSWORD)
    if not configured:
        return login_page("Owner password is not configured yet.")
    email_ok = hmac.compare_digest(email.strip().lower(), OWNER_EMAIL)
    password_ok = hmac.compare_digest(password, OWNER_PASSWORD)
    if not (email_ok and password_ok):
        return login_page("The email or password is incorrect.")
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
    background_tasks.add_task(
        process_job, job_id, input_path, page_name, streamer_name, clip_count, clip_length
    )
    return JSONResponse(jobs[job_id], status_code=202)


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
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
