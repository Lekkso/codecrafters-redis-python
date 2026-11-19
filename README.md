# Redis From Scratch (Python)

A Redis-compatible in-memory database server written from scratch in Python, with no third-party dependencies.

Built while working through the [CodeCrafters "Build Your Own Redis"](https://codecrafters.io/challenges/redis) challenge. The goal is to understand how Redis works internally: TCP networking, an event-driven server, the RESP wire protocol, and the data structures behind the commands.

It works with the standard `redis-cli`.

## Supported commands

| Area | Commands |
|---|---|
| Connection | `PING`, `ECHO` |
| Strings | `SET` (with `EX` / `PX`), `GET`, `INCR` |
| Lists | `RPUSH`, `LPUSH`, `LRANGE`, `LLEN`, `LPOP` (with optional count), `BLPOP` |
| Transactions | `MULTI`, `EXEC`, `DISCARD` |

Details worth knowing:

- **Expiry:** keys set with `EX` (seconds) or `PX` (milliseconds) expire lazily, checked when the key is read.
- **`LRANGE`** supports negative indices, like Redis.
- **`BLPOP`** blocks the client until an element arrives or the timeout passes. A timeout of `0` waits forever. Waiting clients are served in FIFO order.
- **Transactions:** commands sent after `MULTI` are queued per connection and run in order on `EXEC`.
- **Errors** are returned as proper RESP errors (wrong arity, non-integer values, `EXEC without MULTI`, etc.).

## How it works

The server is a single-threaded event loop built on Python's `selectors` module. There are no threads and no `asyncio`.

1. The listening socket and every client socket are non-blocking and registered with one selector. Each registration carries a callback (`on_new_client` or `on_client_data`).
2. `sel.select()` wakes up when a socket is readable, and the matching callback runs.
3. `parse_command` decodes the RESP array into a list of byte arguments.
4. A dispatch table (`HANDLERS`) maps the command name to a handler function that returns an encoded RESP reply.
5. Blocking commands never block the loop. `BLPOP` registers the client in a per-key waiter queue with an optional deadline. A later `RPUSH` / `LPUSH` hands elements to waiting clients, and the `select()` timeout is computed from the nearest deadline so timed-out waiters get a null reply on time.

Everything lives in memory in plain dicts. Strings are stored as `(value, expiry_deadline)` tuples and lists as Python lists.

## Running it

Requires Python 3.14 (see `pyproject.toml`) and [`uv`](https://docs.astral.sh/uv/), which `your_program.sh` uses to run the project.

```bash
git clone https://github.com/Lekkso/codecrafters-redis-python.git
cd codecrafters-redis-python
./your_program.sh
```

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
```

## Known limitations

This is a learning project, and these are the gaps I know about:

- **No per-connection read buffer.** Each `recv` is assumed to contain exactly one complete command, so pipelined commands and values larger than 1 KB are not handled correctly yet.
- **`WATCH` is a stub.** It is accepted but does not track keys, so optimistic locking does not work. `UNWATCH` is not implemented.
- **`BLPOP` takes a single key**, and using it inside `MULTI` is not handled the way Redis does.
- **No type checking between strings and lists.** There is no `WRONGTYPE` error, and the two live in separate dicts.
- **Expiry is lazy only.** Expired keys that are never read again stay in memory.
- **Empty lists are not removed** after their last element is popped.
- **No persistence.** All data is lost when the server stops.

## Roadmap

- [x] Basic commands and key expiry (`PING`, `ECHO`, `SET`, `GET`)
- [x] Lists (`RPUSH`, `LPUSH`, `LRANGE`, `LLEN`, `LPOP`, `BLPOP`)
- [x] Atomic increments (`INCR`)
- [x] Transactions (`MULTI`, `EXEC`, `DISCARD`)
- [ ] Buffered request parsing (pipelining, large values)
- [ ] Optimistic locking (`WATCH`, `UNWATCH`)
- [ ] Tests (socket-level, with `pytest`)
- [ ] Streams
- [ ] Sorted sets
- [ ] Replication
- [ ] RDB persistence
- [ ] Pub/Sub
- [ ] Authentication
- [ ] Geospatial commands

## What I'm learning

- TCP socket programming and non-blocking I/O
- Event loops and how a single thread can serve many clients
- Parsing a binary wire protocol (RESP)
- Implementing blocking operations without blocking the server
- Time-based features like key expiry and command timeouts

---

[CodeCrafters challenge](https://codecrafters.io/challenges/redis)