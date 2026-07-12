#!/usr/bin/env python3
"""
main.py

Black-box CLI entry point. Reads commands on STDIN, one per line,
writes responses to STDOUT, until EXIT or EOF.

Usage:
    python3 main.py [path-to-data-file]

Defaults to ./data.db, per the assignment spec.

Note: commands are read with an explicit sys.stdin.readline() loop
rather than `for line in sys.stdin:`. Iterating a file object uses an
internal read-ahead buffer that can delay processing a line until more
data has arrived on the pipe -- a real problem for a black-box tester
that writes one command at a time and waits for a response before
sending the next. readline() does not have that read-ahead behavior,
so each command is handled as soon as it arrives.
"""

import sys

from store import KVStore


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data.db"
    db = KVStore(db_path)
    try:
        while True:
            line = sys.stdin.readline()
            if line == "":  # EOF, no trailing newline means the stream closed
                break
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.upper() == "EXIT":
                break
            try:
                response = db.execute(stripped)
            except Exception as e:  # noqa: BLE001 - never let a bad command kill the process
                response = f"(error) {e}"
            if response is not None:
                print(response, flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()