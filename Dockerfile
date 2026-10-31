FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY requirements-naming.txt install_naming_models.py ./
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake \
    && CMAKE_ARGS='-DGGML_NATIVE=OFF -DGGML_OPENMP=OFF' CMAKE_BUILD_PARALLEL_LEVEL=2 pip install --no-cache-dir -r requirements-naming.txt \
    && python -c 'import llama_cpp, faster_whisper' \
    && apt-get purge -y --auto-remove build-essential cmake \
    && rm -rf /var/lib/apt/lists/*
ENV CLIPMAGIC_MODELS_DIR=/opt/clipmagic-models
RUN python install_naming_models.py
COPY . .

ENV PORT=10000
ENV CLIPMAGIC_WORK_ROOT=/tmp/clipmagic-jobs
ENV CLIPMAGIC_JOB_TTL_SECONDS=3600

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
