FROM python:3.11-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies first (cached layer — src not needed yet)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and demo data, then install the project itself
COPY README.md ./
COPY src/ ./src/
COPY demo_data/ ./demo_data/
RUN uv sync --frozen --no-dev

# Point the app at the demo DB
ENV SPEND_SLEUTH_DB=/app/demo_data/demo.db

EXPOSE 8501

CMD ["uv", "run", "streamlit", "run", "src/spend_sleuth/app.py", \
     "--server.port=8501", \
     "--server.headless=true", \
     "--server.address=0.0.0.0"]
