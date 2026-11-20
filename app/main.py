import selectors
import socket
import time
import traceback


HOST = "localhost"
PORT = 6379
PONG = b"+PONG\r\n"
OK = b"+OK\r\n"
NULL = b"$-1\r\n"
NULL_ARRAY = b"*-1\r\n"


sel = selectors.DefaultSelector()
notebook = {}
lists = {}
waiters = {}
transactions = {}
watched = {}
versions = {}


def parse_command(data):
    pos = data.find(b"\r\n")
    count = int(data[1:pos])
    pos += 2

    args = []
    for _ in range(count):
        end = data.find(b"\r\n", pos)
        length = int(data[pos + 1:end])
        pos = end + 2

        args.append(data[pos:pos + length])
        pos += length + 2
    return args


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
    if not deadlines:
        return None
    return max(0, min(deadlines) - time.time())


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
}


def execute(conn, args):
    command = args[0].upper()
    try:
        if command == b"BLPOP":
            return handle_blpop(conn, args)
        handler = HANDLERS.get(command)
        if handler is None:
            return err("unknown command")
        return handler(args)
    except ValueError:
        return err("value is not an integer or out of range")


def drop_client(conn):
    transactions.pop(conn, None)
    watched.pop(conn, None)
    for key in list(waiters):
        waiters[key] = [(c, d) for c, d in waiters[key] if c is not conn]
    sel.unregister(conn)
    conn.close()


def on_client_data(conn):
    try:
        data = conn.recv(1024)
    except ConnectionResetError:
        data = b""
    if not data:
        drop_client(conn)
        return

    try:
        args = parse_command(data)
    except ValueError:
        conn.sendall(err("protocol error"))
        return
    if not args:
        return

    command = args[0].upper()
    if command == b"MULTI":
        reply = handle_multi(conn, args)
    elif command == b"EXEC":
        reply = handle_exec(conn, args)
    elif command == b"DISCARD":
        reply = handle_discard(conn, args)
    elif command == b"WATCH":
        reply = handle_watch(conn, args)
    elif command == b"UNWATCH":
        reply = handle_unwatch(conn, args)
    elif conn in transactions:
        transactions[conn].append(args)
        reply = b"+QUEUED\r\n"
    else:
        reply = execute(conn, args)

    if reply is not None:
        conn.sendall(reply)


def main():
    server = socket.create_server((HOST, PORT))
    server.setblocking(False)
    sel.register(server, selectors.EVENT_READ, on_new_client)
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


if __name__ == "__main__":
    main()