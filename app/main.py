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

        if command == b"PING":
            conn.sendall(PONG)
        elif command == b"ECHO":
            if len(args) != 2:
                conn.sendall(b"-ERR wrong number of arguments for 'echo' command\r\n")
            else:
                msg = args[1]
                conn.sendall(bulk_str(msg))
        elif command == b"SET":
            value = args[2]
            if len(args) != 3 and len(args) != 5:
                conn.sendall(b"-ERR wrong number of arguments for 'set' command\r\n")
            elif len(args) == 3:
                key = args[1]
                deadline = None
                notebook[key] = (value, deadline)
                conn.sendall(OK)
            elif len(args) == 5:
                if args[3].upper() == b"PX":
                    key = args[1]
                    ms = int(args[4])
                    secs = ms / 1000
                    deadline = time.time() + secs
                    notebook[key] = (value, deadline)
                    conn.sendall(OK)
                elif args[3].upper() == b"EX":
                    key = args[1]
                    deadline = time.time() + int(args[4])
                    notebook[key] = (value, deadline)
                    
        elif command == b"GET":
            if len(args) != 2:
                conn.sendall(b"-ERR wrong number of arguments for 'get' command\r\n")
            else:
                key = args[1]
                res = notebook.get(key)
                value, deadline = res
                if res is None:
                    conn.sendall(NULL)
                elif deadline is not None and time.time() > deadline:
                    conn.sendall(NULL)
                else:
                    conn.sendall(bulk_str(value))
        elif command == b"RPUSH":
            if len(args) < 3:
                conn.sendall(b"-ERR wrong number of arguments for 'rpush' command\r\n")
            else:
                key = args[1]
                element = args[2]
                lists.setdefault(key, []).append(element)
        else:
            conn.sendall(b"-ERR unknown command\r\n")
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