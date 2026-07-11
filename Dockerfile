# Use a lightweight Python 3.11 slim image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Create and set working directory
WORKDIR /app

# Install system dependencies (including tzdata for timezone support in schedules)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install -r requirements.txt

# Create a non-root user and group
RUN addgroup --system botgroup && adduser --system --group botuser

# Copy the rest of the application code
COPY . .

# Switch to the non-root user
USER botuser

# Command to run the application
CMD ["python", "-m", "bot.main"]
