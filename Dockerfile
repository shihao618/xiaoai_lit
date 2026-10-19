
ARG BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim

# ---------- 阶段 1：builder（编译依赖） ----------
FROM ${BASE_IMAGE} AS builder
WORKDIR /app
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list 2>/dev/null || true \
    && apt-get update && apt-get install -y --no-install-recommends \
       gcc g++ && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install \
    -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# ---------- 阶段 2：runtime（运行时） ----------
FROM ${BASE_IMAGE} AS runtime
# 运行库（ddddocr/onnxruntime 图形依赖）+ tzdata；编译工具链不进本阶段
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list 2>/dev/null || true \
    && apt-get update && apt-get install -y --no-install-recommends \
       libgl1 libglib2.0-0 tzdata curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r appuser && useradd -r -g appuser appuser
COPY --from=builder /install /usr/local
WORKDIR /app
COPY --chown=appuser:appuser . .
RUN mkdir -p /app/cache /app/data && chown -R appuser:appuser /app/cache /app/data
USER appuser
ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai CACHE_DIR=/app/cache DATA_DIR=/app/data
EXPOSE 10379
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:10379/api/status || exit 1
CMD ["python", "main.py"]