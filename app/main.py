import selectors
import socket
import time


HOST = "localhost"
PORT = 6379
PONG = b"+PONG\r\n"
OK = b"+OK\r\n"
NULL = b"$-1\r\n"


sel = selectors.DefaultSelector()
notebook = {}
lists = {}


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
        return None  # unknown option: no reply, same as before

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


def handle_rpush(args):
    if len(args) < 3:
        return b"-ERR wrong number of arguments for 'rpush' command\r\n"

    key = args[1]
    elements = args[2:]
    lists.setdefault(key, []).extend(elements)
    return integer(len(lists[key]))


def handle_lrange(args):
    if len(args) != 4:
        return b"-ERR wrong number of arguments for 'lrange' command\r\n"

    key = args[1]
    start = int(args[2])
    end = int(args[3]) + 1
    items = lists.get(key, [])
    return array(items[start:end])


HANDLERS = {
    b"PING": handle_ping,
    b"ECHO": handle_echo,
    b"SET": handle_set,
    b"GET": handle_get,
    b"RPUSH": handle_rpush,
    b"LRANGE": handle_lrange,
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
        handler = HANDLERS.get(command)
        if handler is None:
            reply = b"-ERR unknown command\r\n"
        else:
            reply = handler(args)

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
        for key, _ in sel.select():
            callback = key.data
            callback(key.fileobj)


if __name__ == "__main__":
    main()