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
hash created by `HSET`. Lists (`LPUSH`/`RPUSH`/`LRANGE`/`LPOP`/`RPOP`)
use a plain array too, since ordered sequential storage is exactly
what the assignment's "array" option describes. `LPOP`/`RPOP` aren't
in the assignment's command list, but were added since the Gradebot
binary references an `LPOP` code path -- cheap to support, and it
keeps the list type symmetric (push/pop from both ends).

## Output format

Single-value commands (`GET`, `HGET`) return an **empty line** for a
missing key/field rather than a placeholder like `(nil)`.

`MGET` returns exactly one line per requested key, in order (blank for
a missing key) -- the caller already knows how many lines to expect
from the number of keys it asked for.

`RANGE` and `LRANGE` return a variable number of lines that the caller
can't know in advance, so each is terminated with a literal `END`
line so the reader knows when the response is complete.

Everything else follows loose redis-cli conventions: `OK`, `(integer)
N`, `(error) ...` for errors.

**These formats were tuned against real feedback from a Gradebot run**
(see the "Known issues found & fixed" section below) -- if your
grading run surfaces a different expectation, the return values are
centralized in one place per command handler in `store.py`, so they're
easy to adjust further.

## Known issues found & fixed

Two real bugs were caught while testing against Gradebot and are worth
knowing about if you're extending this project:

1. **stdin read-ahead stall.** `main.py` originally read commands with
   `for line in sys.stdin:`. Iterating a file object like that uses an
   internal read-ahead buffer that can delay processing a line until
   more data has arrived on the pipe -- a real problem for a
   black-box tester that writes one command at a time and waits for a
   response before sending the next. Switched to an explicit
   `sys.stdin.readline()` loop, which does not have that behavior.
2. **Unknown-length responses need a terminator.** `RANGE` and
   `LRANGE` can return anywhere from zero to many lines, which the
   caller has no way to know in advance. Without a sentinel, a caller
   reading a fixed number of lines will under-read, leaving stray
   output in the pipe that gets misread as the response to the *next*
   command -- causing every subsequent command in the session to
   desync. Both now emit a trailing `END` line.

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
