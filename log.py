"""
log.py

Append-only, crash-safe persistence layer, following the approach from
"Build Your Own Database" ch.1 and ch.6:

  - Every mutation is appended to data.db as one line, never rewritten
    in place.
  - Each line is checksummed. A crash can only ever corrupt the LAST
    write (everything before it was already fsync'ed), so on startup
    we replay lines in order and stop at the first checksum mismatch,
    discarding a possibly torn final write instead of crashing.
  - Every append is immediately flushed and fsync'ed before the
    command returns success to the caller, satisfying the "writes
    must be persisted to disk immediately" requirement.

Records are stored as "<crc32_hex>:<json>\n". JSON is used purely as
the on-disk record encoding (not as an index / map data structure) so
that arbitrary keys/values with spaces or special characters can be
stored unambiguously.
"""

import json
import os
import zlib


class Log:
    def __init__(self, path):
        self.path = path
        # If the process crashed mid-append last time, the file may end
        # with a torn (checksum-invalid, possibly newline-less) final
        # line. Left in place, the next append would be silently glued
        # onto that garbage, corrupting the new record too. So on open
        # we scan for the byte offset up to which every record is
        # valid, and truncate away anything after it.
        valid_length = self._valid_prefix_length(path)
        self._fp = open(self.path, "r+b") if os.path.exists(path) else open(
            self.path, "w+b"
        )
        self._fp.seek(0)
        self._fp.truncate(valid_length)
        self._fp.seek(valid_length)

    @staticmethod
    def _valid_prefix_length(path):
        """Byte offset up to (and including) the last valid record."""
        if not os.path.exists(path):
            return 0
        valid_length = 0
        with open(path, "rb") as f:
            offset = 0
            for raw in f:
                offset += len(raw)
                line = raw.rstrip(b"\n")
                if not line or not raw.endswith(b"\n"):
                    # empty line, or a final line with no trailing
                    # newline (torn write) -> stop here, don't count it
                    break
                try:
                    checksum_hex, payload = line.split(b":", 1)
                    checksum = int(checksum_hex, 16)
                except ValueError:
                    break
                if zlib.crc32(payload) != checksum:
                    break
                try:
                    json.loads(payload.decode("utf-8"))
                except ValueError:
                    break
                valid_length = offset
        return valid_length

    def append(self, record: dict):
        """Append one record, fsync'ed before returning."""
        payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
        checksum = zlib.crc32(payload)
        line = f"{checksum:08x}:".encode("ascii") + payload + b"\n"
        self._fp.write(line)
        self._fp.flush()
        os.fsync(self._fp.fileno())

    def replay(self):
        """
        Read every valid record from disk in order. Used on startup to
        rebuild the in-memory index. Yields dicts.
        """
        records = []
        if not os.path.exists(self.path):
            return records
        with open(self.path, "rb") as f:
            for raw in f:
                raw = raw.rstrip(b"\n")
                if not raw:
                    continue
                try:
                    checksum_hex, payload = raw.split(b":", 1)
                    checksum = int(checksum_hex, 16)
                except ValueError:
                    # malformed header -> torn write, stop replaying
                    break
                if zlib.crc32(payload) != checksum:
                    # corrupted / partially-written last entry -> stop
                    break
                try:
                    record = json.loads(payload.decode("utf-8"))
                except ValueError:
                    break
                records.append(record)
        return records

    def close(self):
        self._fp.close()
