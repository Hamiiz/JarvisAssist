#!/bin/bash
# A simple deployment script for updating the bot on a Linux server using Docker.

set -e

echo "🚀 Starting deployment..."

# Pull latest code
echo "📦 Pulling latest changes from Git..."
git pull origin main

# Rebuild the Docker image and start the container
echo "🐳 Rebuilding and restarting Docker containers..."
docker compose up -d --build

# Prune old images to save disk space
echo "🧹 Cleaning up old Docker images..."
docker image prune -f

echo "✅ Deployment complete! The bot should be running."
echo "Use 'docker logs -f hmassassistant_bot' to view the logs."
