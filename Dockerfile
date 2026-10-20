# LIT 课表提醒服务镜像：多阶段构建（builder 编译依赖 → runtime 仅运行依赖）
# 构建：docker compose build（或 docker build -t lit-course-reminder .），说明见 README。

# 基础镜像默认走 DaoCloud 国内镜像加速（同步官方 library/python，内容一致）。
# 可覆盖：docker build --build-arg BASE_IMAGE=python:3.11-slim .
ARG BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim

# ---------- 阶段 1：builder —— 编译/安装 Python 依赖，产物在运行阶段复用 ----------
FROM ${BASE_IMAGE} AS builder

# pip 不落缓存、不检查新版本；Python 不写 .pyc（与运行阶段保持一致）
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# ddddocr/onnxruntime 等无预编译 wheel 时需 gcc/g++ 回退源码编译；
# 编译工具链只存在于 builder，不进入运行镜像。
# 说明：apt 包不锁定小版本——slim 基础镜像随 Debian 安全更新滚动，硬锁会导致后续构建必然失败。
# hadolint ignore=DL3008
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list 2>/dev/null || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# 依赖清单由仓库统一维护（固定版本），镜像只消费、不改写；
# gunicorn 同样由 requirements.txt 提供（运行阶段 CMD 依赖它）。
COPY requirements.txt /app/requirements.txt

# --prefix=/install 独立安装目录，随后整体 COPY 到运行阶段 /usr/local
RUN pip install --no-cache-dir --prefix=/install \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt

# ---------- 阶段 2：runtime —— 仅运行时库，非 root 运行 ----------
FROM ${BASE_IMAGE} AS runtime

LABEL org.opencontainers.image.title="lit-course-reminder" \
      org.opencontainers.image.description="洛阳理工学院教务课表提醒服务（Flask + 进程内 APScheduler + 小爱音箱云端原生播报）" \
      org.opencontainers.image.source="https://github.com/shihao618/xiaoai_lit"

# 运行期环境：日志即时输出、不写 .pyc、时区、持久化路径
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    CACHE_DIR=/app/cache \
    DATA_DIR=/app/data

# 运行库：libgl1/libglib2.0-0 为 ddddocr(onnxruntime) 的图形依赖，tzdata 提供 Asia/Shanghai。
# 不再安装 curl：健康检查改用镜像内 python 标准库 urllib（体积更小、攻击面更小）。
# 说明：同 builder，apt 包不锁小版本（slim 基础镜像滚动更新）。
# hadolint ignore=DL3008
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list 2>/dev/null || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       libgl1 libglib2.0-0 tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r appuser \
    && useradd -r -g appuser -d /app -s /usr/sbin/nologin appuser

# 只复制 Python 依赖产物（site-packages + gunicorn 等 console scripts），不复制编译工具链
COPY --from=builder /install /usr/local

WORKDIR /app

# 显式复制运行所需文件（不用 COPY . .：tests/、.venv/、data/、凭证等绝不进镜像）
COPY --chown=appuser:appuser main.py wsgi.py /app/
COPY --chown=appuser:appuser lit/ /app/lit/

# 持久化目录：容器内先创建并归 appuser；命名卷挂载后仍由该用户写入
RUN mkdir -p /app/cache /app/data \
    && chown -R appuser:appuser /app/cache /app/data

# 非 root 运行；保留命名用户 appuser（可读性/排障优于裸 UID，DL3066 为 info 级建议，此处有意保留）
# hadolint ignore=DL3066
USER appuser

EXPOSE 10379

# 健康检查：python 标准库 urllib 探活 /api/status。
# 与 docker-compose.yml 的 healthcheck.test 命令逐字一致；start-period 60s 等待依赖导入与首轮初始化。
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:10379/api/status', timeout=3).status == 200 else 1)"]

# 生产入口：gunicorn 单 worker + gthread 8 线程。
# 应用内含进程内 APScheduler 与内存态（会话/缓存），多 worker 必然重复提醒、会话错乱，故 --workers 1；
# 并发由 8 个 gthread 线程承载。--timeout 180 为 worker 心跳超时（gthread 长请求在线程池执行，不受该值直接约束）。
# gunicorn 由 requirements.txt 安装，勿在运行阶段另外安装。
# --no-control-socket：gunicorn 25.1+ 默认在 $HOME/.gunicorn 建控制套接字，容器内非 root 的 HOME 只读，
# 会打出 "Control server error: Permission denied"，本部署不需要 gunicornc 控制接口，直接禁用。
CMD ["gunicorn","--bind","0.0.0.0:10379","--workers","1","--threads","8","--worker-class","gthread","--graceful-timeout","30","--timeout","180","--access-logfile","-","--error-logfile","-","--no-control-socket","wsgi:app"]
