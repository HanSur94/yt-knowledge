FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY ytk ./ytk
COPY scripts ./scripts
COPY DEPLOYED ./DEPLOYED
RUN pip install --no-cache-dir ".[asr]"
RUN mkdir -p /data
VOLUME /data
# Whisper weights live on the volume: downloaded once on first use, not per build.
ENV YTK_DB=/data/ytk.sqlite YTK_THUMBS=/data/thumbs YTK_BIND=0.0.0.0:8499 \
    YTK_WHISPER_DIR=/data/models HF_HOME=/data/models/hf
EXPOSE 8499
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8499/healthz')"
CMD ["ytk"]
