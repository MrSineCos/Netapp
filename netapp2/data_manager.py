import json
import os
from datetime import datetime
import threading

DATA_DIR = "data"

class Message:
    def __init__(self, sender, content, channel, timestamp=None):
        self.sender = sender
        self.content = content
        self.channel = channel
        self.timestamp = timestamp or datetime.now().isoformat()

    def to_dict(self):
        """Chuyển đổi đối tượng Message thành từ điển để serialize thành JSON"""
        try:
            return {
                "sender": str(self.sender),
                "content": str(self.content),
                "channel": str(self.channel),
                "timestamp": str(self.timestamp)
            }
        except Exception as e:
            print(f"[DataManager] Error in Message.to_dict: {e}")
            # Trả về giá trị an toàn nếu xảy ra lỗi
            return {
                "sender": "unknown",
                "content": "error_content",
                "channel": "unknown",
                "timestamp": datetime.now().isoformat()
            }

    @classmethod
    def from_dict(cls, data):
        return cls(
            sender=data["sender"],
            content=data["content"],
            channel=data["channel"],
            timestamp=data["timestamp"]
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
        print(f"[DataManager] Channel {name} created with host {host}")

    def add_message(self, message):
        if isinstance(message, dict):
            message = Message.from_dict(message)
            
        # Kiểm tra tin nhắn trùng lặp trước khi thêm
        for existing_msg in self.messages:
            if (existing_msg.timestamp == message.timestamp and 
                existing_msg.sender == message.sender and 
                existing_msg.content == message.content):
                print(f"[DataManager] Channel.add_message: Duplicate message detected, not adding: {message.sender}/{message.timestamp}")
                return existing_msg
                
        self.messages.append(message)
        return message

    def add_member(self, username):
        if not username or username == "visitor":
            return
        self.members.add(username)
        # Remove from visitors if they were one
        self.visitors.discard(username)
        print(f"[DataManager] Added {username} as member to channel {self.name}")

    def add_visitor(self, username):
        if not username:
            username = "visitor"
        # Only add as visitor if not already a member
        if username not in self.members:
            self.visitors.add(username)
            print(f"[DataManager] Added {username} as visitor to channel {self.name}")

    def remove_member(self, username):
        if username in self.members:
            self.members.discard(username)
            print(f"[DataManager] Removed {username} from members of channel {self.name}")
        if username in self.visitors:
            self.visitors.discard(username)
            print(f"[DataManager] Removed {username} from visitors of channel {self.name}")

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
                    # Đảm bảo rằng mỗi tin nhắn đều là dictionary hợp lệ
                    msg_dict = {
                        "sender": str(msg.sender),
                        "content": str(msg.content),
                        "channel": str(msg.channel),
                        "timestamp": str(msg.timestamp)
                    }
                    messages_list.append(msg_dict)
                except Exception as e:
                    print(f"[DataManager] Error converting message to dict: {e}")
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
            print(f"[DataManager] Error in Channel.to_dict: {e}")
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
        except Exception as e:
            print(f"[DataManager] Error loading channels: {e}")
            
    def load_channel(self, channel_name):
        """Load a specific channel from disk"""
        try:
            print(f"[DataManager] Attempting to load channel {channel_name} from disk")
            filepath = os.path.join(DATA_DIR, f"{channel_name}.json")
            if os.path.exists(filepath):
                print(f"[DataManager] Found channel file: {filepath}")
                with open(filepath, "r") as f:
                    try:
                        data = json.load(f)
                        print(f"[DataManager] Successfully loaded JSON data for channel {channel_name}")
                        
                        channel = Channel(data["name"], data["host"])
                        print(f"[DataManager] Created channel object: {channel.name} with host {channel.host}")
                        
                        # Add members
                        if "members" in data:
                            print(f"[DataManager] Adding {len(data['members'])} members to channel")
                            for member in data.get("members", []):
                                channel.add_member(member)
                            
                        # Add visitors
                        if "visitors" in data:
                            print(f"[DataManager] Adding {len(data['visitors'])} visitors to channel")
                            for visitor in data.get("visitors", []):
                                channel.add_visitor(visitor)
                            
                        # Add messages
                        if "messages" in data:
                            print(f"[DataManager] Adding {len(data['messages'])} messages to channel")
                            for msg_data in data.get("messages", []):
                                channel.add_message(Message.from_dict(msg_data))
                        
                        print(f"[DataManager] Acquiring lock to update channels dictionary")
                        with self._lock:
                            print(f"[DataManager] Lock acquired, updating channels dictionary")
                            self.channels[channel_name] = channel
                            
                            # Update user channels
                            print(f"[DataManager] Updating user_channels for {len(channel.members)} members")
                            for member in channel.members:
                                if member not in self.user_channels:
                                    self.user_channels[member] = set()
                                self.user_channels[member].add(channel_name)
                                
                            # Update hosted channels
                            if channel.host and channel.host != "visitor":
                                print(f"[DataManager] Updating hosted_channels for {channel.host}")
                                if channel.host not in self.hosted_channels:
                                    self.hosted_channels[channel.host] = set()
                                self.hosted_channels[channel.host].add(channel_name)
                                
                        print(f"[DataManager] Loaded channel {channel_name} with {len(channel.messages)} messages")
                        return channel
                    except json.JSONDecodeError as je:
                        print(f"[DataManager] JSON decode error reading channel file {filepath}: {je}")
            else:
                print(f"[DataManager] Channel file not found: {filepath}")
            return None
        except Exception as e:
            print(f"[DataManager] Error loading channel {channel_name}: {e}")
            import traceback
            traceback.print_exc()
            return None
            
    def create_channel(self, channel_name, host):
        """Create a new channel"""
        try:
            print(f"[DataManager] Creating new channel {channel_name} with host {host}")
            with self._lock:
                print(f"[DataManager] Lock acquired for creating channel {channel_name}")
                if channel_name in self.channels:
                    print(f"[DataManager] Channel {channel_name} already exists")
                    return self.channels[channel_name]
                
                print(f"[DataManager] Creating Channel object")
                channel = Channel(channel_name, host)
                print(f"[DataManager] Channel object created successfully")
                self.channels[channel_name] = channel
                
                # Update user channels
                if host and host != "visitor":
                    print(f"[DataManager] Updating user_channels for host {host}")
                    if host not in self.user_channels:
                        self.user_channels[host] = set()
                    self.user_channels[host].add(channel_name)
                    
                    # Update hosted channels
                    print(f"[DataManager] Updating hosted_channels for host {host}")
                    if host not in self.hosted_channels:
                        self.hosted_channels[host] = set()
                    self.hosted_channels[host].add(channel_name)
                
                print(f"[DataManager] Saving new channel to disk")
                self.save_channel(channel_name)
                print(f"[DataManager] Channel {channel_name} created and saved successfully")
                return channel
        except Exception as e:
            print(f"[DataManager] Error creating channel {channel_name}: {e}")
            import traceback
            traceback.print_exc()
            return None
            
    def get_channel(self, channel_name):
        """Get a channel by name, loading from disk if needed"""
        try:
            print(f"[DataManager] Getting channel {channel_name}")
            # Kiểm tra trước nếu channel có trong bộ nhớ
            if channel_name in self.channels:
                print(f"[DataManager] Channel {channel_name} found in memory")
                return self.channels[channel_name]
                
            # Nếu không có trong bộ nhớ, thử tải từ đĩa
            print(f"[DataManager] Channel {channel_name} not in memory, trying to load from disk")
            return self.load_channel(channel_name)
        except Exception as e:
            print(f"[DataManager] Error in get_channel method: {e}")
            return None
        
    def add_message(self, channel_name, sender, content, timestamp=None):
        """Add a message to a channel"""
        with self._lock:
            channel = self.get_channel(channel_name)
            if not channel:
                print(f"[DataManager] Channel {channel_name} not found")
                return None
                
            if not channel.can_write(sender):
                print(f"[DataManager] User {sender} does not have permission to write to channel {channel_name}")
                return None
            
            # Kiểm tra trùng lặp tin nhắn dựa trên timestamp
            msg_timestamp = timestamp or datetime.now().isoformat()
            
            # Kiểm tra nếu tin nhắn với timestamp này đã tồn tại
            for existing_msg in channel.messages:
                if existing_msg.timestamp == msg_timestamp and existing_msg.sender == sender and existing_msg.content == content:
                    print(f"[DataManager] Duplicate message detected, not adding: {sender}/{msg_timestamp}")
                    return existing_msg
                
            # Tạo và thêm tin nhắn mới nếu không trùng lặp
            message = Message(sender, content, channel_name, msg_timestamp)
            channel.add_message(message)
            self.save_channel(channel_name)
            return message
            
    def join_channel(self, channel_name, username, as_visitor=False):
        """Add a user to a channel"""
        print(f"[DataManager] Attempting to add user {username} to channel {channel_name} as {'visitor' if as_visitor else 'member'}")
        
        try:
            print(f"[DataManager] Acquiring lock to join channel")
            with self._lock:
                print(f"[DataManager] Lock acquired, getting channel {channel_name}")
                channel = self.get_channel(channel_name)
                print(f"[DataManager] get_channel result for {channel_name}: {channel is not None}")
                
                if not channel:
                    # Channel doesn't exist, create it if user is authenticated
                    if username and username != "visitor" and not as_visitor:
                        print(f"[DataManager] Channel {channel_name} doesn't exist, creating it with host {username}")
                        channel = self.create_channel(channel_name, username)
                        print(f"[DataManager] create_channel result: {channel is not None}")
                    else:
                        print(f"[DataManager] Channel {channel_name} not found and cannot be created by {username}")
                        return False
                        
                # Check if user is already in channel
                if username and username != "visitor":
                    if channel.is_member(username):
                        print(f"[DataManager] User {username} is already a member of channel {channel_name}")
                        return True
                        
                    if channel.is_visitor(username) and not as_visitor:
                        print(f"[DataManager] Upgrading user {username} from visitor to member in channel {channel_name}")
                    
                # Add user to channel
                print(f"[DataManager] Adding {username} to channel {channel_name}")
                if as_visitor:
                    channel.add_visitor(username)
                    print(f"[DataManager] Added {username} as visitor to channel {channel_name}")
                else:
                    channel.add_member(username)
                    print(f"[DataManager] Added {username} as member to channel {channel_name}")
                    
                # Update user channels
                if username and username != "visitor":
                    print(f"[DataManager] Updating user_channels for {username}")
                    if username not in self.user_channels:
                        self.user_channels[username] = set()
                    self.user_channels[username].add(channel_name)
                    print(f"[DataManager] Updated user_channels list for {username}, now in {len(self.user_channels[username])} channels")
                    
                print(f"[DataManager] Saving channel {channel_name}")
                self.save_channel(channel_name)
                print(f"[DataManager] Join operation completed successfully")
                return True
        except Exception as e:
            print(f"[DataManager] ERROR in join_channel method: {e}")
            return False
            
    def leave_channel(self, channel_name, username):
        """Remove a user from a channel"""
        with self._lock:
            channel = self.get_channel(channel_name)
            if not channel:
                print(f"[DataManager] Channel {channel_name} not found")
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
            
    def add_offline_message(self, username, channel_name, message_data):
        """Store a message for offline delivery"""
        with self._lock:
            if username not in self.offline_messages:
                self.offline_messages[username] = {}
                
            if channel_name not in self.offline_messages[username]:
                self.offline_messages[username][channel_name] = []
                
            self.offline_messages[username][channel_name].append(message_data)
            self._save_offline_messages(username)
            
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
            print(f"[DataManager] Saving channel {channel_name} to disk")
            channel = self.get_channel(channel_name)
            if not channel:
                print(f"[DataManager] Cannot save channel {channel_name}: channel not found")
                return
            
            filepath = os.path.join(DATA_DIR, f"{channel_name}.json")
            print(f"[DataManager] Writing channel data to {filepath}")
            
            # Prepare channel data
            channel_data = channel.to_dict()
            
            with open(filepath, "w") as f:
                json.dump(channel_data, f, indent=2)
                
            print(f"[DataManager] Successfully saved channel {channel_name} to disk")
        except Exception as e:
            print(f"[DataManager] Error saving channel {channel_name}: {e}")
            import traceback
            traceback.print_exc()
            
    def _save_offline_messages(self, username):
        """Save offline messages for a user to disk"""
        try:
            if not username or username == "visitor":
                return
                
            filepath = os.path.join(DATA_DIR, f"user_{username}_offline.json")
            
            if username in self.offline_messages and self.offline_messages[username]:
                with open(filepath, "w") as f:
                    json.dump(self.offline_messages[username], f, indent=2)
            elif os.path.exists(filepath):
                # Remove file if no messages
                os.remove(filepath)
        except Exception as e:
            print(f"[DataManager] Error saving offline messages for {username}: {e}")
            
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
            print(f"[DataManager] Error loading user data for {username}: {e}")
            
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
        """Clear offline messages for a user after syncing"""
        with self._lock:
            if username in self.offline_messages:
                self.offline_messages[username] = {}
                
            # Xóa file offline nếu tồn tại
            filepath = os.path.join(DATA_DIR, f"user_{username}_offline.json")
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    print(f"[DataManager] Removed offline messages file for {username}")
                except Exception as e:
                    print(f"[DataManager] Error removing offline messages file: {e}")
            
            return True 