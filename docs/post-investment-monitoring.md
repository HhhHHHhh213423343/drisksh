# D.Risk 投后监测运行说明

## 运行链路

1. `POST /api/v1/analysis-runs` 创建本次实时分析和持久化任务。
2. worker 强制刷新企业资料、A股财务数据和公开来源；采集失败会保留来源状态，不会回退成“无风险”。
3. 已确认的关联主体在下一次分析中一并采集。
4. 79条规则生成 `hit`、`not_hit`、`insufficient_data` 或 `not_applicable` 结果。
5. 用户上传材料并确认低置信度字段后，应重新运行相同月份的分析。
6. `POST /api/v1/monthly-reports` 基于该月已完成的分析任务生成草稿。
7. 草稿经编辑、审阅和审批后，可下载 Excel 或使用网页打印生成 PDF。

## 关键接口

- `POST /api/v1/auth/login`
- `POST /api/v1/analysis-runs`
- `GET /api/v1/analysis-runs/{run_id}`
- `GET /api/v1/analysis-runs/{run_id}/risk-summary`：普通页面使用，不返回规则原文
- `GET /api/v1/analysis-runs/{run_id}/rule-evaluations`：内部审计接口
- `PATCH /api/v1/rule-evaluations/{evaluation_id}`
- `GET/PATCH /api/v1/companies/{company_id}/related-entities`
- `POST /api/v1/companies/{company_id}/documents`
- `POST /api/v1/companies/{company_id}/manual-facts`
- `GET /api/v1/documents/{document_id}/extracted-facts`
- `POST /api/v1/documents/{document_id}/confirm-facts`
- `POST /api/v1/monthly-reports`
- `PATCH /api/v1/monthly-reports/{report_id}`
- `POST /api/v1/monthly-reports/{report_id}/approve`
- `GET /api/v1/monthly-reports/{report_id}/export.xlsx`

## 数据状态约束

- 只有已确认事实参与规则阈值计算；解析置信度不足的字段保持 `pending`。
- `not_hit` 仅表示所需结构化数据完整且未达到阈值。
- 未检索到资料、来源未接入或内部材料缺失时使用 `insufficient_data`。
- 命中结果必须带事实、网页事件或上传材料证据编号。
- 已审批报告不可原位修改；同一月份重新生成时自动增加版本号。

## 任务进程

正式环境由 backend 接收请求、worker 执行任务。两者连接同一个 PostgreSQL 并共享 `/data/uploads`。worker 启动命令：

```bash
python -m app.worker
```

本地单进程调试可设置 `INLINE_JOB_EXECUTION=true`，FastAPI 会在响应后执行同一个持久化任务。

## 大模型边界

未配置 `LLM_BASE_URL` 和 `LLM_MODEL` 时，系统使用确定性规则结论与固定写作结构生成草稿。配置后，大模型仅用于：

- 映射确定性解析无法识别的少量字段；
- 对存在候选证据的定性规则进行结构化复核；
- 改写月报草稿的语言。

完整文件、未脱敏个人信息和与规则无关的内容不会发送。相同输入通过 SHA-256 缓存，模型返回的证据编号必须来自本次输入。

DeepSeek API 本身不会登录或浏览企业预警通。它可以通过 Tool Calls 决定调用哪个外部采集函数，但网页请求、登录态、验证码和数据返回仍须由本系统提供。因此当前职责分工为：采集器或上传接口取得材料，DeepSeek 负责字段映射、风险判断和报告写作。需要登录的商业网站继续采用官方 API、人工导出上传，或在明确启用后使用本地登录态 Worker。
