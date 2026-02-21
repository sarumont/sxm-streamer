FROM python:3.9-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get purge -y --auto-remove && rm -rf /var/lib/apt/lists/*

COPY . /tmp/sxm-streamer
RUN pip install --no-cache-dir -r /tmp/sxm-streamer/requirements.txt \
    && pip install --no-cache-dir --no-deps /tmp/sxm-streamer \
    && rm -rf /tmp/sxm-streamer

EXPOSE 9999/tcp
CMD ["sxm-streamer"]
