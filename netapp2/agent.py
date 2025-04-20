# agent.py
import time
import json
import os
import threading
from multiprocessing import Queue
from thread_client import send_to_peer
from thread_server import start_peer_server
import socket
from datetime import datetime
from data_manager import DataManager, Message
import requests

TRACKER_IP = "127.0.0.1"
TRACKER_PORT = 12345
MY_IP = "127.0.0.1"
DATA_DIR = "data"

class Agent:
    def __init__(self, port, username, status="online"):
        self.port = port
        self.username = username
        self.status = status
        self.last_sync = datetime.now()
        
        # Theo dõi trạng thái kết nối tracker
        self._tracker_connected_before = False
        self._auto_sync = True  # Mặc định bật tự động đồng bộ
        
        # Use the shared DataManager
        self.data_manager = DataManager()
        
        # Determine if we're in visitor mode
        self.is_authenticated = bool(username)
        
        # Mặc định auth_key là trống
        self.auth_key = ""
        
        # If authenticated, load user data
        if self.is_authenticated:
            self.data_manager.load_user_data(username)
            
            # Sắp xếp tin nhắn trong tất cả các kênh khi khởi động
            self.data_manager.sort_all_channels_messages()
            print(f"[Agent] Sorted messages in all channels on startup")

    def register_to_tracker(self, get_peers=False):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Nếu get_peers=True, gửi thêm "get_peers" vào lệnh
            if get_peers:
                s.connect((TRACKER_IP, TRACKER_PORT))
                s.send(f"send_info {MY_IP} {self.port} {self.username or 'visitor'} {self.status} get_peers\n".encode())
                data = s.recv(4096).decode()
                s.close()
                # Trả về danh sách peers nếu có
                try:
                    peers = json.loads(data)
                    return peers
                except Exception:
                    return []
            else:
                s.connect((TRACKER_IP, TRACKER_PORT))
                s.send(f"send_info {MY_IP} {self.port} {self.username or 'visitor'} {self.status}\n".encode())
                s.recv(1024)
                s.send(b"get_list\n")
                data = json.loads(s.recv(4096).decode())
                s.close()
                
                # Kiểm tra xem đây có phải là kết nối thành công đầu tiên với tracker không
                if not hasattr(self, '_tracker_connected_before'):
                    self._tracker_connected_before = False
                    
                if not self._tracker_connected_before and self.is_authenticated:
                    print("[Agent] First successful connection to tracker, performing full sync")
                    self._tracker_connected_before = True
                    # Đồng bộ dữ liệu sau khi kết nối thành công đầu tiên
                    self.sync_all(sync_type="reconnect")
                
                return data
        except Exception as e:
            print(f"[Agent] Error connecting to tracker: {e}")
            return []

    def sync_all(self, channel_name=None, sync_type="normal"):
        """
        Hàm đồng bộ thống nhất xử lý tất cả các loại đồng bộ.
        
        Tham số:
        - channel_name (str, optional): Tên kênh cần đồng bộ, None để đồng bộ tất cả kênh
        - sync_type (str): Loại đồng bộ cần thực hiện (không còn phân biệt loại, chỉ giữ lại để tương thích)
            
        Trả về:
        - bool: True nếu đồng bộ thành công, False nếu thất bại

        Mô tả hoạt động của hàm:
        - Kiểm tra điều kiện đồng bộ
        - Kiểm tra trạng thái online với server trước khi đồng bộ
        - Tạo biến theo dõi kết quả
        
        """
        # Kiểm tra điều kiện đồng bộ
        if not self.is_authenticated:
            print("[Agent] Không thể đồng bộ ở chế độ khách")
            return False
            
        # Kiểm tra và in trạng thái hiện tại
        print(f"[Agent] Current status before sync: {self.status}")
        
        # Kiểm tra trạng thái online với server trước khi đồng bộ
        # Bỏ qua kiểm tra nếu đang đồng bộ khi kết nối lại hoặc đồng bộ offline
        check_online = sync_type not in ["offline", "reconnect"]
        if check_online:
            is_online = self.check_online_status()
            print(f"[Agent] Online check result: {is_online}")
            
            if not is_online:
                print("[Agent] Không thể đồng bộ khi offline") 
                return False
        
        # BƯỚC THÊM MỚI: Lấy danh sách kênh từ tracker và phát hiện kênh mới
        try:
            print("[Agent] Requesting channel list from tracker")
            available_channels = self.list_available_channels()
            if available_channels:
                print(f"[Agent] Found {len(available_channels)} channels on tracker")
                # Lấy danh sách kênh hiện có trong dữ liệu cục bộ
                local_channels = self.data_manager.get_all_channels()
                
                # Nếu người dùng không phải là host của những kênh này,
                # vẫn cần phải có dữ liệu cục bộ về các kênh để tiến hành đồng bộ
                new_channels_count = 0
                new_hosted_channels = []  # Danh sách các kênh vừa thêm mà mình là host
                
                for channel_info in available_channels:
                    channel_name_on_tracker = channel_info.get("name")
                    if not channel_name_on_tracker:
                        continue
                        
                    if channel_name_on_tracker not in local_channels:
                        print(f"[Agent] Discovered new channel: {channel_name_on_tracker}")
                        
                        # Lấy thông tin về host từ channel_info
                        host = channel_info.get("host", "unknown")
                        
                        # Tạo kênh mới trong dữ liệu cục bộ (chưa tham gia)
                        self.data_manager.add_channel(channel_name_on_tracker, host)
                        print(f"[Agent] Created local record for channel {channel_name_on_tracker} (host: {host})")
                        new_channels_count += 1
                        
                        # Nếu mình là host của kênh này, thêm vào danh sách cần đồng bộ
                        if host == self.username:
                            new_hosted_channels.append(channel_name_on_tracker)
                
                if new_channels_count > 0:
                    print(f"[Agent] Added {new_channels_count} new channels to local database")
        except Exception as e:
            print(f"[Agent] Error while checking for new channels: {e}")
        
        # Biến theo dõi kết quả
        sync_success = True
        channels_synced = 0
        messages_synced = 0
        
        # Quyết định kênh cần đồng bộ
        channels_to_sync = []
        if channel_name:
            # Đồng bộ một kênh cụ thể
            channel = self.data_manager.get_channel(channel_name)
            if channel:
                channels_to_sync.append(channel)
            else:
                print(f"[Agent] Kênh {channel_name} không tồn tại")
                return False
        else:
            # Đồng bộ tất cả các kênh người dùng đã tham gia
            # Thay vì đồng bộ tất cả các kênh có trong cơ sở dữ liệu
            user_channels = self.data_manager.get_user_channels(self.username)  # Lấy danh sách kênh người dùng đã tham gia
            if not user_channels:
                print(f"[Agent] User {self.username} hasn't joined any channels")
                # Nếu có kênh vừa thêm mà mình là host, vẫn cần đồng bộ các kênh này
                user_channels = []
                
            print(f"[Agent] Found {len(user_channels)} channels joined by user {self.username}")
            for ch_name in user_channels:
                channel = self.data_manager.get_channel(ch_name)
                if channel:
                    channels_to_sync.append(channel)
            
            # Bổ sung các kênh vừa thêm mà mình là host vào danh sách cần đồng bộ (nếu chưa có)
            for ch_name in getattr(locals(), "new_hosted_channels", []):
                if ch_name not in user_channels:
                    channel = self.data_manager.get_channel(ch_name)
                    if channel and channel not in channels_to_sync:
                        print(f"[Agent] Adding newly hosted channel {ch_name} to sync list")
                        channels_to_sync.append(channel)
        
        print(f"[Agent] Preparing to sync {len(channels_to_sync)} channels")
        
        # Đối với mỗi kênh, thực hiện quy trình đồng bộ
        for channel in channels_to_sync:
            channel_name = channel.name
            is_host = channel.host == self.username
            
            print(f"[Agent] Processing channel {channel_name} (host: {is_host})")
            
            # PHẦN 1: Đồng bộ tin nhắn pending lên tracker và peers
            
            # Đếm tin nhắn pending
            pending_messages = []
            for msg in channel.messages:
                if hasattr(msg, "status") and msg.status == "pending":
                    pending_messages.append(msg)
            
            print(f"[Agent] Found {len(pending_messages)} pending messages in channel {channel_name}")
            
            if pending_messages:
                # 1.1 Đồng bộ với tracker
                tracker_sync_success = False
                try:
                    print(f"[Agent] Sending pending messages to tracker for channel {channel_name}")
                    # Chuẩn bị dữ liệu kênh để gửi
                    channel_data = {
                        "name": channel_name,
                        "host": channel.host,
                        "members": list(channel.members),
                        "messages": [
                            {
                                "sender": msg.sender,
                                "content": msg.content,
                                "channel": channel_name,
                                "timestamp": msg.timestamp
                            } for msg in channel.messages
                        ]
                    }
                    
                    # Gửi dữ liệu lên tracker
                    try:
                        sync_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sync_socket.settimeout(10)
                        sync_socket.connect((TRACKER_IP, TRACKER_PORT))
                        
                        # Gửi lệnh và dữ liệu JSON
                        json_data = json.dumps(channel_data)
                        sync_socket.send(f"sync_channel {json_data}\n".encode())
                        
                        # Nhận phản hồi
                        response = sync_socket.recv(1024).decode().strip()
                        sync_socket.close()
                        
                        if response.startswith("OK"):
                            print(f"[Agent] Successfully synced channel {channel_name} with tracker")
                            tracker_sync_success = True
                        else:
                            print(f"[Agent] Failed to sync channel {channel_name} with tracker: {response}")
                    except Exception as e:
                        print(f"[Agent] Error syncing channel {channel_name} with tracker: {e}")
                except Exception as e:
                    print(f"[Agent] Error preparing data for channel {channel_name}: {e}")
                
                # 1.2 Đồng bộ tin nhắn trực tiếp với các peers đang online trong kênh
                peers_sync_success = False
                try:
                    # Lấy danh sách peers đang online
                    peers = self.register_to_tracker(get_peers=True)
                    if peers:
                        # Lọc ra các peers trong kênh
                        channel_members = channel.get_all_users()
                        channel_peers = [p for p in peers if p["username"] in channel_members and p["username"] != self.username]
                        
                        print(f"[Agent] Found {len(channel_peers)} online members in channel {channel_name}")
                        
                        # Gửi tin nhắn pending tới từng peer
                        for pending_msg in pending_messages:
                            message_data = {
                                "type": "message",
                                "channel": channel_name,
                                "content": pending_msg.content,
                                "sender": pending_msg.sender,
                                "timestamp": pending_msg.timestamp
                            }
                            
                            # Đếm số peer đã gửi thành công
                            success_count = 0
                            for peer in channel_peers:
                                try:
                                    send_to_peer(peer["ip"], int(peer["port"]), json.dumps(message_data))
                                    success_count += 1
                                except Exception as e:
                                    print(f"[Agent] Error sending message to peer {peer['username']}: {e}")
                            
                            if success_count > 0:
                                print(f"[Agent] Message sent to {success_count} peers")
                                peers_sync_success = True
                except Exception as e:
                    print(f"[Agent] Error synchronizing with peers: {e}")
                
                # 1.3 Cập nhật trạng thái tin nhắn nếu đồng bộ thành công
                if tracker_sync_success or peers_sync_success:
                    for msg in pending_messages:
                        msg.status = "sent"
                        messages_synced += 1
                    
                    # Lưu kênh sau khi cập nhật trạng thái tin nhắn
                    self.data_manager.save_channel(channel_name)
                    print(f"[Agent] Updated status of {len(pending_messages)} messages to 'sent'")
            
            # PHẦN 2: Lấy lịch sử tin nhắn từ các nguồn phù hợp
            
            if is_host:
                # 2.1 Nếu là host, lấy lịch sử từ tracker
                try:
                    print(f"[Agent] Fetching message history from tracker for channel {channel_name} (as host)")
                    updated_channel = self.fetch_channel_from_tracker(channel_name)
                    if updated_channel:
                        print(f"[Agent] Successfully fetched latest data for channel {channel_name} from tracker")
                        channels_synced += 1
                except Exception as e:
                    print(f"[Agent] Error fetching channel history from tracker: {e}")
                    sync_success = False
            else:
                # 2.2 Nếu không phải host, thử lấy lịch sử từ host trước, nếu host offline thì lấy từ tracker
                host_username = channel.host
                
                if not host_username or host_username == "unknown" or host_username == "visitor":
                    # Không có host hợp lệ, lấy từ tracker
                    print(f"[Agent] Channel {channel_name} has no valid host, fetching from tracker")
                    try:
                        updated_channel = self.fetch_channel_from_tracker(channel_name)
                        if updated_channel:
                            print(f"[Agent] Successfully fetched channel {channel_name} data from tracker")
                            channels_synced += 1
                    except Exception as e:
                        print(f"[Agent] Error fetching channel from tracker: {e}")
                        sync_success = False
                else:
                    # Kiểm tra host có online không
                    host_status = self.check_peer_status(host_username)
                    print(f"[Agent] Host {host_username} status for channel {channel_name}: {host_status}")
                    
                    if host_status == "online":
                        # Host online, lấy trực tiếp từ host
                        print(f"[Agent] Requesting message history from host {host_username}")
                        
                        # Lấy danh sách peers trên tracker
                        peers = self.register_to_tracker(get_peers=True)
                        host_peer = None
                        for peer in peers:
                            if peer["username"] == host_username:
                                host_peer = peer
                                break
                                
                        if host_peer:
                            try:
                                request_data = {
                                    "type": "request_history",
                                    "channel": channel_name,
                                    "username": self.username
                                }
                                
                                # Test connection to host first
                                test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                test_socket.settimeout(2)
                                
                                try:
                                    test_socket.connect((host_peer["ip"], int(host_peer["port"])))
                                    test_socket.close()
                                    
                                    # Nếu kết nối được, gửi yêu cầu lịch sử
                                    send_to_peer(host_peer["ip"], int(host_peer["port"]), json.dumps(request_data))
                                    print(f"[Agent] History request sent to host {host_username}")
                                    channels_synced += 1
                                except Exception:
                                    print(f"[Agent] Host {host_username} is not responsive, falling back to tracker")
                                    updated_channel = self.fetch_channel_from_tracker(channel_name)
                                    if updated_channel:
                                        print(f"[Agent] Successfully fetched channel {channel_name} data from tracker")
                                        channels_synced += 1
                            except Exception as e:
                                print(f"[Agent] Error requesting history from host: {e}")
                                sync_success = False
                        else:
                            print(f"[Agent] Could not find host {host_username} in peer list, falling back to tracker")
                            updated_channel = self.fetch_channel_from_tracker(channel_name)
                            if updated_channel:
                                print(f"[Agent] Successfully fetched channel {channel_name} data from tracker")
                                channels_synced += 1
                    else:
                        # Host offline, lấy từ tracker
                        print(f"[Agent] Host {host_username} is offline, fetching from tracker")
                        try:
                            updated_channel = self.fetch_channel_from_tracker(channel_name)
                            if updated_channel:
                                print(f"[Agent] Successfully fetched channel {channel_name} data from tracker")
                                channels_synced += 1
                        except Exception as e:
                            print(f"[Agent] Error fetching channel from tracker: {e}")
                            sync_success = False
        
        # Cập nhật thời gian đồng bộ
        self.last_sync = datetime.now()
        
        # Báo cáo kết quả
        print(f"[Agent] Sync complete: {channels_synced} channels synchronized, {messages_synced} messages updated")
        return sync_success
        
    def _handle_normal_sync(self, channel_name=None):
        """Xử lý đồng bộ thông thường cho kênh do người dùng làm host"""
        # Hàm này không còn được sử dụng - giữ lại để tương thích
        return self.sync_all(channel_name, "normal")
            
    def _handle_non_hosted_sync(self, channel_name):
        """Xử lý đồng bộ kênh không phải host với tracker"""
        # Hàm này không còn được sử dụng - giữ lại để tương thích
        return self.sync_all(channel_name, "non_hosted")
            
    def _handle_offline_sync(self):
        """Xử lý đồng bộ tin nhắn offline"""
        # Hàm này không còn được sử dụng - giữ lại để tương thích
        return self.sync_all(None, "offline")
        
    def _handle_reconnect_sync(self):
        """Xử lý đồng bộ khi kết nối lại với tracker"""
        # Hàm này không còn được sử dụng - giữ lại để tương thích
        return self.sync_all(None, "reconnect")

    def fetch_channel_from_tracker(self, channel_name):
        """Fetch channel data from the centralized server"""
        try:
            print(f"[Agent] Fetching channel {channel_name} data from tracker")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10)  # Tăng timeout lên 10 giây
            s.connect((TRACKER_IP, TRACKER_PORT))
            s.send(f"get_channel {channel_name}\n".encode())
            print(f"[Agent] Sent get_channel request to tracker for {channel_name}")
            
            # Nhận dữ liệu theo từng phần
            buffer = ""
            while True:
                try:
                    chunk = s.recv(4096).decode()
                    if not chunk:  # Kết nối đã đóng
                        print(f"[Agent] Connection closed by tracker while fetching {channel_name}")
                        break
                    
                    buffer += chunk
                    print(f"[Agent] Received {len(chunk)} bytes chunk for channel {channel_name}")
                    
                    # Kiểm tra nếu đã nhận xong dữ liệu (JSON phải hoàn chỉnh)
                    if buffer.count('{') == buffer.count('}') and '{' in buffer:
                        print(f"[Agent] JSON data for {channel_name} appears complete")
                        break
                except socket.timeout:
                    print(f"[Agent] Timeout receiving data for channel {channel_name}")
                    break
            
            s.close()
            
            if not buffer:
                print(f"[Agent] No data received for channel {channel_name}")
                return None
                
            if buffer.startswith("ERROR"):
                print(f"[Agent] Error fetching channel {channel_name}: {buffer}")
                return None
            
            try:
                print(f"[Agent] Parsing JSON data for channel {channel_name}")
                channel_data = json.loads(buffer)
                
                # Create or update channel in data manager
                channel = self.data_manager.get_channel(channel_name)
                if not channel:
                    print(f"[Agent] Creating new local channel {channel_name}")
                    self.data_manager.create_channel(channel_name, channel_data["host"])
                    channel = self.data_manager.get_channel(channel_name)
                else:
                    print(f"[Agent] Updating existing local channel {channel_name}")
                
                # Update channel data
                if channel:
                    # Update host if needed (only if our local copy doesn't have a host)
                    if not channel.host and "host" in channel_data and channel_data["host"]:
                        print(f"[Agent] Updating channel {channel_name} host to {channel_data['host']}")
                        channel.host = channel_data["host"]
                        
                    # Add members
                    members_added = 0
                    for member in channel_data.get("members", []):
                        if member not in channel.members:
                            members_added += 1
                            self.data_manager.join_channel(channel_name, member)
                    
                    if members_added > 0:
                        print(f"[Agent] Added {members_added} new members to channel {channel_name}")
                    
                    # Lấy danh sách timestamps của tin nhắn hiện có trong cục bộ
                    existing_timestamps = set()
                    for msg in channel.messages:
                        existing_timestamps.add(msg.timestamp)
                    
                    # Add messages, kiểm tra trùng lặp dựa trên timestamp
                    msg_count = 0
                    message_list = channel_data.get("messages", [])
                    print(f"[Agent] Processing {len(message_list)} messages from tracker for channel {channel_name}")
                    
                    for msg_data in message_list:
                        if isinstance(msg_data, dict):
                            timestamp = msg_data.get("timestamp")
                            if timestamp is None:
                                print(f"[Agent] Warning: Message without timestamp found, skipping")
                                continue
                                
                            # Chỉ thêm tin nhắn nếu chưa tồn tại trong cục bộ
                            if timestamp not in existing_timestamps:
                                try:
                                    # Sử dụng add_message_direct để bỏ qua kiểm tra quyền của người gửi
                                    self.add_message_direct(
                                        channel_name, 
                                        msg_data["sender"], 
                                        msg_data["content"], 
                                        timestamp,
                                        status="received"  # Mặc định là "received" nếu không có status
                                    )
                                    msg_count += 1
                                except KeyError as e:
                                    print(f"[Agent] Error adding message: Missing field {e}")
                            else:
                                print(f"[Agent] Skipping message with timestamp {timestamp} (already exists locally)")
                    
                    # Đảm bảo cập nhật được lưu vào file
                    if msg_count > 0:
                        print(f"[Agent] Saving channel {channel_name} after adding {msg_count} messages")
                        self.data_manager.save_channel(channel_name)
                        
                        # Sắp xếp tin nhắn theo thời gian sau khi thêm
                        self.data_manager.sort_channel_messages(channel)
                        print(f"[Agent] Sorted messages in channel {channel_name} after adding new messages from tracker")
                    
                    print(f"[Agent] Fetched channel {channel_name} from tracker: {msg_count} new messages added")
                    if msg_count == 0:
                        print(f"[Agent] No new messages for channel {channel_name}")
                    
                    return channel
                else:
                    print(f"[Agent] Failed to create/update channel {channel_name}")
                    return None
                    
            except json.JSONDecodeError as e:
                print(f"[Agent] JSON decode error: {e}")
                print(f"[Agent] Received data starts with: {buffer[:100]}...")
                return None
                
        except ConnectionRefusedError:
            print(f"[Agent] Connection refused by tracker. Make sure tracker is running.")
            return None
        except socket.timeout:
            print(f"[Agent] Connection to tracker timed out")
            return None
        except Exception as e:
            print(f"[Agent] Error fetching channel from tracker: {e}")
            return None

    def add_message_direct(self, channel_name, sender, content, timestamp=None, status="pending"):
        """
        Add a message directly to a channel without permission checks.
        Used only for synchronization from tracker.
        """
        try:
            channel = self.data_manager.get_channel(channel_name)
            if not channel:
                print(f"[Agent] Channel {channel_name} not found")
                return None
            
            # Kiểm tra trùng lặp tin nhắn dựa trên timestamp
            for existing_msg in channel.messages:
                if existing_msg.timestamp == timestamp and existing_msg.sender == sender and existing_msg.content == content:
                    print(f"[Agent] Duplicate message detected, not adding: {sender}/{timestamp}")
                    return existing_msg
            
            # Tạo và thêm tin nhắn mới nếu không trùng lặp
            message = Message(sender, content, channel_name, timestamp, status)
            channel.add_message(message)
            
            # Sắp xếp tin nhắn theo thời gian sau khi thêm
            self.data_manager.sort_channel_messages(channel)
            
            print(f"[Agent] Directly added message from {sender} to channel {channel_name} and sorted by timestamp")
            return message
        except Exception as e:
            print(f"[Agent] Error in add_message_direct: {e}")
            return None

    def add_message_to_channel(self, sender, content, channel_name):
        """
        Thêm tin nhắn vào kênh cục bộ với trạng thái 'pending'.
        Không thực hiện gửi đến peers hoặc tracker.
        """
        print(f"[Agent] Adding message to channel {channel_name}")
        
        # Get channel or return None if doesn't exist
        channel = self.data_manager.get_channel(channel_name)
        if not channel:
            print(f"[Agent] Channel {channel_name} does not exist")
            return False
        
        # Create message with initial status "pending"
        message = Message(sender, content, channel_name)
        message.update_status("pending")
        
        # Add message to channel
        channel.add_message(message)
        
        # Lưu message ngay lập tức với trạng thái pending
        self.data_manager.save_channel(channel_name)
        print(f"[Agent] Message saved locally with status 'pending'")
        
        return message  # Trả về message object để có thể cập nhật trạng thái sau này

    def list_available_channels(self):
        """List all available channels from tracker"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((TRACKER_IP, TRACKER_PORT))
            s.send(b"list_channels\n")
            data = s.recv(4096).decode()
            s.close()
            
            channels = json.loads(data)
            return channels
        except Exception as e:
            print(f"[Agent] Error listing channels: {e}")
            return []

    def check_peer_status(self, username):
        """Kiểm tra trạng thái của một peer cụ thể từ tracker"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((TRACKER_IP, TRACKER_PORT))
            s.send(f"check_status {username}\n".encode())
            response = s.recv(1024).decode()
            s.close()
            
            print(f"[Agent] Tracker response for {username} status: {response}")
            
            if response.startswith("STATUS:"):
                status = response.split(":", 1)[1].strip()
                return status
            else:
                return f"Error: {response}"
        except Exception as e:
            print(f"[Agent] Error checking peer status: {e}")
            return f"Error: {str(e)}"

    def handle_command(self, cmd):
        """Handle a command from the CLI
        Mô tả hàm:
        - Xử lý các lệnh đơn giản như login, list, join, logout, status, create, history
        - Các lệnh cần xác thực người dùng như join, create, history
        - Các lệnh không cần xác thực như list, exit, quit
        """
        # Setup common response
        response = {"status": "error", "message": "Unknown command", "username": self.username, "status_value": self.status}
        
        # Process command
        try:
            if not cmd:
                return response
                
            cmd_parts = cmd.split(' ', 1)
            action = cmd_parts[0].lower()
            params = cmd_parts[1] if len(cmd_parts) > 1 else ""
            
            # Commands available even when not authenticated
            if action == "exit" or action == "quit":
                print("[Agent] Shutting down...")
                response = {"status": "exit", "message": "Agent shutting down", "username": self.username, "status_value": self.status}
            
            elif action.startswith("login:"):
                # Special login command with username directly embedded
                username = action.split(":", 1)[1]
                self.username = username
                self.is_authenticated = True
                print(f"[Agent] Logged in as {username}")
                
                # Load user data
                self.data_manager.load_user_data(username)
                
                # Register with tracker
                self.register_to_tracker()
                
                # Đồng bộ dữ liệu đầy đủ từ tracker ngay sau khi đăng nhập
                if self.status != "offline":
                    print(f"[Agent] Performing initial data synchronization for user {username}")
                    # Sử dụng phương thức đồng bộ tin nhắn offline đã cải tiến
                    sync_result = self.sync_all(sync_type="offline")
                    if sync_result:
                        print("[Agent] Initial synchronization successful")
                    else:
                        print("[Agent] Warning: Issues occurred during initial synchronization")
                
                response = {
                    "status": "ok",
                    "message": f"Logged in as {username}",
                    "username": self.username,
                    "status_value": self.status
                }
            
            elif action == "list":
                # List online peers
                peers = self.register_to_tracker(get_peers=True)
                if peers:
                    print("\n[Peers]")
                    for peer in peers:
                        status_icon = "🟢" if peer.get("status") == "online" else "🔴" if peer.get("status") == "offline" else "⚪"
                        print(f"{status_icon} {peer.get('username')} ({peer.get('ip')}:{peer.get('port')})")
                    print()
                    # Trả về danh sách peers trong response
                    response = {
                        "status": "ok",
                        "message": "Listed peers",
                        "peers": peers,
                        "username": self.username,
                        "status_value": self.status
                    }
                else:
                    print("[Agent] No peers available")
                    response = {
                        "status": "ok",
                        "message": "No peers available",
                        "peers": [],
                        "username": self.username,
                        "status_value": self.status
                    }
            
            elif action == "list_all":
                # List all available channels from tracker
                channels = self.list_available_channels()
                if channels:
                    print("\n[Available Channels]")
                    for channel in channels:
                        # Đảm bảo members là một list trước khi gọi len()
                        members = channel.get('members', [])
                        if not isinstance(members, list):
                            members = []
                        print(f"📢 {channel['name']} (Host: {channel['host']}, Members: {len(members)})")
                    print()
                else:
                    print("[Agent] No channels available")
                response = {
                    "status": "ok",
                    "message": "Listed all channels",
                    "username": self.username,
                    "status_value": self.status
                }
            
            elif action == "join" and params:
                if not self.check_online_status():
                    print("[Agent] Cannot join channel while offline")
                    return {"status": "error", "message": "Cannot join channel while offline", "username": self.username, "status_value": self.status}
                channel_name = params.strip()
                
                # Visitors join as read-only
                as_visitor = not self.is_authenticated
                
                print(f"[Agent] Attempting to join channel: {channel_name} as {'visitor' if as_visitor else 'member'}")
                
                if self.data_manager.join_channel(channel_name, self.username or "visitor", as_visitor):
                    print(f"[Agent] Successfully joined channel {channel_name} as {'visitor (read-only)' if as_visitor else 'member'}")
                    
                    # Notify other peers
                    # Lấy danh sách peers từ tracker
                    peers = self.register_to_tracker(get_peers=True)
                    print(f"[Agent] Notifying {len(peers)} peers about joining channel")
                    
                    # Prepare join notification
                    join_data = {
                        "type": "join_channel",
                        "channel": channel_name,
                        "username": self.username or "visitor"
                    }
                    
                    # Send to peers
                    notify_count = 0
                    for peer in peers:
                        if peer["username"] != self.username:
                            try:
                                send_to_peer(peer["ip"], int(peer["port"]), json.dumps(join_data))
                                notify_count += 1
                            except Exception as e:
                                print(f"[Agent] Error notifying peer {peer['username']} about join: {e}")
                    
                    print(f"[Agent] Notified {notify_count} peers about join")
                    
                    # --- Gửi thông báo join_channel đến tracker ---
                    try:
                        tracker_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        tracker_sock.connect((TRACKER_IP, TRACKER_PORT))
                        tracker_sock.send((json.dumps(join_data) + "\n").encode())
                        tracker_sock.close()
                        print("[Agent] Notified tracker about joining channel")
                    except Exception as e:
                        print(f"[Agent] Error notifying tracker about join: {e}")
                    # ------------------------------------------------
                                
                    # If not visitor mode, request channel history
                    if self.is_authenticated:
                        print(f"[Agent] Requesting channel history for {channel_name}")
                        self.request_channel_history(channel_name)
                    
                    response = {
                        "status": "ok",
                        "message": f"Joined channel {channel_name}",
                        "username": self.username,
                        "status_value": self.status
                    }
                else:
                    print(f"[Agent] Failed to join channel {channel_name}")
                    response = {
                        "status": "error",
                        "message": f"Failed to join channel {channel_name}",
                        "username": self.username,
                        "status_value": self.status
                    }
            
            # Commands that require authentication
            elif not self.is_authenticated:
                response = {
                    "status": "error",
                    "message": "This command requires authentication. Please login first.",
                    "username": self.username,
                    "status_value": self.status
                }
            
            elif action == "logout":
                print(f"[Agent] Logging out user {self.username}")
                
                # Clear user data
                self.data_manager.clear_user_data(self.username)
                
                # Reset agent state
                old_username = self.username
                self.username = ""
                self.is_authenticated = False
                
                # Register with tracker as visitor
                self.register_to_tracker()
                
                response = {
                    "status": "ok",
                    "message": f"Logged out user {old_username}",
                    "username": self.username,
                    "status_value": self.status
                }
            
            elif action == "status" and params:
                # Check if this is a request to check another peer's status
                if params.startswith("check "):
                    target_username = params.split(' ', 1)[1].strip()
                    status = self.check_peer_status(target_username)
                    print(f"Status of {target_username}: {status}")
                    response = {
                        "status": "ok",
                        "message": f"Checked status of {target_username}",
                        "username": self.username,
                        "status_value": self.status
                    }
                    return response
                
                # Original status command to change own status
                status = params.strip().lower()
                old_status = self.status
                if status in ["online", "offline", "invisible"]:
                    self.status = status
                    
                    # Update tracker
                    if status != "invisible":
                        self.register_to_tracker()
                        
                    # Nếu chuyển từ offline sang online, đồng bộ tin nhắn
                    if old_status == "offline" and status == "online":
                        print("[Agent] Status changed from offline to online, syncing messages...")
                        # Sử dụng phương thức sync_offline_messages để thực hiện đồng bộ đúng thứ tự
                        sync_result = self.sync_all(sync_type="offline")
                        if sync_result:
                            print("[Agent] Successfully synchronized offline messages with peers and tracker")
                        else:
                            print("[Agent] Some issues occurred during offline message synchronization")
                    
                    print(f"[Agent] Status changed to {status}")
                    response = {
                        "status": "ok",
                        "message": f"Status changed to {status}",
                        "username": self.username,
                        "status_value": self.status
                    }
                else:
                    response = {
                        "status": "error",
                        "message": "Invalid status. Use 'online', 'offline', or 'invisible'.",
                        "username": self.username,
                        "status_value": self.status
                    }
            
            elif action == "channels":
                # List joined channels
                user_channels = self.data_manager.get_user_channels(self.username)
                hosted_channels = self.data_manager.get_hosted_channels(self.username)
                
                if user_channels:
                    print("\n[Your Channels]")
                    for channel_name in user_channels:
                        channel = self.data_manager.get_channel(channel_name)
                        if channel:
                            if channel_name in hosted_channels:
                                print(f"🔑 {channel_name} (Host: You, Members: {len(channel.members)})")
                            else:
                                print(f"📢 {channel_name} (Host: {channel.host}, Members: {len(channel.members)})")
                    print()
                else:
                    print("[Agent] You haven't joined any channels")
                
                response = {
                    "status": "ok",
                    "message": "Listed channels",
                    "username": self.username,
                    "status_value": self.status
                }
            
            elif action == "create" and params:
                # Only allow creating channels when online
                if self.check_online_status():
                    channel_name = params.strip()
                    
                    # Create channel with current user as host
                    channel = self.data_manager.create_channel(channel_name, self.username)
                    if channel:
                        print(f"[Agent] Created channel {channel_name} (you are the host)")
                        
                        # --- Gửi thông tin kênh mới lên tracker để tracker tạo kênh ---
                        try:
                            channel_data = {
                                "name": channel_name,
                                "host": self.username,
                                "members": [self.username],
                                "messages": []
                            }
                            sync_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sync_socket.settimeout(10)
                            sync_socket.connect((TRACKER_IP, TRACKER_PORT))
                            json_data = json.dumps(channel_data)
                            sync_socket.send(f"sync_channel {json_data}\n".encode())
                            response = sync_socket.recv(1024).decode().strip()
                            sync_socket.close()
                            if response.startswith("OK"):
                                print(f"[Agent] Tracker created channel {channel_name} successfully")
                            else:
                                print(f"[Agent] Tracker failed to create channel {channel_name}: {response}")
                        except Exception as e:
                            print(f"[Agent] Error notifying tracker to create channel: {e}")
                        # ----------------------------------------------------------
                        
                        # Sync with tracker (đồng bộ lại để chắc chắn)
                        self.sync_all(channel_name=channel_name, sync_type="normal")
                        
                        # Notify other peers
                        peers = self.register_to_tracker(get_peers=True)
                        
                        # Prepare create channel notification
                        create_data = {
                            "type": "join_channel",
                            "channel": channel_name,
                            "username": self.username
                        }
                        
                        # Send to peers
                        for peer in peers:
                            if peer["username"] != self.username:
                                try:
                                    send_to_peer(peer["ip"], int(peer["port"]), json.dumps(create_data))
                                except Exception as e:
                                    print(f"[Agent] Error notifying peer {peer['username']} about channel creation: {e}")
                        
                        response = {
                            "status": "ok",
                            "message": f"Created channel {channel_name}",
                            "username": self.username,
                            "status_value": self.status
                        }
                    else:
                        response = {
                            "status": "error",
                            "message": f"Failed to create channel {channel_name}",
                            "username": self.username,
                            "status_value": self.status
                        }
                else:
                    print("[Agent] Cannot create channel while offline")
                    response = {
                        "status": "error",
                        "message": "Cannot create channel while offline",
                        "username": self.username,
                        "status_value": self.status
                    }

            elif action == "leave" and params:
                channel_name = params.strip()
                
                # Leave channel
                if self.data_manager.leave_channel(channel_name, self.username):
                    print(f"[Agent] Left channel {channel_name}")
                    
                    # Notify other peers
                    peers = self.register_to_tracker(get_peers=True)
                    
                    # Prepare leave notification
                    leave_data = {
                        "type": "leave_channel",
                        "channel": channel_name,
                        "username": self.username
                    }
                    
                    # Send to peers
                    for peer in peers:
                        if peer["username"] != self.username:
                            try:
                                send_to_peer(peer["ip"], int(peer["port"]), json.dumps(leave_data))
                            except Exception as e:
                                print(f"[Agent] Error notifying peer {peer['username']} about leaving: {e}")
                    
                    response = {
                        "status": "ok",
                        "message": f"Left channel {channel_name}",
                        "username": self.username,
                        "status_value": self.status
                    }
                else:
                    response = {
                        "status": "error",
                        "message": f"Failed to leave channel {channel_name}",
                        "username": self.username,
                        "status_value": self.status
                    }
            
            elif action == "send" and params:
                try:
                    parts = params.split(' ', 1)
                    if len(parts) < 2:
                        response = {
                            "status": "error",
                            "message": "Invalid send command. Use 'send <channel> <message>'",
                            "username": self.username,
                            "status_value": self.status
                        }
                    else:
                        channel_name = parts[0].strip()
                        message_content = parts[1].strip()
                        
                        print(f"[Agent] Trying to send message '{message_content}' to channel '{channel_name}'")
                        
                        # Verify channel exists and user is member
                        user_channels = self.data_manager.get_user_channels(self.username)
                        if channel_name not in user_channels:
                            print(f"[Agent] Cannot send message: not in channel {channel_name}")
                            response = {
                                "status": "error",
                                "message": f"You are not a member of channel {channel_name}",
                                "username": self.username,
                                "status_value": self.status
                            }
                        else:
                            # Add message locally với trạng thái ban đầu là pending
                            message = self.add_message_to_channel(self.username, message_content, channel_name)
                            if not message:
                                print(f"[Agent] Failed to add message to local channel {channel_name}")
                                response = {
                                    "status": "error",
                                    "message": f"Failed to add message to channel {channel_name}",
                                    "username": self.username,
                                    "status_value": self.status
                                }
                                return response
                            
                            # Biến theo dõi trạng thái gửi
                            sent_successfully = False
                            
                            try:
                                # Get the channel object
                                channel = self.data_manager.get_channel(channel_name)
                                if not channel:
                                    print(f"[Agent] Could not retrieve channel {channel_name} for message sending")
                                    response = {
                                        "status": "error",
                                        "message": f"Failed to retrieve channel for message sending",
                                        "username": self.username,
                                        "status_value": self.status
                                    }
                                    return response
                                
                                # Chuẩn bị dữ liệu tin nhắn để gửi đi
                                message_data = {
                                    "type": "message",
                                    "channel": channel_name,
                                    "content": message_content,
                                    "sender": self.username,
                                    "timestamp": message.timestamp  # Use the same timestamp for consistency
                                }
                                
                                # Kiểm tra xem đang online hay offline
                                print(f"[Agent] Checking online status before sending message...")
                                print(f"[Agent] Current status: {self.status}")
                                is_online = self.check_online_status()
                                print(f"[Agent] Online check result: {is_online}")
                                
                                if is_online:
                                    # Get peers from tracker
                                    peers = self.register_to_tracker(get_peers=True)
                                    
                                    # Send to all peers in the channel
                                    sent_count = 0
                                    for peer in peers:
                                        if peer["username"] != self.username and peer["username"] in channel.get_all_users():
                                            try:
                                                send_to_peer(peer["ip"], int(peer["port"]), json.dumps(message_data))
                                                sent_count += 1
                                                sent_successfully = True
                                            except Exception as e:
                                                print(f"[Agent] Error sending message to peer {peer['username']}: {e}")
                                    
                                    print(f"[Agent] Message sent to {sent_count} peers in channel {channel_name}")
                                    
                                    # Nếu là host của channel, hoặc không gửi được đến peer nào, thử đồng bộ với tracker
                                    if channel.host == self.username or sent_count == 0:
                                        try:
                                            print(f"[Agent] Attempting to sync channel {channel_name} with tracker")
                                            if channel.host == self.username:
                                                tracker_sync = self.sync_all(channel_name=channel_name, sync_type="normal")
                                            else:
                                                tracker_sync = self.sync_all(channel_name=channel_name, sync_type="non_hosted")
                                                
                                            if tracker_sync:
                                                sent_successfully = True
                                                print(f"[Agent] Successfully synced message with tracker")
                                            else:
                                                print(f"[Agent] Failed to sync message with tracker")
                                        except Exception as e:
                                            print(f"[Agent] Error syncing with tracker: {e}")
                                else:
                                    print(f"[Agent] Agent is offline. Message will remain in 'pending' status.")
                                
                                # Cập nhật trạng thái tin nhắn nếu đã gửi thành công
                                if sent_successfully:
                                    message.update_status("sent")
                                    self.data_manager.save_channel(channel_name)
                                    print(f"[Agent] Message status updated to 'sent'")
                                
                            except Exception as e:
                                print(f"[Agent] Error sending message to peers: {e}")
                                # Vẫn coi là thành công nếu lưu cục bộ được, nhưng không cập nhật trạng thái
                            
                            response = {
                                "status": "ok",
                                "message": f"Message {'sent' if sent_successfully else 'queued'} to channel {channel_name}",
                                "username": self.username,
                                "status_value": self.status
                            }
                except Exception as e:
                    print(f"[Agent] Error processing send command: {e}")
                    response = {
                        "status": "error",
                        "message": f"Error processing send command: {e}",
                        "username": self.username,
                        "status_value": self.status
                    }
            
            elif action == "history" and params:
                channel_name = params.strip()
                
                # Request message history
                if self.request_channel_history(channel_name):
                    response = {
                        "status": "ok",
                        "message": f"Requested message history for channel {channel_name}",
                        "username": self.username,
                        "status_value": self.status
                    }
                else:
                    response = {
                        "status": "error",
                        "message": f"Failed to request message history for channel {channel_name}",
                        "username": self.username,
                        "status_value": self.status
                    }
            
            elif action == "sync":
                # Force sync with tracker
                if params.strip() == "auto":
                    # Thiết lập chế độ đồng bộ tự động
                    print("[Agent] Enabling automatic sync with tracker")
                    response = {
                        "status": "ok",
                        "message": "Automatic sync enabled",
                        "username": self.username,
                        "status_value": self.status
                    }
                    self._auto_sync = True
                elif params.strip() == "manual":
                    # Tắt chế độ đồng bộ tự động
                    print("[Agent] Disabling automatic sync with tracker")
                    response = {
                        "status": "ok",
                        "message": "Automatic sync disabled",
                        "username": self.username,
                        "status_value": self.status
                    }
                    self._auto_sync = False
                else:
                    # Force sync with tracker
                    if self.sync_all(sync_type="normal"):
                        response = {
                            "status": "ok",
                            "message": "Synced with tracker",
                            "username": self.username,
                            "status_value": self.status
                        }
                    else:
                        response = {
                            "status": "error",
                            "message": "Failed to sync with tracker",
                            "username": self.username,
                            "status_value": self.status
                        }
            
            elif action == "help":
                # Help command is handled by CLI
                response = {
                    "status": "ok",
                    "message": "Help displayed",
                    "username": self.username,
                    "status_value": self.status
                }
                
            else:
                response = {
                    "status": "error",
                    "message": f"Unknown command: {action}",
                    "username": self.username,
                    "status_value": self.status
                }
                
        except Exception as e:
            print(f"[Agent] Error handling command: {e}")
            response = {
                "status": "error",
                "message": f"Error: {e}",
                "username": self.username,
                "status_value": self.status
            }
            
        return response

    def request_channel_history(self, channel_name):
        """Request message history for a channel from its host"""
        try:
            # First check if channel exists and user can read it
            channel = self.data_manager.get_channel(channel_name)
            if not channel:
                # Try to fetch from tracker
                channel = self.fetch_channel_from_tracker(channel_name)
                if not channel:
                    print(f"[Agent] Channel {channel_name} not found")
                    return False
            
            # If we're the host, we already have the history - display it
            if channel.is_host(self.username):
                print(f"[Agent] You are the host of channel {channel_name}, displaying message history:")
                if not channel.messages:
                    print("\n[No messages in this channel yet]\n")
                else:
                    # Tạo một danh sách đã hiển thị để tránh hiển thị trùng lặp
                    displayed_messages = set()
                    
                    print(f"\n[Message History for {channel_name}]")
                    
                    # Sắp xếp tin nhắn theo thời gian
                    sorted_messages = sorted(channel.messages, key=lambda x: x.timestamp)
                    
                    for msg in sorted_messages:
                        # Tạo một định danh duy nhất cho tin nhắn
                        msg_id = f"{msg.timestamp}:{msg.sender}:{msg.content}"
                        
                        # Chỉ hiển thị nếu chưa hiển thị trước đó
                        if msg_id not in displayed_messages:
                            try:
                                timestamp = datetime.fromisoformat(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
                            except:
                                timestamp = msg.timestamp
                            # Add status indicator
                            status_icon = ""
                            if hasattr(msg, 'status'):
                                if msg.status == "pending":
                                    status_icon = "⌛" # Pending
                                elif msg.status == "sent":
                                    status_icon = "✅" # Fully delivered
                                elif msg.status == "received":
                                    status_icon = "📥"
                                else:
                                    status_icon = "❓" # Unknown status
                            
                            print(f"[{timestamp}] {msg.sender}: {msg.content} {status_icon}")
                            displayed_messages.add(msg_id)
                    print()
                return True
            
            # Get host information from tracker
            host = channel.host
            if not host or host == "unknown" or host == "visitor":
                print(f"[Agent] Channel {channel_name} has no valid host")
                return self.get_history_from_tracker(channel_name)
                
            # Get peer list from tracker
            peers = self.register_to_tracker(get_peers=True)
            host_peer = None
            for peer in peers:
                if peer["username"] == host:
                    host_peer = peer
                    break
                    
            if not host_peer:
                print(f"[Agent] Host {host} for channel {channel_name} is not online")
                return self.get_history_from_tracker(channel_name)
                
            # Request history from host
            try:
                request_data = {
                    "type": "request_history",
                    "channel": channel_name,
                    "username": self.username or "visitor"
                }
                
                print(f"[Agent] Requesting history for channel {channel_name} from host {host}")
                
                # Test connection to host first
                test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_socket.settimeout(2)  # Short timeout for the test
                
                try:
                    test_socket.connect((host_peer["ip"], int(host_peer["port"])))
                    test_socket.close()
                    
                    # If we get here, the connection was successful, send the history request
                    success = send_to_peer(host_peer["ip"], int(host_peer["port"]), json.dumps(request_data))
                    
                    if not success:
                        print(f"[Agent] Could not send request to host {host}. Trying tracker instead.")
                        return self.get_history_from_tracker(channel_name)
                        
                    print(f"[Agent] History request sent to host {host}. Please wait for response...")
                    return True
                    
                except (socket.timeout, ConnectionRefusedError, ConnectionError):
                    test_socket.close()
                    print(f"[Agent] Host {host} is not responsive. Trying tracker instead.")
                    return self.get_history_from_tracker(channel_name)
                    
            except Exception as e:
                print(f"[Agent] Error connecting to host {host}: {e}")
                return self.get_history_from_tracker(channel_name)
            
        except Exception as e:
            print(f"[Agent] Error requesting channel history: {e}")
            return False
            
    def get_history_from_tracker(self, channel_name):
        """Helper method to get history from tracker"""
        print(f"[Agent] Attempting to fetch history from tracker for channel {channel_name}")
        success = self.fetch_channel_from_tracker(channel_name) is not None
        if success:
            print(f"[Agent] Successfully retrieved channel data from tracker")
            # Display history from what we got from tracker
            channel = self.data_manager.get_channel(channel_name)
            if channel and channel.messages:
                # Tạo một danh sách đã hiển thị để tránh hiển thị trùng lặp
                displayed_messages = set()
                
                print(f"\n[Message History for {channel_name} (from tracker)]")
                
                # Sắp xếp tin nhắn theo thời gian
                sorted_messages = sorted(channel.messages, key=lambda x: x.timestamp)
                
                for msg in sorted_messages:
                    # Tạo một định danh duy nhất cho tin nhắn
                    msg_id = f"{msg.timestamp}:{msg.sender}:{msg.content}"
                    
                    # Chỉ hiển thị nếu chưa hiển thị trước đó
                    if msg_id not in displayed_messages:
                        try:
                            timestamp = datetime.fromisoformat(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
                        except:
                            timestamp = msg.timestamp
                            
                        # Add status indicator
                        status_icon = ""
                        if hasattr(msg, 'status'):
                            if msg.status == "pending":
                                status_icon = "⌛" # Pending
                            elif msg.status == "sent":
                                status_icon = "✅" # Fully delivered
                            elif msg.status == "received":
                                status_icon = "📥"
                            else:
                                status_icon = "❓" # Unknown status
                                
                        print(f"[{timestamp}] {msg.sender}: {msg.content} {status_icon}")
                        displayed_messages.add(msg_id)
                print()
            else:
                print("\n[No messages in this channel yet]\n")
        return success

    def update_username(self, new_username):
        """Update the username and re-register with tracker"""
        if not new_username:
            print("[Agent] Invalid username")
            return False
            
        old_username = self.username
        self.username = new_username
        self.is_authenticated = True
        
        # Register with tracker
        self.register_to_tracker()
        
        print(f"[Agent] Username changed from {old_username or 'visitor'} to {new_username}")
        return True

    def check_online_status(self):
        """Kiểm tra xem Agent có đang online không bằng cách ping đến tracker"""
        try:    
            # Thử kết nối đến tracker để kiểm tra
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)  # 3 giây timeout
            s.connect((TRACKER_IP, TRACKER_PORT))
            s.send(b"ping\n")
            response = s.recv(1024).decode().strip()
            s.close()
            
            if response == "pong":
                self.status = "online"
                return True
            else:
                self.status = "offline"
                return False
        except Exception as e:
            print(f"[Agent] Error checking online status: {e}")
            self.status = "offline"
            return False

def agent_main(command_queue: Queue, my_port: int, username: str = "", status: str = "online", response_queue: Queue = None):
    # Create agent object
    agent = Agent(my_port, username, status)
    
    # Create a shared variable to store the current username for the server thread
    # This will be accessed by a function that server threads can call to get the current username
    server_username = [username]  # Use a list so it can be modified by reference
    
    # Define a function to get the current username for server threads
    def get_current_username():
        return server_username[0]
    
    # Create server in a separate thread
    server_thread = threading.Thread(target=start_peer_server, args=(my_port, get_current_username))
    server_thread.daemon = True
    server_thread.start()
    
    print(f"[Agent] Started on port {my_port}")
    
    # Khởi tạo trạng thái kết nối ban đầu
    initial_status = agent.status
    try:
        # Trước tiên, thử kết nối tới tracker để xác định trạng thái ban đầu
        is_online = agent.check_online_status()
        if is_online:
            print(f"[Agent] Initially online, connected to tracker")
        else:
            print(f"[Agent] Initially offline, cannot connect to tracker")
        
        # Register with tracker (attempt)
        agent.register_to_tracker()
    except Exception as e:
        print(f"[Agent] Error during startup: {e}")
    
    # Variable to track tracker connection state
    tracker_connected = agent.check_online_status()
    last_connection_check = time.time()
    last_auto_sync = time.time()
    
    # Mặc định bật chế độ tự động đồng bộ
    if not hasattr(agent, '_auto_sync'):
        agent._auto_sync = True
    
    running = True
    while running:
        try:
            # Wait for commands from CLI
            if not command_queue.empty():
                cmd = command_queue.get()
                print(f"[Agent] Received command: {cmd}")
                
                # Process command
                result = agent.handle_command(cmd)
                
                # Send response back to CLI if response queue is provided
                if response_queue:
                    response_queue.put(result)  # <-- Trả về kết quả thực sự thay vì chỉ "done"
                
                # Check if we should exit
                if result["status"] == "exit":
                    running = False
                    break
                    
                # Check if username was updated
                if cmd.startswith("login:"):
                    # Update server thread username
                    new_username = cmd.split(":", 1)[1]
                    server_username[0] = new_username
                    print(f"[Agent] Updated server thread username to {new_username}")
            
            current_time = time.time()
            
            # Kiểm tra định kỳ kết nối với tracker (mỗi 10 giây)
            if current_time - last_connection_check > 10 and agent.is_authenticated:
                last_connection_check = current_time
                current_connection = agent.check_online_status()
                print(f"[Agent] Checking tracker connection status: {current_connection}")
                # Phát hiện kết nối lại với tracker
                if not tracker_connected and current_connection:
                    print("[Agent] Tracker connection re-established!")
                    # Reset trạng thái online
                    if agent.status == "offline":
                        print("[Agent] Updating status from offline to online")
                        agent.status = "online"
                        # Register lại với tracker với trạng thái mới
                        agent.register_to_tracker()
                    # Đồng bộ dữ liệu với tracker
                    agent.sync_all(sync_type="reconnect")


                # Cập nhật trạng thái kết nối
                tracker_connected = current_connection
        
            # Tự động đồng bộ định kỳ (mỗi 1 phút)
            if agent._auto_sync and current_time - last_auto_sync > 60 and agent.status != "offline" and agent.is_authenticated and tracker_connected:
                last_auto_sync = current_time
                print("[Agent] Performing scheduled automatic sync...")
                agent.sync_all(sync_type="reconnect")
                    
            # Brief sleep to prevent CPU hogging
            time.sleep(0.1)
            
        except KeyboardInterrupt:
            print("\n[Agent] Received interrupt, shutting down...")
            running = False
        except Exception as e:
            print(f"[Agent] Error: {e}")
    
    print("[Agent] Shutting down")
    # Cleanup operations here if needed

