# Mycelium off-chain indexer — read API + (optional) ingest worker.
#
# Build from the REPO ROOT so the local mycelium-sdk / mycelium-cli packages are
# in the build context (mirrors how compiler/Dockerfile expects a root context):
#
#   docker build -f indexer/Dockerfile -t mycelium-indexer:latest .
#   docker run --rm -p 8080:8080 \
#     -e FIREBASE_CREDENTIALS_JSON="$(cat serviceAccount.json)" \
#     -e MYCELIUM_BOARD_ADDRESS=CAIGNIJBUA4GKKJBIO27JOAELZQ4KA7AYMB2F5C3W2D3DGQANZZCJGEH \
#     -e RUN_INDEXER_WORKER=1 \
#     mycelium-indexer:latest
#
# The container serves the FastAPI read API on $PORT (default 8080). With
# RUN_INDEXER_WORKER=1 it also runs the Firestore ingest loop in a background
# thread, so a single service both serves discovery and keeps the cache fresh.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install the local SDK + CLI first (their deps rarely change → better caching).
COPY sdk/ /app/sdk/
COPY cli/ /app/cli/
RUN pip install ./sdk ./cli

# Indexer-specific deps.
COPY indexer/requirements.txt /app/indexer/requirements.txt
RUN pip install -r /app/indexer/requirements.txt

# Indexer source last (changes most often).
COPY indexer/ /app/indexer/

EXPOSE 8080

# $PORT is provided by Render (and overridable locally); shell form expands it.
CMD ["sh", "-c", "uvicorn indexer.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
