# LIT 课表提醒服务

洛阳理工学院教务系统课表查询 + 小爱音箱上课提醒 Docker 应用。

## 功能

- 自动登录教务系统（OCR识别验证码 + AES加密密码）
- 通过 JSON API 获取课表数据，每日自动刷新
- 上午/下午智能提醒：根据课表自动判断提醒时间
- 先闹钟提示，再播报课程名称和教室

## 提醒逻辑

- **上午**：1-2节有课 → 07:30提醒；1-2节无课但3-4节有课 → 09:30提醒
- **下午**：5-6节有课 → 14:00提醒；5-6节无课但7-8节有课 → 16:00提醒
- 提醒方式：先播报"叮叮叮叮！上课提醒！"，等待4秒后播报课程详情（课程名称+教室）

## 上课时间表（来自教务系统）

| 节次 | 开始时间 | 结束时间 |
|------|---------|---------|
| 第1节 | 08:00 | 08:45 |
| 第2节 | 08:55 | 09:40 |
| 第3节 | 10:00 | 10:45 |
| 第4节 | 10:55 | 11:40 |
| 第5节 | 14:30 | 15:15 |
| 第6节 | 15:25 | 16:10 |
| 第7节 | 16:30 | 17:15 |
| 第8节 | 17:25 | 18:10 |
| 第9节 | 19:10 | 19:55 |
| 第10节 | 20:05 | 20:50 |

## 快速开始

### 一键安装（Docker）

```bash
git clone https://github.com/shihao618/xiaoai_lit.git && cd xiaoai_lit && docker compose up -d --build
```

启动后打开 `http://localhost:10379` 完成后续配置（教务账号 + 扫码绑定音箱）。

### 1. 配置教务系统账号

编辑 `docker-compose.yml`，填入教务系统账号（`JWXT_ACCOUNT` / `JWXT_PASSWORD`），然后重启容器：

```bash
docker compose restart
```

### 2. 构建并启动

```bash
# Docker（推荐）
docker compose up -d --build

# 或本地直接运行
python main.py
```

### 3. 扫码登录小爱音箱

1. 打开 `http://localhost:10379`，进入小爱音箱配置区
2. 点击"扫码登录"，用小米手机（已绑定小爱音箱的账号）扫描页面二维码并确认
3. 登录成功后页面自动展示设备列表，选择目标音箱即可

无需输入小米账号密码，也无需短信验证码。若扫码不便，也可在页面"手动设置"中填入 Cookie 或 Token（兜底方式）。

### 4. 查看日志

```bash
docker-compose logs -f
```

## 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `JWXT_BASE_URL` | `https://lljw.lit.edu.cn` | 教务系统地址 |
| `JWXT_ACCOUNT` | -（必填） | 教务系统账号 |
| `JWXT_PASSWORD` | -（必填） | 教务系统密码 |
| `XNXQDM` | `202601` | 学年学期代码 |
| `XIAOAI_TTS_BACKUP_URL` | - | TTS 被拒时的备用音频 URL（可选） |
| `REMIND_BEFORE_MINUTES` | `30` | 提前提醒分钟数 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

## 文件结构

