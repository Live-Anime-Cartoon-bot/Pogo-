#!/bin/bash
# Live Recorder Bot — Quick Start
# Requirements: Python 3.10+, ffmpeg, yt-dlp

set -e

echo "📦 Installing dependencies..."
pip install -r requirements.txt -q

if [ ! -f .env ]; then
    echo "⚠️  No .env file found. Copy .env.example and fill in your credentials:"
    echo "    cp .env.example .env"
    exit 1
fi

echo "🤖 Starting bot..."
python main.py
