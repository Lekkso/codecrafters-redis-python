import argparse
import selectors
import socket
import time
import traceback


HOST = "localhost"
DEFAULT_PORT = 6379
PONG = b"+PONG\r\n"
OK = b"+OK\r\n"
NULL = b"$-1\r\n"
NULL_ARRAY = b"*-1\r\n"
EMPTY_RDB = bytes.fromhex(
    "524544495330303131fa0972656469732d76657205372e322e30fa0a72656469732d62697473"
    "c040fa056374696d65c26d08bc65fa08757365642d6d656dc2b0c41000fa08616f662d626173"
    "65c000fff06e3bfec0ff5aa2"
)
WRITE_COMMANDS = {b"SET", b"INCR", b"RPUSH", b"LPUSH", b"LPOP"}


sel = selectors.DefaultSelector()
notebook = {}
lists = {}
waiters = {}
transactions = {}
watched = {}
versions = {}
buffers = {}
replicas = []
replica_acks = {}
wait_requests = []


role = "master"
master_replid = "8371b4fb1155b71f4a04d3e1bc3e18c4a990aeeb"
master_repl_offset = 0
replica_offset = 0


def parse_command(data):
    if not data:
        return None
    if data[0:1] != b"*":
        raise ValueError("protocol error")
    end = data.find(b"\r\n")
    if end == -1:
        return None
    count = int(data[1:end])
    pos = end + 2

    args = []
    for _ in range(count):
        if pos >= len(data):
            return None
        if data[pos:pos + 1] != b"$":
            raise ValueError("protocol error")
        end = data.find(b"\r\n", pos)
        if end == -1:
            return None
        length = int(data[pos + 1:end])
        pos = end + 2
        if len(data) < pos + length + 2:
            return None

        args.append(data[pos:pos + length])
        pos += length + 2
    return args, pos


def on_new_client(server):
    conn, _ = server.accept()
    conn.setblocking(False)
    sel.register(conn, selectors.EVENT_READ, on_client_data)


def bulk_str(value):
    bulk_string = (b"$" + str(len(value)).encode() + b"\r\n" + value + b"\r\n")
    return bulk_string


def integer(number):
    num = (b":" + str(number).encode() + b"\r\n")
    return num


def array(items):
    result = b"*" + str(len(items)).encode() + b"\r\n"
    for item in items:
        result += bulk_str(item)
    return result


def err(message):
    return b"-ERR " + message.encode() + b"\r\n"


def wrong_args(name):
    return err(f"wrong number of arguments for '{name}' command")


def touch(key):
    versions[key] = versions.get(key, 0) + 1


def propagate(args):
    global master_repl_offset
    data = array(args)
    master_repl_offset += len(data)
    for replica in replicas[:]:
        try:
            replica.sendall(data)
        except OSError:
            replicas.remove(replica)


def handle_ping(args):
    return PONG


def handle_echo(args):
    if len(args) != 2:
        return wrong_args("echo")
    return bulk_str(args[1])


def handle_set(args):
    if len(args) != 3 and len(args) != 5:
        return wrong_args("set")

    key = args[1]
    value = args[2]

    if len(args) == 3:
        notebook[key] = (value, None)
        touch(key)
        return OK

    option = args[3].upper()
    if option == b"PX":
        deadline = time.time() + int(args[4]) / 1000
    elif option == b"EX":
        deadline = time.time() + int(args[4])
    else:
        return err("syntax error")

    notebook[key] = (value, deadline)
    touch(key)
    return OK


def handle_get(args):
    if len(args) != 2:
        return wrong_args("get")

    res = notebook.get(args[1])
    if res is None:
        return NULL

    value, deadline = res
    if deadline is not None and time.time() > deadline:
        del notebook[args[1]]
        return NULL
    return bulk_str(value)


def serve_waiters(key):
    lst = lists.get(key, [])
    queue = waiters.get(key, [])
    while lst and queue:
        waiter_conn, _ = queue.pop(0)
        element = lst.pop(0)
        touch(key)
        waiter_conn.sendall(array([key, element]))


