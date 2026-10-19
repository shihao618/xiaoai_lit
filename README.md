# LIT 课表提醒服务

洛阳理工学院教务系统课表提醒 + 小爱音箱语音播报 Docker 应用。

## 简介

- **教务 API 直取课表**：经教务系统 JSON 接口获取周次定位与当周课程（零 HTML 解析），调课等排课差异以教务端数据为准
- **掉线自动重登**：首次登录记住账号密码（持久化到数据卷），Cookie 失效后预算内自动恢复，锁定期零请求不触发风控
- **小爱音箱播报**：上午/下午课程提醒，最早课前 30 分钟（可配）TTS 播报课程名称与教室

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

浏览器打开 http://localhost:10379 ：

1. 教务配置区填写账号密码登录（或经环境变量预置引导凭证）
2. 小爱配置区扫码登录音箱账号

登录成功后凭证自动持久化到命名卷，容器重启/掉线均自动恢复，无需重复登录。

## Configuration

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `JWXT_BASE_URL` | `https://lljw.lit.edu.cn` | 教务系统地址 |
| `JWXT_ACCOUNT` | （空） | **引导凭证**，登录成功后以卷内持久化凭证为准 |
| `JWXT_PASSWORD` | （空） | **引导凭证**，登录成功后以卷内持久化凭证为准 |
| `XNXQDM` | （空） | 学年学期代码（自动获取并缓存，通常无需配置） |
| `XIAOAI_TTS_BASE_URL` | （空） | 小爱 TTS 服务地址（空 = 自动获取） |
| `XIAOAI_TTS_BACKUP_URL` | （空） | 小爱 TTS 备用地址 |
| `REMIND_BEFORE_MINUTES` | `30` | 提醒提前量（分钟） |
| `COOKIE_REFRESH_INTERVAL` | `3600` | Cookie 检查间隔（秒） |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `CACHE_DIR` | `/app/cache` | 缓存目录 |
| `DATA_DIR` | `/app/data` | 数据目录 |

**凭证优先级**：数据卷内 `data/jwxt/credentials.json`（最近一次成功登录）优先；环境变量仅在全新部署无持久化凭证时作引导。修改环境变量不会覆盖已持久化凭证——切换账号请在前端登录页重新登录，或删除卷内 `credentials.json` 后重启。

## Volumes

| 卷 | 挂载点 | 内容 | 寿命 |
|---|---|---|---|
| `lit-data` | `/app/data` | 教务凭证/状态/锁定记录、小爱凭证 | `docker compose down -v` 清除 |
| `lit-cache` | `/app/cache` | 课表缓存、TTS 音频缓存 | `docker compose down -v` 清除 |

日常重启（`docker compose restart` / `down` 不带 `-v`）数据保留。

备份与恢复：

```bash
# 备份（容器运行中导出）
docker cp lit-course-reminder:/app/data ./backup
# 恢复
docker cp ./backup/. lit-course-reminder:/app/data/
docker compose restart
```

## Healthcheck

镜像内置 `HEALTHCHECK`（30s 间隔 / 5s 超时 / 40s 启动宽限 / 3 次重试），探活端点 `/api/status` 消费内存态状态——**零教务请求**：探活不消耗教务请求预算、不触发自动重登。

```bash
docker inspect --format "{{.State.Health.Status}}" lit-course-reminder   # healthy
```

若持续 `unhealthy`：`docker logs lit-course-reminder` 查看启动日志（常见为端口占用或启动刷新慢于 40s 宽限窗口）。

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

数据全清后重新走首次登录引导——**无迁移、无回滚兼容**（旧版本数据整体消失属预期行为）。

### 小爱凭证备份提示

`down -v` 会一并清除小爱扫码凭证（重新扫码成本较高），如需保留：

```bash
# 清除卷之前备份
docker cp lit-course-reminder:/app/data/xiaoai/xiaoai_credentials.json ./
# 新容器启动后恢复
docker cp ./xiaoai_credentials.json lit-course-reminder:/app/data/xiaoai/
```

进阶选项——将小爱凭证隔离到 external 卷（`down -v` 不波及）：

```yaml
# docker-compose.override.yml 片段（不作默认配置）
volumes:
  xiaoai-data:
    external: true
```

（先 `docker volume create xiaoai-data`，再把 `/app/data/xiaoai` 挂载到该卷。）

### 凭证重置

- 前端重新登录（新凭证自动覆盖持久化文件）
- 或删除卷内凭证文件：`docker exec lit-course-reminder rm /app/data/jwxt/credentials.json`，回到引导态

### 学期切换

新学期首次刷新时自动检测（教务接口返回新学年学期代码）——自动切换、课表按新学期第 1 周重查，日志出现"学期切换"，无需人工干预。寒暑假期间徽章显示"学期外"、不注册提醒，开学自动恢复。

### 开发与测试

```bash
pip install -r requirements-dev.txt
python -m pytest tests/    # 当前基线：230 用例
```

## Security

- **密码明文落盘**：`data/jwxt/credentials.json` 以明文保存教务账号密码（文件权限 `600`）。单机自部署场景下，可逆加密因密钥同机存放不构成实质防线，故采用明文 + 权限隔离方案；请自行确保宿主机与 Docker 环境访问安全
- **卷访问控制**：命名卷由 Docker 直接管理（位于 Docker Desktop 虚拟机文件系统内），宿主普通用户无直接路径访问
- **权限**：凭证文件 `0600`（仅容器内运行用户可读）

## Troubleshooting

| 现象 | 处置 |
|---|---|
| 账号锁定/冷却 | **勿手动反复重试**——系统在锁定期自动零请求等待解锁（日志含解锁时间），持续失效请到配置页重新登录 |
| 日志"凭证疑似失效" | 教务账号密码已变更——前端重新登录即可（自动重登已停止，防反复试错触发风控） |
| 教务接口异常 | 自动缓存降级：当日缓存有效时按缓存提醒（宁可多提醒不可停提醒）；跨周缓存自动弃用，不会播报过期课表 |
| Windows 宿主目录直接挂载（bind mount）失败 | 本项目仅支持命名卷部署（non-root 运行 + Windows 权限语义限制），不提供以 root 运行的降级方案 |
| 周次徽章显示"学期外" | 寒暑假正常显示（教务接口无当日教学周数据）；开学后自动恢复为"第 N 周" |