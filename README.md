# Redis — Built From Scratch in Python

A Redis-inspired in-memory key-value database implemented from scratch using Python.

Built as part of the [CodeCrafters Build Your Own Redis challenge](https://codecrafters.io/challenges/redis), this project explores the internals of Redis, including TCP networking, event-driven architecture, the RESP protocol, and in-memory data storage.

## Features

- **TCP Server** — Handles multiple client connections using non-blocking sockets and Python's `selectors` module.
- **RESP Protocol** — Parses incoming Redis commands and serializes server responses.
- **Command Support**
  - `PING`
  - `ECHO`
  - `SET`
  - `GET`
- **In-Memory Storage** — Stores and retrieves key-value pairs.
- **Key Expiration** — Supports expiration through `EX` and `PX` options.

## Architecture

The server uses an event-driven architecture built around Python's `selectors` module.

Rather than creating a separate thread for every client, the event loop monitors multiple socket connections and dispatches incoming data to the appropriate handlers.

The implementation consists of three main components:

- **Networking:** TCP server, client connections, and event loop.
- **Protocol:** RESP command parsing and response serialization.
- **Storage:** In-memory key-value store with expiration support.

## Tech Stack

- Python
- TCP Sockets
- RESP (Redis Serialization Protocol)
- `selectors`
- `time`

## Running Locally

Clone the repository:

```bash
git clone https://github.com/Lekkso/codecrafters-redis-python.git
cd codecrafters-redis-python
```

Start the server:

```bash
./your_program.sh
```

The server listens on `localhost:6379`.

Connect using the Redis CLI:

```bash
redis-cli
```

## What I'm Learning

- TCP networking and socket programming
- Event loops and non-blocking I/O
- Client-server communication
- Binary data handling and protocol parsing
- In-memory data structures
- Time-based operations and key expiration

## Roadmap

Planned extensions:

- [ ] Lists
- [ ] Sorted Sets
- [ ] Streams
- [ ] Transactions
- [ ] Optimistic Locking
- [ ] Replication
- [ ] RDB Persistence
- [ ] AOF Persistence
- [ ] Pub/Sub
- [ ] Authentication
- [ ] Bitmaps
- [ ] Geospatial Commands

---

**Status:** Base challenge completed. Extensions in progress.

[CodeCrafters Challenge](https://codecrafters.io/challenges/redis) · [GitHub Repository](https://github.com/Lekkso/codecrafters-redis-python)