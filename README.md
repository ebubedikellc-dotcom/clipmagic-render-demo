# ClipMagic Automation Engine

This repository contains a Render-ready clipping and publishing service.

- Main site: `/`
- Owner control panel: `/control-panel.html`
- Registered user workspaces and up to seven saved automation projects.
- FFmpeg clipping, scene-change selection, vertical formatting and page-name/FOLLOW branding.
- Optional speech-based title generation when `OPENAI_API_KEY` is configured.
- A persistent publishing queue with retries, delivery status and automatic file cleanup.
- Facebook Page, Instagram Reels, YouTube and TikTok posting adapters.
- Pause, resume, delete and authorized source-video ingestion controls.

Every destination needs a valid official access token with posting permission. TikTok Direct Post and unverified YouTube projects remain subject to platform review. A public page URL alone does not authorize or provide an official download of its source videos; a source connector must deliver media the user owns or is allowed to reuse. Free Render storage is temporary, so production should use an always-on service with persistent storage/database.
