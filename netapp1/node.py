# node.py
import socket
import threading
import time
from thread_server import start_peer_server
from thread_client import send_to_peer

MY_IP = "127.0.0.1"
MY_PORT = int(input("Nhập PORT cho node này (ví dụ 9001, 9002): "))
TRACKER_IP = "127.0.0.1"
TRACKER_PORT = 12345

def register_to_tracker():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((TRACKER_IP, TRACKER_PORT))
    s.send(f"send_info {MY_IP} {MY_PORT}\n".encode())
    s.recv(1024)
    s.send(b"get_list\n")
    data = s.recv(4096).decode().splitlines()
    s.close()
    return data

def connect_to_peers(peer_list):
    print(f"[Node] Đang kết nối đến các peer khác...")
    for peer in peer_list:
        ip, port = peer.split(":")
        if int(port) == MY_PORT:
            continue
        send_to_peer(ip, int(port), f"Hello from peer {MY_PORT}!")

if __name__ == "__main__":
    # Chạy peer server
    threading.Thread(target=start_peer_server, args=(MY_PORT,), daemon=True).start()
    
    time.sleep(1)  # Chờ server khởi động

    # Đăng ký với tracker
    peer_list = register_to_tracker()
    print(f"[Node] Danh sách peer hiện tại: {peer_list}")

    # Gửi dữ liệu sang các peer khác
    connect_to_peers(peer_list)

    # Để chương trình tiếp tục chạy
    while True:
        time.sleep(1)
