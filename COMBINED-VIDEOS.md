# Combined videos

Open `/combined.html` inside the ClipMagic engine site. The normal clipping screen links to this workspace.

Signed-in customers can keep up to five lower videos, select one, upload a source video, and render a 540×960 split-screen preview. The source is above and the saved video below; a shorter lower video loops. Audio can come from either video or be muted. Outputs include page branding and a visual follow instruction. Caption and signature appear below the preview; the clickable profile link opens the chosen social profile, not an automatic follow action.

Captions preserve supplied source text and add a customer-written observation or a varied discussion question plus an optional signature. No paid AI calls are added, and this mode does not claim to understand video content or rewrite it semantically.

A combined automation project reuses the existing authorized source inbox and publishing queue. Its inbox accepts multipart `video` and optional `source_caption`. A profile/account URL alone does not monitor or retrieve new posts; an authorized source connector is still required. No platform scraping or access-control bypass is implemented.

Preview never publishes. The separately labelled upload-and-auto-post action uses saved connected destinations. Existing platform restrictions and authorization requirements still apply. Normal clipping projects retain their processing path.

Lower assets are stored below `CLIPMAGIC_WORK_ROOT/_combined`. Persistent hosting storage is needed to retain them across server replacements. Preview outputs expire after the existing job retention period; automation outputs get 24 hours to publish. Images, captions and outputs are not guaranteed to survive ephemeral hosting restarts. No hosting plan was upgraded by this change.

Validation: `python -m unittest test_combined -v`, `node --check combined.js`. Tests use an isolated temporary database and generated video fixtures; they do not send social posts.
