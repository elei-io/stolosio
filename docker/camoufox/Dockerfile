FROM python:3.12-bookworm

ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libasound2 \
        libdbus-glib-1-2 \
        libgtk-3-0 \
        libx11-xcb1 \
        libxt6 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir camoufox "playwright==1.55.0"
RUN python -m camoufox fetch

WORKDIR /app
COPY server.py ./server.py

EXPOSE 1234
CMD ["python", "server.py"]
