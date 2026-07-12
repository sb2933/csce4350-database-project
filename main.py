#!/usr/bin/env python3
"""
main.py

Black-box CLI entry point. Reads commands on STDIN, one per line,
writes responses to STDOUT, until EXIT or EOF.

Usage:
    python3 main.py [path-to-data-file]

Defaults to ./data.db, per the assignment spec.
"""

import sys

from store import KVStore


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data.db"
    db = KVStore(db_path)
    try:
        for line in sys.stdin:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.upper() == "EXIT":
                break
            response = db.execute(stripped)
            if response is not None:
                print(response, flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
