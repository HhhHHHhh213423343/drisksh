from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import socket
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from enterprise_sentinel.company_profile.collector import (
    CaptchaRequiredError,
    CompanyProfileCollector,
)
from enterprise_sentinel.company_profile.contract import (
    OPTIONAL_MODULE_KEYS,
    SOP_VERSION,
    core_specs_for_mode,
    specs_for_mode,
)
from enterprise_sentinel.company_profile.excel import build_company_profile_workbook
from enterprise_sentinel.engine import BrowserConfig, LoginRequiredError
from enterprise_sentinel.profile_manager import cleanup_prepared_profile, prepare_runtime_profile


class WorkerApiError(RuntimeError):
    pass


class WorkerApi:
    def __init__(self, base_url: str, collection_key: str, timeout: int = 90) -> None:
        self.base_url = base_url.rstrip("/")
        self.collection_key = collection_key
        self.timeout = timeout

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Collection-Key": self.collection_key,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                if response.status == 204:
                    return None
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise WorkerApiError(f"平台返回 HTTP {exc.code}：{detail}") from exc
        except URLError as exc:
            raise WorkerApiError(f"无法连接 D.Risk 平台：{exc.reason}") from exc


class CompanyProfileAgent:
    def __init__(
        self,
        api: WorkerApi,
        browser_config: BrowserConfig,
        *,
        worker_id: str,
        poll_interval: float = 5,
        clone_profile: bool = True,
        clone_root_dir: Path | None = None,
    ) -> None:
        self.api = api
        self.browser_config = browser_config
        self.worker_id = worker_id
        self.poll_interval = poll_interval
        self.clone_profile = clone_profile
        self.clone_root_dir = clone_root_dir

    def run(self, *, once: bool = False) -> None:
        while True:
            try:
                job = self.api.post(
                    "/api/v1/company-profile/worker/claim",
                    {"worker_id": self.worker_id},
                )
            except WorkerApiError as exc:
                print(
                    json.dumps(
                        {
                            "event": "company_profile_worker_api_unavailable",
                            "worker_id": self.worker_id,
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if once:
                    raise
                time.sleep(max(self.poll_interval, 5))
                continue
            if job:
                self._execute(job)
            if once:
                return
            time.sleep(self.poll_interval)

    def _execute(self, job: dict[str, Any]) -> None:
        job_started = time.perf_counter()
        run = job["run"]
        company = job["company"]
        run_id = str(run["id"])
        mode = str(run.get("mode") or "full")
        statuses: dict[str, str] = {}
        prepared = None
        collector = None
        try:
            profile_started = time.perf_counter()
            prepared = prepare_runtime_profile(
                source_user_data_dir=self.browser_config.user_data_dir,
                profile_directory=self.browser_config.profile_directory,
                clone_profile=self.clone_profile,
                clone_root_dir=self.clone_root_dir,
            )
            profile_prepare_ms = round((time.perf_counter() - profile_started) * 1000)
            runtime_browser = BrowserConfig(
                user_data_dir=prepared.user_data_dir,
                profile_directory=self.browser_config.profile_directory,
                browser_path=self.browser_config.browser_path,
                use_live_profile=not self.clone_profile,
                load_timeout=self.browser_config.load_timeout,
                packet_timeout=self.browser_config.packet_timeout,
                max_pages=self.browser_config.max_pages,
                max_packets_per_page=self.browser_config.max_packets_per_page,
                start_minimized=True,
                direct_connection=self.browser_config.direct_connection,
                attach_port=self.browser_config.attach_port,
            )
            browser_started = time.perf_counter()
            collector = CompanyProfileCollector(runtime_browser)
            browser_startup_ms = round((time.perf_counter() - browser_started) * 1000)

            def heartbeat(current: int, module: str, module_statuses: dict[str, str]) -> None:
                statuses.clear()
                statuses.update(module_statuses)
                self.api.post(
                    f"/api/v1/company-profile/worker/runs/{run_id}/heartbeat",
                    {
                        "worker_id": self.worker_id,
                        "progress_current": current,
                        "current_module": module,
                        "module_statuses": module_statuses,
                    },
                )

            collection_started = time.perf_counter()
            result = collector.collect(
                company["name"],
                company_code=str((company.get("qyyjt_profile") or {}).get("company_code") or ""),
                heartbeat=heartbeat,
                specs=specs_for_mode(mode),
            )
            collection_ms = round((time.perf_counter() - collection_started) * 1000)
            incomplete = {
                key: status
                for key, status in result["module_statuses"].items()
                if key not in OPTIONAL_MODULE_KEYS and status not in {"PASS", "EMPTY_VALID"}
            }
            if incomplete:
                raise RuntimeError(
                    f"企业全景存在不完整模块，不发布新快照：{incomplete}"
                )
            excel_started = time.perf_counter()
            filename, excel_bytes = build_company_profile_workbook(
                company["name"],
                result["normalized_data"],
                result["captured_at"],
                core_specs_for_mode(mode),
            )
            excel_ms = round((time.perf_counter() - excel_started) * 1000)
            agent_stages = {
                "profile_prepare_ms": profile_prepare_ms,
                "browser_startup_ms": browser_startup_ms,
                "collection_ms": collection_ms,
                "excel_ms": excel_ms,
            }
            result["raw_data"].setdefault("performance", {})["agent_stages"] = agent_stages
            result["normalized_data"].setdefault("performance_summary", {})[
                "agent_stages"
            ] = agent_stages
            upload_started = time.perf_counter()
            self.api.post(
                f"/api/v1/company-profile/worker/runs/{run_id}/complete",
                {
                    "worker_id": self.worker_id,
                    "captured_at": result["captured_at"].isoformat(),
                    "sop_version": SOP_VERSION,
                    "company_code": result["company_code"],
                    "module_statuses": result["module_statuses"],
                    "normalized_data": result["normalized_data"],
                    "raw_data": result["raw_data"],
                    "excel_filename": filename,
                    "excel_sha256": hashlib.sha256(excel_bytes).hexdigest(),
                    "excel_base64": base64.b64encode(excel_bytes).decode("ascii"),
                },
            )
            upload_ms = round((time.perf_counter() - upload_started) * 1000)
            print(
                json.dumps(
                    {
                        "event": "company_profile_performance",
                        "run_id": run_id,
                        **agent_stages,
                        "upload_ms": upload_ms,
                        "total_ms": round((time.perf_counter() - job_started) * 1000),
                    },
                    ensure_ascii=False,
                )
            )
        except LoginRequiredError as exc:
            self._fail(run_id, "waiting_for_login", "login_required", str(exc), statuses)
        except CaptchaRequiredError as exc:
            self._fail(run_id, "waiting_for_captcha", "captcha_required", str(exc), statuses)
        except PermissionError as exc:
            self._fail(run_id, "failed", "permission_denied", str(exc), statuses)
        except Exception as exc:
            self._fail(run_id, "failed", type(exc).__name__, str(exc), statuses)
        finally:
            if collector:
                collector.close()
            cleanup_prepared_profile(prepared)

    def _fail(
        self,
        run_id: str,
        status: str,
        error_code: str,
        error_message: str,
        module_statuses: dict[str, str],
    ) -> None:
        try:
            self.api.post(
                f"/api/v1/company-profile/worker/runs/{run_id}/fail",
                {
                    "worker_id": self.worker_id,
                    "status": status,
                    "error_code": error_code[:64],
                    "error_message": error_message[:4000],
                    "module_statuses": module_statuses,
                },
            )
        except WorkerApiError as exc:
            print(
                json.dumps(
                    {
                        "event": "company_profile_failure_report_deferred",
                        "run_id": run_id,
                        "status": status,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )


def default_worker_id() -> str:
    user = getpass.getuser().strip() or "worker"
    return f"{socket.gethostname()}-{user}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="D.Risk 企业全景采集 Worker")
    parser.add_argument("--api-url", default=os.getenv("DRISK_API_URL", "http://127.0.0.1:3000"))
    parser.add_argument("--collection-key", default=os.getenv("COLLECTION_API_KEY", ""))
    parser.add_argument("--user-data-dir", required=True)
    parser.add_argument("--profile-directory", default="Default")
    parser.add_argument("--browser-path", default="")
    parser.add_argument("--worker-id", default=default_worker_id())
    parser.add_argument("--poll-interval", type=float, default=5)
    parser.add_argument("--use-live-profile", action="store_true")
    parser.add_argument("--clone-root-dir", default=os.getenv("DRISK_PROFILE_CLONE_ROOT", ""))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--direct-connection", action="store_true", help="专用浏览器不走系统代理")
    parser.add_argument("--attach-port", type=int, default=0, help="附着到已打开专用浏览器的调试端口")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.collection_key:
        raise SystemExit("必须通过 --collection-key 或 COLLECTION_API_KEY 配置采集密钥。")
    agent = CompanyProfileAgent(
        WorkerApi(args.api_url, args.collection_key),
        BrowserConfig(
            user_data_dir=Path(args.user_data_dir).expanduser(),
            profile_directory=args.profile_directory,
            browser_path=args.browser_path or None,
            start_minimized=True,
            direct_connection=args.direct_connection,
            attach_port=args.attach_port,
        ),
        worker_id=args.worker_id,
        poll_interval=args.poll_interval,
        clone_profile=not args.use_live_profile,
        clone_root_dir=(Path(args.clone_root_dir).expanduser() if args.clone_root_dir else None),
    )
    agent.run(once=args.once)


if __name__ == "__main__":
    main()
