# thread_client.py
import socket
import time

def send_to_peer(ip, port, message):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)  # Thêm timeout để tránh treo
        s.connect((ip, port))
        s.send(message.encode())
        # Đợi một chút để đảm bảo tin nhắn được gửi
        time.sleep(0.1)
        s.close()
        return True
    except Exception as e:
        print(f"[Peer client] Failed to connect to {ip}:{port} - {e}")
        return False
