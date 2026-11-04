import selectors
import socket


HOST = "localhost"
PORT = 6379
PONG = b"+PONG\r\n"


sel = selectors.DefaultSelector()


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
                conn.sendall(b"$" + str(len(msg)).encode() + b"\r\n" + msg + b"\r\n")
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