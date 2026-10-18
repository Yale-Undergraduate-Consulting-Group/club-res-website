#!/bin/bash
# Start the YUCG Outreach backend
cd "$(dirname "$0")"

# Activate venv if it exists
if [ -d "venv" ]; then
  source venv/bin/activate
fi

# Run with python -m uvicorn (works even if uvicorn not in PATH)
python -m uvicorn main:app --reload --port 8000
