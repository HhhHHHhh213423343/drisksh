# GitHub + Zeabur 部署说明

这套仓库正式运行投后监测时拆成 4 个服务：

- `frontend`：Next.js
- `backend`：FastAPI
- `worker`：采集、材料解析、规则评估和月报生成
- `postgres`：Zeabur 托管 PostgreSQL

不建议直接把根目录的 `docker-compose.public.yml` 拿去给 Zeabur 用。Zeabur 官方文档明确说明目前不支持从 Docker Compose YAML 直接部署，因此这里应改为同仓库多服务部署。

## 推送到 GitHub 前

1. 先确认根目录 `.gitignore` 已生效，避免把 `.venv`、缓存、调试输出、数据库文件推上去。
2. 这个仓库当前没有现成远端仓库，推送前需要先创建 GitHub 仓库。
3. 如果你不希望公开代码，应创建 Private Repository。

## 推荐部署结构

### Demo 方案

如果你只是先把站点放到公网演示，不追求完整线上数据库，可以先只部署：

- `frontend`
- `backend`

当前仓库已经把 `backend/demo.db` 打进后端镜像里，并且当 `DATABASE_URL` 未配置时，会默认使用这个 SQLite 演示库。

也就是说，Zeabur 上即使暂时不创建 PostgreSQL，页面也可以先展示 demo 数据。

这个两服务方案只适合浏览旧演示数据。投后监测任务需要 backend 和 worker 共享数据库与上传文件，因此正式环境必须使用 PostgreSQL，并给两个服务挂载同一个 `/data/uploads` 持久化卷。

前端到后端的 `/api/v1/*` 请求现在由 Next.js 在运行时代理，因此在 Zeabur 上只需要给前端服务配置运行时 `BACKEND_URL`，不再依赖构建阶段写死地址。

### 1. PostgreSQL

在 Zeabur 项目里先新增一个 PostgreSQL 服务。

后端会使用这个数据库的连接串作为 `DATABASE_URL`。

### 2. Backend 服务

- 部署来源：GitHub
- 仓库：当前仓库
- Root Directory：`backend`
- 构建方式：使用 `backend/Dockerfile`

建议配置的环境变量：

- `DATABASE_URL`：指向 Zeabur PostgreSQL 的连接串
- `FASTGPT_BASE_URL`
- `FASTGPT_API_KEY`
- `FASTGPT_DATASET_ID`
- `FASTGPT_DATASET_UPSERT_PATH`
- `FASTGPT_CHAT_PATH`
- `CORS_ORIGINS`：前端公网域名
- `COLLECTION_API_KEY`：至少 32 位随机值，保护采集接口，Mac Worker 使用同一值
- `SERPER_API_KEY`：可选；用于白名单权威站点的线索发现，最终仍回到原文核验
- `AUTH_REQUIRED=true`
- `APP_USERNAME` 与 `APP_PASSWORD` 或 `APP_PASSWORD_HASH`
- `SESSION_SECRET`：长随机值
- `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`：可选；未配置时生成确定性草稿
- `UPLOAD_ROOT=/data/uploads`
- `INLINE_JOB_EXECUTION=false`
- `COMPANY_PROFILE_WORKER_ENABLED=true`
- `DEMO_COMPANY_NAME=上海携程金融信息服务有限公司`
- `DEMO_REPORT_AS_OF_DATE=2026-09-24`

为 backend 挂载持久化卷到 `/data/uploads`。

后端 Dockerfile 已改为优先读取 `$PORT`，更适合 Zeabur 的端口分配方式。

每日权威数据更新由外部定时任务调用后端接口：

```text
POST https://<backend-domain>/api/v1/authoritative-ingestion/daily/run?mode=daily&lookback_days=2
X-Collection-Key: <COLLECTION_API_KEY>
```

建议每天凌晨调用一次。首次上线先使用
`mode=backfill&backfill_years=5` 完成五年回补，再切换为每日增量。

### 3. Worker 服务

- 部署来源、Root Directory 和 Dockerfile 与 backend 相同；
- 启动命令覆盖为 `python -m app.worker`；
- 使用与 backend 相同的 `DATABASE_URL`、数据源和 LLM 配置；
- 挂载与 backend 相同的 `/data/uploads` 持久化卷；
- 不需要公网域名或入站端口。

Worker 服务也需要配置与 backend 相同的 `DEMO_COMPANY_NAME`、
`DEMO_REPORT_AS_OF_DATE` 和模型参数，保证后台生成的报告口径一致。

### 4. Frontend 服务

- 部署来源：GitHub
- 仓库：当前仓库
- Root Directory：`frontend`
- 构建方式：使用 `frontend/Dockerfile`

建议配置的环境变量：

- `BACKEND_URL`：后端服务地址
- `CSRC_MONITOR_ROOT`：如果需要监管看板，指向容器内挂载目录

如果前后端都放在同一个 Zeabur 项目里，优先使用后端服务的 Private Networking 地址，而不是写死公网域名。

## Zeabur 操作顺序

1. 在 GitHub 创建仓库并推送代码。
2. 在 Zeabur 创建新项目。
3. 添加 PostgreSQL。
4. 从 GitHub 添加 `backend` 服务，并把 Root Directory 设为 `backend`。
5. 从同一 backend 目录添加 `worker` 服务，覆盖启动命令为 `python -m app.worker`。
6. 给 backend 与 worker 挂载同一个上传文件持久化卷。
7. 添加 `frontend` 服务，并把 Root Directory 设为 `frontend`。
8. 给前端生成 `zeabur.app` 域名，确认登录、实时分析、上传、审批和 Excel 下载。
9. 如需正式域名，再绑定自定义域名。

## Mac 上传企业预警通快照

代码部署完成后，先在平台的“企业全景”中发起一次采集任务，然后在 Mac 执行：

```bash
./scripts/company-profile-mac-login.sh
```

完成登录并关闭专用浏览器窗口后，在当前终端设置密钥并执行单次 Worker：

```bash
export COLLECTION_API_KEY='<与 Zeabur backend 一致的密钥>'
export DRISK_API_URL='https://<D.Risk 公网域名>'
./scripts/company-profile-mac-worker.sh
```

Worker 只主动通过 HTTPS 上传八模块结构化快照和 Excel；
登录会话保存在已忽略的 `.runtime/qyyjt-edge/`，不会进入 Git。

## 当前仓库对 Zeabur 已做的适配

- `frontend/Dockerfile`：可直接作为前端服务构建入口
- `backend/Dockerfile`：可直接作为后端服务构建入口
- `backend/app/worker.py`：复用同一后端镜像执行持久化任务队列
- `backend/demo.db`：可作为演示数据直接随镜像发布
- `frontend/next.config.ts`：通过 `BACKEND_URL` 做 API rewrite
- 知识库问答已改为后端代理，不需要把 FastGPT Key 暴露到浏览器

## 注意事项

- 爬虫 `enterprise_sentinel` 依赖登录态 Chrome，不适合直接放在 Zeabur 对公网暴露
- 如果要跑监管看板，相关数据目录需要你另外上传或挂载，不能指望 Zeabur 自动带上本地文件
- 不配置 PostgreSQL 时只能浏览单容器演示数据，不能可靠运行投后监测 worker
- 上传卷未持久化或未共享时，worker 将无法解析 backend 接收的材料
