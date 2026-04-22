FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NODE_ENV=production

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nodejs tini \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY frontend/server.mjs ./frontend/server.mjs
COPY frontend/package.json ./frontend/package.json
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /data/transcodes /data/helper_releases /media

EXPOSE 4173 8000

ENTRYPOINT ["tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
