"""`make verify-audit`: verify the audit hash chain (FR-61, approval-and-audit §5).

Exit codes: 0 chain intact, 1 chain broken (tampering or corruption), 2 cannot verify
(database not migrated). Prints sequence numbers and the kind of break, never event data.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

from app.audit.chain import verify_chain
from app.audit.models import AuditEvent
from app.config import get_settings
from app.db import make_engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the invoice-flow audit hash chain.")
    parser.add_argument(
        "--database-url",
        help="SQLAlchemy URL (default: the database under INVOICE_FLOW_DATA_DIR)",
    )
    args = parser.parse_args(argv)
    url = args.database_url or get_settings().database_url

    engine = make_engine(url)
    try:
        with engine.connect() as conn:
            if not inspect(conn).has_table(AuditEvent.__tablename__):
                print(
                    f"cannot verify: no {AuditEvent.__tablename__} table "
                    "(run `uv run alembic upgrade head` first)",
                    file=sys.stderr,
                )
                return 2
            result = verify_chain(conn)
    except OperationalError:
        print("cannot verify: the database could not be opened", file=sys.stderr)
        return 2
    finally:
        engine.dispose()

    if result.ok:
        print(f"audit chain OK: {result.checked} events verified")
        return 0
    print(f"audit chain BROKEN: {len(result.breaks)} problem(s) in {result.checked} events")
    for brk in result.breaks:
        print(f"  seq {brk.seq}: {brk.problem}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
