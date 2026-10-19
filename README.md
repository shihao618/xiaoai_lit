# LIT 课表提醒服务

洛阳理工学院教务系统课表查询 + 小爱音箱上课提醒 Docker 应用。

## 功能

- 自动登录教务系统（OCR识别验证码 + AES加密密码）
- 通过 JSON API 获取课表数据，每日自动刷新
- 上午/下午智能提醒：根据课表自动判断提醒时间
- 先闹钟提示，再播报课程名称和教室

## 提醒逻辑

- **周次过滤**：课表区分单双周与部分周课程——仅当课程周次包含当前教学周（或课程为每周上课）才会提醒；学期外日期（未开学/放假）不提醒
- **上午**：1-2节有课 → 07:30提醒；1-2节无课但3-4节有课 → 09:30提醒
- **下午**：5-6节有课 → 14:00提醒；5-6节无课但7-8节有课 → 16:00提醒
- 提醒方式：先播报"叮叮叮叮！上课提醒！"，等待4秒后播报课程详情（课程名称+教室）

## 学期锚点（周次过滤）

系统需要知道本学期**第 1 教学周周一**的日期，才能推算当前教学周次：

- **自动探测**：每日课表刷新时若锚点缺失，系统附加查询指定周次课表（每条课程含实际上课日期），反推学期起始周一（周一校验 + 多数一致投票剔除调课污染 + 换周重试，最多取样 3 个周次），成功后自动持久化
- **手动配置**：页面教务配置区"学期锚点配置"可直接填写第 1 教学周周一（仅周一合法，如 `2026-09-07`），手动值优先于自动探测
- 学期切换后（学年学期代码变化）旧锚点自动失效，下次刷新重新探测；探测失败不阻断课表刷新（回退仅按星期过滤，宁可多提醒不可停提醒）


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

