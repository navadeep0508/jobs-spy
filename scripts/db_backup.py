from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from db.db import create_backup, integrity_check, restore_backup


def main() -> int:
    parser = argparse.ArgumentParser(description="Jobs Spy SQLite backup and restoration")
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup")
    backup.add_argument("--source", required=True)
    backup.add_argument("--destination", required=True)

    restore = sub.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--target", required=True)

    check = sub.add_parser("check")
    check.add_argument("--database", required=True)

    args = parser.parse_args()
    if args.command == "backup":
        ok, detail = create_backup(args.source, args.destination)
    elif args.command == "restore":
        ok, detail = restore_backup(args.backup, args.target)
    else:
        ok, detail = integrity_check(args.database)
    print(detail)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
