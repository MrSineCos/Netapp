# tracker.py
import socket
import threading
import json
import os
import time
from datetime import datetime
import logging

# Thiết lập logging để ghi ra file app.log dùng chung
logging.basicConfig(
    filename='tracker.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class Peer:
    def __init__(self, ip, port, username, status):
        self.ip = ip
        self.port = port
        self.username = username
        self.status = status
        self.last_seen = datetime.now()  # Thêm timestamp cho lần cuối cùng peer được thấy

    def to_dict(self):
        return {
            "ip": self.ip,
            "port": self.port,
            "username": self.username,
            "status": self.status
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            ip=data["ip"],
            port=data["port"],
            username=data["username"],
            status=data["status"]
        )
        
    def update_last_seen(self):
        self.last_seen = datetime.now()
        
    def is_likely_offline(self, timeout_seconds=60):
        """Kiểm tra xem peer có khả năng offline hay không dựa trên thời gian cuối cùng được thấy"""
        now = datetime.now()
        time_diff = (now - self.last_seen).total_seconds()
        return time_diff > timeout_seconds

# Message class for storing channel messages
class Message:
    def __init__(self, sender, content, channel, timestamp=None):
        self.sender = sender
        self.content = content
        self.channel = channel
        self.timestamp = timestamp or datetime.now().isoformat()

    def to_dict(self):
        return {
            "sender": self.sender,
            "content": self.content,
            "channel": self.channel,
            "timestamp": self.timestamp
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            sender=data["sender"],
            content=data["content"],
            channel=data["channel"],
            timestamp=data["timestamp"]
        )

# Channel class for centralized storage
class Channel:
    def __init__(self, name, host):
        self.name = name
        self.host = host
        self.messages = []
        self.members = set()
        self.members.add(host)  # Add host as member
        logging.info(f"[Channel] Created channel {name} with host {host}")

    def add_message(self, message_data):
        message = Message(
            message_data["sender"], 
            message_data["content"], 
            message_data["channel"],
            message_data.get("timestamp")
        )
        self.messages.append(message)
        
        # Sắp xếp tin nhắn theo thời gian sau khi thêm
        try:
            self.messages.sort(key=lambda msg: msg.timestamp)
            logging.info(f"[Channel] Messages sorted by timestamp after adding new message to channel {self.name}")
        except Exception as e:
            logging.error(f"[Channel] Error sorting messages: {e}")
            
        logging.info(f"[Channel] Added message from {message.sender} to channel {self.name}")
        self.save_to_disk()
        return message

    def add_member(self, username):
        if username and username != "visitor":
            self.members.add(username)
            logging.info(f"[Channel] Added member {username} to channel {self.name}")
            self.save_to_disk()

    def remove_member(self, username):
        if username in self.members:
            self.members.discard(username)
            logging.info(f"[Channel] Removed member {username} from channel {self.name}")
            self.save_to_disk()

    def to_dict(self):
        return {
            "name": self.name,
            "host": self.host,
            "members": list(self.members),
            "messages": [m.to_dict() for m in self.messages]
        }

    def save_to_disk(self):
        # Create data directory if it doesn't exist
        os.makedirs("data", exist_ok=True)
        
        # Save channel data to JSON file
        with open(f"data/{self.name}.json", "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logging.info(f"[Channel] Saved channel {self.name} to disk")

    @classmethod
    def load_from_disk(cls, channel_name):
        try:
            with open(f"data/{channel_name}.json", "r") as f:
                data = json.load(f)
                channel = cls(data["name"], data["host"])
                channel.members = set(data["members"])
                channel.messages = [Message.from_dict(m) for m in data["messages"]]
                
                # Sắp xếp tin nhắn theo thời gian
                try:
                    channel.messages.sort(key=lambda msg: msg.timestamp)
                    logging.info(f"[Channel] Messages sorted by timestamp after loading channel {channel_name}")
                except Exception as e:
                    logging.error(f"[Channel] Error sorting messages for channel {channel_name}: {e}")
                
                return channel
        except (FileNotFoundError, json.JSONDecodeError):
            return None

peer_list = []
channels = {}  # Store channels on the tracker
peer_lock = threading.Lock()  # Lock for thread-safe access to peer_list
channel_lock = threading.RLock()  # Lock for thread-safe access to channels (RLock allows recursive locking)

# Load saved channels from disk on startup
def load_channels():
    global channels
    if not os.path.exists("data"):
        os.makedirs("data", exist_ok=True)
        logging.info("[Tracker] Created data directory")
        return
        
    try:
        # Load all channel files
        for filename in os.listdir("data"):
            if filename.endswith(".json") and not filename.startswith("user_"):
                channel_name = filename[:-5]  # Remove .json extension
                channel = Channel.load_from_disk(channel_name)
                if channel:
                    with channel_lock:
                        channels[channel_name] = channel
                        logging.info(f"[Tracker] Loaded channel {channel_name} with {len(channel.messages)} messages")
    except Exception as e:
        logging.error(f"[Tracker] Error loading channels: {e}")

def check_peer_status(peer):
    """Kiểm tra trạng thái của peer bằng cách kết nối tới nó"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)  # Timeout 3 giây
        s.connect((peer.ip, int(peer.port)))
        s.close()
        return True  # Peer online
    except Exception:
        return False  # Peer offline

def update_peer_status():
    """Cập nhật trạng thái của tất cả các peer theo định kỳ"""
    while True:
        try:
            # Đảm bảo thread-safe khi truy cập peer_list
            with peer_lock:
                offline_peers = []
                for i, peer in enumerate(peer_list):
                    # Chỉ kiểm tra các peer mà status là "online" hoặc đã lâu không thấy
                    if peer.status == "online" or peer.is_likely_offline() or peer.status == "invisible":
                        # Nếu peer đã lâu không được thấy, kiểm tra trạng thái
                        if peer.is_likely_offline():
                            is_online = check_peer_status(peer)
                            if not is_online and peer.status != "offline":
                                logging.info(f"[Tracker] Peer {peer.username} ({peer.ip}:{peer.port}) is now offline")
                                peer.status = "offline"
                            elif is_online and peer.status == "offline":
                                logging.info(f"[Tracker] Peer {peer.username} ({peer.ip}:{peer.port}) is back online")
                                peer.status = "online"
                                peer.update_last_seen()
                        
                        # Xóa các peer không còn hoạt động sau một thời gian rất dài (1 giờ)
                        if (datetime.now() - peer.last_seen).total_seconds() > 3600 and peer.status == "offline":
                            offline_peers.append(i)
                
                # Xóa các peer đã offline quá lâu (từ cuối danh sách để tránh lỗi index)
                for idx in sorted(offline_peers, reverse=True):
                    removed_peer = peer_list.pop(idx)
                    logging.info(f"[Tracker] Removed inactive peer: {removed_peer.username} ({removed_peer.ip}:{removed_peer.port})")
            
            # Tạm dừng để tránh dùng quá nhiều CPU
            time.sleep(30)  # Kiểm tra mỗi 30 giây
        except Exception as e:
            logging.error(f"[Tracker] Error in status updating thread: {e}")
            time.sleep(30)  # Nếu có lỗi, vẫn đợi trước khi thử lại

def ping_peer(peer):
    """Ping một peer để kiểm tra trạng thái và cập nhật last_seen"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)  # Timeout 3 giây
        s.connect((peer.ip, int(peer.port)))
        s.send(b"ping\n")
        response = s.recv(1024).decode().strip()
        s.close()
        if response == "pong":
            return True
        return False
    except Exception:
        return False

def handle_client(conn):
    global peer_list, channels
    try:
        while True:
            data = conn.recv(1024).decode()
            if not data:
                break
            # --- Bổ sung: Kiểm tra nếu là JSON (join_channel) ---
            if data.strip().startswith("{"):
                try:
                    msg = json.loads(data.strip())
                    if msg.get("type") == "join_channel":
                        channel_name = msg.get("channel")
                        username = msg.get("username")
                        with channel_lock:
                            if channel_name in channels and username:
                                channels[channel_name].add_member(username)
                                logging.info(f"[Tracker] Added member {username} to channel {channel_name} via join_channel")
                                channels[channel_name].save_to_disk()
                                conn.send(b"OK\n")
                            else:
                                conn.send(b"ERROR: Channel not found or invalid username\n")
                        continue
                except Exception as e:
                    logging.error(f"[Tracker] Error processing join_channel: {e}")
                    conn.send(b"ERROR: Invalid join_channel message\n")
                    continue
            # --- Kết thúc bổ sung ---
            parts = data.strip().split()
            if not parts:
                continue
            cmd = parts[0]
            
            if cmd == "send_info":
                ip, port, username, status = parts[1], parts[2], parts[3], parts[4]
                # Kiểm tra nếu có "get_peers" ở cuối lệnh
                get_peers = False
                if len(parts) > 5 and parts[5] == "get_peers":
                    get_peers = True
                new_peer = Peer(ip, port, username, status)
                
                # Đảm bảo thread-safe khi truy cập peer_list
                with peer_lock:
                    # Update or add peer
                    for i, p in enumerate(peer_list):
                        if p.ip == ip and p.port == port:
                            # Nếu username thay đổi, cập nhật
                            if p.username != username:
                                logging.info(f"[Tracker] Username changed for peer at {ip}:{port} from {p.username} to {username}")
                                p.username = username
                            
                            # Nếu trạng thái là offline, cập nhật ngay lập tức
                            if status == "offline":
                                p.status = "offline"
                                logging.info(f"[Tracker] Peer {username} at {ip}:{port} set to offline by client exit")
                            # Chỉ cập nhật trạng thái nếu peer đã đăng ký là trạng thái khác "offline"
                            # hoặc nếu trạng thái mới là "online" (peer đang báo là đã online lại)
                            elif status != "offline" or p.status == "offline":
                                p.status = status
                            
                            p.update_last_seen()
                            logging.info(f"[Tracker] Updated peer {username} at {ip}:{port} with status {p.status}")
                            break
                    else:
                        # Peer mới
                        peer_list.append(new_peer)
                        logging.info(f"[Tracker] Registered new peer {username} at {ip}:{port} with status {status}")
                
                if get_peers:
                    # Trả về danh sách peers ngay lập tức
                    with peer_lock:
                        peer_data = [p.to_dict() for p in peer_list]
                    conn.send(json.dumps(peer_data).encode() + b'\n')
                else:
                    conn.send(b"OK\n")
                
            elif cmd == "get_list":
                # Đảm bảo thread-safe khi truy cập peer_list
                with peer_lock:
                    # Send peer list as JSON
                    peer_data = [p.to_dict() for p in peer_list]
                conn.send(json.dumps(peer_data).encode() + b'\n')
                logging.info(f"[Tracker] Sent list of {len(peer_data)} peers")
                
            elif cmd == "ping":
                # Phản hồi lại ping từ client
                conn.send(b"pong\n")

            elif cmd == "check_status":
                # Cho phép client kiểm tra trạng thái của một peer cụ thể
                if len(parts) < 2:
                    conn.send(b"ERROR: Missing peer username\n")
                    continue
                
                target_username = parts[1]
                found = False
                
                # Đảm bảo thread-safe khi truy cập peer_list
                with peer_lock:
                    for peer in peer_list:
                        if peer.username == target_username:
                            # Kiểm tra trạng thái thực tế của peer
                            is_online = check_peer_status(peer)
                            if is_online:
                                conn.send(f"STATUS: {peer.username} is online\n".encode())
                            else:
                                conn.send(f"STATUS: {peer.username} is offline\n".encode())
                            found = True
                            break
                
                if not found:
                    conn.send(f"ERROR: Peer {target_username} not found\n".encode())
            
            elif cmd == "sync_channel":
                # Receive channel data from a host for backup
                try:
                    # Read the initial data
                    buffer = data.strip()
                    
                    # Find where the JSON starts
                    json_start = buffer.find('{')
                    if json_start == -1:
                        conn.send(b"ERROR: Invalid JSON format\n")
                        continue
                    
                    # Extract the command prefix and JSON part
                    command_prefix = buffer[:json_start].strip()
                    json_buffer = buffer[json_start:]
                    
                    # Check if JSON is complete by counting brackets
                    open_braces = json_buffer.count('{')
                    close_braces = json_buffer.count('}')
                    
                    # If JSON is incomplete, keep reading until it's complete
                    while open_braces > close_braces:
                        logging.info(f"[Tracker] JSON incomplete, reading more data ({open_braces} vs {close_braces})")
                        
                        # Set a timeout for the socket to avoid hanging
                        conn.settimeout(5.0)
                        try:
                            more_data = conn.recv(4096).decode()
                            if not more_data:
                                break
                            
                            json_buffer += more_data
                            open_braces = json_buffer.count('{')
                            close_braces = json_buffer.count('}')
                        except socket.timeout:
                            logging.warning("[Tracker] Timeout while reading JSON data")
                            break
                    
                    # Reset timeout
                    conn.settimeout(None)
                    
                    # Now try to parse the complete JSON
                    logging.info(f"[Tracker] Received complete JSON data ({len(json_buffer)} bytes)")
                    channel_data = json.loads(json_buffer)
                    channel_name = channel_data["name"]
                    
                    logging.info(f"[Tracker] Received sync request for channel {channel_name}")
                    
                    # Kiểm tra xem sender có phải là host không
                    sender_is_host = False
                    sender_ip = conn.getpeername()[0]
                    sender_port = None
                    sender_username = None
                    
                    with peer_lock:
                        for peer in peer_list:
                            if peer.ip == sender_ip:
                                sender_username = peer.username
                                sender_port = peer.port
                                break
                    
                    logging.info(f"[Tracker] Sync request from {sender_username if sender_username else 'unknown'} ({sender_ip})")
                    
                    # Sử dụng channel_lock để đảm bảo thread-safe khi truy cập và sửa đổi channels
                    with channel_lock:
                        # Check if sender is the host
                        if channel_name in channels:
                            if sender_username and sender_username == channels[channel_name].host:
                                sender_is_host = True
                                logging.info(f"[Tracker] Sender is the host of channel {channel_name}")
                        
                        if channel_name not in channels:
                            logging.info(f"[Tracker] Creating new channel {channel_name} from sync")
                            channels[channel_name] = Channel(channel_name, channel_data["host"])
                        
                        # Nếu người gửi không phải là host, chỉ thêm tin nhắn mới
                        # Giữ nguyên thông tin host và members
                        if not sender_is_host:
                            logging.info(f"[Tracker] Non-host sync from {sender_username}, preserving host and member data")
                            
                            # Cho phép client không phải host đồng bộ tin nhắn bất kể host có online hay không
                            host_is_online = False
                            host_username = channels[channel_name].host
                            
                            if host_username:
                                with peer_lock:
                                    for peer in peer_list:
                                        if peer.username == host_username and (peer.status == "online" or peer.status == "invisible"):
                                            host_is_online = True
                                            break
                            
                            if host_is_online:
                                logging.info(f"[Tracker] Host {host_username} is online, but accepting non-host message sync")
                            
                            # Chỉ thêm tin nhắn mới từ client không phải host
                            if "messages" in channel_data and channel_data["messages"]:
                                # Lấy các timestamp hiện có
                                existing_timestamps = set()
                                for msg in channels[channel_name].messages:
                                    existing_timestamps.add(msg.timestamp)
                                
                                # Thêm các tin nhắn chưa có
                                new_messages = 0
                                for msg in channel_data["messages"]:
                                    if msg.get("timestamp") not in existing_timestamps:
                                        channels[channel_name].add_message(msg)
                                        new_messages += 1
                                
                                logging.info(f"[Tracker] Added {new_messages} new messages from non-host client {sender_username}")
                        else:
                            # Update channel data
                            channels[channel_name].host = channel_data["host"]
                            
                            # Ensure host is a member
                            if channel_data["host"] and channel_data["host"] != "visitor":
                                channels[channel_name].members.add(channel_data["host"])
                            
                            # Add other members
                            if "members" in channel_data:
                                for member in channel_data["members"]:
                                    if member and member != "visitor":
                                        channels[channel_name].members.add(member)
                            
                            # Add new messages
                            if "messages" in channel_data and channel_data["messages"]:
                                # Check if message is already in channel by timestamp
                                timestamps = [m.timestamp for m in channels[channel_name].messages]
                                new_messages = 0
                                for msg in channel_data["messages"]:
                                    if msg.get("timestamp") not in timestamps:
                                        channels[channel_name].add_message(msg)
                                        new_messages += 1
                                
                                logging.info(f"[Tracker] Added {new_messages} messages from host {sender_username}")
                        
                        # Save to disk
                        channels[channel_name].save_to_disk()
                        conn.send(b"OK\n")
                        logging.info(f"[Tracker] Synced channel {channel_name} with {len(channels[channel_name].messages)} messages")
                except json.JSONDecodeError as e:
                    logging.error(f"[Tracker] JSON decode error: {e}")
                    logging.error(f"[Tracker] Received data: {' '.join(parts[1:])}")
                    conn.send(b"ERROR: Invalid JSON\n")
                except Exception as e:
                    logging.error(f"[Tracker] Error during sync: {str(e)}")
                    conn.send(f"ERROR: {str(e)}\n".encode())
                    
            elif cmd == "get_channel":
                # Send channel data to a peer
                try:
                    channel_name = parts[1]
                    # Sử dụng channel_lock để đảm bảo thread-safe khi đọc dữ liệu channels
                    with channel_lock:
                        if channel_name in channels:
                            channel_data = channels[channel_name].to_dict()
                            conn.send(json.dumps(channel_data).encode() + b'\n')
                            logging.info(f"[Tracker] Sent channel {channel_name} data with {len(channel_data['messages'])} messages")
                        else:
                            conn.send(b"ERROR: Channel not found\n")
                            logging.warning(f"[Tracker] Channel {channel_name} not found on request")
                except Exception as e:
                    logging.error(f"[Tracker] Error sending channel data: {str(e)}")
                    conn.send(f"ERROR: {str(e)}\n".encode())
                    
            elif cmd == "list_channels":
                # Send list of available channels
                channel_list = []
                # Sử dụng channel_lock để đảm bảo thread-safe khi đọc dữ liệu channels
                with channel_lock:
                    for name, channel in channels.items():
                        channel_list.append({
                            "name": name,
                            "host": channel.host,
                            "members": len(channel.members),
                            "messages": len(channel.messages)
                        })
                conn.send(json.dumps(channel_list).encode() + b'\n')
                logging.info(f"[Tracker] Sent list of {len(channel_list)} channels")
                
            elif cmd == "debug":
                # Debug command to list all channels
                debug_info = []
                # Sử dụng channel_lock để đảm bảo thread-safe khi đọc dữ liệu channels
                with channel_lock:
                    for name, channel in channels.items():
                        debug_info.append({
                            "name": name,
                            "host": channel.host,
                            "members": list(channel.members),
                            "message_count": len(channel.messages)
                        })
                conn.send(json.dumps(debug_info).encode() + b'\n')
                logging.info(f"[Tracker] Sent debug info for {len(debug_info)} channels")
                
    except Exception as e:
        # Chỉ in lỗi nếu không phải lỗi đóng kết nối thông thường
        if isinstance(e, ConnectionResetError) or isinstance(e, ConnectionAbortedError) or (
            hasattr(e, 'winerror') and e.winerror == 10053
        ):
            logging.info("[Tracker] Client disconnected.")
        else:
            logging.error(f"[Tracker] Client handling error: {e}")
    finally:
        conn.close()

def main():
    # Load channels from disk
    load_channels()
    
    # Bắt đầu thread kiểm tra trạng thái
    status_thread = threading.Thread(target=update_peer_status, daemon=True)
    status_thread.start()
    logging.info("[Tracker] Started peer status monitoring thread")
    
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # Avoid bind errors
    server.bind(("0.0.0.0", 12345))  # Lắng nghe trên mọi IP, cho phép các máy khác truy cập
    # Lưu ý: Các client phải kết nối bằng IP LAN thực tế của máy chủ tracker (ví dụ "192.168.x.x"), không dùng "127.0.0.1" hoặc "localhost".
    server.listen()
    logging.info("Tracker is running on port 12345...")
    print("Tracker is running on port 12345...")

    try:
        while True:
            conn, addr = server.accept()
            threading.Thread(target=handle_client, args=(conn,), daemon=True).start()
    except KeyboardInterrupt:
        logging.info("[Tracker] Shutting down gracefully...")
        server.close()

if __name__ == "__main__":
    main()
