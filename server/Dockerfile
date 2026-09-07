# Phase W: base dependencies now include pyannote.audio/torchcodec (the
# speaker-embedding model used for the live in-process instant-recognition
# check, app/services/diarization/embedding.py) — this image is meaningfully
# heavier than before as a result, a deliberate tradeoff for lower live
# recognition latency (see the plan's Phase W1 for the reasoning). ffmpeg IS
# needed here despite the embedding path only ever handling raw PCM/WAV —
# found empirically, not assumed: torchcodec (pyannote 4.x's audio-IO
# backend) dynamically loads FFmpeg's own shared libraries (libavcodec etc.)
# at runtime to decode *any* audio, even a plain WAV built in-process,
# raising "Could not load libtorchcodec" without them installed. Confirmed
# by actually running the in-process embedding path against the isolated
# corella-verify stack before assuming ffmpeg could be skipped here.
FROM python:3.11-slim

WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev build-essential ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
# Same CPU-only torch index as Dockerfile.worker (see its own comment) —
# needed here now too since pyannote.audio/torchcodec moved to base deps.
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    --extra-index-url https://pypi.org/simple \
    -e .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
