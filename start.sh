#!/bin/bash
set -e

echo "[start] launching ComfyUI from Network Volume..."
cd /workspace/ComfyUI
python main.py \
    --listen 127.0.0.1 \
    --port 8188 \
    --preview-method none \
    --output-directory /workspace/ComfyUI/output \
    > /tmp/comfyui.log 2>&1 &

echo "[start] ComfyUI PID: $!"

echo "[start] launching serverless handler..."
cd /app
exec python handler.py
