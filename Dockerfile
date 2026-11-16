FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    REPOTRAIL_DATA_DIR=/var/lib/repotrail
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 repotrail \
    && useradd --uid 10001 --gid 10001 --create-home repotrail \
    && mkdir -p /app /var/lib/repotrail && chown -R repotrail:repotrail /app /var/lib/repotrail
WORKDIR /app
COPY --chown=repotrail:repotrail . .
RUN pip install --no-cache-dir '.[embeddings]' \
    && python -c "from fastembed import TextEmbedding; assert TextEmbedding.list_supported_models()"
USER repotrail
EXPOSE 8765
CMD ["repotrail", "serve", "--team", "--host", "0.0.0.0", "--port", "8765"]