def next_timeout():
    deadlines = [d for queue in waiters.values() for _, d in queue if d is not None]
    deadlines += [r[2] for r in wait_requests if r[2] is not None]
    if not deadlines:
        return None
    return max(0, min(deadlines) - time.time())


def expire_waits():
    now = time.time()
    for request in wait_requests[:]:
        client, needed, deadline, target = request
        if deadline is not None and now > deadline:
            wait_requests.remove(request)
            client.sendall(integer(count_acked(target)))


def expire_waiters():
    now = time.time()
    for key, queue in waiters.items():
        for entry in queue[:]:
            waiter_conn, deadline = entry
            if deadline is not None and now > deadline:
                queue.remove(entry)
                waiter_conn.sendall(NULL_ARRAY)


def handle_rpush(args):
    if len(args) < 3:
        return wrong_args("rpush")

    key = args[1]
    elements = args[2:]
    lists.setdefault(key, []).extend(elements)
    touch(key)
    length = len(lists[key])
    serve_waiters(key)
    return integer(length)


def handle_lrange(args):
    if len(args) != 4:
        return wrong_args("lrange")

    key = args[1]
    items = lists.get(key, [])
    length = len(items)
    start = int(args[2])
    end = int(args[3])
    if start < 0:
        start += length
        if start < 0:
            start = 0
    if end < 0:
        end += length
        if end < 0:
            return array([])
    end += 1
    return array(items[start:end])


def handle_lpush(args):
    if len(args) < 3:
        return wrong_args("lpush")

    key = args[1]
    elements = args[2:]
    items = lists.setdefault(key, [])
    for item in elements:
        items.insert(0, item)
    touch(key)
    length = len(items)
    serve_waiters(key)
    return integer(length)


def handle_llen(args):
    if len(args) != 2:
        return wrong_args("llen")

    lst = lists.get(args[1], [])
    return integer(len(lst))


def handle_lpop(args):
    if len(args) not in (2, 3):
        return wrong_args("lpop")

    key = args[1]
    lst = lists.get(key, [])
    if len(args) == 2:
        if not lst:
            return NULL
        first = lst.pop(0)
        touch(key)
        return bulk_str(first)
    if len(args) == 3:
        count = int(args[2])
        removed = lst[:count]
        del lst[:count]
        if removed:
            touch(key)
        return array(removed)


def handle_blpop(conn, args):
    if len(args) != 3:
        return wrong_args("blpop")
    timeout = float(args[2])
    if timeout == 0:
        deadline = None
    else:
        deadline = time.time() + timeout
    key = args[1]
    lst = lists.get(key, [])
    if lst:
        element = lst.pop(0)
        touch(key)
        return array([key, element])

    waiters.setdefault(key, []).append((conn, deadline))
    return None


def handle_incr(args):
    if len(args) != 2:
        return wrong_args("incr")
    key = args[1]
    res = notebook.get(key)
    if res is None:
        value, deadline = b"0", None
    else:
        value, deadline = res
        if deadline is not None and time.time() > deadline:
            value, deadline = b"0", None
    new = int(value) + 1
    notebook[key] = (str(new).encode(), deadline)
    touch(key)
    return integer(new)


def handle_multi(conn, args):
    if len(args) != 1:
        return wrong_args("multi")
    transactions[conn] = []
    return OK


def handle_exec(conn, args):
    if len(args) != 1:
        return wrong_args("exec")
    if conn not in transactions:
        return err("EXEC without MULTI")
    queue = transactions.pop(conn)
    snapshot = watched.pop(conn, {})
    for key, version in snapshot.items():
        if versions.get(key, 0) != version:
            return NULL_ARRAY
    replies = []
    for queued_args in queue:
        reply = execute(conn, queued_args)
        if reply is None:
            reply = err("syntax error")
        replies.append(reply)
    header = b"*" + str(len(replies)).encode() + b"\r\n"
    return header + b"".join(replies)


