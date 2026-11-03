"""为单公司演示补充携程金融的公开风险数据（逻辑见 app.services.ctrip_demo_seed）。

运行方式（在项目根目录）：

    python3 scripts/seed_ctrip_demo_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.db.session import SessionLocal  # noqa: E402
from app.services.ctrip_demo_seed import seed_ctrip_demo  # noqa: E402


def seed() -> None:
    db = SessionLocal()
    try:
        seed_ctrip_demo(db)
    finally:
        db.close()


if __name__ == "__main__":
    seed()
