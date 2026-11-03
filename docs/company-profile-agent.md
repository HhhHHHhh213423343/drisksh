# D.Risk 企业全景 Windows 采集 Worker

企业全景的网页、任务和快照仍保存在现有 `frontend + backend + PostgreSQL` 服务器。Windows Worker 主动通过 HTTPS 领取任务，使用专用 Edge Profile 采集企业预警通，再上传规范化 JSON 和最终 Excel。服务器不保存企业预警通密码、Cookie 或验证码。

## 1. 部署前提

- Windows 10/11 可以正常打开企业预警通；
- Windows 可以通过 HTTPS 访问 D.Risk 前后端；
- 已安装 Microsoft Edge 和 Python 3.11 或更高版本；
- 后端已部署本版本的 `/api/v1/company-profile/worker/*` 接口；
- Zeabur backend 已配置至少 32 位的 `COLLECTION_API_KEY`。

后端根路径返回 `404 Not Found` 不代表服务失败。正式健康检查地址是：

```text
https://<D.Risk 后端域名>/health
```

## 2. Windows 首次安装

将项目放到 Windows 本地磁盘，在 PowerShell 中进入 `deploy\windows`，按顺序运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
powershell -ExecutionPolicy Bypass -File .\login.ps1
powershell -ExecutionPolicy Bypass -File .\configure.ps1 -ApiUrl 'https://d-risk-ai.zeabur.app'
powershell -ExecutionPolicy Bypass -File .\preflight.ps1
```

`login.ps1` 会打开独立的 Edge Profile。请在其中完成企业预警通登录，打开任意企业详情页验证权限，然后关闭该 Edge 窗口。登录失效或出现验证码时重新运行该脚本；Worker 不会绕过验证码。

Windows Worker 直接使用这份专用 Edge Profile，以保留企业预警通刷新后的 Cookie。运行 Worker 前必须关闭 `login.ps1` 打开的专用 Edge 窗口；普通浏览器窗口不应使用该专用 Profile。

`configure.ps1` 将 D.Risk 地址和采集密钥写入当前 Windows 用户的环境变量，密钥不会写入代码库或任务计划参数。

## 3. 单次验收

先在 D.Risk 搜索下列任一安全别名：

- `上海携程金融信息服务有限公司`
- `携程金融信息服务有限公司`
- `上海携程金融信息服务`
- `携程金融信息服务`

这些输入均会在入库前映射为法定全称。输入较短的 `携程金融` 时，页面只显示候选并要求用户确认，不会立即建立采集任务。

随后在 Windows 运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\run-worker.ps1 -Once
```

验收标准：

1. 任务状态从“等待 Windows 采集节点”变为“正在采集”；
2. 固定 8 个模块全部为 `PASS` 或 `EMPTY_VALID`；
3. 工商变更包含独立的“变更前”和“变更后”列；
4. 如页面显示数量大于实际采集数量，模块标记为 `PARTIAL` 且不覆盖正式快照；
5. Excel 文件只包含固定顺序的 8 个 Sheet；
6. 24 小时内再次搜索直接复用完整快照，点击“重新采集”才强制新建任务。

## 3.1 司法模块校准（1.1.0 起）

1.1.0 起每次采集在八个核心模块之后追加四个司法模块：裁判文书、开庭公告、立案信息、被执行人（`enterprise_sentinel/company_profile/contract.py` 的 `LITIGATION_SPECS`）。司法模块为可选模块：失败时状态记为 `FAILED`、原因写入 `performance.module_errors`，**不影响**八个核心模块的快照发布，月报法律板块此时只依据动态监测和监管处罚。

司法模块的页面锚点是按企业预警通信用页结构预设的，首次部署必须在已登录的 Windows 上校准：

```powershell
powershell -ExecutionPolicy Bypass -File .\calibrate-litigation.ps1
```

没有 Windows 时可在 Mac 上校准（同样需要中国大陆网络和企业预警通账号）：

```bash
sh scripts/company-profile-mac-login.sh        # 在弹出的专用浏览器中登录后关闭窗口
sh scripts/company-profile-mac-calibrate.sh
```

Mac 脚本启动的专用浏览器默认不走系统代理（`--no-proxy-server`），本机开着 VPN 时企业预警通仍以直连访问，其他浏览器继续使用 VPN；如需专用浏览器也走系统代理，加 `DRISK_USE_SYSTEM_PROXY=1`。默认优先使用 Edge；要用 Google Chrome，三个 Mac 脚本前都加 `DRISK_BROWSER=chrome`（Chrome 的登录数据单独保存在 `.runtime/qyyjt-chrome`，与 Edge 不通用）。

脚本只读页面，不上传平台。Mac 结果写入 `.runtime/logs/litigation-calibration-*.json`。结果写入 `.runtime\logs\litigation-calibration-*.json`：

- `module_statuses` 四项均为 `PASS` 或 `EMPTY_VALID`：校准通过，无需修改；
- 某项为 `FAILED`：将该 JSON 发给开发，根据 `credit_page_headings` 中的实际标题和 `id` 修正对应模块的页面、锚点和表头后重新校准。

## 3.2 合作方、关联方轻量采集

被投企业的“开始分析”在 `COMPANY_PROFILE_WORKER_ENABLED=true` 时，会为已确认的合作方、关联方各排队一个 `monitor` 模式任务：只采监管处罚、动态监测和四个司法模块，Excel 只含监管处罚、动态监测两张表。任务仍按 24 小时缓存复用，Worker 依次领取；采集结果在**下一次**分析时进入规则评估和月报的按主体段落。携程金融 10 个关联主体单轮约需 10 个任务，请在月报生成前预留采集时间。

## 4. 设为 Windows 登录后自动运行

单次验收通过后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-startup-task.ps1 -StartNow
```

任务计划名称为 `D.Risk Company Profile Worker`。Worker 主动出站访问 D.Risk，Windows 不需要开放入站端口。日志保存在 `.runtime\logs`。

如需停用登录启动任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall-startup-task.ps1
```

## 5. 采集和发布规则

- 采集器按 `overview`、`creditData`、`monitor` 三个页面族复用标签页；
- SOP 版本为 `QYYJT-COMPANY-PROFILE-V3.2`；
- 企业名称、企业代码与页面身份必须一致；
- 头像首字和已知企业标签会进行结构化清洗；
- 任一模块失败或 `PARTIAL` 时，本轮不发布新快照；
- 登录、验证码、账号权限和区域访问问题不自动绕过。

macOS LaunchAgent 模板仍保留在 `deploy/com.drisk.company-profile-agent.plist.example`，但当前生产路径以 Windows Worker 为准。
