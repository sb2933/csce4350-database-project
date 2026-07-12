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


def _force_utf8_lf_stdio():
    """
    Reconfigure stdin/stdout to UTF-8 with LF-only line endings.

    On Windows, Python's stdio streams default to the process's ANSI
    codepage (e.g. cp1252) rather than UTF-8, and text-mode stdout
    silently translates every '\\n' -- including ones embedded inside
    multi-line responses like RANGE/LRANGE/MGET -- into '\\r\\n'. A
    byte-oriented black-box tester reading a known response length can
    get desynced by those extra bytes, corrupting every command after
    it (the same class of framing bug documented in the README). This
    pins both streams to a fixed, unambiguous encoding/newline so the
    program behaves identically on every platform.
    """
    sys.stdin.reconfigure(encoding="utf-8", newline="\n")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")


def main():
    _force_utf8_lf_stdio()
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
