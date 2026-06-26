# -------------------------------------------------------
# Dockerfile — VCBridge Bot
# Python 3.11-slim + FFmpeg + PulseAudio
# -------------------------------------------------------
FROM python:3.11-slim

LABEL maintainer="VCBridge Bot"
LABEL description="Telegram Voice Chat Bridge Bot with Py-TgCalls"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        pulseaudio \
        kmod \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Create working directory
WORKDIR /app

# Install Python dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Create logs directory
RUN mkdir -p logs

# Expose no ports (bot uses long-polling)

# Default command
CMD ["python", "-u", "bot.py"]
