# thread_server.py
import socket
import threading

def start_peer_server(port):
    def handle_peer(conn):
        data = conn.recv(1024).decode()
        print(f"[Peer server] Received from peer: {data}")
        conn.close()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('', port))
    server.listen()
    print(f"[Peer server] Running on port {port}...")
    while True:
        conn, _ = server.accept()
        threading.Thread(target=handle_peer, args=(conn,), daemon=True).start()
