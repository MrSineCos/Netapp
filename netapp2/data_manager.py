import json
import os
from datetime import datetime
import threading
import logging

DATA_DIR = "data"

# Thiết lập logging dùng chung file app.log ở thư mục gốc
log_file = os.path.join("app.log")
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

class Message:
    """Class representing a message in a channel"""
    def __init__(self, sender, content, channel, timestamp=None, status="pending"):
        self.sender = sender
        self.content = content
        self.channel = channel
        self.timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        self.status = status
    
    def to_dict(self):
        """Convert message to dictionary"""
        return {
            "sender": self.sender,
            "content": self.content,
            "channel": self.channel,
            "timestamp": self.timestamp,
            "status": self.status
        }
        
    def update_status(self, new_status):
        """Update message status"""
        self.status = new_status
        
    @classmethod
    def from_dict(cls, data):
        """Create message from dictionary"""
        # Check if status exists in data, if not default to "pending"
        status = data.get("status", "pending")
        return cls(
            data["sender"],
            data["content"],
            data["channel"],
            data.get("timestamp"),
            status
        )

class Channel:
    def __init__(self, name, host):
        self.name = name
        self.host = host
        self.messages = []
        self.members = set()
        self.visitors = set()  # Visitors who can read but not write
        # Automatically add host as member
        if host and host != "visitor":
            self.add_member(host)
        logger.info(f"[Channel] Channel {name} created with host {host}")

    def add_message(self, message):
        if isinstance(message, dict):
            message = Message.from_dict(message)
            
        # Kiểm tra tin nhắn trùng lặp trước khi thêm
        for existing_msg in self.messages:
            if (existing_msg.timestamp == message.timestamp and 
                existing_msg.sender == message.sender and 
                existing_msg.content == message.content):
                logger.info(f"[Channel] Channel.add_message: Duplicate message detected, not adding: {message.sender}/{message.timestamp}")
                return existing_msg
                
        # Thêm tin nhắn mới
        self.messages.append(message)
        
        # Sắp xếp tin nhắn theo thời gian
        try:
            self.messages.sort(key=lambda msg: msg.timestamp)
            logger.info(f"[Channel] Messages sorted by timestamp after adding new message")
        except Exception as e:
            logger.error(f"[Channel] Error sorting messages: {e}")
        
        return message

    def add_member(self, username):
        if not username or username == "visitor":
            return
        self.members.add(username)
        # Remove from visitors if they were one
        self.visitors.discard(username)
        logger.info(f"[Channel] Added {username} as member to channel {self.name}")

    def add_visitor(self, username):
        if not username:
            username = "visitor"
        # Only add as visitor if not already a member
        if username not in self.members:
            self.visitors.add(username)
            logger.info(f"[Channel] Added {username} as visitor to channel {self.name}")

    def remove_member(self, username):
        if username in self.members:
            self.members.discard(username)
            logger.info(f"[Channel] Removed {username} from members of channel {self.name}")
        if username in self.visitors:
            self.visitors.discard(username)
            logger.info(f"[Channel] Removed {username} from visitors of channel {self.name}")

    def is_member(self, username):
        return username in self.members

    def is_visitor(self, username):
        return username in self.visitors
        
    def is_host(self, username):
        # Check if username is the host
        return username == self.host
        
    def can_read(self, username):
        # Anyone who is a member or visitor can read
        # Also allow "visitor" as generic visitor username
        if username == "visitor":
            return True
        return username in self.members or username in self.visitors
        
    def can_write(self, username):
        # Only members can write
        if not username or username == "visitor":
            return False
        return username in self.members

    def get_all_users(self):
        return list(self.members) + list(self.visitors)
        
    def to_dict(self):
        """Chuyển đổi đối tượng Channel thành từ điển để serialize thành JSON"""
        try:
            # Tạo ra danh sách tin nhắn an toàn cho JSON serialization
            messages_list = []
            for msg in self.messages:
                try:
                    # Đảm bảo rằng mỗi tin nhắn đều là dictionary hợp lệ với đầy đủ thông tin bao gồm status
                    msg_dict = {
                        "sender": str(msg.sender),
                        "content": str(msg.content),
                        "channel": str(msg.channel),
                        "timestamp": str(msg.timestamp),
                        "status": str(msg.status) if hasattr(msg, "status") else "pending"
                    }
                    messages_list.append(msg_dict)
                except Exception as e:
                    logger.error(f"[Channel] Error converting message to dict: {e}")
                    # Bỏ qua tin nhắn lỗi
                    continue
            
            # Chuyển đổi sets thành lists để JSON serialization
            return {
                "name": str(self.name),
                "host": str(self.host),
                "members": [str(m) for m in self.members],
                "visitors": [str(v) for v in self.visitors],
                "messages": messages_list
            }
        except Exception as e:
            logger.error(f"[Channel] Error in Channel.to_dict: {e}")
            # Trả về cấu trúc tối giản nếu xảy ra lỗi
            return {
                "name": str(self.name),
                "host": str(self.host),
                "members": [],
                "visitors": [],
                "messages": []
            }

    def debug_info(self):
        """Return debug info about channel"""
        return f"Channel {self.name}: Host={self.host}, Members={self.members}, Visitors={self.visitors}, Messages={len(self.messages)}"

