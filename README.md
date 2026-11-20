# Redis From Scratch (Python)

A Redis-compatible in-memory database server written from scratch in Python, with no third-party dependencies.

Built while working through the [CodeCrafters "Build Your Own Redis"](https://codecrafters.io/challenges/redis) challenge. The goal is to understand how Redis works internally: TCP networking, an event-driven server, the RESP wire protocol, and the data structures and semantics behind the commands.

It works with the standard `redis-cli`.

## Supported commands

| Area | Commands |
|---|---|
| Connection | `PING`, `ECHO` |
| Strings | `SET` (with `EX` / `PX`), `GET`, `INCR` |
| Lists | `RPUSH`, `LPUSH`, `LRANGE`, `LLEN`, `LPOP` (with optional count), `BLPOP` |
| Transactions | `MULTI`, `EXEC`, `DISCARD` |
| Optimistic locking | `WATCH`, `UNWATCH` |

Details worth knowing:

- **Expiry:** keys set with `EX` (seconds) or `PX` (milliseconds) expire lazily, checked when the key is read. `INCR` treats an expired key as missing.
- **`LRANGE`** supports negative indices, like Redis.
- **`BLPOP`** blocks the client until an element arrives or the timeout passes. A timeout of `0` waits forever. Waiting clients are served in FIFO order.
- **Transactions:** commands sent after `MULTI` are queued per connection and answered with `QUEUED`. `EXEC` runs them in order and returns all replies as one array. `DISCARD` throws the queue away.
- **Optimistic locking:** `WATCH` records the version of each watched key. If any of them is modified before `EXEC`, even if it is set back to its old value, `EXEC` aborts and returns a null array. Watches are cleared by `EXEC`, `DISCARD` and `UNWATCH`, and `WATCH` inside `MULTI` is rejected.
- **Errors** are returned as proper RESP errors (wrong arity, non-integer values, `EXEC without MULTI`, etc.).

## How it works

The server is a single-threaded event loop built on Python's `selectors` module. There are no threads and no `asyncio`.

1. The listening socket and every client socket are non-blocking and registered with one selector. Each registration carries a callback (`on_new_client` or `on_client_data`).
2. `sel.select()` wakes up when a socket is readable, and the matching callback runs.
3. `parse_command` decodes the RESP array into a list of byte arguments. It reads by the length labels (`$3`), not by splitting on `\r\n`, so values containing line breaks are safe.
4. `on_client_data` handles the commands that depend on per-connection state first (`MULTI`, `EXEC`, `DISCARD`, `WATCH`, `UNWATCH`), then queues everything else if the client is inside a transaction, and otherwise passes the command to `execute`.
5. `execute` looks the command up in a dispatch table (`HANDLERS`) and returns an encoded RESP reply. A bad number where an integer is expected becomes a RESP error instead of a crash.
6. Blocking commands never block the loop. `BLPOP` registers the client in a per-key waiter queue with an optional deadline. A later `RPUSH` / `LPUSH` hands elements to waiting clients, and the `select()` timeout is computed from the nearest deadline so timed-out waiters get a null reply on time.
7. Every write bumps a per-key version counter (`touch`). `WATCH` snapshots those versions, and `EXEC` compares them to decide whether to abort.
8. A client that disconnects is cleaned up in one place (`drop_client`): its transaction, its watches and its parked `BLPOP` entries are removed before the socket closes. Unexpected errors inside a callback are logged and do not take the server down.

Everything lives in memory in plain dicts. Strings are stored as `(value, expiry_deadline)` tuples and lists as Python lists.

## Running it

Requires Python (see `.python-version` and `pyproject.toml` for the version) and [`uv`](https://docs.astral.sh/uv/), which `your_program.sh` uses to run the project.

```bash
git clone https://github.com/Lekkso/codecrafters-redis-python.git
cd codecrafters-redis-python
./your_program.sh
```

On Windows, run the script from Git Bash or WSL, or start the server directly with `python app/main.py`.

The server listens on `localhost:6379`. In another terminal:

```bash
redis-cli
127.0.0.1:6379> SET greeting hello EX 10
OK
127.0.0.1:6379> RPUSH queue a b c
(integer) 3
127.0.0.1:6379> BLPOP queue 0
1) "queue"
2) "a"
127.0.0.1:6379> WATCH greeting
OK
127.0.0.1:6379> MULTI
OK
127.0.0.1:6379(TX)> INCR counter
QUEUED
127.0.0.1:6379(TX)> EXEC
1) (integer) 1
```

## Known limitations

This is a learning project, and these are the gaps I know about:

- **No per-connection read buffer.** Each `recv` is assumed to contain exactly one complete command, so pipelined commands, commands split across reads and values larger than 1 KB are not handled correctly yet. This is the next thing to fix.
- **`sendall` on non-blocking sockets** is not handled for very large replies or clients that vanish mid-write.
- **`BLPOP` takes a single key.** Inside `MULTI` on an empty list, `EXEC` parks the client and also returns an error, where Redis returns a null array immediately.
- **No type checking between strings and lists.** There is no `WRONGTYPE` error, and the two live in separate dicts.
- **Expiry is lazy only.** Expired keys that are never read again stay in memory, and expiry does not count as a modification for `WATCH`.
- **Empty lists are not removed** after their last element is popped.
- **Small gaps:**
  - `PING` ignores an argument.
  - `MULTI` inside `MULTI` resets the queue instead of returning an error.
  - `UNWATCH` inside `MULTI` runs immediately instead of being queued.
  - `LPOP key count` on a missing key returns an empty array instead of a null array, and negative counts are not rejected.
- **No persistence.** All data is lost when the server stops.

## Roadmap

CodeCrafters extensions, in the order the challenge lists them:

- [x] Base stages: `PING`, `ECHO`, `SET` / `GET`, expiry, concurrent clients
- [x] Lists
- [x] Transactions (`INCR`, `MULTI`, `EXEC`, `DISCARD`)
- [x] Optimistic locking (`WATCH`, `UNWATCH`)
- [ ] Replication (in progress)
- [ ] RDB persistence
- [ ] AOF persistence
- [ ] Pub/Sub
- [ ] Sorted sets
- [ ] Bitmaps
- [ ] Geospatial commands
- [ ] Authentication
- [ ] Streams

My own improvements:

- [ ] Buffered request parsing (pipelining, large values), needed before the replication handshake
- [ ] Tests (socket-level, with `pytest`)

## What I'm learning

- TCP socket programming and non-blocking I/O
- Event loops and how a single thread can serve many clients
- Parsing a binary wire protocol (RESP)
- Implementing blocking operations without blocking the server
- Time-based features like key expiry and command timeouts
- Optimistic concurrency control with per-key version counters

---

[CodeCrafters challenge](https://codecrafters.io/challenges/redis)