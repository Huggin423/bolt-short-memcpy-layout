FROM ubuntu:24.04@sha256:2198e4c65076e9cb0b3d695219df667c0e3b0072da64ca3c2648aca17dcecaf5
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 binutils libstdc++6 zlib1g libzstd1 \
    && rm -rf /var/lib/apt/lists/*
ENV LC_ALL=C PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /course
COPY payload/ /course/
ENTRYPOINT ["python3", "/course/reproduce.py"]
CMD ["smoke"]
