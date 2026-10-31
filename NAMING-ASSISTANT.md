# Local Qwen naming assistant

Qwen2.5-0.5B-Instruct Q4_K_M creates post titles (80 characters maximum) and up to five relevant hashtags. A CPU/int8 Faster Whisper tiny model transcribes the actual finished clip first. Combined videos use only the first rendered duration of the top/source input, even if the selected output audio is from the lower video or muted. Existing combined captions, additions and signatures are retained.

No OpenAI account, API call or per-title inference charge is used. Model weights download from pinned Hugging Face revisions during the Docker build. User videos and transcripts are processed locally. Speech and text models run in separate subprocesses, sequentially after FFmpeg; processes exit to release memory. Inference has a timeout and the assistant serializes jobs. It does not visually understand silent video; a supplied source caption is the fallback evidence. Tiny speech recognition can make errors, especially with accents or noisy audio, so review initial results.

## Hosting

The Dockerfile installs the runtime and model weights. This makes the image larger. `/health` includes `naming_assistant` with installed/ready/reason. The assistant refuses to load on a container limited to less than 1536 MiB rather than risking the existing video service. Start with a 2 GB or larger service and benchmark real clips; concurrent posting or long inputs may need more memory. No hosting plan is changed by this patch.

`CLIPMAGIC_MODELS_DIR` defaults to `/opt/clipmagic-models` (built into the image). Set `CLIPMAGIC_NAMING_ENABLED=0` to disable inference. Keep `CLIPMAGIC_MAX_ACTIVE_JOBS=1` on a small service. Models do not belong under the job cleanup directory.

Use permanent storage for `CLIPMAGIC_WORK_ROOT` and `CLIPMAGIC_DATABASE_PATH`. Back up/migrate existing data before changing paths or redeploying an ephemeral service. Keep existing encryption/session keys unchanged during migration. This patch does not authorize a paid upgrade or migrate any live database.

## Preferences and honest fallback

Open `/naming.html` after signing into ClipMagic. Normal and combined workflows each save a private style and up to six paired clip-description/title examples. The newest two examples are included as references in future generation. This is saved prompt context, not weight training. Preferences are stored in the existing customer database and isolated by user and workflow.

Each output records `title_source` and `naming_warning`. Unavailable models, insufficient memory, timeout, or missing evidence use a neutral fallback (normal) or preserve the existing combined caption. A fallback is never marked as a Qwen result. Social account authorization and source collection remain separate requirements.

## Checks

Run `python -m unittest test_naming test_combined test_posting_accounts` and `node --test test_job_client.js`. For real inference, install `requirements-naming.txt`, set `CLIPMAGIC_MODELS_DIR`, and run `python install_naming_models.py`; then invoke `naming_worker.py` with `transcribe` or `generate` and a JSON payload over stdin. Do not put customer transcripts in public test artifacts.

## Local validation (2026-10-31)

29 Python tests and 3 JavaScript tests pass. Real Faster Whisper transcription and Qwen generation completed end-to-end using the public JFK sample from whisper.cpp. On the development machine the short sample took about 4.7 seconds and the largest model subprocess used about 709 MiB RSS; Render performance is not yet measured. Cooking and goalkeeper text samples also generated titles. Generated hashtags are filtered against the evidence text to remove unrelated tags and formatting placeholders. Titles can still be generic or mistaken; this tiny model is not a guarantee of factual accuracy. The live free service has not been upgraded or redeployed.

The CPU runtime is compiled against the image OS rather than using an incompatible prebuilt wheel. PyAV is pinned to a version compatible with Faster Whisper. The Docker build imports both runtimes before downloading models.