```
LIT/
├── main.py               # 唯一入口：日志初始化 → create_app() → app.run
├── conftest.py           # pytest 根配置（sys.path 锚定 + 目录隔离）
├── lit/                  # 统一业务包
│   ├── config/           # 配置域：环境变量加载与默认值
│   │   ├── __init__.py   #   通用配置（调度/路径/日志）+ 13 属性汇总导出
│   │   ├── jwxt.py       #   教务系统配置（地址/账号/学期代码）
│   │   └── xiaoai.py     #   小爱音箱配置（TTS 地址）
│   ├── jwxt/             # 教务系统域（门面 + 5 领域模块）
│   │   ├── client.py     #   JWXTClient 门面（对外唯一接口）
│   │   ├── auth.py       #   登录与验证码 OCR
│   │   ├── lock.py       #   账号锁定与冷却
│   │   ├── cookies.py    #   Cookie 持久化
│   │   ├── xnxqdm.py     #   学期代码缓存
│   │   └── courses.py    #   课表获取与解析
│   ├── xiaoai/           # 小爱音箱域（10 模块）
│   │   ├── speaker.py    #   XiaoAISpeaker 门面（状态机 + 对外接口）
│   │   ├── account.py    #   小米账号扫码协议（申请/轮询/换 Token）
│   │   ├── mina.py       #   MiNA 播报协议（TTS/URL 播放/状态）
│   │   ├── credentials.py#   凭证存储（持久化/恢复/失效标记）
│   │   ├── qrlogin.py    #   扫码会话管理与二维码生成
│   │   ├── tts.py        #   TTS 合成与音频缓存
│   │   └── ...           #   httpclient / constants / exceptions
│   ├── tasks.py          # 统一后台任务：单调度器（Cookie 检查/课表刷新/上下午提醒）
│   └── web/              # Web 层
│       ├── app.py        #   create_app() 应用工厂（对象装配 + 启动后台任务）
│       └── blueprints/   #   5 蓝图：pages（首页）/ jwxt（5 路由）/ xiaoai（9 路由）/ audio（TTS 音频）/ status（聚合状态）
├── templates/index.html  # 前端单页
├── tests/                # 测试（tests/jwxt/ + tests/xiaoai/，174 用例）
├── Dockerfile / docker-compose.yml / requirements.txt
├── cache/                # 课表缓存（course_table.json）
└── data/                 # 运行数据（教务 Cookie/学期代码/小爱凭证/TTS 音频缓存）
```

## 模块地图

| 模块 | 职责 |
|------|------|
| `lit/config/` | 环境变量加载与默认值：jwxt / xiaoai 两域文件 + `__init__.py` 通用配置（调度/路径/日志）与 13 属性汇总导出 |
| `lit/jwxt/` | 教务系统登录与课表获取，JWXTClient 门面封装 5 个领域模块 |
| `lit/xiaoai/` | 小爱音箱扫码登录与 MiNA 播报，XiaoAISpeaker 门面对外 |
| `lit/tasks.py` | 统一后台调度：Cookie 检查每 30 分钟、课表刷新每日 06:00、课前 30 分钟提醒 |
| `lit/web/` | Flask 应用工厂与 5 蓝图（共 18 条路由），对象装配与启动时序集中于此 |

## 技术细节

- **登录**：POST `/new/login`，验证码OCR + AES-ECB加密密码
- **课表获取**：POST `/new/student/xsgrkb/getCalendarWeekDatas`，返回JSON格式课表数据
- **课表字段**：课程名(kcmc)、节次(ps/pe)、星期(xq)、教室(jxcdmc)、时间(qssj/jssj)、周次(zc)
- **小爱控制**：自研实现小米扫码登录协议（`/longPolling/loginUrl` 轮询 + serviceToken 换取）与 MiNA `ubus` 接口（TTS 播报 / URL 播放 / 播放状态查询），不依赖第三方 mi-service 库
- **定时调度**：使用 `APScheduler` 实现cron定时任务
- **课表缓存**：JSON格式缓存到本地，避免频繁登录

## 注意事项

1. 小爱音箱需与小米账号绑定，且账号下有已配网的小爱音箱设备；扫码登录使用的是扫码手机所登录的小米账号
2. 登录凭证持久化在 `data/xiaoai_credentials.json`，容器重启后自动恢复登录态，无需重新扫码
3. 凭证失效（如在其他设备重新登录导致失效）时，页面会提示重新扫码，不会自动用账号密码重登
2. 教务系统验证码识别可能有失败情况，已设置10次重试
3. 课表数据每日06:00自动刷新，也可通过重启容器手动触发
4. 学年学期代码需根据实际情况设置（如202601表示2026-2027学年第一学期）