def handle_discard(conn, args):
    if len(args) != 1:
        return wrong_args("discard")
    if conn not in transactions:
        return err("DISCARD without MULTI")
    del transactions[conn]
    watched.pop(conn, None)
    return OK


def handle_watch(conn, args):
    if len(args) < 2:
        return wrong_args("watch")
    if conn in transactions:
        return err("WATCH inside MULTI is not allowed")
    snapshot = watched.setdefault(conn, {})
    for key in args[1:]:
        snapshot.setdefault(key, versions.get(key, 0))
    return OK


def handle_unwatch(conn, args):
    if len(args) != 1:
        return wrong_args("unwatch")
    watched.pop(conn, None)
    return OK


def handle_info(args):
    if len(args) > 2:
        return wrong_args("info")
    info = (
        f"role:{role}\r\n"
        f"master_replid:{master_replid}\r\n"
        f"master_repl_offset:{master_repl_offset}"
    )
    return bulk_str(info.encode())


def handle_replconf(conn, args):
    if len(args) > 2 and args[1].upper() == b"ACK":
        replica_acks[conn] = int(args[2])
        check_waits()
        return None
    return OK


def handle_psync(conn, args):
    if len(args) != 3:
        return wrong_args("psync")
    header = f"+FULLRESYNC {master_replid} {master_repl_offset}\r\n".encode()
    rdb = b"$" + str(len(EMPTY_RDB)).encode() + b"\r\n" + EMPTY_RDB
    conn.sendall(header + rdb)
    replicas.append(conn)
    replica_acks[conn] = 0
    return None


def count_acked(target):
    return sum(1 for r in replicas if replica_acks.get(r, 0) >= target)


def check_waits():
    for request in wait_requests[:]:
        client, needed, deadline, target = request
        count = count_acked(target)
        if count >= needed:
            wait_requests.remove(request)
            client.sendall(integer(count))


def handle_wait(conn, args):
    global master_repl_offset
    if len(args) != 3:
        return wrong_args("wait")
    needed = int(args[1])
    timeout_ms = int(args[2])

    if master_repl_offset == 0:
        return integer(len(replicas))

    target = master_repl_offset
    if count_acked(target) >= needed:
        return integer(count_acked(target))

    getack = array([b"REPLCONF", b"GETACK", b"*"])
    for replica in replicas[:]:
        try:
            replica.sendall(getack)
        except OSError:
            replicas.remove(replica)
    master_repl_offset += len(getack)

    deadline = None if timeout_ms == 0 else time.time() + timeout_ms / 1000
    wait_requests.append((conn, needed, deadline, target))
    return None


HANDLERS = {
    b"PING": handle_ping,
    b"ECHO": handle_echo,
    b"SET": handle_set,
    b"GET": handle_get,
    b"RPUSH": handle_rpush,
    b"LRANGE": handle_lrange,
    b"LPUSH": handle_lpush,
    b"LLEN": handle_llen,
    b"LPOP": handle_lpop,
    b"INCR": handle_incr,
    b"INFO": handle_info,
}


def execute(conn, args):
    command = args[0].upper()
    try:
        if command == b"BLPOP":
            return handle_blpop(conn, args)
        handler = HANDLERS.get(command)
        if handler is None:
            return err("unknown command")
        reply = handler(args)
    except ValueError:
        return err("value is not an integer or out of range")
    if (
        command in WRITE_COMMANDS
        and role == "master"
        and reply is not None
        and not reply.startswith(b"-")
    ):
        propagate(args)
    return reply


def dispatch(conn, args):
    command = args[0].upper()
    if command == b"MULTI":
        return handle_multi(conn, args)
    if command == b"EXEC":
        return handle_exec(conn, args)
    if command == b"DISCARD":
        return handle_discard(conn, args)
    if command == b"WATCH":
        return handle_watch(conn, args)
    if command == b"UNWATCH":
        return handle_unwatch(conn, args)
    if command == b"REPLCONF":
        return handle_replconf(conn, args)
    if command == b"PSYNC":
        return handle_psync(conn, args)
    if command == b"WAIT":
        return handle_wait(conn, args)
    if conn in transactions:
        transactions[conn].append(args)
        return b"+QUEUED\r\n"
    return execute(conn, args)


