#!/bin/bash
# Deploy script - push changes to GitHub
# Run this after making changes and committing locally

set -e

REPO="peterkokosharov/rex"

cd /home/petark/runtime-ai

# Check for uncommitted changes
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "⚠️  Uncommitted changes detected. Commit first:"
    git status --short
    exit 1
fi

# Get current branch
BRANCH=$(git branch --show-current)
echo "📦 Deploying branch: $BRANCH"

# Push to origin
git push origin $BRANCH

echo "✅ Deployed to https://github.com/$REPO (branch: $BRANCH)"