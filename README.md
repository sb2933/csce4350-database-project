# Simple Key-Value Store

A persistent, command-line key-value database built from scratch in Python,
following the "Build Your Own Database" approach: an append-only log for
durability, and a hand-rolled (non-`dict`) in-memory index.

## Running

```bash
python3 main.py            # uses ./data.db
python3 main.py mydata.db  # or a custom path
```

Commands are read one per line from STDIN, responses are written to STDOUT.
Type `EXIT` or send EOF to quit.

## Architecture

| File         | Responsibility                                                             |
|--------------|-----------------------------------------------------------------------------|
| `index.py`   | Custom array-based key→value index (linear scan, no built-in `dict`/`map`). |
| `log.py`     | Append-only, checksummed, `fsync`'ed on-disk log (`data.db`).               |
| `store.py`   | The database engine: command handlers, TTL, transactions, replay logic.     |
| `main.py`    | STDIN/STDOUT REPL, the black-box-testable entry point.                      |

### Persistence

Every mutating command is turned into a small canonical JSON record
(e.g. `{"op": "SET", "key": "a", "value": "1"}`), which is:

1. applied to the in-memory `Index`, and
2. appended as one line to `data.db`, prefixed with a CRC32 checksum,
   then `flush()`'ed and `fsync()`'ed before the command returns.

On startup, the log is replayed in order to rebuild the index from scratch.
If the process crashed mid-write last time, the final line may be
incomplete or fail its checksum — that line (and only that line, since
everything before it was already `fsync`'ed) is discarded, and the file
is truncated back to the last valid record so future writes aren't glued
onto corrupted bytes.

`EXPIRE key seconds` is converted to an absolute `EXPIREAT` record before
being logged, so TTLs survive a restart correctly instead of restarting
the countdown from whenever the log happens to be replayed.

### Transactions

`BEGIN` starts buffering subsequent mutating commands in memory without
applying or persisting them. `COMMIT` applies and logs the whole buffered
block in order; `ABORT` discards it. Reads always see the last committed
state — writes made inside an open transaction are not visible until
`COMMIT`.

### Indexing

The assignment requires a hand-built index rather than a language
built-in map. `index.py` implements this as a flat array of `[key,
value]` pairs with linear-scan lookup/insert/delete, and last-write-wins
semantics (an existing slot is mutated in place rather than duplicated).
This same `Index` class backs both the top-level database and each
hash created by `HSET`. Lists (`LPUSH`/`RPUSH`/`LRANGE`) use a plain
array too, since ordered sequential storage is exactly what the
assignment's "array" option describes.

## Output format

Responses loosely follow redis-cli conventions: `OK`, `(integer) N`,
`(nil)`, `(empty)` for an empty multi-value result, and `(error) ...`
for errors. **If Gradebot expects an different exact format, adjust the
return strings in `store.py`** — they're centralized in one place per
command handler.

## Testing

```bash
python3 -m unittest discover -v
```

`test_store.py` covers the custom index, all commands, transactions, and
two persistence scenarios: a clean restart, and recovery from a
simulated crash (torn/corrupted final log write).

## Linting

```bash
pip install flake8
flake8 . --max-line-length=100
```

Enforced automatically in CI (`.github/workflows/ci.yml`) on every push.

## Blackbox testing (Gradebot)

1. Work directory: the repo root.
2. Command to run: `python3 main.py`
3. After running Gradebot, save the rubric screenshot as
   `gradebot_screenshot.png` in the repo root and commit it.

## Git

Tag the final working version:

```bash
git tag project
git push origin project
```