class DataManager:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DataManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._initialized = True
        self.channels = {}  # Name -> Channel
        self.user_channels = {}  # Username -> [channels]
        self.hosted_channels = {}  # Username -> [channels]
        self.offline_messages = {}  # Username -> {channel -> [messages]}
        self._lock = threading.Lock()
        
        # Create data directory if it doesn't exist
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR, exist_ok=True)
            
        # Load all saved channels
        self._load_channels()
        
    def _load_channels(self):
        """Load all channel data from disk"""
        try:
            for filename in os.listdir(DATA_DIR):
                if filename.endswith(".json") and not filename.startswith("user_"):
                    channel_name = filename[:-5]  # Remove .json extension
                    self.load_channel(channel_name)
                    
            # Sắp xếp tin nhắn theo thời gian cho tất cả các kênh sau khi tải
            self.sort_all_channels_messages()
            logger.info(f"[DataManager] Sorted messages in all channels after loading")
        except Exception as e:
            logger.error(f"[DataManager] Error loading channels: {e}")
            
    def sort_all_channels_messages(self):
        """Sắp xếp tin nhắn trong tất cả các kênh theo thời gian"""
        with self._lock:
            for channel_name, channel in self.channels.items():
                self.sort_channel_messages(channel)
                
    def sort_channel_messages(self, channel):
        """Sắp xếp tin nhắn trong một kênh theo thời gian"""
        try:
            if channel and channel.messages:
                channel.messages.sort(key=lambda msg: msg.timestamp)
                logger.info(f"[DataManager] Sorted {len(channel.messages)} messages in channel {channel.name}")
        except Exception as e:
            logger.error(f"[DataManager] Error sorting messages in channel {channel.name}: {e}")
            
    def load_channel(self, channel_name):
        """Load a specific channel from disk"""
        try:
            logger.info(f"[DataManager] Attempting to load channel {channel_name} from disk")
            filepath = os.path.join(DATA_DIR, f"{channel_name}.json")
            if os.path.exists(filepath):
                logger.info(f"[DataManager] Found channel file: {filepath}")
                with open(filepath, "r") as f:
                    try:
                        data = json.load(f)
                        logger.info(f"[DataManager] Successfully loaded JSON data for channel {channel_name}")
                        
                        channel = Channel(data["name"], data["host"])
                        logger.info(f"[DataManager] Created channel object: {channel.name} with host {channel.host}")
                        
                        # Add members
                        if "members" in data:
                            logger.info(f"[DataManager] Adding {len(data['members'])} members to channel")
                            for member in data.get("members", []):
                                channel.add_member(member)
                            
                        # Add visitors
                        if "visitors" in data:
                            logger.info(f"[DataManager] Adding {len(data['visitors'])} visitors to channel")
                            for visitor in data.get("visitors", []):
                                channel.add_visitor(visitor)
                            
                        # Add messages
                        if "messages" in data:
                            logger.info(f"[DataManager] Adding {len(data['messages'])} messages to channel")
                            for msg_data in data.get("messages", []):
                                # Ensure message status is preserved if present
                                if "status" not in msg_data:
                                    msg_data["status"] = "pending"
                                    logger.info(f"[DataManager] Added default 'pending' status to message")
                                else:
                                    logger.info(f"[DataManager] Message has status: {msg_data['status']}")
                                
                                message = Message.from_dict(msg_data)
                                channel.add_message(message)
                        
                        # Sắp xếp tin nhắn sau khi tải
                        self.sort_channel_messages(channel)
                        
                        logger.info(f"[DataManager] Updating channels dictionary")
                        # Use a direct lock check instead of nested lock acquisition
                        if threading.current_thread() is threading.main_thread():
                            # If we're already in a locked context in the main thread, update directly
                            self.channels[channel_name] = channel
                            
                            # Update user channels
                            logger.info(f"[DataManager] Updating user_channels for {len(channel.members)} members")
                            for member in channel.members:
                                if member not in self.user_channels:
                                    self.user_channels[member] = set()
                                self.user_channels[member].add(channel_name)
                                
                            # Update hosted channels
                            if channel.host and channel.host != "visitor":
                                logger.info(f"[DataManager] Updating hosted_channels for {channel.host}")
                                if channel.host not in self.hosted_channels:
                                    self.hosted_channels[channel.host] = set()
                                self.hosted_channels[channel.host].add(channel_name)
                        else:
                            # Otherwise acquire lock normally
                            logger.info(f"[DataManager] Acquiring lock to update channels dictionary")
                            with self._lock:
                                logger.info(f"[DataManager] Lock acquired, updating channels dictionary")
                                self.channels[channel_name] = channel
                                
                                # Update user channels
                                logger.info(f"[DataManager] Updating user_channels for {len(channel.members)} members")
                                for member in channel.members:
                                    if member not in self.user_channels:
                                        self.user_channels[member] = set()
                                    self.user_channels[member].add(channel_name)
                                    
                                # Update hosted channels
                                if channel.host and channel.host != "visitor":
                                    logger.info(f"[DataManager] Updating hosted_channels for {channel.host}")
                                    if channel.host not in self.hosted_channels:
                                        self.hosted_channels[channel.host] = set()
                                    self.hosted_channels[channel.host].add(channel_name)
                                
                        logger.info(f"[DataManager] Loaded channel {channel_name} with {len(channel.messages)} messages")
                        return channel
                    except json.JSONDecodeError as je:
                        logger.error(f"[DataManager] JSON decode error reading channel file {filepath}: {je}")
            else:
                logger.info(f"[DataManager] Channel file not found: {filepath}")
            return None
        except Exception as e:
            logger.error(f"[DataManager] Error loading channel {channel_name}: {e}")
            import traceback
            traceback.print_exc()
            return None
            
    def create_channel(self, channel_name, host):
        """Create a new channel"""
        try:
            logger.info(f"[DataManager] Creating new channel {channel_name} with host {host}")
            with self._lock:
                if channel_name in self.channels:
                    logger.info(f"[DataManager] Channel {channel_name} already exists")
                    return self.channels[channel_name]
                channel = Channel(channel_name, host)
                self.channels[channel_name] = channel
                # Update user channels
                if host and host != "visitor":
                    if host not in self.user_channels:
                        self.user_channels[host] = set()
                    self.user_channels[host].add(channel_name)
                    # Update hosted channels
                    if host not in self.hosted_channels:
                        self.hosted_channels[host] = set()
                    self.hosted_channels[host].add(channel_name)
                self.save_channel(channel_name)
                logger.info(f"[DataManager] Channel {channel_name} created and saved successfully")
                return channel
        except Exception as e:
            logger.error(f"[DataManager] Error creating channel {channel_name}: {e}")
            import traceback
            traceback.print_exc()
            return None
            
    def get_channel(self, channel_name) -> Channel:
        """Get a channel by name, loading from disk if needed"""
        try:
            logger.info(f"[DataManager] Getting channel {channel_name}")
            # Kiểm tra trước nếu channel có trong bộ nhớ
            if channel_name in self.channels:
                logger.info(f"[DataManager] Channel {channel_name} found in memory")
                return self.channels[channel_name]
                
            # Nếu không có trong bộ nhớ, thử tải từ đĩa
            logger.info(f"[DataManager] Channel {channel_name} not in memory, trying to load from disk")
            return self.load_channel(channel_name)
        except Exception as e:
            logger.error(f"[DataManager] Error in get_channel method: {e}")
            return None
        
    def add_message(self, channel_name, sender, content, timestamp=None):
        """Add a message to a channel"""
        with self._lock:
            channel = self.get_channel(channel_name)
            if not channel:
                logger.info(f"[DataManager] Channel {channel_name} not found")
                return None
                
            if not channel.can_write(sender):
                logger.info(f"[DataManager] User {sender} does not have permission to write to channel {channel_name}")
                return None
            
            # Kiểm tra trùng lặp tin nhắn dựa trên timestamp
            msg_timestamp = timestamp or datetime.now().isoformat()
            
            # Kiểm tra nếu tin nhắn với timestamp này đã tồn tại
            for existing_msg in channel.messages:
                if existing_msg.timestamp == msg_timestamp and existing_msg.sender == sender and existing_msg.content == content:
                    logger.info(f"[DataManager] Duplicate message detected, not adding: {sender}/{msg_timestamp}")
                    return existing_msg
                
            # Tạo và thêm tin nhắn mới nếu không trùng lặp
            message = Message(sender, content, channel_name, msg_timestamp, "received")
            channel.add_message(message)
            self.save_channel(channel_name)
            return message
            
    def join_channel(self, channel_name, username, as_visitor=False):
        """Add a user to a channel"""
        logger.info(f"[DataManager] Attempting to add user {username} to channel {channel_name} as {'visitor' if as_visitor else 'member'}")
        
        try:
            logger.info(f"[DataManager] Acquiring lock to join channel")
            with self._lock:
                logger.info(f"[DataManager] Lock acquired, getting channel {channel_name}")
                channel = self.get_channel(channel_name)
                logger.info(f"[DataManager] get_channel result for {channel_name}: {channel is not None}")
                
                if not channel:
                    # Channel doesn't exist, create it if user is authenticated
                    if username and username != "visitor" and not as_visitor:
                        logger.info(f"[DataManager] Channel {channel_name} doesn't exist, creating it with host {username}")
                        channel = self.create_channel(channel_name, username)
                        logger.info(f"[DataManager] create_channel result: {channel is not None}")
                    else:
                        logger.info(f"[DataManager] Channel {channel_name} not found and cannot be created by {username}")
                        return False
                        
                # Check if user is already in channel
                if username and username != "visitor":
                    if channel.is_member(username):
                        logger.info(f"[DataManager] User {username} is already a member of channel {channel_name}")
                        return True
                        
                    if channel.is_visitor(username) and not as_visitor:
                        logger.info(f"[DataManager] Upgrading user {username} from visitor to member in channel {channel_name}")
                    
                # Add user to channel
                logger.info(f"[DataManager] Adding {username} to channel {channel_name}")
                if as_visitor:
                    channel.add_visitor(username)
                    logger.info(f"[DataManager] Added {username} as visitor to channel {channel_name}")
                else:
                    channel.add_member(username)
                    logger.info(f"[DataManager] Added {username} as member to channel {channel_name}")
                    
                # Update user channels
                if username and username != "visitor":
                    logger.info(f"[DataManager] Updating user_channels for {username}")
                    if username not in self.user_channels:
                        self.user_channels[username] = set()
                    self.user_channels[username].add(channel_name)
                    logger.info(f"[DataManager] Updated user_channels list for {username}, now in {len(self.user_channels[username])} channels")
                    
                logger.info(f"[DataManager] Saving channel {channel_name}")
                self.save_channel(channel_name)
                logger.info(f"[DataManager] Join operation completed successfully")
                return True
        except Exception as e:
            logger.error(f"[DataManager] ERROR in join_channel method: {e}")
            return False
            
    def leave_channel(self, channel_name, username):
        """Remove a user from a channel"""
        with self._lock:
            channel = self.get_channel(channel_name)
            if not channel:
                logger.info(f"[DataManager] Channel {channel_name} not found")
                return False
                
            channel.remove_member(username)
            
            # Update user channels
            if username in self.user_channels:
                self.user_channels[username].discard(channel_name)
                
            self.save_channel(channel_name)
            return True
            
    def get_user_channels(self, username):
        """Get all channels a user belongs to"""
        with self._lock:
            if username in self.user_channels:
                return list(self.user_channels[username])
            return []
            
    def get_hosted_channels(self, username):
        """Get all channels a user hosts"""
        with self._lock:
            if username in self.hosted_channels:
                return list(self.hosted_channels[username])
            return []
            
    def get_all_channels(self):
        """Get all known channels"""
        with self._lock:
            return list(self.channels.keys())
            
    def add_channel(self, channel_name, host):
        """
        Thêm thông tin kênh vào dữ liệu cục bộ mà không tự động tham gia kênh.
        Khác với create_channel, hàm này không thêm host vào danh sách thành viên
        và không cập nhật user_channels.
        
        Tham số:
        - channel_name: Tên kênh
        - host: Người tạo kênh
        
        Trả về:
        - Channel object nếu thành công, None nếu thất bại
        """
        try:
            logger.info(f"[DataManager] Adding channel {channel_name} with host {host} to local database")
            
            # Check if channel already exists
            if channel_name in self.channels:
                logger.info(f"[DataManager] Channel {channel_name} already exists in local database")
                return self.channels[channel_name]
                
            # Check if we're already in a locked context in the main thread
            if threading.current_thread() is threading.main_thread():
                # Direct update without re-acquiring the lock
                logger.info(f"[DataManager] Direct creation in main thread for channel {channel_name}")
                
                # Create new channel but DON'T add host as member
                channel = Channel(channel_name, host)
                # Clear members list (since Channel constructor adds host by default)
                channel.members.clear()
                logger.info(f"[DataManager] Channel object created with empty members list")
                
                self.channels[channel_name] = channel
                
                # Update hosted_channels but NOT user_channels
                if host and host != "visitor":
                    logger.info(f"[DataManager] Updating hosted_channels for host {host}")
                    if host not in self.hosted_channels:
                        self.hosted_channels[host] = set()
                    self.hosted_channels[host].add(channel_name)
                
                # Save to disk
                self.save_channel(channel_name)
                logger.info(f"[DataManager] Channel {channel_name} added to local database successfully")
                return channel
            else:
                # Use lock normally for non-main threads
                with self._lock:
                    logger.info(f"[DataManager] Lock acquired for adding channel {channel_name}")
                    if channel_name in self.channels:
                        logger.info(f"[DataManager] Channel {channel_name} already exists")
                        return self.channels[channel_name]
                    
                    # Create new channel but DON'T add host as member
                    channel = Channel(channel_name, host)
                    # Clear members list (since Channel constructor adds host by default)
                    channel.members.clear()
                    logger.info(f"[DataManager] Channel object created with empty members list")
                    
                    self.channels[channel_name] = channel
                    
                    # Update hosted_channels but NOT user_channels
                    if host and host != "visitor":
                        logger.info(f"[DataManager] Updating hosted_channels for host {host}")
                        if host not in self.hosted_channels:
                            self.hosted_channels[host] = set()
                        self.hosted_channels[host].add(channel_name)
                    
                    # Save to disk
                    self.save_channel(channel_name)
                    logger.info(f"[DataManager] Channel {channel_name} added to local database successfully")
                    return channel
        except Exception as e:
            logger.error(f"[DataManager] Error adding channel {channel_name} to local database: {e}")
            import traceback
            traceback.print_exc()
            return None
            
    def add_offline_message(self, username, channel_name, message_data):
        """Store a message for offline delivery"""
        with self._lock:
            if username not in self.offline_messages:
                self.offline_messages[username] = {}
                
            if channel_name not in self.offline_messages[username]:
                self.offline_messages[username][channel_name] = []
                
            # Ensure message has status field
            if "status" not in message_data:
                message_data["status"] = "pending"
                
            self.offline_messages[username][channel_name].append(message_data)
            self._save_offline_messages(username)
            logger.info(f"[DataManager] Added offline message for {username} in channel {channel_name} with status: {message_data['status']}")
            
    def get_offline_messages(self, username):
        """Get all offline messages for a user"""
        with self._lock:
            if username in self.offline_messages:
                messages = self.offline_messages[username]
                # Clear messages after retrieval
                self.offline_messages[username] = {}
                self._save_offline_messages(username)
                return messages
            return {}
            
    def save_channel(self, channel_name):
        """Save a channel to disk"""
        try:
            logger.info(f"[DataManager] Saving channel {channel_name} to disk")
            channel = self.get_channel(channel_name)
            if not channel:
                logger.info(f"[DataManager] Cannot save channel {channel_name}: channel not found")
                return
            
            filepath = os.path.join(DATA_DIR, f"{channel_name}.json")
            logger.info(f"[DataManager] Writing channel data to {filepath}")
            
            # Prepare channel data with all message details including status
            channel_data = channel.to_dict()
            
            # Ensure all messages have status field
            for msg_data in channel_data.get("messages", []):
                if "status" not in msg_data:
                    msg_data["status"] = "pending"
            
            with open(filepath, "w") as f:
                json.dump(channel_data, f, indent=2)
                
            logger.info(f"[DataManager] Successfully saved channel {channel_name} to disk with {len(channel_data.get('messages', []))} messages including status information")
        except Exception as e:
            logger.error(f"[DataManager] Error saving channel {channel_name}: {e}")
            import traceback
            traceback.print_exc()
            
    def _save_offline_messages(self, username):
        """Save offline messages for a user to disk"""
        try:
            if not username or username == "visitor":
                return
                
            filepath = os.path.join(DATA_DIR, f"user_{username}_offline.json")
            
            if username in self.offline_messages and self.offline_messages[username]:
                # Ensure all offline messages have status field
                for channel_name, messages in self.offline_messages[username].items():
                    for msg in messages:
                        if "status" not in msg:
                            msg["status"] = "pending"
                
                with open(filepath, "w") as f:
                    json.dump(self.offline_messages[username], f, indent=2)
                    
                logger.info(f"[DataManager] Saved {sum(len(msgs) for msgs in self.offline_messages[username].values())} offline messages with status for {username}")
            elif os.path.exists(filepath):
                # Remove file if no messages
                os.remove(filepath)
                logger.info(f"[DataManager] Removed empty offline messages file for {username}")
        except Exception as e:
            logger.error(f"[DataManager] Error saving offline messages for {username}: {e}")
            
    def load_user_data(self, username):
        """Load user-specific data including offline messages"""
        try:
            if not username or username == "visitor":
                return
                
            # Load offline messages
            filepath = os.path.join(DATA_DIR, f"user_{username}_offline.json")
            if os.path.exists(filepath):
                with open(filepath, "r") as f:
                    with self._lock:
                        self.offline_messages[username] = json.load(f)
                        
                        # Ensure all loaded offline messages have status
                        offline_msg_count = 0
                        for channel_name, messages in self.offline_messages[username].items():
                            for msg in messages:
                                offline_msg_count += 1
                                if "status" not in msg:
                                    msg["status"] = "pending"
                        
                        logger.info(f"[DataManager] Loaded {offline_msg_count} offline messages for {username}")
                        
            # Rebuild user channels and hosted channels
            with self._lock:
                self.user_channels[username] = set()
                self.hosted_channels[username] = set()
                
                for channel_name, channel in self.channels.items():
                    if username in channel.members:
                        self.user_channels[username].add(channel_name)
                        
                    if channel.host == username:
                        self.hosted_channels[username].add(channel_name)
                        
        except Exception as e:
            logger.error(f"[DataManager] Error loading user data for {username}: {e}")
            
    def clear_user_data(self, username):
        """Clear user-specific data (for logout)"""
        with self._lock:
            if username in self.user_channels:
                self.user_channels[username] = set()
                
            if username in self.hosted_channels:
                self.hosted_channels[username] = set()
                
            if username in self.offline_messages:
                self.offline_messages[username] = {} 
            
    def clear_offline_messages(self, username):
        """Clear offline messages for a user"""
        with self._lock:
            if username in self.offline_messages:
                self.offline_messages[username] = {}
                self._save_offline_messages(username)
                logger.info(f"[DataManager] Cleared offline messages for {username}")
                return True
            return False