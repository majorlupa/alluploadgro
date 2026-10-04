FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ALLEGRO_STATE_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 allegro \
    && useradd --uid 10001 --gid allegro --create-home allegro \
    && mkdir /data \
    && chown allegro:allegro /data
COPY allegro ./allegro
USER allegro
ENTRYPOINT ["python", "-m", "allegro"]
CMD ["--help"]
