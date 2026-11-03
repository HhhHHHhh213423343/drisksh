# 投后监测月报部署与数据重建

本说明面向部署同事：从 GitHub 部署后，用一条命令在服务器数据库中重建携程金融的月度监测数据，并生成与本地测试一致结构的月报。服务结构、环境变量和数据库配置沿用 [zeabur-deploy.md](zeabur-deploy.md)。

## 1. 代码与数据在哪里

| 内容 | 位置 |
|---|---|
| 月报生成、评级、导出 | `backend/app/services/monthly_reports.py` |
| 携程金融演示数据（公司档案、历史事件、行业事件） | `backend/app/services/ctrip_demo_seed.py` |
| 监测实体清单（2 个合作方、8 个关联方） | `backend/app/data/monitoring_scopes.json` |
| 82 条监测规则 | `backend/app/data/rules_2026.json` |
| 每月人工整理的数据 | `backend/app/data/monthly/<YYYY-MM>/`（`*.xlsx` 外部风险数据、`宏观指标_*.csv`） |
| 一键重建命令 | `backend/app/bootstrap_monthly.py` |
| 本地测试生成的参照报告 | `docs/monitoring-data/2026-10/携程金融风险监测报告_2026-10_v11.xlsx` |

本地数据库（`.runtime/*.db`）不进入 Git；服务器上的数据全部由上表中的代码和数据文件重建。

## 2. 部署

1. 按 [zeabur-deploy.md](zeabur-deploy.md) 部署 `frontend`、`backend`、`worker` 和 PostgreSQL，部署分支为 `main`。
2. backend 与 worker 需配置的关键环境变量：
   - `DATABASE_URL`：PostgreSQL 连接串（两个服务相同）
   - `LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=deepseek-flash`、`LLM_API_KEY`（或 `DEEPSEEK_API_KEY`）：用于月报正文改写；不配置时生成确定性草稿
   - `DEMO_COMPANY_NAME=上海携程金融信息服务有限公司`
   - `AUTH_REQUIRED=true`、`APP_USERNAME`、`APP_PASSWORD`（或 `APP_PASSWORD_HASH`）、`SESSION_SECRET`
   - 密钥只配置在部署平台的环境变量中，不要写入仓库。

## 3. 首次部署后重建数据并生成月报

在 backend 服务的终端（Zeabur 控制台 → backend 服务 → 终端/执行命令，工作目录 `/app`）执行一次：

```bash
python -m app.bootstrap_monthly --period 2026-10 --generate
```

命令依次完成：建表 → 写入携程金融演示数据和 10 个监测主体 → 导入 `app/data/monthly/2026-10/` 下的外部风险数据与宏观指标 → 按 2026 年 10 月评估 82 条规则 → 生成月报。正常输出示例：

```text
期间：2026-10
  导入 携程金融-2026年10月外部风险数据(企业预警通+公开检索).xlsx：风险事件 14 条，行业事件 23 条
  导入 携程金融月度监测资料_2026-10.xlsx：风险事件 0 条，行业事件 7 条
  宏观指标 宏观指标_2026-10.csv：新增 7，更新 0
  月报 v1（deepseek-flash）：<报告编号>
  评级：external=…，operations=…，…
```

- 输出中出现“未使用大模型改写：…”时，说明大模型未配置或调用失败，报告为确定性草稿；按提示检查 `LLM_*` 变量后重新执行即可。
- 命令可重复执行：已导入的记录自动跳过，每次 `--generate` 新增一个月报版本。
- 需要同时拿到 Excel 时加 `--export /tmp/report.xlsx`。

## 4. 查看报告

登录网站 → 打开“上海携程金融信息服务有限公司” → 投后监测 → 月报，选择 2026-10 的最新版本，可网页审阅、调整评级、审批并下载 Excel。

报告结构与本地参照版一致：九个板块五档评级、外部风险五段（宏观经济趋势 / 行业监管政策 / 市场行业格局 / 头部机构动态 / 风险分析）、法律与舆情按主体分段、六张工作表的 Excel。正文由大模型改写，每次生成的措辞和个别板块评级可能略有差异，以人工审阅、调整后的版本为准。

## 5. 以后每月更新

1. 按 `docs/templates/携程金融月度监测资料收集模板.xlsx` 或 2026-10 的表格格式整理当月数据，宏观指标按 `宏观指标_2026-10.csv` 的列填写。
2. 放入 `backend/app/data/monthly/<YYYY-MM>/`，提交并推送到 GitHub，等待重新部署。
3. 在 backend 终端执行 `python -m app.bootstrap_monthly --period <YYYY-MM> --generate`。

本地导入同一份数据可使用 `python3 scripts/import_monthly_external_data.py <xlsx> --dry-run` 先检查。
