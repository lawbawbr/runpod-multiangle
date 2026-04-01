FROM runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04

# Deps
RUN pip install runpod httpx boto3 --no-cache-dir

# Copy handler and templates
WORKDIR /app
COPY handler.py .
COPY templates/multiangle.api ./templates/multiangle.api

# Start script: launch ComfyUI (from Network Volume) then handler
COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
