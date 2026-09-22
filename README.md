# ClipMagic Working Engine

This repository now contains a real FastAPI + FFmpeg clipping service for Render.

- Main site: `/`
- Owner control panel: `/control-panel.html`
- Uploads user-owned video files.
- Creates 1–5 real vertical MP4 clips.
- Adds the streamer title, page name and FOLLOW branding.
- Provides playable/downloadable results.
- Deletes temporary uploads and clips automatically after one hour.
- Keeps social publishing locked until official OAuth authorization is configured.

The Render Blueprint retains the original static demonstration and adds the working Docker service as `clipmagic-engine`.
