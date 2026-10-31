"""Download pinned, public model weights at image build time, not during a user's job."""
import os
from pathlib import Path
from huggingface_hub import hf_hub_download, snapshot_download

root = Path(os.environ.get('CLIPMAGIC_MODELS_DIR', '/opt/clipmagic-models'))
root.mkdir(parents=True, exist_ok=True)
hf_hub_download('Qwen/Qwen2.5-0.5B-Instruct-GGUF', 'qwen2.5-0.5b-instruct-q4_k_m.gguf',
                revision='9217f5db79a29953eb74d5343926648285ec7e67', local_dir=str(root))
snapshot_download('Systran/faster-whisper-tiny', revision='d90ca5fe260221311c53c58e660288d3deb8d356',
                  local_dir=str(root / 'speech'),
                  allow_patterns=['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.*'])
print('Local naming models installed.')
