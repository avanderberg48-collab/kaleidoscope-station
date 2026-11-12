FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && apt-get clean
WORKDIR /app
COPY engine.py /app/engine.py
COPY bootstrap.py /app/bootstrap.py
RUN mkdir -p /app/radio/music /app/radio/live /app/radio/incoming
ENV RADIO_DIR=/app/radio PYTHONUNBUFFERED=1
EXPOSE 10000
CMD ["sh", "-c", "python3 /app/bootstrap.py; exec python3 -u /app/engine.py"]
