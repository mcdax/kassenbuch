FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    KASSENBUCH_DATA=/data

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY kassenbuch ./kassenbuch
RUN pip install .

ENTRYPOINT ["kassenbuch"]
CMD ["poll"]
