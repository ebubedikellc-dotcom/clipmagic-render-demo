# ClipMagic Automation Engine

This repository contains a Render-ready clipping and publishing service.

- Main site: `/`
- Owner control panel: `/control-panel.html`
- Registered user workspaces and up to seven saved automation projects.
- FFmpeg clipping, scene-change selection, vertical formatting and page-name/FOLLOW branding.
- Optional speech-based title generation when `OPENAI_API_KEY` is configured.
- A persistent publishing queue with retries, delivery status and automatic file cleanup.
- Facebook Page, Instagram Reels, YouTube and TikTok posting adapters.
- Official one-tap Meta, Google/YouTube and TikTok OAuth connection flows.
- Automatic OAuth token renewal for YouTube and TikTok.
- Pause, resume, delete, manual test upload and a secure automatic source-video inbox for every project.

## One-time owner configuration

Register ClipMagic as a developer app with Meta, Google and TikTok, then set the
`META_CLIENT_ID`, `META_CLIENT_SECRET`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `TIKTOK_CLIENT_ID` and `TIKTOK_CLIENT_SECRET`
environment variables. The exact callback addresses are:

- `https://clipmagic-engine.onrender.com/api/oauth/facebook/callback`
- `https://clipmagic-engine.onrender.com/api/oauth/instagram/callback`
- `https://clipmagic-engine.onrender.com/api/oauth/youtube/callback`
- `https://clipmagic-engine.onrender.com/api/oauth/tiktok/callback`

Every destination needs a valid official access token with posting permission. TikTok Direct Post and unverified YouTube projects remain subject to platform review. A public page URL alone does not authorize or provide an official download of its source videos; a source connector must deliver media the user owns or is allowed to reuse. Free Render storage is temporary, so production should use an always-on service with persistent storage/database.
