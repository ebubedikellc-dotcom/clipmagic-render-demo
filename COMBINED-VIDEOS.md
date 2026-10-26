# Combined videos

Open `/combined.html` inside the ClipMagic engine site. The normal clipping screen links to this workspace.

Signed-in customers can keep up to five lower videos, select one, upload a source video, and render a 540×960 split-screen preview. The source is above and the saved video below; a shorter lower video loops. Audio can come from either video or be muted. Outputs include page branding and a visual follow instruction. Caption and signature appear below the preview; the clickable profile link opens the chosen social profile, not an automatic follow action.

Captions preserve supplied source text and add a customer-written observation or a varied discussion question plus an optional signature. No paid AI calls are added, and this mode does not claim to understand video content or rewrite it semantically.

A combined automation project reuses the existing authorized source inbox and publishing queue. Its inbox accepts multipart `video` and optional `source_caption`. A profile/account URL alone does not monitor or retrieve new posts; an authorized source connector is still required. No platform scraping or access-control bypass is implemented.

Both modes use the same customer's existing social API authorizations and saved destination links. Their video sources, processing rules, captions, projects and queues remain separate. Uploads into normal clipping never feed combined-video projects, and combined uploads never trigger normal clipping.

The combined workspace has destination-link fields, Save link and official Connect/Reconnect controls for Facebook, Instagram, YouTube, TikTok, X and Snapchat. Connect returns to this workspace using a marker in the signed OAuth state. Existing account tokens and developer-app settings are reused privately. Saving a profile URL does not authorize a new account or change the account represented by an OAuth token.

**Post to all connected accounts** resolves this customer's authorizations when a combined video finishes, including accounts connected later. Existing projects retain selected destinations unless explicitly switched. **Create & auto-post this video** uses the chosen combined connection's saved lower video, caption settings and destinations. Preview remains nonpublishing. Each connection shows per-platform delivery status.

Pause prevents new queued posts from starting; an upload already in progress may finish. Deleting a connection cancels queued/retry/blocked posts. No social API credentials, lower videos or production settings are copied between customers.

Preview never publishes. The separately labelled upload-and-auto-post action uses saved connected destinations. Existing platform restrictions and authorization requirements still apply. Normal clipping projects retain their processing path.

Lower assets are stored below `CLIPMAGIC_WORK_ROOT/_combined`. Persistent hosting storage is needed to retain them across server replacements. Preview outputs expire after the existing job retention period; automation outputs get 24 hours to publish. Images, captions and outputs are not guaranteed to survive ephemeral hosting restarts. No hosting plan was upgraded by this change.

Validation: `python -m unittest test_combined -v`, `node --check combined.js`. Tests use an isolated temporary database and generated video fixtures; they do not send social posts.

Posting integration checks: `python -m unittest test_posting_accounts.PostingAccountTests -v`, `node test_job_client.js`. These cover shared authorizations, all six destination queues, later connections, independent processing paths, private job ownership, pause, migration and safe OAuth return routing. Actual publication remains subject to account permissions and platform approval; these local checks do not send live social posts.
