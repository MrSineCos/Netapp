# agent.py
import time
import json
import os
import threading
from multiprocessing import Process, Queue
from thread_client import send_to_peer
from thread_server import start_peer_server
import socket
from datetime import datetime
from data_manager import DataManager

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
        
        # If authenticated, load user data
        if self.is_authenticated:
            self.data_manager.load_user_data(username)

    def register_to_tracker(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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
                self.sync_on_tracker_reconnect()
            
            return data
        except Exception as e:
            print(f"[Agent] Error connecting to tracker: {e}")
            return []

    def sync_with_tracker(self, channel_name=None):
        """Synchronize channel data with the centralized server"""
        # Visitors cannot sync
        if not self.is_authenticated:
            print("[Agent] Cannot sync in visitor mode")
            return False
            
        if self.status == "offline":
            print("[Agent] Cannot sync while offline")
            return False

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((TRACKER_IP, TRACKER_PORT))
            
            if channel_name:
                # Sync specific channel
                hosted_channels = self.data_manager.get_hosted_channels(self.username)
                if channel_name in hosted_channels:
                    channel = self.data_manager.get_channel(channel_name)
                    if channel:
                        channel_data = channel.to_dict()
                        s.send(f"sync_channel {json.dumps(channel_data)}\n".encode())
                        response = s.recv(1024).decode()
                        if response.startswith("OK"):
                            print(f"[Agent] Synced channel {channel_name} with tracker")
                            result = True
                        else:
                            print(f"[Agent] Failed to sync channel {channel_name}: {response}")
                            result = False
                    else:
                        print(f"[Agent] Cannot sync channel {channel_name}: no data")
                        result = False
                else:
                    print(f"[Agent] Cannot sync channel {channel_name}: not a host")
                    result = False
            else:
                # Sync all hosted channels
                result = True
                hosted_channels = self.data_manager.get_hosted_channels(self.username)
                for channel_name in hosted_channels:
                    channel = self.data_manager.get_channel(channel_name)
                    if channel:
                        channel_data = channel.to_dict()
                        s.send(f"sync_channel {json.dumps(channel_data)}\n".encode())
                        response = s.recv(1024).decode()
                        if response.startswith("OK"):
                            print(f"[Agent] Synced channel {channel_name} with tracker")
                        else:
                            print(f"[Agent] Failed to sync channel {channel_name}: {response}")
                            result = False
            
            self.last_sync = datetime.now()
            s.close()
            return result
        except Exception as e:
            print(f"[Agent] Error syncing with tracker: {e}")
            return False

    def fetch_channel_from_tracker(self, channel_name):
        """Fetch channel data from the centralized server"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10)  # Tăng timeout lên 10 giây
            s.connect((TRACKER_IP, TRACKER_PORT))
            s.send(f"get_channel {channel_name}\n".encode())
            
            # Nhận dữ liệu theo từng phần
            buffer = ""
            while True:
                try:
                    chunk = s.recv(4096).decode()
                    if not chunk:  # Kết nối đã đóng
                        break
                    
                    buffer += chunk
                    
                    # Kiểm tra nếu đã nhận xong dữ liệu (JSON phải hoàn chỉnh)
                    if buffer.count('{') == buffer.count('}') and '{' in buffer:
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
                channel_data = json.loads(buffer)
                
                # Create or update channel in data manager
                channel = self.data_manager.get_channel(channel_name)
                if not channel:
                    self.data_manager.create_channel(channel_name, channel_data["host"])
                    channel = self.data_manager.get_channel(channel_name)
                    
                # Update channel data
                if channel:
                    # Add members
                    for member in channel_data.get("members", []):
                        self.data_manager.join_channel(channel_name, member)
                    
                    # Lấy danh sách timestamps của tin nhắn hiện có trong cục bộ
                    existing_timestamps = set()
                    for msg in channel.messages:
                        existing_timestamps.add(msg.timestamp)
                    
                    # Add messages, kiểm tra trùng lặp dựa trên timestamp
                    msg_count = 0
                    for msg_data in channel_data.get("messages", []):
                        if isinstance(msg_data, dict):
                            # Chỉ thêm tin nhắn nếu chưa tồn tại trong cục bộ
                            if msg_data.get("timestamp") not in existing_timestamps:
                                self.data_manager.add_message(
                                    channel_name, 
                                    msg_data["sender"], 
                                    msg_data["content"], 
                                    msg_data.get("timestamp")
                                )
                                msg_count += 1
                
                print(f"[Agent] Fetched channel {channel_name} from tracker with {msg_count} new messages")
                return channel
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

    def add_message_to_channel(self, channel, sender, content):
        """Add a message to a channel and sync with server if host"""
        # Visitors cannot send messages
        if not self.is_authenticated:
            print("[Agent] Cannot send messages in visitor mode")
            return False
            
        # Check if user is in channel
        user_channels = self.data_manager.get_user_channels(self.username)
        if channel not in user_channels:
            print(f"[Agent] Cannot add message: not in channel {channel}")
            return False
        
        # Create message
        message = {
            "sender": sender,
            "content": content,
            "channel": channel,
            "timestamp": datetime.now().isoformat()
        }
        
        # If offline, store in offline messages queue
        if self.status == "offline":
            # Lưu tin nhắn vào cục bộ
            self.data_manager.add_message(channel, sender, content, message.get("timestamp"))
            self.data_manager.add_offline_message(self.username, channel, message)
            print(f"[Agent] Message stored offline for channel {channel}")
            return True
            
        # Check if we're the host
        hosted_channels = self.data_manager.get_hosted_channels(self.username)
        if channel in hosted_channels:
            # Add message to channel
            self.data_manager.add_message(channel, sender, content)
            
            # Sync with tracker if online
            self.sync_with_tracker(channel)
        else:
            # Nếu không phải host, vẫn lưu tin nhắn cục bộ
            self.data_manager.add_message(channel, sender, content, message.get("timestamp"))
            
            # Đồng bộ tin nhắn với tracker bất kể host có online hay không
            print(f"[Agent] Non-host client sending message, synchronizing with tracker")
            sync_result = self.sync_non_hosted_channel_with_tracker(channel)
            if not sync_result:
                print(f"[Agent] Warning: Failed to sync message with tracker, it's only stored locally")
            
        return True

    def sync_offline_messages(self):
        """Sync any messages that were stored while offline"""
        if not self.is_authenticated:
            print("[Agent] Cannot sync in visitor mode")
            return False
            
        if self.status == "offline":
            print("[Agent] Cannot sync while offline")
            return False
        
        # Kiểm tra kết nối với tracker
        tracker_available = self.check_tracker_connection()
        if not tracker_available:
            print("[Agent] Cannot sync with tracker: Connection failed")
            return False
            
        # Get offline messages
        offline_messages = self.data_manager.get_offline_messages(self.username)
        if not offline_messages:
            print("[Agent] No offline messages to sync")
            
            # Đồng bộ tất cả các kênh không phải host mà client đã tham gia
            user_channels = self.data_manager.get_user_channels(self.username)
            hosted_channels = self.data_manager.get_hosted_channels(self.username)
            
            # Lấy các kênh mà client tham gia nhưng không phải host
            non_hosted_channels = [ch for ch in user_channels if ch not in hosted_channels]
            
            if non_hosted_channels:
                print(f"[Agent] Checking {len(non_hosted_channels)} non-hosted channels for sync")
                for channel_name in non_hosted_channels:
                    channel = self.data_manager.get_channel(channel_name)
                    if channel and channel.host:
                        # Kiểm tra trạng thái host
                        host_status = self.check_peer_status(channel.host)
                        if "offline" in host_status.lower():
                            # Nếu host offline, tiến hành đồng bộ với tracker
                            print(f"[Agent] Host for channel {channel_name} is offline, syncing with tracker")
                            self.sync_non_hosted_channel_with_tracker(channel_name)
            
            return True
            
        # Process each message
        for channel_name, messages in offline_messages.items():
            print(f"[Agent] Processing {len(messages)} offline messages for channel {channel_name}")
            
            # Kiểm tra xem mình có phải là host của kênh không
            hosted_channels = self.data_manager.get_hosted_channels(self.username)
            is_host = channel_name in hosted_channels
            
            for message in messages:
                # Send to peers
                try:
                    # Send message through peer server
                    message_data = {
                        "type": "message",
                        "channel": message["channel"],
                        "content": message["content"],
                        "sender": message["sender"]
                    }
                    
                    # Connect to own server first
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.connect((MY_IP, self.port))
                    s.send(json.dumps(message_data).encode())
                    s.close()
                    
                    # Also get peers from tracker and send to them
                    peers = self.register_to_tracker()
                    
                    # Send to all peers in the channel
                    channel = self.data_manager.get_channel(channel_name)
                    if channel:
                        # Get peer list from tracker
                        for peer in peers:
                            peer_username = peer["username"]
                            if peer_username != self.username and peer_username in channel.get_all_users():
                                try:
                                    send_to_peer(peer["ip"], int(peer["port"]), json.dumps(message_data))
                                except Exception as e:
                                    print(f"[Agent] Error sending message to peer {peer_username}: {e}")
                except Exception as e:
                    print(f"[Agent] Error sending offline message: {e}")
            
            # Đồng bộ với tracker
            if is_host:
                # Nếu là host, sử dụng phương thức sync thông thường
                self.sync_with_tracker(channel_name)
            else:
                # Nếu không phải host, kiểm tra host có online không
                channel = self.data_manager.get_channel(channel_name)
                if channel and channel.host:
                    host_status = self.check_peer_status(channel.host)
                    if "offline" in host_status.lower():
                        # Nếu host offline, dùng phương thức sync cho non-host
                        self.sync_non_hosted_channel_with_tracker(channel_name)
        
        # Xóa tin nhắn offline sau khi đã đồng bộ
        self.data_manager.clear_offline_messages(self.username)
        
        print("[Agent] Offline message sync complete")
        return True

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
        """Handle a command from the CLI"""
        # Setup common response
        response = {"status": "error", "message": "Unknown command"}
        
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
                response = {"status": "exit", "message": "Agent shutting down"}
            
            elif action.startswith("login:"):
                # Special login command with username directly embedded
                username = action.split(":", 1)[1]
                self.username = username
                self.is_authenticated = True
                print(f"[Agent] Logged in as {username}")
                
                # Register with tracker
                self.register_to_tracker()
                
                # Load user data
                self.data_manager.load_user_data(username)
                
                # Sync any offline messages
                if self.status != "offline":
                    self.sync_offline_messages()
                
                response = {"status": "ok", "message": f"Logged in as {username}"}
            
            elif action == "list":
                # List online peers
                peers = self.register_to_tracker()
                if peers:
                    print("\n[Peers]")
                    for peer in peers:
                        status_icon = "🟢" if peer["status"] == "online" else "🔴" if peer["status"] == "offline" else "⚪"
                        print(f"{status_icon} {peer['username']} ({peer['ip']}:{peer['port']})")
                    print()
                else:
                    print("[Agent] No peers available")
                response = {"status": "ok", "message": "Listed peers"}
            
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
                response = {"status": "ok", "message": "Listed all channels"}
            
            elif action == "join" and params:
                channel_name = params.strip()
                
                # Visitors join as read-only
                as_visitor = not self.is_authenticated
                
                print(f"[Agent] Attempting to join channel: {channel_name} as {'visitor' if as_visitor else 'member'}")
                
                if self.data_manager.join_channel(channel_name, self.username or "visitor", as_visitor):
                    print(f"[Agent] Successfully joined channel {channel_name} as {'visitor (read-only)' if as_visitor else 'member'}")
                    
                    # Notify other peers
                    peers = self.register_to_tracker()
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
                                
                    # If not visitor mode, request channel history
                    if self.is_authenticated:
                        print(f"[Agent] Requesting channel history for {channel_name}")
                        self.request_channel_history(channel_name)
                    
                    response = {"status": "ok", "message": f"Joined channel {channel_name}"}
                else:
                    print(f"[Agent] Failed to join channel {channel_name}")
                    response = {"status": "error", "message": f"Failed to join channel {channel_name}"}
            
            # Commands that require authentication
            elif not self.is_authenticated:
                response = {"status": "error", "message": "This command requires authentication. Please login first."}
            
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
                
                response = {"status": "ok", "message": f"Logged out user {old_username}"}
            
            elif action == "status" and params:
                # Check if this is a request to check another peer's status
                if params.startswith("check "):
                    target_username = params.split(' ', 1)[1].strip()
                    status = self.check_peer_status(target_username)
                    print(f"Status of {target_username}: {status}")
                    response = {"status": "ok", "message": f"Checked status of {target_username}"}
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
                        # Đồng bộ tin nhắn offline
                        sync_result = self.sync_offline_messages()
                        if sync_result:
                            print("[Agent] Successfully synchronized offline messages")
                        else:
                            print("[Agent] Some issues occurred during message synchronization")
                            
                        # Đồng bộ các kênh không phải host
                        user_channels = self.data_manager.get_user_channels(self.username)
                        hosted_channels = self.data_manager.get_hosted_channels(self.username)
                        non_hosted_channels = [ch for ch in user_channels if ch not in hosted_channels]
                        
                        if non_hosted_channels:
                            print(f"[Agent] Checking {len(non_hosted_channels)} non-hosted channels for additional sync")
                            for channel_name in non_hosted_channels:
                                channel = self.data_manager.get_channel(channel_name)
                                if channel and channel.host:
                                    host_status = self.check_peer_status(channel.host)
                                    if "offline" in host_status.lower():
                                        print(f"[Agent] Host for channel {channel_name} is offline, performing additional sync")
                                        self.sync_non_hosted_channel_with_tracker(channel_name)
                    
                    print(f"[Agent] Status changed to {status}")
                    response = {"status": "ok", "message": f"Status changed to {status}"}
                else:
                    response = {"status": "error", "message": "Invalid status. Use 'online', 'offline', or 'invisible'."}
            
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
                
                response = {"status": "ok", "message": "Listed channels"}
            
            elif action == "create" and params:
                channel_name = params.strip()
                
                # Create channel with current user as host
                channel = self.data_manager.create_channel(channel_name, self.username)
                if channel:
                    print(f"[Agent] Created channel {channel_name} (you are the host)")
                    
                    # Sync with tracker
                    self.sync_with_tracker(channel_name)
                    
                    # Notify other peers
                    peers = self.register_to_tracker()
                    
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
                    
                    response = {"status": "ok", "message": f"Created channel {channel_name}"}
                else:
                    response = {"status": "error", "message": f"Failed to create channel {channel_name}"}
            
            elif action == "leave" and params:
                channel_name = params.strip()
                
                # Leave channel
                if self.data_manager.leave_channel(channel_name, self.username):
                    print(f"[Agent] Left channel {channel_name}")
                    
                    # Notify other peers
                    peers = self.register_to_tracker()
                    
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
                    
                    response = {"status": "ok", "message": f"Left channel {channel_name}"}
                else:
                    response = {"status": "error", "message": f"Failed to leave channel {channel_name}"}
            
            elif action == "send" and params:
                try:
                    parts = params.split(' ', 1)
                    if len(parts) < 2:
                        response = {"status": "error", "message": "Invalid send command. Use 'send <channel> <message>'"}
                    else:
                        channel_name = parts[0].strip()
                        message_content = parts[1].strip()
                        
                        print(f"[Agent] Trying to send message '{message_content}' to channel '{channel_name}'")
                        
                        # Verify channel exists and user is member
                        user_channels = self.data_manager.get_user_channels(self.username)
                        if channel_name not in user_channels:
                            print(f"[Agent] Cannot send message: not in channel {channel_name}")
                            response = {"status": "error", "message": f"You are not a member of channel {channel_name}"}
                        else:
                            # Add message locally
                            add_result = self.add_message_to_channel(channel_name, self.username, message_content)
                            if not add_result:
                                print(f"[Agent] Failed to add message to local channel {channel_name}")
                                response = {"status": "error", "message": f"Failed to add message to channel {channel_name}"}
                                return response
                            
                            try:
                                # Send message to peers
                                message_data = {
                                    "type": "message",
                                    "channel": channel_name,
                                    "content": message_content,
                                    "sender": self.username
                                }
                                
                                # Get peers from tracker
                                peers = self.register_to_tracker()
                                
                                # Send to all peers in the channel
                                channel = self.data_manager.get_channel(channel_name)
                                if channel:
                                    sent_count = 0
                                    for peer in peers:
                                        if peer["username"] != self.username and peer["username"] in channel.get_all_users():
                                            try:
                                                send_to_peer(peer["ip"], int(peer["port"]), json.dumps(message_data))
                                                sent_count += 1
                                            except Exception as e:
                                                print(f"[Agent] Error sending message to peer {peer['username']}: {e}")
                                    
                                    print(f"[Agent] Message sent to {sent_count} peers in channel {channel_name}")
                                else:
                                    print(f"[Agent] Could not retrieve channel object for {channel_name}")
                            except Exception as e:
                                print(f"[Agent] Error sending message to peers: {e}")
                                # Still consider it a success if local storage worked
                            
                            response = {"status": "ok", "message": f"Message sent to channel {channel_name}"}
                except Exception as e:
                    print(f"[Agent] Error processing send command: {e}")
                    response = {"status": "error", "message": f"Error processing send command: {e}"}
            
            elif action == "history" and params:
                channel_name = params.strip()
                
                # Request message history
                if self.request_channel_history(channel_name):
                    response = {"status": "ok", "message": f"Requested message history for channel {channel_name}"}
                else:
                    response = {"status": "error", "message": f"Failed to request message history for channel {channel_name}"}
            
            elif action == "sync":
                # Force sync with tracker
                if params.strip() == "auto":
                    # Thiết lập chế độ đồng bộ tự động
                    print("[Agent] Enabling automatic sync with tracker")
                    response = {"status": "ok", "message": "Automatic sync enabled"}
                    self._auto_sync = True
                elif params.strip() == "manual":
                    # Tắt chế độ đồng bộ tự động
                    print("[Agent] Disabling automatic sync with tracker")
                    response = {"status": "ok", "message": "Automatic sync disabled"}
                    self._auto_sync = False
                else:
                    # Force sync with tracker
                    if self.sync_with_tracker():
                        response = {"status": "ok", "message": "Synced with tracker"}
                    else:
                        response = {"status": "error", "message": "Failed to sync with tracker"}
            
            elif action == "help":
                # Help command is handled by CLI
                response = {"status": "ok", "message": "Help displayed"}
                
            else:
                response = {"status": "error", "message": f"Unknown command: {action}"}
                
        except Exception as e:
            print(f"[Agent] Error handling command: {e}")
            response = {"status": "error", "message": f"Error: {e}"}
            
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
                            print(f"[{timestamp}] {msg.sender}: {msg.content}")
                            displayed_messages.add(msg_id)
                    print()
                return True
            
            # Get host information from tracker
            host = channel.host
            if not host or host == "unknown" or host == "visitor":
                print(f"[Agent] Channel {channel_name} has no valid host")
                return self.get_history_from_tracker(channel_name)
                
            # Get peer list from tracker
            peers = self.register_to_tracker()
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
                        print(f"[{timestamp}] {msg.sender}: {msg.content}")
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

    def sync_non_hosted_channel_with_tracker(self, channel_name):
        """Cho phép client không phải host đồng bộ dữ liệu kênh với tracker khi host offline hoặc khi tracker khởi động lại"""
        try:
            print(f"[Agent] Attempting to sync non-hosted channel {channel_name} with tracker")
            if not self.is_authenticated:
                print("[Agent] Cannot sync in visitor mode")
                return False
                
            if self.status == "offline":
                print("[Agent] Cannot sync while offline")
                return False
            
            # Kiểm tra kết nối với tracker
            if not self.check_tracker_connection():
                print("[Agent] Cannot sync with tracker: Connection failed")
                return False
            
            # Lấy dữ liệu kênh cục bộ
            channel = self.data_manager.get_channel(channel_name)
            if not channel:
                print(f"[Agent] Cannot sync channel {channel_name}: no local data")
                return False
                
            # Lấy dữ liệu kênh từ tracker trước khi đồng bộ
            # để đảm bảo không ghi đè lên tin nhắn khác
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(10)
                s.connect((TRACKER_IP, TRACKER_PORT))
                s.send(f"get_channel {channel_name}\n".encode())
                
                # Nhận dữ liệu theo từng phần
                buffer = ""
                while True:
                    try:
                        chunk = s.recv(4096).decode()
                        if not chunk:  # Kết nối đã đóng
                            break
                        
                        buffer += chunk
                        
                        # Kiểm tra nếu đã nhận xong dữ liệu (JSON phải hoàn chỉnh)
                        if buffer.count('{') == buffer.count('}') and '{' in buffer:
                            break
                    except socket.timeout:
                        break
                
                s.close()
                
                # Nếu có dữ liệu từ tracker, hợp nhất với dữ liệu cục bộ
                if buffer and not buffer.startswith("ERROR"):
                    tracker_data = json.loads(buffer)
                    
                    # Lấy danh sách timestamp các tin nhắn từ tracker
                    tracker_timestamps = set()
                    if "messages" in tracker_data:
                        for msg in tracker_data["messages"]:
                            if isinstance(msg, dict) and "timestamp" in msg:
                                tracker_timestamps.add(msg["timestamp"])
                    
                    # Tách các tin nhắn cục bộ mà tracker chưa có
                    new_messages = []
                    for msg in channel.messages:
                        if msg.timestamp not in tracker_timestamps:
                            new_messages.append(msg.to_dict())
                    
                    # Nếu có tin nhắn mới, đồng bộ với tracker
                    if new_messages:
                        print(f"[Agent] Found {len(new_messages)} new messages to sync with tracker")
                        
                        # Cập nhật dữ liệu từ tracker
                        for msg_data in new_messages:
                            if "messages" not in tracker_data:
                                tracker_data["messages"] = []
                            tracker_data["messages"].append(msg_data)
                        
                        # Đảm bảo dữ liệu là hợp lệ trước khi gửi
                        # Loại bỏ các trường có thể gây lỗi
                        safe_data = {
                            "name": tracker_data["name"],
                            "host": tracker_data["host"],
                            "members": list(tracker_data["members"]) if "members" in tracker_data else [],
                            "messages": tracker_data["messages"]
                        }
                        
                        # Kiểm tra JSON trước khi gửi
                        try:
                            json_data = json.dumps(safe_data)
                            
                            # Gửi dữ liệu đã cập nhật lên tracker
                            sync_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sync_socket.connect((TRACKER_IP, TRACKER_PORT))
                            
                            # Send command and JSON in separate parts to avoid issues with large data
                            sync_socket.send(b"sync_channel ")
                            
                            # Send JSON data in chunks to ensure all data is sent
                            json_bytes = json_data.encode()
                            chunk_size = 2048
                            
                            for i in range(0, len(json_bytes), chunk_size):
                                chunk = json_bytes[i:i+chunk_size]
                                sync_socket.send(chunk)
                                # Small delay to prevent overwhelming the receiver
                                time.sleep(0.01)
                            
                            # Send newline to mark end of command
                            sync_socket.send(b"\n")
                            
                            response = sync_socket.recv(4096).decode()
                            sync_socket.close()
                            
                            if response.startswith("OK"):
                                print(f"[Agent] Successfully synced {len(new_messages)} messages with tracker")
                                return True
                            else:
                                print(f"[Agent] Failed to sync with tracker: {response}")
                                return False
                        except Exception as e:
                            print(f"[Agent] Error preparing JSON data: {e}")
                            print(f"[Agent] Trying with simpler data structure")
                            
                            # Thử với cấu trúc đơn giản hơn
                            minimal_data = {
                                "name": channel_name,
                                "host": channel.host,
                                "members": list(channel.members),
                                "messages": new_messages
                            }
                            
                            try:
                                json_data = json.dumps(minimal_data)
                                
                                sync_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                sync_socket.connect((TRACKER_IP, TRACKER_PORT))
                                
                                # Send command and JSON in separate parts to avoid issues with large data
                                sync_socket.send(b"sync_channel ")
                                
                                # Send JSON data in chunks to ensure all data is sent
                                json_bytes = json_data.encode()
                                chunk_size = 2048
                                
                                for i in range(0, len(json_bytes), chunk_size):
                                    chunk = json_bytes[i:i+chunk_size]
                                    sync_socket.send(chunk)
                                    # Small delay to prevent overwhelming the receiver
                                    time.sleep(0.01)
                                
                                # Send newline to mark end of command
                                sync_socket.send(b"\n")
                                
                                response = sync_socket.recv(1024).decode()
                                sync_socket.close()
                                
                                if response.startswith("OK"):
                                    print(f"[Agent] Successfully synced with simplified data structure")
                                    return True
                                else:
                                    print(f"[Agent] Failed to sync with simplified data: {response}")
                                    return False
                            except Exception as e2:
                                print(f"[Agent] Error with simplified JSON data: {e2}")
                                return False
                    else:
                        print(f"[Agent] No new messages to sync with tracker")
                        return True
                else:
                    # Nếu tracker không có dữ liệu hoặc trả về lỗi, gửi dữ liệu cục bộ
                    if buffer.startswith("ERROR"):
                        print(f"[Agent] Error from tracker: {buffer}")
                    print(f"[Agent] Creating/updating channel on tracker with local data")
                    
                    # Chuẩn bị dữ liệu để gửi, chỉ bao gồm những thông tin cần thiết
                    try:
                        # Chuẩn bị tin nhắn
                        messages_data = []
                        for msg in channel.messages:
                            msg_dict = {
                                "sender": msg.sender,
                                "content": msg.content,
                                "channel": msg.channel,
                                "timestamp": msg.timestamp
                            }
                            messages_data.append(msg_dict)
                        
                        clean_channel_data = {
                            "name": channel.name,
                            "host": channel.host,
                            "members": list(channel.members),
                            "messages": messages_data
                        }
                        
                        json_data = json.dumps(clean_channel_data)
                        
                        sync_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sync_socket.connect((TRACKER_IP, TRACKER_PORT))
                        
                        # Send command and JSON in separate parts to avoid issues with large data
                        sync_socket.send(b"sync_channel ")
                        
                        # Send JSON data in chunks to ensure all data is sent
                        json_bytes = json_data.encode()
                        chunk_size = 2048
                        
                        for i in range(0, len(json_bytes), chunk_size):
                            chunk = json_bytes[i:i+chunk_size]
                            sync_socket.send(chunk)
                            # Small delay to prevent overwhelming the receiver
                            time.sleep(0.01)
                        
                        # Send newline to mark end of command
                        sync_socket.send(b"\n")
                        
                        response = sync_socket.recv(1024).decode()
                        sync_socket.close()
                        
                        if response.startswith("OK"):
                            print(f"[Agent] Successfully synced channel with tracker")
                            return True
                        else:
                            print(f"[Agent] Failed to sync with tracker: {response}")
                            return False
                    except Exception as e:
                        print(f"[Agent] Error preparing channel data: {e}")
                        
                        # Thử với cấu trúc tối giản
                        try:
                            minimal_data = {
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
                            
                            json_data = json.dumps(minimal_data)
                            
                            sync_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sync_socket.connect((TRACKER_IP, TRACKER_PORT))
                            
                            # Send command and JSON in separate parts to avoid issues with large data
                            sync_socket.send(b"sync_channel ")
                            
                            # Send JSON data in chunks to ensure all data is sent
                            json_bytes = json_data.encode()
                            chunk_size = 2048
                            
                            for i in range(0, len(json_bytes), chunk_size):
                                chunk = json_bytes[i:i+chunk_size]
                                sync_socket.send(chunk)
                                # Small delay to prevent overwhelming the receiver
                                time.sleep(0.01)
                            
                            # Send newline to mark end of command
                            sync_socket.send(b"\n")
                            
                            response = sync_socket.recv(1024).decode()
                            sync_socket.close()
                            
                            if response.startswith("OK"):
                                print(f"[Agent] Successfully synced with minimal data structure")
                                return True
                            else:
                                print(f"[Agent] Failed to sync with minimal data: {response}")
                                return False
                        except Exception as e2:
                            print(f"[Agent] Error with minimal JSON data: {e2}")
                            return False
            except json.JSONDecodeError as je:
                print(f"[Agent] JSON decode error: {je}")
                return False
            except Exception as e:
                print(f"[Agent] Error syncing with tracker: {e}")
                return False
        except Exception as e:
            print(f"[Agent] Error in sync_non_hosted_channel_with_tracker: {e}")
            return False

    def check_tracker_connection(self):
        """Kiểm tra kết nối với tracker server"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)  # 3 giây timeout
            s.connect((TRACKER_IP, TRACKER_PORT))
            s.send(b"ping\n")
            response = s.recv(1024).decode().strip()
            s.close()
            return response == "pong"
        except Exception as e:
            print(f"[Agent] Tracker connection check failed: {e}")
            return False
            
    def sync_on_tracker_reconnect(self):
        """Đồng bộ khi kết nối lại với tracker sau khi tracker khởi động"""
        if not self.is_authenticated:
            print("[Agent] Cannot sync in visitor mode")
            return False
            
        if self.status == "offline":
            print("[Agent] Cannot sync while offline")
            return False
            
        print("[Agent] Performing full sync after reconnecting to tracker")
        
        # 1. Đồng bộ tin nhắn offline trước
        self.sync_offline_messages()
        
        # 2. Đồng bộ tất cả các kênh mà client tham gia
        user_channels = self.data_manager.get_user_channels(self.username)
        hosted_channels = self.data_manager.get_hosted_channels(self.username)
        
        print(f"[Agent] Syncing {len(user_channels)} channels after tracker reconnection")
        
        for channel_name in user_channels:
            channel = self.data_manager.get_channel(channel_name)
            if not channel:
                continue
                
            if channel_name in hosted_channels:
                # Nếu là host, gửi tất cả tin nhắn đến tracker
                print(f"[Agent] Syncing hosted channel {channel_name} with tracker")
                self.sync_with_tracker(channel_name)
            else:
                # Nếu không phải host, đồng bộ tin nhắn từ các bên
                print(f"[Agent] Syncing non-hosted channel {channel_name}")
                
                # Lấy tin nhắn từ tracker trước để hợp nhất
                tracker_channel = self.fetch_channel_from_tracker(channel_name)
                
                if not tracker_channel:
                    # Nếu tracker không có dữ liệu kênh, đồng bộ dữ liệu client lên
                    print(f"[Agent] Channel {channel_name} not found on tracker, syncing local data")
                    self.sync_non_hosted_channel_with_tracker(channel_name)
                else:
                    # Nếu đã có dữ liệu ở cả hai bên, hợp nhất rồi đồng bộ lại
                    print(f"[Agent] Merging local and tracker data for channel {channel_name}")
                    # Dữ liệu đã được hợp nhất trong fetch_channel_from_tracker
                    # Giờ đồng bộ ngược lại với tracker để đảm bảo nhất quán
                    self.sync_non_hosted_channel_with_tracker(channel_name)
        
        print("[Agent] Full sync after tracker reconnection completed")
        return True

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
    
    # Register with tracker
    agent.register_to_tracker()
    
    # Variable to track tracker connection state
    tracker_connected = agent.check_tracker_connection()
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
                    response_queue.put("done")
                
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
            
            # Kiểm tra định kỳ kết nối với tracker (mỗi 30 giây)
            if current_time - last_connection_check > 30 and agent.status != "offline" and agent.is_authenticated:
                last_connection_check = current_time
                current_connection = agent.check_tracker_connection()
                
                # Phát hiện kết nối lại với tracker
                if not tracker_connected and current_connection:
                    print("[Agent] Tracker connection re-established, syncing data...")
                    agent.sync_on_tracker_reconnect()
                
                # Cập nhật trạng thái kết nối
                tracker_connected = current_connection
            
            # Tự động đồng bộ định kỳ (mỗi 5 phút nếu bật chế độ tự động)
            if agent._auto_sync and current_time - last_auto_sync > 300 and agent.status != "offline" and agent.is_authenticated and tracker_connected:
                last_auto_sync = current_time
                print("[Agent] Performing scheduled automatic sync...")
                agent.sync_on_tracker_reconnect()
                    
            # Brief sleep to prevent CPU hogging
            time.sleep(0.1)
            
        except KeyboardInterrupt:
            print("\n[Agent] Received interrupt, shutting down...")
            running = False
        except Exception as e:
            print(f"[Agent] Error: {e}")
    
    print("[Agent] Shutting down")
    # Cleanup operations here if needed

