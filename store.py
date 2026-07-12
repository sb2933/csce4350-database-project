"""
store.py

The database engine. Wires together:
  - Index: the custom (non-dict) in-memory index
  - Log:   the append-only, fsync'ed, checksummed on-disk log

Design notes
------------
Every *mutating* command is turned into a small canonical "record"
(e.g. {"op": "SET", "key": "a", "value": "1"}) before being applied.
The same record is:
  1. applied to the in-memory Index via `_apply_record`, and
  2. appended to the on-disk log via `Log.append`.

On startup we replay the log and feed every record straight back
through `_apply_record` (skipping the disk write, since it's already
on disk) to rebuild identical in-memory state. This guarantees the
live path and the replay path can never drift apart.

TTL handling: EXPIRE <key> <seconds> is converted to an absolute
EXPIREAT record (key, epoch timestamp) before logging. This matters
for correctness across restarts -- if we logged the relative number
of seconds, replaying the log later would restart the countdown from
the wrong point in time.

Transactions: BEGIN starts buffering subsequent mutating commands
in memory *without* applying or logging them. COMMIT applies + logs
every buffered record in order (so the block lands on disk together).
ABORT just discards the buffer. Reads always see the last committed
state; uncommitted writes in an open transaction are not visible.
"""

import time

from index import Index
from log import Log


class StoreError(Exception):
    pass


class Entry:
    """One value slot in the top-level index."""

    __slots__ = ("type", "data", "expire_at")

    def __init__(self, type_, data, expire_at=None):
        self.type = type_          # "string" | "hash" | "list"
        self.data = data
        self.expire_at = expire_at  # epoch seconds, or None


