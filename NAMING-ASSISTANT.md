# ClipMagic lightweight naming memory

This version uses Python's standard library, supplied captions and explicit teaching examples. It installs no language model, speech model, inference library or external AI API. Existing video processing still uses FFmpeg.

## How it learns

The first title comes from a sentence in the current caption. The owner can save up to 200 description/title pairs separately for Normal and Combined. Matching descriptions reuse the approved title (case and punctuation ignored; numbers and negation preserved). For new descriptions, word retention statistics learned from approved pairs help rank sentences and hashtags in the current caption. It never borrows new facts from a different example. Generating its own output does not count as approval or learning.

This is lightweight extractive learning and remembered corrections, not human-like understanding or a generative AI trained from scratch. It does not watch frames or transcribe audio. If a source connector supplies no caption, the result is a neutral creator/clip label with an explanation. Multiple clips from the same source may share a title because the caption describes the source, not each segment. Review titles for clip relevance.

## Use

Open `/naming.html` after signing into ClipMagic. Choose a workflow and naming style (direct, brief or creator first), add examples, and save. Try a caption in the preview box; nothing is posted. Download a JSON teaching backup and restore it through this page. Backup import requires an explicit Save before writing to the server.

Normal preview uploads accept a source description. Automatic intake accepts `source_caption`. Combined titles keep the existing caption, addition and signature. Both workflows use the existing posting authorizations; naming does not connect or authorize social accounts.

## Hosting and data

No paid hosting change is required by the naming code. Free Render limits and temporary storage still apply to the existing application. Examples share the existing SQLite database; download backups because a restart or deploy can erase ephemeral storage. Back up existing live app data before deploying. This change does not migrate a live database or upgrade a hosting plan. The old Qwen installer files are unused; the Dockerfile does not run them or install their dependencies.

## Validation

Tests cover customer/workflow isolation, learned correction reuse, preservation of negation, feedback-driven sentence selection, caption-free fallback, naming styles, private preview, input validation, existing clipping and combined captions. Run `python -m unittest test_naming test_combined test_posting_accounts` and `node --test test_job_client.js`.
