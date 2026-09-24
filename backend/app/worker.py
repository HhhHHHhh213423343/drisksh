from __future__ import annotations

from app.db.base import Base
from app.db.session import engine
from app.models import *  # noqa: F401,F403
from app.services.monitoring_jobs import run_worker_forever
from app.services.rule_seed import ensure_default_rule_set
from app.db.session import SessionLocal


def main() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        ensure_default_rule_set(db)
    run_worker_forever()


if __name__ == "__main__":
    main()
