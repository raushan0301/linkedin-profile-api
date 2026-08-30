# syntax=docker/dockerfile:1

# Use slim Python image — full image adds ~400MB we don't need
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy dependency manifest first (Docker layer caching — dependencies
# only reinstall when requirements.txt changes, not on every code edit)
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Create a non-root user for security
RUN adduser --disabled-password --gecos "" appuser
USER appuser

# Expose port (Railway injects $PORT at runtime)
EXPOSE 8000

# Run the ASGI server
# - --host 0.0.0.0 is required for container networking
# - $PORT is set by Railway; falls back to 8000 locally
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
