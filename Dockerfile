# ==============================================================================
# DiffQA Production Docker Container
# Pre-packaged with Python 3.12, Playwright, Chromium, and CLI tools
# ==============================================================================

FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -e .

# Pre-install Playwright Chromium browser
RUN playwright install chromium

# Copy application codebase
COPY . /app/

# Environment defaults
ENV PYTHONUNBUFFERED=1
ENV DIFFQA_PROVIDER=mock
ENV HEADLESS=true

# Entrypoint
ENTRYPOINT ["diffqa"]
CMD ["demo", "--headless"]