class KVStore:
    def __init__(self, path="data.db"):
        self.index = Index()
        self.log = Log(path)
        self.in_tx = False
        self.tx_buffer = []  # list of pending records during a transaction
        self._replay()

    # ------------------------------------------------------------------
    # startup replay
    # ------------------------------------------------------------------
    def _replay(self):
        for record in self.log.replay():
            self._apply_record(record)

    # ------------------------------------------------------------------
    # low level helpers
    # ------------------------------------------------------------------
    def _live_entry(self, key):
        """Return the Entry for key if present and not expired."""
        entry = self.index.get(key)
        if entry is None:
            return None
        if entry.expire_at is not None and time.time() >= entry.expire_at:
            self.index.delete(key)
            return None
        return entry

    def _apply_record(self, record):
        op = record["op"]
        if op == "SET":
            self.index.set(record["key"], Entry("string", record["value"]))
        elif op == "DEL":
            self.index.delete(record["key"])
        elif op == "MSET":
            for k, v in record["pairs"]:
                self.index.set(k, Entry("string", v))
        elif op == "EXPIREAT":
            entry = self.index.get(record["key"])
            if entry is not None:
                entry.expire_at = record["expire_at"]
        elif op == "HSET":
            entry = self.index.get(record["hash"])
            if entry is None or entry.type != "hash":
                entry = Entry("hash", Index())
                self.index.set(record["hash"], entry)
            entry.data.set(record["key"], record["value"])
        elif op == "LPUSH":
            entry = self.index.get(record["key"])
            if entry is None or entry.type != "list":
                entry = Entry("list", [])
                self.index.set(record["key"], entry)
            entry.data.insert(0, record["value"])
        elif op == "RPUSH":
            entry = self.index.get(record["key"])
            if entry is None or entry.type != "list":
                entry = Entry("list", [])
                self.index.set(record["key"], entry)
            entry.data.append(record["value"])
        elif op == "LPOP":
            entry = self.index.get(record["key"])
            if entry is not None and entry.type == "list" and entry.data:
                entry.data.pop(0)
        elif op == "RPOP":
            entry = self.index.get(record["key"])
            if entry is not None and entry.type == "list" and entry.data:
                entry.data.pop()
        elif op == "FLUSHDB":
            self.index.clear()
        else:
            raise StoreError(f"unknown record op {op!r} during replay")

    def _commit(self, record):
        """Apply + persist one record, respecting an open transaction."""
        if self.in_tx:
            self.tx_buffer.append(record)
        else:
            self._apply_record(record)
            self.log.append(record)

    # ------------------------------------------------------------------
    # command dispatch
    # ------------------------------------------------------------------
    def execute(self, line):
        """Parse and run one command line, returning the response text."""
        tokens = line.strip().split()
        if not tokens:
            return None
        cmd = tokens[0].upper()
        args = tokens[1:]
        try:
            handler = getattr(self, f"cmd_{cmd}", None)
            if handler is None:
                return f"(error) unknown command '{cmd}'"
            return handler(args)
        except StoreError as e:
            return f"(error) {e}"

    # -- basic --------------------------------------------------------
    def cmd_SET(self, args):
        if len(args) < 2:
            raise StoreError("SET requires <key> <value>")
        key, value = args[0], " ".join(args[1:])
        self._commit({"op": "SET", "key": key, "value": value})
        return "OK"

    def cmd_GET(self, args):
        if len(args) != 1:
            raise StoreError("GET requires <key>")
        entry = self._live_entry(args[0])
        if entry is None:
            return ""
        if entry.type != "string":
            raise StoreError("wrong type for GET")
        return entry.data

    def cmd_DEL(self, args):
        if len(args) != 1:
            raise StoreError("DEL requires <key>")
        existed = self._live_entry(args[0]) is not None
        self._commit({"op": "DEL", "key": args[0]})
        return f"(integer) {1 if existed else 0}"

    def cmd_EXISTS(self, args):
        if len(args) != 1:
            raise StoreError("EXISTS requires <key>")
        return f"(integer) {1 if self._live_entry(args[0]) is not None else 0}"

    # -- multi ----------------------------------------------------------
    def cmd_MSET(self, args):
        if len(args) < 2 or len(args) % 2 != 0:
            raise StoreError("MSET requires <k1> <v1> [k2 v2 ...]")
        pairs = [[args[i], args[i + 1]] for i in range(0, len(args), 2)]
        self._commit({"op": "MSET", "pairs": pairs})
        return "OK"

    def cmd_MGET(self, args):
        if not args:
            raise StoreError("MGET requires at least one <key>")
        out = []
        for k in args:
            entry = self._live_entry(k)
            if entry is None or entry.type != "string":
                out.append("")
            else:
                out.append(entry.data)
        return "\n".join(out)

    # -- expiration -------------------------------------------------------
    def cmd_EXPIRE(self, args):
        if len(args) != 2:
            raise StoreError("EXPIRE requires <key> <seconds>")
        key, seconds = args[0], args[1]
        try:
            seconds = float(seconds)
        except ValueError:
            raise StoreError("EXPIRE seconds must be numeric")
        entry = self._live_entry(key)
        if entry is None:
            return "(integer) 0"
        expire_at = time.time() + seconds
        self._commit({"op": "EXPIREAT", "key": key, "expire_at": expire_at})
        return "(integer) 1"

    def cmd_TTL(self, args):
        if len(args) != 1:
            raise StoreError("TTL requires <key>")
        entry = self._live_entry(args[0])
        if entry is None:
            return "(integer) -2"  # no such key
        if entry.expire_at is None:
            return "(integer) -1"  # no expiry set
        remaining = int(entry.expire_at - time.time())
        return f"(integer) {max(remaining, 0)}"

    # -- range --------------------------------------------------------
    def cmd_RANGE(self, args):
        if len(args) != 2:
            raise StoreError("RANGE requires <start> <end>")
        start, end = args
        matches = []
        for key in self.index.keys():
            if self._live_entry(key) is None:  # also prunes expired keys
                continue
            if start <= key <= end:
                matches.append(key)
        matches.sort()
        return "\n".join(matches + ["END"])

    # -- transactions ---------------------------------------------------
    def cmd_BEGIN(self, args):
        if self.in_tx:
            raise StoreError("transaction already in progress")
        self.in_tx = True
        self.tx_buffer = []
        return "OK"

    def cmd_COMMIT(self, args):
        if not self.in_tx:
            raise StoreError("no transaction in progress")
        buffered = self.tx_buffer
        self.in_tx = False
        self.tx_buffer = []
        for record in buffered:
            self._apply_record(record)
            self.log.append(record)
        return "OK"

    def cmd_ABORT(self, args):
        if not self.in_tx:
            raise StoreError("no transaction in progress")
        self.in_tx = False
        self.tx_buffer = []
        return "OK"

    # -- hashes -----------------------------------------------------------
    def cmd_HSET(self, args):
        if len(args) < 3:
            raise StoreError("HSET requires <hash> <key> <value>")
        hash_key, field, value = args[0], args[1], " ".join(args[2:])
        existing = self._live_entry(hash_key)
        is_new_field = not (
            existing is not None
            and existing.type == "hash"
            and existing.data.exists(field)
        )
        self._commit({"op": "HSET", "hash": hash_key, "key": field, "value": value})
        return f"(integer) {1 if is_new_field else 0}"

    def cmd_HGET(self, args):
        if len(args) != 2:
            raise StoreError("HGET requires <hash> <key>")
        entry = self._live_entry(args[0])
        if entry is None or entry.type != "hash":
            return ""
        value = entry.data.get(args[1])
        return "" if value is None else value

    def cmd_HGETALL(self, args):
        if len(args) != 1:
            raise StoreError("HGETALL requires <hash>")
        entry = self._live_entry(args[0])
        if entry is None or entry.type != "hash":
            return ""
        items = entry.data.items()
        if not items:
            return ""
        return "\n".join(f"{k} {v}" for k, v in items)

    # -- lists --------------------------------------------------------
    def cmd_LPUSH(self, args):
        if len(args) < 2:
            raise StoreError("LPUSH requires <key> <value>")
        key, value = args[0], " ".join(args[1:])
        self._commit({"op": "LPUSH", "key": key, "value": value})
        entry = self._live_entry(key)
        return f"(integer) {len(entry.data)}"

    def cmd_RPUSH(self, args):
        if len(args) < 2:
            raise StoreError("RPUSH requires <key> <value>")
        key, value = args[0], " ".join(args[1:])
        self._commit({"op": "RPUSH", "key": key, "value": value})
        entry = self._live_entry(key)
        return f"(integer) {len(entry.data)}"

    def cmd_LRANGE(self, args):
        if len(args) != 3:
            raise StoreError("LRANGE requires <key> <start> <stop>")
        key = args[0]
        try:
            start, stop = int(args[1]), int(args[2])
        except ValueError:
            raise StoreError("LRANGE start/stop must be integers")
        entry = self._live_entry(key)
        if entry is None or entry.type != "list":
            return "END"
        lst = entry.data
        n = len(lst)
        # normalize negative indices, redis-style inclusive range
        if start < 0:
            start = max(n + start, 0)
        if stop < 0:
            stop = n + stop
        stop = min(stop, n - 1)
        if start > stop or n == 0:
            return "END"
        return "\n".join(lst[start:stop + 1] + ["END"])

    def cmd_LPOP(self, args):
        if len(args) != 1:
            raise StoreError("LPOP requires <key>")
        entry = self._live_entry(args[0])
        if entry is None or entry.type != "list" or not entry.data:
            return ""
        value = entry.data[0]
        self._commit({"op": "LPOP", "key": args[0]})
        return value

    def cmd_RPOP(self, args):
        if len(args) != 1:
            raise StoreError("RPOP requires <key>")
        entry = self._live_entry(args[0])
        if entry is None or entry.type != "list" or not entry.data:
            return ""
        value = entry.data[-1]
        self._commit({"op": "RPOP", "key": args[0]})
        return value

    # -- counters -----------------------------------------------------
    def _incr_by(self, key, delta):
        entry = self._live_entry(key)
        if entry is None:
            current = 0
        elif entry.type != "string":
            raise StoreError("value is not an integer")
        else:
            try:
                current = int(entry.data)
            except ValueError:
                raise StoreError("value is not an integer")
        new_value = current + delta
        self._commit({"op": "SET", "key": key, "value": str(new_value)})
        return new_value

    def cmd_INCR(self, args):
        if len(args) != 1:
            raise StoreError("INCR requires <key>")
        return f"(integer) {self._incr_by(args[0], 1)}"

    def cmd_DECR(self, args):
        if len(args) != 1:
            raise StoreError("DECR requires <key>")
        return f"(integer) {self._incr_by(args[0], -1)}"

    # -- management -----------------------------------------------------
    def cmd_FLUSHDB(self, args):
        self._commit({"op": "FLUSHDB"})
        return "OK"

    def close(self):
        self.log.close()
