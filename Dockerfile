FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

WORKDIR /app
COPY . /app/
# --with-deps: pip may pull a newer Playwright than the base image's, needing newer system libraries.
RUN pip install --no-cache-dir . && playwright install --with-deps chromium

ENV PYTHONUNBUFFERED=1 \
    DIFFQA_PROVIDER=mock \
    HEADLESS=true

ENTRYPOINT ["diffqa"]
CMD ["demo", "--headless"]