def run_master_command(conn, args):
    command = args[0].upper()
    if command == b"REPLCONF" and len(args) > 1 and args[1].upper() == b"GETACK":
        conn.sendall(array([b"REPLCONF", b"ACK", str(replica_offset).encode()]))
        return
    execute(conn, args)


def process_buffer(conn, from_master=False):
    global replica_offset
    while True:
        buf = buffers.get(conn, b"")
        try:
            parsed = parse_command(buf)
        except ValueError:
            buffers[conn] = b""
            if not from_master:
                conn.sendall(err("protocol error"))
            return
        if parsed is None:
            return
        args, used = parsed
        buffers[conn] = buf[used:]
        if args:
            if from_master:
                run_master_command(conn, args)
            else:
                reply = dispatch(conn, args)
                if reply is not None:
                    conn.sendall(reply)
        if from_master:
            replica_offset += used


def drop_client(conn):
    transactions.pop(conn, None)
    watched.pop(conn, None)
    buffers.pop(conn, None)
    replica_acks.pop(conn, None)
    wait_requests[:] = [r for r in wait_requests if r[0] is not conn]
    if conn in replicas:
        replicas.remove(conn)
    for key in list(waiters):
        waiters[key] = [(c, d) for c, d in waiters[key] if c is not conn]
    sel.unregister(conn)
    conn.close()


def read_data(conn):
    try:
        data = conn.recv(4096)
    except ConnectionResetError:
        data = b""
    except BlockingIOError:
        return None
    if not data:
        drop_client(conn)
        return None
    buffers[conn] = buffers.get(conn, b"") + data
    return data


def on_client_data(conn):
    if read_data(conn) is not None:
        process_buffer(conn)


def on_master_data(conn):
    if read_data(conn) is not None:
        process_buffer(conn, from_master=True)


def parse_options():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--replicaof", type=str, default=None)
    return parser.parse_args()


def recv_until(sock, buf, marker):
    while marker not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("master closed the connection")
        buf += chunk
    line, _, rest = buf.partition(marker)
    return line, rest


def recv_exact(sock, buf, size):
    while len(buf) < size:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("master closed the connection")
        buf += chunk
    return buf[:size], buf[size:]


def connect_to_master(master_address, replica_port):
    master_host, master_port = master_address.split()
    sock = socket.create_connection((master_host, int(master_port)))
    buf = b""
    handshake = [
        [b"PING"],
        [b"REPLCONF", b"listening-port", str(replica_port).encode()],
        [b"REPLCONF", b"capa", b"psync2"],
        [b"PSYNC", b"?", b"-1"],
    ]
    for command in handshake:
        sock.sendall(array(command))
        _, buf = recv_until(sock, buf, b"\r\n")
    header, buf = recv_until(sock, buf, b"\r\n")
    _, buf = recv_exact(sock, buf, int(header[1:]))
    sock.setblocking(False)
    return sock, buf


def main():
    global role
    options = parse_options()
    if options.replicaof is not None:
        role = "slave"
    server = socket.create_server((HOST, options.port))
    server.setblocking(False)
    sel.register(server, selectors.EVENT_READ, on_new_client)
    if options.replicaof is not None:
        master_conn, leftover = connect_to_master(options.replicaof, options.port)
        buffers[master_conn] = leftover
        sel.register(master_conn, selectors.EVENT_READ, on_master_data)
        process_buffer(master_conn, from_master=True)
    while True:
        for key, _ in sel.select(timeout=next_timeout()):
            callback = key.data
            try:
                callback(key.fileobj)
            except Exception:
                traceback.print_exc()
        try:
            expire_waiters()
        except Exception:
            traceback.print_exc()
        try:
            expire_waits()
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    main()