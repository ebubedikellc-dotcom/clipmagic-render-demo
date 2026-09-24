# ClipMagic Automation Engine

This repository contains a Render-ready clipping and publishing service.

- Main site: `/`
- Owner control panel: `/control-panel.html`
- Registered user workspaces and up to seven saved automation projects.
- FFmpeg clipping, scene-change selection, vertical formatting and page-name/FOLLOW branding.
- Optional speech-based title generation when `OPENAI_API_KEY` is configured.
- A persistent publishing queue with retries, delivery status and automatic file cleanup.
- Facebook Page, Instagram Reels, YouTube, TikTok, X, Snapchat Spotlight and Dailymotion posting adapters.
- Official one-tap Meta, Google/YouTube, TikTok, X and Snapchat OAuth connection flows.
- Automatic OAuth token renewal for YouTube, TikTok, X and Snapchat.
- Rumble export remains manual because Rumble does not publish an official VOD-upload API.
- Pause, resume, delete, manual test upload and a secure automatic source-video inbox for every project.

## One-time owner configuration

Register ClipMagic as a developer app with Meta, Google, TikTok, X and Snapchat, then enter the
credentials in the owner control panel (or set the corresponding environment variables). Dailymotion
uses each customer's private API key, API secret and profile ID on the main site. The supported
environment variables include
`META_CLIENT_ID`, `META_CLIENT_SECRET`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `TIKTOK_CLIENT_ID` and `TIKTOK_CLIENT_SECRET`
plus `X_CLIENT_ID`, `X_CLIENT_SECRET`, `SNAPCHAT_CLIENT_ID` and `SNAPCHAT_CLIENT_SECRET`.
The exact callback addresses are:

- `https://clipmagic-engine.onrender.com/api/oauth/facebook/callback`
- `https://clipmagic-engine.onrender.com/api/oauth/instagram/callback`
- `https://clipmagic-engine.onrender.com/api/oauth/youtube/callback`
- `https://clipmagic-engine.onrender.com/api/oauth/tiktok/callback`
- `https://clipmagic-engine.onrender.com/api/oauth/x/callback`
- `https://clipmagic-engine.onrender.com/api/oauth/snapchat/callback`

Every destination needs valid official posting permission. TikTok Direct Post, unverified YouTube apps and Snapchat Public Profile access remain subject to platform review or allowlisting. A public page URL alone does not authorize or provide an official download of its source videos; a source connector must deliver media the user owns or is allowed to reuse. Free Render storage is temporary, so production should use an always-on service with persistent storage/database.
