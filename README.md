# LIT 课表提醒服务

[![Tests](https://github.com/shihao618/xiaoai_lit/actions/workflows/tests.yml/badge.svg)](https://github.com/shihao618/xiaoai_lit/actions/workflows/tests.yml)
[![Release & Publish](https://github.com/shihao618/xiaoai_lit/actions/workflows/release.yml/badge.svg)](https://github.com/shihao618/xiaoai_lit/actions/workflows/release.yml)
[![GHCR](https://img.shields.io/badge/image-ghcr.io%2Fshihao618%2Fxiaoai__lit-blue)](https://github.com/shihao618/xiaoai_lit/pkgs/container/xiaoai_lit)

洛阳理工学院教务系统课表提醒 + 小爱音箱语音播报 Docker 应用。

## 简介

- **教务 API 直取课表**：经教务系统 JSON 接口获取周次定位与当周课程（零 HTML 解析），调课等排课差异以教务端数据为准
- **掉线自动重登**：首次登录记住账号密码（持久化到数据卷）；每 `COOKIE_REFRESH_INTERVAL`（默认 30 分钟）检测一次，Cookie 失效自动重登恢复；每个周期最多 2 次验证码尝试（OCR 偶发错误自动换图重试），失败后下个周期继续；网络异常不误判失效；凭证错误立即停止并提示人工登录；锁定期零请求不触发风控
- **小爱音箱播报（云端原生）**：上午/下午课程提醒，最早课前 30 分钟（可配）；文字直接交小米 IoT 云由音箱原生朗读，无需本地 TTS、无需局域网 IP/拉流

## 镜像与运行（Docker 标准）

- **多阶段构建**：builder 阶段安装编译工具链并 `pip install` 依赖，runtime 阶段只保留运行库与依赖产物（镜像内无 gcc/g++、无 curl、无测试代码）
- **非 root 运行**：容器内 `appuser` 用户运行；Compose 启用 `init: true`（tini 转发 SIGTERM、回收僵尸进程）与 `no-new-privileges`
- **生产 WSGI**：容器入口为 gunicorn `--workers 1 --threads 8 --worker-class gthread`（应用内含进程内 APScheduler 与内存态会话/缓存，多 worker 会重复提醒、会话错乱，因此**禁止多副本/多 worker**）；本地开发仍用 `python main.py`
- **依赖版本固定**：`requirements.txt` 锁定全部直接依赖版本（传递依赖由 pip 解析）；`gunicorn` 带 `sys_platform == "linux"` 标记，Windows 本地开发自动跳过
- **健康检查**：镜像内置 `HEALTHCHECK` 与 Compose healthcheck 同一命令——镜像内 Python 标准库 `urllib` 探活 `/api/status`，不依赖 curl
- **播报仅走云端**：**MIoT 原生文字播报**（`siid=5/aiid=3` play-text）——文字直接交小米 IoT 云由音箱原生朗读，**无本地 TTS 合成、无音频拉流、无静音垫底、无 pause 收尾**，播完设备自动停止；原生通道失败时自动回退 mibrain 原生 TTS（同为云端，部分型号会被服务端静默忽略，会附警示）
- **无需局域网 IP**：播报不再依赖音箱访问宿主机地址，换 IP/DHCP 均不影响播报
- **构建上下文最小化**：`.dockerignore` 排除 `.venv/`、`.git/`、`tests/`、`data/`、`cache/` 及全部开发工具产物，凭证与本地数据绝不进入镜像
- **日志**：应用日志写 stdout，gunicorn access/error 日志写 stdout/stderr，统一由 `docker logs` 收集（compose 侧 `json-file` 10MB × 3 轮转），不使用容器内日志文件

## 项目结构

```
main.py              本地开发入口（Flask dev server，仅本地调试用）
wsgi.py              容器生产入口（gunicorn 调用）
lit/config/          环境变量与路径配置
lit/jwxt/            教务客户端：登录/学期代码/周课表/凭证/锁/状态
lit/xiaoai/          小爱音箱：扫码登录/云端原生播报/诊断
lit/web/             Flask 应用工厂 + 4 个蓝图 + templates/index.html
lit/tasks.py         单调度器后台任务（课表刷新/提醒/Cookie 检查/缓存清理）
tests/               单元测试（jwxt / xiaoai / 任务调度），fixtures/ 存放脱敏样本
Dockerfile           多阶段镜像构建；docker-compose.yml 单容器编排
docker-compose.fnos.yml  飞牛OS（fnOS）构建安装编排（本地目录持久化、PUID/PGID、start_period 90s）
docker-compose.fnos-image.yml  飞牛OS 免构建安装（直接拉取 GHCR 预构建镜像）
docker-compose.fnos.env.example  fnOS 环境变量模板（复制为 .env）
.github/workflows/tests.yml  push/PR 自动跑测试（发布门禁）
.github/workflows/release.yml  推送 v* 标签自动构建镜像 + 发布 GitHub Release
requirements.txt     生产依赖（固定版本）；requirements-dev.txt 追加 pytest
```

## Prerequisites

- Docker Desktop 4.x+（含 Compose V2）
- 宿主机 10379 端口空闲
- 教务系统账号 + 小爱音箱（小米账号，页面扫码登录）

## Quick Start

```bash
git clone https://github.com/shihao618/xiaoai_lit.git
cd xiaoai_lit
docker compose up -d --build
```

浏览器打开 `http://localhost:10379`（仅本机）或 `http://<宿主机局域网IP>:10379` ：

1. 教务配置区填写账号密码登录（或经环境变量预置引导凭证）
2. 小爱配置区扫码登录音箱账号

登录成功后凭证自动持久化到数据目录（本机默认为命名卷；飞牛OS 为项目目录 `data/`）。容器重启或教务会话掉线后，首次课表刷新/下个 Cookie 检查周期会自动检测并重登恢复，无需重复扫码/重新登录。

> **播报无需局域网 IP**：原生播报由小米 IoT 云执行，不需要音箱访问宿主机地址；换 IP / DHCP 均不影响播报。

可选：在仓库根目录创建 `.env`（已被 gitignore）写入 `JWXT_ACCOUNT`、`JWXT_PASSWORD` 等，Compose 会自动插值，无需修改 `docker-compose.yml`。

首次部署建议：在页面"播报测试"现场听音核对，确认音箱真实出声（若用的是 `miot` 原生通道，无需配置任何 IP）。

## 预构建镜像（免构建安装）

推送 `v*` 标签时，GitHub Actions 会自动构建镜像、推送 GHCR，并创建 GitHub Release：

| 用途 | 镜像地址 |
|---|---|
| 最新 | `ghcr.io/shihao618/xiaoai_lit:latest` |
| 指定版本 | `ghcr.io/shihao618/xiaoai_lit:vX.Y.Z` |

```bash
docker pull ghcr.io/shihao618/xiaoai_lit:latest
```

飞牛 OS 免构建安装：使用 [docker-compose.fnos-image.yml](docker-compose.fnos-image.yml)（数据落在项目目录下 `data/`、`cache/`；镜像地址可用 `LIT_IMAGE` 覆盖）。

> 首次拉取若提示无权限：GHCR 镜像包默认私有，需到 GitHub → 个人/仓库 → Packages → 该镜像包 → Package settings → Change visibility → **Public**；或 NAS 上执行 `docker login ghcr.io`（用户名 GitHub 账号，密码用具备 `read:packages` 权限的 PAT）。
>
> 镜像平台为 **linux/amd64**（x86_64 NAS）。自行构建请见 Quick Start / 飞牛OS 章节。

## 飞牛 OS（fnOS）部署

fnOS 是 NAS 系统，**持久化数据需要落在 NAS 的本地目录**（文件管理器可见、可直接备份/迁移），因此两份 fnOS 编排使用**项目目录下的本地目录**，而不是 Docker 命名卷：

| 容器内路径 | NAS 本地目录（默认） | 内容 |
|---|---|---|
| `/app/data` | `<项目目录>/data` | 教务凭证/状态/锁定记录、小爱凭证、MIoT 令牌 |
| `/app/cache` | `<项目目录>/cache` | 教务课表接口缓存 |

- **免构建（推荐）**：[docker-compose.fnos-image.yml](docker-compose.fnos-image.yml) —— 直接拉取 GHCR 预构建镜像，NAS 零构建
- **NAS 构建**：[docker-compose.fnos.yml](docker-compose.fnos.yml) —— 从源码构建（无外网镜像仓库时使用）
- 容器以 `PUID:PGID`（默认 `1000:1000`，即 fnOS 默认用户）运行，保证能写本地目录
- 可用 `DATA_PATH` / `CACHE_PATH` 把数据放到绝对路径（如 `/vol1/1000/docker/lit-data`）
- 环境变量模板：[docker-compose.fnos.env.example](docker-compose.fnos.env.example)（复制为 `.env`）
- 播报走云端 MIoT，NAS 无需固定 IP、无需额外端口放行（10379 仅 Web 管理页）

### 前置：创建本地持久化目录（必须）

```bash
# 在项目目录（compose 文件所在目录）执行；
# 也可用 fnOS 文件管理器新建 data、cache 两个文件夹（属主默认就是当前用户）。
mkdir -p data cache
chown -R 1000:1000 data cache   # fnOS 默认用户 1000:1000；不同请按 `id -u`/`id -g` 修改
```

> 不要交给 Docker 自动创建：自动创建的宿主目录属主是 root，容器内非 root 用户会写入失败（Permission denied）。

### 方式 A：免构建（推荐，拉取预构建镜像）

1. 在 NAS 建项目目录，例如 `/vol1/1000/docker/lit-course-reminder`，把 [docker-compose.fnos-image.yml](docker-compose.fnos-image.yml) 复制进去（可改名为 `docker-compose.yml`）
2. 创建 `data/`、`cache/` 并确认属主（见上）
3. fnOS 应用中心 → Docker → Compose → 新建项目，选择该目录与 compose 文件，启动（自动拉取镜像）
4. 浏览器打开 `http://<NAS 局域网 IP>:10379`，按页面登录教务、扫码登录小爱

### 方式 B：从源码构建

1. 将本仓库上传/解压到 NAS 项目目录，例如 `/vol1/1000/docker/lit-course-reminder`
2. 创建 `data/`、`cache/` 并确认属主
3. fnOS Docker → Compose → 新建项目：工作目录选仓库目录，Compose 文件选 `docker-compose.fnos.yml`，构建并启动（首次约 5~10 分钟）
4. NAS 性能较弱时：可在电脑上 `docker compose -f docker-compose.fnos.yml build` 后 `docker save`，NAS `docker load`，再用免构建编排启动

### 数据 / 备份 / 升级

- **数据目录**：`<项目目录>/data`（或 `DATA_PATH` 指定目录），fnOS 文件管理器可直接查看、打包备份
- 备份：`tar czf lit-data-backup.tgz data cache`；恢复：解压回项目目录后重启容器
- 升级（免构建）：修改 `LIT_IMAGE` 版本或重新 `docker pull latest` → 重启项目；数据目录不变
- 升级（源码）：替换代码后重新构建项目即可
- 说明：仓库根目录 `docker-compose.yml`（通用/本机）仍使用命名卷；fnOS 两份编排改用本地目录
- **不做旧数据兼容**：全新部署请使用空的数据目录；旧版本的数据格式不会被迁移读取

## Configuration

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `JWXT_BASE_URL` | `https://lljw.lit.edu.cn` | 教务系统地址 |
| `JWXT_ACCOUNT` | （空） | **引导凭证**，登录成功后以卷内持久化凭证为准 |
| `JWXT_PASSWORD` | （空） | **引导凭证**，登录成功后以卷内持久化凭证为准 |
| `XNXQDM` | （空） | 学年学期代码（自动获取并缓存，通常无需配置） |
| `REMIND_BEFORE_MINUTES` | `30` | 提醒提前量（分钟） |
| `COOKIE_REFRESH_INTERVAL` | `1800` | Cookie 检查间隔（秒，默认 30 分钟） |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `CACHE_DIR` | `/app/cache` | 缓存目录 |
| `DATA_DIR` | `/app/data` | 数据目录 |

**凭证优先级**：数据卷内 `data/jwxt/credentials.json`（最近一次成功登录）优先；环境变量仅在全新部署无持久化凭证时作引导。修改环境变量不会覆盖已持久化凭证——切换账号请在前端登录页重新登录，或删除卷内 `credentials.json` 后重启。

## Volumes

| 卷 | 挂载点 | 内容 | 寿命 |
|---|---|---|---|
| `lit-data` | `/app/data` | 教务凭证/状态/锁定记录、小爱凭证、MIoT 令牌 | `docker compose down -v` 清除 |
| `lit-cache` | `/app/cache` | 教务课表接口缓存 | `docker compose down -v` 清除 |

日常重启（`docker compose restart` / `down` 不带 `-v`）数据保留。

Compose 项目名已在 `docker-compose.yml` 固定为 `lit`（`name: lit`），命名卷恒为 `lit_lit-data` / `lit_lit-cache`——更换部署目录或重新 clone 后 `docker compose up` 仍复用同一份凭证数据，不会出现"重新扫码/重新登录"。本机专属配置（如 `JWXT_ACCOUNT`/`JWXT_PASSWORD`）可写入仓库根目录 `.env`（已 gitignore），Compose 自动插值。

备份与恢复：

```bash
# 备份（容器运行中导出）
docker cp lit-course-reminder:/app/data ./backup
# 恢复
docker cp ./backup/. lit-course-reminder:/app/data/
# 属主修正：容器以非 root appuser 运行，docker cp 写入的文件可能属 root，需改回 appuser
docker exec -u 0 lit-course-reminder chown -R appuser:appuser /app/data
docker compose restart
```

## Healthcheck

镜像内置 `HEALTHCHECK`（30s 间隔 / 5s 超时 / 60s 启动宽限 / 3 次重试），探活端点 `/api/status` 消费内存态状态——**零教务请求**：探活不消耗教务请求预算、不触发自动重登。探活由镜像内 Python 标准库 `urllib` 完成（运行镜像不装 curl），Compose 的 healthcheck 与 Dockerfile 使用同一命令。

```bash
docker inspect --format "{{.State.Health.Status}}" lit-course-reminder   # healthy
```

若持续 `unhealthy`：`docker logs lit-course-reminder` 查看启动日志（常见为端口占用、依赖导入超时，或启动首轮刷新慢于 60s 宽限窗口）。冷启动导入 `onnxruntime`/`ddddocr` 较慢的低配机器可能出现短暂 unhealthy，只要日志正常、端口可访问即会自动转 healthy。

## Maintenance

### 日志查看

```bash
docker logs -f --tail 100 lit-course-reminder
```

### 更新升级（铲掉重部署）

```bash
docker compose down -v        # 清除容器与数据卷，回到全新基线
docker compose up -d --build
```

数据全清后重新走首次登录引导。**本项目不做旧数据兼容**：升级 = 全新部署（清空 `data/`、`cache/` 后重新登录），旧版本数据不会迁移读取。

### 小爱凭证备份提示

`down -v` 会一并清除小爱扫码凭证（重新扫码成本较高），如需保留：

```bash
# 清除卷之前备份
docker cp lit-course-reminder:/app/data/xiaoai/xiaoai_credentials.json ./
# 新容器启动后恢复
docker cp ./xiaoai_credentials.json lit-course-reminder:/app/data/xiaoai/
# 属主修正（非 root 运行 + 凭证 0600，避免 appuser 读不到）
docker exec -u 0 lit-course-reminder chown appuser:appuser /app/data/xiaoai/xiaoai_credentials.json
```

进阶选项——将小爱凭证隔离到 external 卷（`down -v` 不波及）：

```yaml
# docker-compose.override.yml 片段（不作默认配置）
services:
  lit-course-reminder:
    volumes:
      - xiaoai-data:/app/data/xiaoai
volumes:
  xiaoai-data:
    external: true
```

（先 `docker volume create xiaoai-data`，再把 `/app/data/xiaoai` 挂载到该卷；`down -v` 不会删除 external 卷。）

### 凭证重置

- 前端重新登录（新凭证自动覆盖持久化文件）
- 或删除卷内凭证文件：`docker exec lit-course-reminder rm /app/data/jwxt/credentials.json`，回到引导态

### 学期切换

新学期首次刷新时自动检测（教务接口返回新学年学期代码）——自动切换、课表按新学期第 1 周重查，日志出现"学期切换"，无需人工干预。寒暑假期间徽章显示"学期外"、不注册提醒，开学自动恢复。

### 开发与测试

本地开发直接运行 Flask dev server（**仅本地调试用**，生产容器走 gunicorn）：

```bash
pip install -r requirements-dev.txt   # Windows 下 gunicorn 因 sys_platform 标记自动跳过
python main.py                        # 本地启动 http://127.0.0.1:10379
```

测试：

```bash
python -m pytest tests/    # 当前基线：250 用例（仅云端播报通道）
```

CI（GitHub Actions）：
- 每次 push `master` / PR 自动运行上述测试（[tests.yml](.github/workflows/tests.yml)）
- 发布工作流（打 `v*` 标签）**以测试通过为前置门槛**，测试失败不会构建/发布镜像

## Security

- **最小权限运行**：容器以非 root `appuser` 运行；Compose 启用 `init: true`（tini）与 `no-new-privileges`；镜像为多阶段构建，不含 gcc/g++ 等编译工具链，运行阶段不再安装 curl 等非必需包
- **构建上下文隔离**：`.dockerignore` 排除 `.venv/`、`.git/`、`tests/`、`data/`、`cache/`、`.env*` 及开发工具产物，凭证与本地数据绝不进入镜像层
- **依赖固定**：`requirements.txt` 锁定全部直接依赖版本，降低供应链漂移风险（传递依赖未锁定）
- **密码明文落盘**：`data/jwxt/credentials.json` 以明文保存教务账号密码（文件权限 `600`）。单机自部署场景下，可逆加密因密钥同机存放不构成实质防线，故采用明文 + 权限隔离方案；请自行确保宿主机与 Docker 环境访问安全
- **卷访问控制**：命名卷由 Docker 直接管理（位于 Docker Desktop 虚拟机文件系统内），宿主普通用户无直接路径访问
- **权限**：凭证与令牌文件 `0600`（仅容器内运行用户可读，含 `jwxt/credentials.json`、`jwxt/jwxt.json`、`xiaoai/xiaoai_credentials.json`、`xiaoai/miot_token.json`）

## Troubleshooting

| 现象 | 处置 |
|---|---|
| 账号锁定/冷却 | **勿手动反复重试**——系统在锁定期自动零请求等待解锁（日志含解锁时间），持续失效请到配置页重新登录 |
| 日志"凭证疑似失效" | 教务账号密码已变更——前端重新登录即可（自动重登已停止，防反复试错触发风控） |
| 重启容器后显示"未登录"/课表不刷新 | 属正常自恢复过程：启动时与每个检查周期（默认 30 分钟）都会尝试自动重登；通常首个周期内恢复。想立即恢复可在页面重新登录一次；若日志持续出现"凭证疑似失效"则需人工核对账号密码 |
| 自动重登一直失败（日志"自动重登临时失败"） | 查看是否"验证码错误"（OCR 识别波动，程序已每周期换图重试）或网络异常（不会耗尽重登预算）；连续失败当日最多 5 个周期，次日自动重置预算 |
| 教务接口异常 | 自动缓存降级：当日缓存有效时按缓存提醒（宁可多提醒不可停提醒）；跨周缓存自动弃用，不会播报过期课表 |
| Windows 宿主目录直接挂载（bind mount）失败 | Windows 下请用命名卷（默认 `docker-compose.yml`）；飞牛OS/Linux 请用专用编排的本地目录方案（见「飞牛 OS 部署」），两者分别适配各平台权限语义 |
| 周次徽章显示"学期外" | 寒暑假正常显示（教务接口无当日教学周数据）；开学后自动恢复为"第 N 周" |
| 播报提示成功但音箱无声 | ①**先检查音箱是否被静音**（音箱静音时接口仍返回成功；米家 App 或语音"取消静音"）②查看日志是否有 `MIoT 服务令牌获取成功`；令牌失效会自动重建，失败则重新扫码登录小爱账号 ③页面"播报诊断"看通道/分级/警示 ④现场听音核对 |
| 需要立刻停止播报 | `curl -X POST http://127.0.0.1:10379/api/xiaoai/stop_playback`（局域网内换成宿主机 IP 同理） |
| 页面提示"指令已接受未确认播放" | 这是**正常分级语义**：云端已受理并下发设备，但应用没观察到设备处于播放状态（MIoT 播放状态属性读取不稳定）。①先确认音箱**音量/静音/勿扰**状态（米家 App）②以现场听音为准 |
| 播报无声但日志显示播报成功 | 检查音箱音量是否为 0、是否开启勿扰/夜间模式（米家 App），再点页面"播报测试"确认 |
| 容器启动即退出/反复重启 | `docker logs lit-course-reminder` 查看：常见为 `requirements.txt` 缺 gunicorn（容器入口依赖它）、依赖安装失败或数据目录权限异常。Windows 用命名卷；飞牛OS 用专用编排的本地目录，需先创建目录并 `chown 1000:1000` |
| 页面/接口超时或 502 | 先 `docker compose config` 校验配置，再确认端口映射 10379 与宿主机防火墙；日志出现 `WORKER TIMEOUT` 可调大 Dockerfile 中 `--timeout`（gthread 下该值是 worker 心跳超时，不是单请求超时） |
| 构建慢/拉取失败 | Python 依赖走清华 PyPI 镜像；基础镜像默认 DaoCloud 国内加速，可 `docker compose build --build-arg BASE_IMAGE=python:3.11-slim` 或改 Dockerfile ARG 切换源 |
