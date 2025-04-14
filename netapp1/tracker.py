# tracker.py
import socket
import threading

peer_list = []

def handle_client(conn):
    global peer_list
    while True:
        try:
            data = conn.recv(1024).decode()
            if not data:
                break
            parts = data.strip().split()
            if not parts:
                continue
            cmd = parts[0]
            if cmd == "send_info":
                ip, port = parts[1], parts[2]
                peer = f"{ip}:{port}"
                if peer not in peer_list:
                    peer_list.append(peer)
                conn.send(b"OK\n")
            elif cmd == "get_list":
                conn.send('\n'.join(peer_list).encode() + b'\n')
        except:
            break
    conn.close()

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("0.0.0.0", 12345))
    server.listen()
    print("Tracker is running on port 12345...")
    while True:
        conn, _ = server.accept()
        threading.Thread(target=handle_client, args=(conn,), daemon=True).start()

if __name__ == "__main__":
    main()
