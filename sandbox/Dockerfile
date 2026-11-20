FROM python:3.11-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GOPROXY=off \
    CARGO_NET_OFFLINE=true

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build clang clang-tidy clang-format cppcheck shellcheck \
    default-jdk-headless nodejs npm golang-go rustc cargo git patch \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir pytest ruff bandit

COPY runner.py /opt/dex-sandbox/runner.py
RUN chmod 0555 /opt/dex-sandbox/runner.py
WORKDIR /workspace
ENTRYPOINT ["python", "/opt/dex-sandbox/runner.py"]
