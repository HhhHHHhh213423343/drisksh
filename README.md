# Enterprise_Sentinel

Enterprise_Sentinel 是一个基于 DrissionPage 的企业风险舆情采集工具，目标是通过监听站点后端 JSON 接口，而不是解析复杂 DOM，输出结构化 Excel 报表。

## 项目组成

- `enterprise_sentinel/`：依赖本机 Chrome 登录态的企业风险采集工具。
- `backend/`：FastAPI API、SQLite/PostgreSQL 数据模型和公开数据接入服务。
- `frontend/`：Next.js 企业风险分析、监管看板和报告组装页面。
- `tests/`：后端服务与公开数据接入的回归测试。
- `docs/`：公网部署、Zeabur 部署和产品结构资料。

## Web 应用本地开发

项目附带一个小型 SQLite 演示库 `backend/demo.db`，无需先配置 PostgreSQL 即可启动。

启动后端：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
uvicorn app.main:app --reload --port 8000
```

另开一个终端启动前端：

```bash
cd frontend
npm ci
npm run dev
```

浏览器访问 `http://localhost:3000`；后端健康检查地址为 `http://localhost:8000/health`。

可选的 FastGPT、企查查和公网部署配置见 `.env.public.example`。真实密钥只应放在本地环境变量或部署平台的 Secret 中，不要提交到仓库。

运行后端回归测试：

```bash
source .venv/bin/activate
pytest -q
```

## 输出表头

根据你最新确认的要求，Excel 固定按以下顺序输出：

`序号`、`监测日期`、`事件发生日期`、`主体名称`、`当前持股比例`、`关联实体`、`关联关系`、`标题及主要内容`、`分类`、`执行标的金额`、`重要性`、`正负面`、`来源`

当前版本默认输出一个总表 Sheet：`监测结果`，并且会持续累计到同一个 Excel 文件中。

## 目录说明

- `enterprise_sentinel/engine.py`：浏览器启动、页面导航、接口监听。
- `enterprise_sentinel/parser.py`：从 JSON 数据包中抽取字段并做中文映射，自动拼接标题和主要内容。
- `enterprise_sentinel/excel_writer.py`：写入 Excel 并设置基础格式。
- `target_list.txt`：目标公司名单及主体关联信息。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行

先准备好本机 Chrome 的用户目录，确保你在对应平台里已经登录过。

macOS 常见用户目录：

```bash
~/Library/Application\ Support/Google/Chrome
```

先编辑 `target_list.txt`，推荐每行四列，用制表符分隔：

```text
主体名称    当前持股比例    关联实体    关联关系
比亚迪股份有限公司
```

如果你暂时只有企业名称，也可以先只写一列，后三列会留空。

执行示例：

```bash
python -m enterprise_sentinel \
  --platform tianyancha \
  --user-data-dir "~/Library/Application Support/Google/Chrome" \
  --profile-directory "Default" \
  --target-file ./target_list.txt \
  --output-dir ./output
```

如果你要抓企业预警通，把 `--platform` 改成 `qyyjt` 即可。

默认会在输出目录生成固定文件名：

```text
Enterprise_Sentinel_tianyancha_累计台账.xlsx
```

后续每天运行时，会先读取这个历史台账，再把当天新抓到的数据合并进去，并按 `主体名称 + 事件发生日期 + 标题及主要内容 + 来源` 去重。

## 登录态与 Chrome 配置

- 默认会先复制一份临时 Chrome 配置到 `/tmp` 再启动 DrissionPage，避免和你正在使用的 Chrome 冲突。
- 如果你确认当前 Chrome 已关闭，并且希望直接复用原始配置目录，可以加 `--use-live-profile`。
- 如果页面被重定向到登录页，脚本现在会直接提示“登录态不可用”，而不是误报成页面结构问题。

## 单公司调试

如果需要单独排查某一家公司的页面跳转和接口命中情况，可以运行：

```bash
python -m enterprise_sentinel.debug_probe \
  --platform tianyancha \
  --company "北京寺库商贸有限公司" \
  --user-data-dir "~/Library/Application Support/Google/Chrome" \
  --profile-directory "Default" \
  --output-json ./debug/tianyancha_beijing_siku.json
```

这个调试探针会输出：

- 最终页面标题
- 最终页面地址
- 捕获到的前若干个请求摘要
- 可选的完整调试 JSON

## 维护建议

- 要改抓取名单或主体关联信息：直接编辑 `target_list.txt`。
- 要改监听关键词：编辑 `enterprise_sentinel/config.py` 里的 `LISTEN_KEYWORDS`。
- 要改字段映射：编辑 `enterprise_sentinel/config.py` 里的 `*_FIELDS`、`IMPORTANCE_MAP`、`SENTIMENT_MAP`。
- 要改累计去重规则：编辑 `enterprise_sentinel/main.py` 里的 `merge_history_and_new()`。
- 如果站点改版：优先调整 `enterprise_sentinel/config.py` 中的平台定位器，不要先动解析逻辑。

## 注意事项

