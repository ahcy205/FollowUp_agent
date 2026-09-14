# ---- 1) Build the React frontend
FROM node:24-slim AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2) Python runtime serving API + built SPA
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production
WORKDIR /app/backend
RUN useradd --create-home --uid 10001 app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/ ./
COPY --from=web /app/backend/static ./static
RUN chmod +x start.sh && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["./start.sh"]
