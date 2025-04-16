# thread_client.py
import socket
import time

def send_to_peer(ip, port, message):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)  # Increase timeout for larger messages
        s.connect((ip, port))
        
        # Encode the message
        encoded_message = message.encode()
        
        # Send in chunks if large
        chunk_size = 4096
        for i in range(0, len(encoded_message), chunk_size):
            chunk = encoded_message[i:i+chunk_size]
            s.send(chunk)
            # Small pause between chunks
            time.sleep(0.01)
            
        # Send end marker
        s.send(b"\n")
        
        # Wait to ensure message is sent
        time.sleep(0.2)
        s.close()
        return True
    except Exception as e:
        print(f"[Peer client] Failed to connect to {ip}:{port} - {e}")
        return False
