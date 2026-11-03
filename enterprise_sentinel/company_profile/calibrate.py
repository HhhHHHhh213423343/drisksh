"""在已登录的 Windows Edge 上试采司法模块，输出锚点校准结果。

只读取页面，不上传 D.Risk、不写数据库。结果 JSON 包含每个司法模块的状态、
识别到的表头和行数，以及信用页上所有可见标题，用于修正 contract.LITIGATION_SPECS。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from enterprise_sentinel.company_profile.collector import CompanyProfileCollector
from enterprise_sentinel.company_profile.contract import LITIGATION_SPECS
from enterprise_sentinel.engine import BrowserConfig


HEADINGS_SCRIPT = r"""
const visible = (node) => node.getClientRects().length > 0;
return Array.from(document.querySelectorAll("h1,h2,h3,h4,h5,h6,[role=heading],[class*=title],[id]"))
  .filter(visible)
  .map((node) => ({ id: node.id || "", text: (node.innerText || "").trim().slice(0, 40) }))
  .filter((item) => item.text && item.text.length <= 40)
  .slice(0, 300);
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="司法模块锚点校准")
    parser.add_argument("--company", default="上海携程金融信息服务有限公司")
    parser.add_argument("--company-code", default="C71376C50868F97142E2EDD20C553765")
    parser.add_argument("--user-data-dir", required=True)
    parser.add_argument("--profile-directory", default="Default")
    parser.add_argument("--browser-path", default="")
    parser.add_argument("--output-json", default="litigation-calibration.json")
    parser.add_argument("--direct-connection", action="store_true", help="专用浏览器不走系统代理")
    parser.add_argument("--attach-port", type=int, default=0, help="附着到已打开专用浏览器的调试端口")
    args = parser.parse_args()

    collector = CompanyProfileCollector(
        BrowserConfig(
            user_data_dir=Path(args.user_data_dir).expanduser(),
            profile_directory=args.profile_directory,
            browser_path=args.browser_path or None,
            use_live_profile=True,
            direct_connection=args.direct_connection,
            attach_port=args.attach_port,
        )
    )
    try:
        result = collector.collect(
            args.company, company_code=args.company_code, specs=LITIGATION_SPECS
        )
        page = collector.engine.browser.new_tab()
        try:
            page.get(LITIGATION_SPECS[0].url(args.company_code))
            headings = page.run_js(HEADINGS_SCRIPT)
        finally:
            page.close()
    finally:
        collector.close()

    modules = result["normalized_data"]["modules"]
    report = {
        "company": args.company,
        "module_statuses": result["module_statuses"],
        "module_errors": result["raw_data"]["performance"].get("module_errors", {}),
        "modules": {
            key: {
                "row_count": module.get("row_count"),
                "display_total": module.get("display_total"),
                "sections": [
                    {"title": section.get("title"), "columns": section.get("columns")}
                    for section in module.get("sections") or []
                ],
            }
            for key, module in modules.items()
        },
        "credit_page_headings": headings,
    }
    Path(args.output_json).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["module_statuses"], ensure_ascii=False))
    print(f"校准结果已写入 {args.output_json}")


if __name__ == "__main__":
    main()
