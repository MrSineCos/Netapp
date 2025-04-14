# thread_client.py
import socket

def send_to_peer(ip, port, message):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((ip, port))
        s.send(message.encode())
        s.close()
    except Exception as e:
        print(f"[Peer client] Failed to connect to {ip}:{port} - {e}")
