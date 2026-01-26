# Using slim base image as requested
FROM python:3.11-slim-bookworm

# Env vars
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install system deps
# Removed git as it shouldn't be needed in production runtime (usually)
# unless one of the python packages needs it to install. 
# Keeping curl for healthcheck.
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create user
RUN useradd -m -u 1000 appuser

# Workdir
WORKDIR /app

# Install Python deps
# Copy requirements first to leverage cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install playwright

# Install Browsers (Chromium only)
RUN playwright install --with-deps chromium

# Copy App code
# This is done last so changes to code don't invalidate dep layers
COPY app/ ./app/

# Create data directories with permissions
RUN mkdir -p /music /data /app/downloads && \
    chown -R appuser:appuser /music /data /app/downloads /app

# Switch user
USER appuser

# Expose
EXPOSE 8080

# Entrypoint
# Default to web mode, but can be overridden
CMD ["python", "-m", "app.main", "web", "--host", "0.0.0.0", "--port", "8080"]
