import selectors
import socket
import time


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


def handle_ping(args):
    return PONG


def handle_echo(args):
    if len(args) != 2:
        return b"-ERR wrong number of arguments for 'echo' command\r\n"
    return bulk_str(args[1])


def handle_set(args):
    if len(args) != 3 and len(args) != 5:
        return b"-ERR wrong number of arguments for 'set' command\r\n"

    key = args[1]
    value = args[2]

    if len(args) == 3:
        notebook[key] = (value, None)
        return OK

    option = args[3].upper()
    if option == b"PX":
        deadline = time.time() + int(args[4]) / 1000
    elif option == b"EX":
        deadline = time.time() + int(args[4])
    else:
        return None

    notebook[key] = (value, deadline)
    return OK


def handle_get(args):
    if len(args) != 2:
        return b"-ERR wrong number of arguments for 'get' command\r\n"

    res = notebook.get(args[1])
    if res is None:
        return NULL

    value, deadline = res
    if deadline is not None and time.time() > deadline:
        return NULL
    return bulk_str(value)


def serve_waiters(key):
    lst = lists.get(key, [])
    queue = waiters.get(key, [])
    while lst and queue:
        waiter_conn, _ = queue.pop(0)
        element = lst.pop(0)
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
        return b"-ERR wrong number of arguments for 'rpush' command\r\n"

    key = args[1]
    elements = args[2:]
    lists.setdefault(key, []).extend(elements)
    length = len(lists[key])
    serve_waiters(key)
    return integer(length)


def handle_lrange(args):
    if len(args) != 4:
        return b"-ERR wrong number of arguments for 'lrange' command\r\n"

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
        return b"-ERR wrong number of arguments for 'lpush' command\r\n"

    key = args[1]
    elements = args[2:]
    items = lists.setdefault(key, [])
    for item in elements:
        items.insert(0, item)
    length = len(items)
    serve_waiters(key)
    return integer(length)


def handle_llen(args):
    if len(args) != 2:
        return b"-ERR wrong number of arguments for 'llen' command\r\n"

    lst = lists.get(args[1], [])
    return integer(len(lst))


def handle_lpop(args):
    if len(args) not in (2, 3):
        return b"-ERR wrong number of arguments for 'lpop' command\r\n"

    lst = lists.get(args[1], [])
    if len(args) == 2:
        if not lst:
            return NULL
        first = lst.pop(0)
        return bulk_str(first)
    if len(args) == 3:
        count = int(args[2])
        removed = lst[:count]
        del lst[:count]
        return array(removed)


def handle_blpop(conn, args):
    if len(args) != 3:
        return b"-ERR wrong number of arguments for 'blpop' command\r\n"
    timeout = float(args[2])
    if timeout == 0:
        deadline = None
    else:
        deadline = time.time() + timeout
    key = args[1]
    lst = lists.get(key, [])
    if lst:
        element = lst.pop(0)
        return array([key, element])

    waiters.setdefault(key, []).append((conn, deadline))
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
    b"LPOP": handle_lpop
}


def on_client_data(conn):
    try:
        data = conn.recv(1024)
    except ConnectionResetError:
        data = b""
    if data:
        try:
            args = parse_command(data)
        except ValueError:
            conn.sendall(b"-ERR protocol error\r\n")
            return
        if not args:
            return

        command = args[0].upper()
        if command == b"BLPOP":
            reply = handle_blpop(conn, args)
        else:
            handler = HANDLERS.get(command)
            if handler is None:
                reply = b"-ERR unknown command\r\n"
            else:
                try:
                    reply = handler(args)
                except ValueError:
                    reply = b"-ERR value is not an integer or out of range\r\n"

        if reply is not None:
            conn.sendall(reply)
    else:
        sel.unregister(conn)
        conn.close()


def main():
    server = socket.create_server((HOST, PORT))
    server.setblocking(False)
    sel.register(server, selectors.EVENT_READ, on_new_client)
    while True:
        for key, _ in sel.select(timeout=next_timeout()):
            callback = key.data
            callback(key.fileobj)
        expire_waiters()


if __name__ == "__main__":
    main()