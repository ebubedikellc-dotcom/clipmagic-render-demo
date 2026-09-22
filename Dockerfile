FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PORT=10000
ENV CLIPMAGIC_WORK_ROOT=/tmp/clipmagic-jobs
ENV CLIPMAGIC_JOB_TTL_SECONDS=3600

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