1. 站点页面结构和接口命名可能会变化，当前方案已经把不稳定部分集中在 `config.py`。
2. 代码优先监听包含 `news/list`、`risk`、`warning` 等关键词的接口。
3. 如果首次未抓到数据，请先确认登录态仍然有效，再根据实际接口名称补充 `LISTEN_KEYWORDS`。

## 公网部署

仓库里的 Web 前后端单机容器化部署说明见 `docs/public-deploy.md`。

如果你要走 GitHub + Zeabur 的方式，说明见 `docs/zeabur-deploy.md`。

需要注意：`enterprise_sentinel` 爬虫依赖登录态 Chrome 配置，不建议直接暴露成公网接口；更合适的方式是把它作为内网定时任务产出数据，再由 `frontend + backend` 对外提供查询和分析页面。

## D.Risk 企业全景轻量版

“上海携程金融信息服务有限公司”已启用按需企业全景采集：网站创建异步任务，Windows 采集 Worker 复用专用 Edge 登录态，八个模块完整通过后才更新快照和 Excel。已审批的企业别名会在入库前归一为法定全称，较短的“携程金融”只显示确认候选。部署、登录项与现场验收说明见 [docs/company-profile-agent.md](docs/company-profile-agent.md)。

该旧采集链路现在由 `COMPANY_PROFILE_WORKER_ENABLED` 控制，默认关闭；代码和历史数据仍保留。新的首页“开始分析”使用下述投后监测任务，不依赖 Windows 登录态。

## D.Risk 投后监测

投后监测模块将“银联商务2026年股权投资风险监测项目”79条规则作为版本化规则集。每次点击开始分析都会创建新的 `analysis_run`，重新请求 A 股数据、公司官网、监管披露和权威公开来源；数据库只用于历史对比和留痕。

主要能力：

- 79条规则全量评估，严格区分 `hit`、`not_hit`、`insufficient_data` 和 `not_applicable`。
- 上传 XLSX、XLS、CSV、PDF、DOCX、TXT、PNG、JPG；扫描文件使用中文 OCR。
- 识别结果保留期间、单位、页码/单元格和置信度，低置信度字段需人工确认。
- 关联企业先进入候选清单，确认后才在下一次实时分析中采集。
- 手动生成月报草稿，支持网页审阅、审批、版本保留、Excel 下载和打印/PDF。
- 大模型未配置时仍能生成确定性草稿；配置 OpenAI-compatible API 后，仅发送命中规则、证据摘录和必要指标，并缓存相同输入。

正式部署需要 PostgreSQL、一个 backend Web 服务、一个执行 `python -m app.worker` 的 worker 服务，以及两个服务共享的 `/data/uploads` 持久化卷。生产环境必须设置 `AUTH_REQUIRED=true`、内部账号密码和随机 `SESSION_SECRET`。完整配置见 `.env.public.example`。

如不希望在 Secret 中保存明文密码，可在 backend 环境执行 `python -m app.services.auth`，输入密码后将输出写入 `APP_PASSWORD_HASH`。

本地未启用 `AUTH_REQUIRED` 时，登录页接受任意非空测试账号和密码；正式环境不得使用这一模式。

本机预览使用项目根目录 `.env.local`。启用 DeepSeek 时只需填写 `DEEPSEEK_API_KEY` 并重启 `scripts/run-local.sh`；默认使用 `https://api.deepseek.com` 和低成本的 `deepseek-flash`，Key 留空时不会发起模型请求。

`DEEPSEEK_API_KEY` 用于分析、归纳和报告写作，不等同于网页搜索。需要新闻与公开网页检索时另行填写 `SERPER_API_KEY`；A股财务和国家宏观指标通过 AkShare/官方来源采集，不依赖 Serper。

接口、状态语义和运行链路见 [docs/post-investment-monitoring.md](docs/post-investment-monitoring.md)。

## D.Risk 每日公开数据接入

第一版 Web 数据接入走“上市公司优先、每日批处理、合规稳定优先”的路线，不绕过验证码、不模拟登录，也不硬爬强反爬商业站点。

已接入的数据源注册表位于 `backend/app/services/generic_ingestion.py`：

- `cninfo_announcements`：巨潮资讯公告。
- `exchange_disclosure`：上交所 / 深交所公开披露搜索页摘要。
- `akshare_finance`：AkShare 金融数据补充。
- `news_search_api`：新闻搜索 API，只有配置 `SERPER_API_KEY` 后才会启用。

手动触发每日批处理：

```bash
python3 ingest_daily_data.py --company-name 比亚迪股份有限公司 --stock-code 002594
```

调试时可以只跑指定来源，且不写数据库：

```bash
python3 ingest_daily_data.py \
  --company-name 比亚迪股份有限公司 \
  --stock-code 002594 \
  --source cninfo_announcements \
  --dry-run
```

后端 API：

- `GET /api/v1/ingestion/sources`：查看来源注册表。
- `POST /api/v1/ingestion/daily-run`：触发一次批处理。
- `GET /api/v1/ingestion/runs/latest`：查看最近一次运行。
- `GET /api/v1/ingestion/dashboard-summary`：前端数据看板汇总。
