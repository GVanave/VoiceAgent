FROM python:3.11-slim

# INSTALL_LOCAL=true bundles faster-whisper for offline transcription (TRANSCRIBER=local); the image is much larger.
ARG INSTALL_LOCAL=false
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY requirements.txt requirements-local.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$INSTALL_LOCAL" = "true" ]; then pip install --no-cache-dir -r requirements-local.txt; fi

COPY app ./app
RUN useradd --create-home agent
USER agent

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
