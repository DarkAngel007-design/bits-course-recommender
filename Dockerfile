# Stage 1: build the dashboard
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx vite build

# Stage 2: API + published snapshot (no PDFs needed at runtime)
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY backend ./backend
COPY ingestion ./ingestion
RUN pip install --no-cache-dir ".[llm,postgres]"
COPY data/published ./data/published
COPY tests/fixtures ./tests/fixtures
COPY --from=web /web/dist ./frontend/dist
ENV DATA_DIR=/app/data/published
EXPOSE 8000
CMD ["uvicorn", "backend.app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