无需输入小米账号密码，也无需短信验证码。

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
│   ├── layout.py         # 启动迁移编排：旧平铺布局 → 按域分组（一次性幂等）
│   ├── jwxt/             # 教务系统域（门面 + 6 领域模块）
│   │   ├── client.py     #   JWXTClient 门面（对外唯一接口）
│   │   ├── auth.py       #   登录与验证码 OCR
│   │   ├── lock.py       #   账号锁定与冷却
│   │   ├── cookies.py    #   Cookie 持久化（jwxt.json cookies 域）
│   │   ├── state.py      #   合并状态 jwxt.json 唯一读写方（九键 + 原子写）
│   │   ├── term.py       #   学期锚点与教学周（解析/推算/周次过滤/探测反推）
│   │   ├── xnxqdm.py     #   学期代码缓存（jwxt.json xnxqdm 域）
│   │   └── courses.py    #   课表获取与解析
│   ├── xiaoai/           # 小爱音箱域（10 模块）
│   │   ├── speaker.py    #   XiaoAISpeaker 门面（状态机 + 对外接口）
│   │   ├── account.py    #   小米账号扫码协议（申请/轮询/换 Token）
│   │   ├── mina.py       #   MiNA 播报协议（TTS/URL 播放/状态）
│   │   ├── credentials.py#   凭证存储（持久化/恢复/失效标记）
│   │   ├── qrlogin.py    #   扫码会话管理与二维码生成
│   │   ├── tts.py        #   TTS 合成与音频缓存（每日 06:05 清理）
│   │   └── ...           #   httpclient / constants / exceptions
│   ├── tasks.py          # 统一后台任务：单调度器（Cookie 检查/课表刷新/上下午提醒/TTS 清理）
│   └── web/              # Web 层
│       ├── app.py        #   create_app() 应用工厂（迁移编排 + 对象装配 + 启动后台任务）
│       ├── blueprints/   #   5 蓝图：pages（首页）/ jwxt（5 路由）/ xiaoai（7 路由）/ audio（TTS 音频）/ status（聚合状态）
│       └── templates/    #   前端单页 index.html（随包入镜像）
├── tests/                # 测试（tests/jwxt/ + tests/xiaoai/ + test_layout.py + test_tasks_week.py，229 用例）
├── Dockerfile / docker-compose.yml / requirements.txt
├── cache/                # 缓存卷：cache/jwxt/course_table.json + cache/xiaoai/tts_cache/
└── data/                 # 数据卷：data/jwxt/jwxt.json（+ jwxt_lock.json）+ data/xiaoai/（凭证）
```

## 模块地图

| 模块 | 职责 |
|------|------|
| `lit/config/` | 环境变量加载与默认值：jwxt / xiaoai 两域文件 + `__init__.py` 通用配置（调度/路径/日志）与 13 属性汇总导出 |
| `lit/layout.py` | 启动迁移编排：旧平铺布局自动搬移至按域分组目录（幂等可重入，失败不阻断启动） |
| `lit/jwxt/` | 教务系统登录与课表获取，JWXTClient 门面封装 6 个领域模块 |
| `lit/jwxt/state.py` | 教务合并状态 `data/jwxt/jwxt.json` 唯一读写方：九键结构、读-合并-写、原子落位 |
| `lit/jwxt/term.py` | 学期锚点与教学周纯函数：锚点解析/有效性校验、教学周推算、单双周双条件过滤、探测反推算法 |
| `lit/xiaoai/` | 小爱音箱扫码登录与 MiNA 播报，XiaoAISpeaker 门面对外 |
| `lit/tasks.py` | 统一后台调度：Cookie 检查每 30 分钟、课表刷新每日 06:00、TTS 缓存清理每日 06:05、课前 30 分钟提醒 |
| `lit/web/` | Flask 应用工厂与 5 蓝图（共 15 条路由），对象装配与启动时序集中于此 |

## 技术细节

- **登录**：POST `/new/login`，验证码OCR + AES-ECB加密密码
- **课表获取**：POST `/new/student/xsgrkb/getCalendarWeekDatas`，返回JSON格式课表数据
- **课表字段**：课程名(kcmc)、节次(ps/pe)、星期(xq)、教室(jxcdmc)、时间(qssj/jssj)、周次(zc)
- **小爱控制**：自研实现小米扫码登录协议（`/longPolling/loginUrl` 轮询 + serviceToken 换取）与 MiNA `ubus` 接口（TTS 播报 / URL 播放 / 播放状态查询），不依赖第三方 mi-service 库
- **定时调度**：使用 `APScheduler` 实现cron定时任务（课表刷新 06:00、TTS 缓存清理 06:05，错峰执行）
- **课表缓存**：JSON格式缓存到 `cache/jwxt/course_table.json`，避免频繁登录
- **教务状态合并**：登录态、学期代码与学期锚点合并于 `data/jwxt/jwxt.json`（扁平九键：cookies / login_time / account / xnxqdm / updated / version + 锚点域三键 term_start_monday / term_xnxqdm / term_start_source，version 保持 1），读-合并-写协议保证各域互不覆盖，原子落位防半写

## 注意事项

1. 小爱音箱需与小米账号绑定，且账号下有已配网的小爱音箱设备；扫码登录使用的是扫码手机所登录的小米账号
2. 登录凭证持久化在 `data/xiaoai/xiaoai_credentials.json`，容器重启后自动恢复登录态，无需重新扫码
3. 凭证失效（如在其他设备重新登录导致失效）时，页面会提示重新扫码，不会自动用账号密码重登
4. TTS 语音缓存位于 `cache/xiaoai/tts_cache/`，每日 06:05 自动全量清理（静音垫底文件豁免）
5. 从旧版本升级时启动自动迁移存量数据（教务 Cookie 与学期代码合并、凭证/课表缓存/TTS 缓存搬移至按域分组目录），无需手工干预
6. 教务系统验证码识别可能有失败情况，已设置10次重试
7. 课表数据每日06:00自动刷新，也可通过重启容器手动触发
8. 学年学期代码需根据实际情况设置（如202601表示2026-2027学年第一学期）