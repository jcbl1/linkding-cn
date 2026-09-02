#!/usr/bin/env bash

# Make sure Chromium is installed
uv run playwright install chromium

# Build frontend assets
npm run build

# Run E2E tests
uv run manage.py test bookmarks.tests_e2e --pattern="e2e_test_*.py"
