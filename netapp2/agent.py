# agent.py
import time
import json
import os
from multiprocessing import Process, Queue
from thread_client import send_to_peer
from thread_server import start_peer_server
import socket
from datetime import datetime

TRACKER_IP = "127.0.0.1"
TRACKER_PORT = 12345
MY_IP = "127.0.0.1"
DATA_DIR = "data"

class Agent:
    def __init__(self, port, username, status="online"):
        self.port = port
        self.username = username
        self.status = status
        self.channels = set()
        self.hosted_channels = set()  # Channels where this agent is the host
        self.last_sync = datetime.now()
        self.channel_data = {}  # Store channel data locally
        self.offline_messages = {}  # Store messages when offline
        
        # Create data directory if it doesn't exist
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR, exist_ok=True)
            
        # Load locally cached data
        self.load_local_data()
        
        # Determine if we're in visitor mode
        self.is_authenticated = bool(username)

    def load_local_data(self):
        try:
            # Load cached channel data
            if self.username:  # Only load for authenticated users
                cache_file = os.path.join(DATA_DIR, f"user_{self.username}_cache.json")
                if os.path.exists(cache_file):
                    with open(cache_file, "r") as f:
                        data = json.load(f)
                        self.channels = set(data.get("channels", []))
                        self.hosted_channels = set(data.get("hosted_channels", []))
                        self.channel_data = data.get("channel_data", {})
                        self.offline_messages = data.get("offline_messages", {})
                    print(f"[Agent] Loaded cached data for {len(self.channels)} channels")
        except Exception as e:
            print(f"[Agent] Error loading cached data: {e}")

    def save_local_data(self):
        try:
            if self.username:  # Only save for authenticated users
                cache_file = os.path.join(DATA_DIR, f"user_{self.username}_cache.json")
                with open(cache_file, "w") as f:
                    data = {
                        "channels": list(self.channels),
                        "hosted_channels": list(self.hosted_channels),
                        "channel_data": self.channel_data,
                        "offline_messages": self.offline_messages
                    }
                    json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[Agent] Error saving cached data: {e}")

    def register_to_tracker(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((TRACKER_IP, TRACKER_PORT))
            s.send(f"send_info {MY_IP} {self.port} {self.username or 'visitor'} {self.status}\n".encode())
            s.recv(1024)
            s.send(b"get_list\n")
            data = json.loads(s.recv(4096).decode())
            s.close()
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
                if channel_name in self.hosted_channels and channel_name in self.channel_data:
                    channel_data = self.channel_data[channel_name]
                    s.send(f"sync_channel {json.dumps(channel_data)}\n".encode())
                    response = s.recv(1024).decode()
                    if response.startswith("OK"):
                        print(f"[Agent] Synced channel {channel_name} with tracker")
                        result = True
                    else:
                        print(f"[Agent] Failed to sync channel {channel_name}: {response}")
                        result = False
                else:
                    print(f"[Agent] Cannot sync channel {channel_name}: not a host or no data")
                    result = False
            else:
                # Sync all hosted channels
                result = True
                for channel in self.hosted_channels:
                    if channel in self.channel_data:
                        channel_data = self.channel_data[channel]
                        s.send(f"sync_channel {json.dumps(channel_data)}\n".encode())
                        response = s.recv(1024).decode()
                        if response.startswith("OK"):
                            print(f"[Agent] Synced channel {channel} with tracker")
                        else:
                            print(f"[Agent] Failed to sync channel {channel}: {response}")
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
            s.connect((TRACKER_IP, TRACKER_PORT))
            s.send(f"get_channel {channel_name}\n".encode())
            response = s.recv(4096).decode()
            s.close()
            
            if response.startswith("ERROR"):
                print(f"[Agent] Error fetching channel {channel_name}: {response}")
                return None
            
            channel_data = json.loads(response)
            self.channel_data[channel_name] = channel_data
            self.save_local_data()
            
            print(f"[Agent] Fetched channel {channel_name} from tracker with {len(channel_data['messages'])} messages")
            return channel_data
        except Exception as e:
            print(f"[Agent] Error fetching channel from tracker: {e}")
            return None

    def add_message_to_channel(self, channel, sender, content):
        """Add a message to a channel and sync with server if host"""
        # Visitors cannot send messages
        if not self.is_authenticated:
            print("[Agent] Cannot send messages in visitor mode")
            return False
            
        if channel not in self.channels:
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
            if channel not in self.offline_messages:
                self.offline_messages[channel] = []
            self.offline_messages[channel].append(message)
            self.save_local_data()
            print(f"[Agent] Message stored offline for channel {channel}")
            return True
            
        # If host, add to channel data and sync
        if channel in self.hosted_channels:
            if channel not in self.channel_data:
                self.channel_data[channel] = {
                    "name": channel,
                    "host": self.username,
                    "members": [self.username],
                    "messages": []
                }
            
            self.channel_data[channel]["messages"].append(message)
            self.save_local_data()
            
            # Sync with tracker if online
            self.sync_with_tracker(channel)
            
        return True

    def sync_offline_messages(self):
        """Sync messages that were stored while offline"""
        # Visitors cannot sync
        if not self.is_authenticated:
            print("[Agent] Cannot sync in visitor mode")
            return False
            
        if self.status == "offline":
            print("[Agent] Cannot sync offline messages while still offline")
            return False
        
        if not self.offline_messages:
            return True
            
        print(f"[Agent] Syncing {sum(len(msgs) for msgs in self.offline_messages.values())} offline messages")
        
        for channel, messages in self.offline_messages.items():
            if not messages:
                continue
                
            # Send messages to peers
            peers = self.register_to_tracker()
            for message in messages:
                message_data = {
                    "type": "message",
                    "channel": message["channel"],
                    "content": message["content"],
                    "sender": message["sender"],
                    "timestamp": message["timestamp"]
                }
                
                # If this is a channel we host, update our channel data
                if channel in self.hosted_channels:
                    if channel not in self.channel_data:
                        self.channel_data[channel] = {
                            "name": channel,
                            "host": self.username,
                            "members": [self.username],
                            "messages": []
                        }
                    self.channel_data[channel]["messages"].append(message)
                
                # Send to all peers
                for peer in peers:
                    ip, port = peer["ip"], peer["port"]
                    if int(port) != self.port:
                        try:
                            send_to_peer(ip, int(port), json.dumps(message_data))
                        except Exception as e:
                            print(f"[Error sending offline message to {peer['username']}]: {e}")
        
        # Sync with tracker if we host any channels
        if self.hosted_channels:
            self.sync_with_tracker()
            
        # Clear offline messages
        self.offline_messages = {}
        self.save_local_data()
        return True

    def list_available_channels(self):
        """Get list of all available channels from tracker"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((TRACKER_IP, TRACKER_PORT))
            s.send(b"list_channels\n")
            response = s.recv(4096).decode()
            s.close()
            
            if response.startswith("ERROR"):
                print(f"[Agent] Error listing channels: {response}")
                return []
            
            return json.loads(response)
        except Exception as e:
            print(f"[Agent] Error listing channels: {e}")
            return []

    def handle_command(self, cmd):
        if cmd == "list":
            peers = self.register_to_tracker()
            print("\n[Agent] Current Peers:")
            for peer in peers:
                print(f"- {peer['username']} ({peer['status']})")
            print()
            
        elif cmd == "channels":
            print(f"[Agent] Your channels: {list(self.channels)}")
            if self.hosted_channels:
                print(f"[Agent] Channels you host: {list(self.hosted_channels)}")
            
        elif cmd == "list_all":
            channels = self.list_available_channels()
            print("\n[Agent] Available channels:")
            if not channels:
                print("No channels available")
            else:
                for channel in channels:
                    print(f"- {channel['name']} (Host: {channel['host']}, Members: {channel['members']}, Messages: {channel['messages']})")
            print()
            
        elif cmd.startswith("join "):
            channel = cmd.split(" ", 1)[1]
            if channel not in self.channels:
                self.channels.add(channel)
                self.save_local_data()
                
                # First check if channel exists on tracker
                channel_exists = False
                channels = self.list_available_channels()
                for ch in channels:
                    if ch["name"] == channel:
                        channel_exists = True
                        break
                
                # If channel doesn't exist and we're authenticated, create it
                if not channel_exists and self.is_authenticated:
                    print(f"[Agent] Channel {channel} does not exist. Creating it as host.")
                    self.hosted_channels.add(channel)
                    self.channel_data[channel] = {
                        "name": channel,
                        "host": self.username,
                        "members": [self.username],
                        "messages": []
                    }
                    # Sync with tracker
                    if self.status != "offline":
                        self.sync_with_tracker(channel)
                
                # Notify all peers about join
                peers = self.register_to_tracker()
                for peer in peers:
                    ip, port = peer["ip"], peer["port"]
                    if int(port) != self.port:
                        message = {
                            "type": "join_channel",
                            "channel": channel,
                            "username": self.username or "visitor",
                            "as_visitor": not self.is_authenticated  # Mark as visitor for unauthenticated users
                        }
                        send_to_peer(ip, int(port), json.dumps(message))
                
                join_type = "visitor (read-only)" if not self.is_authenticated else "member"
                print(f"[Agent] Joined channel: {channel} as {join_type}")
                
                # Request message history
                self.request_channel_history(channel)
            else:
                print(f"[Agent] You are already in channel: {channel}")

        elif cmd == "debug":
            # Debug command to show channel state
            peers = self.register_to_tracker()
            debug_data = {
                "type": "debug_channel"
            }
            # Send to all peers
            for peer in peers:
                ip, port = peer["ip"], peer["port"]
                if int(port) != self.port:
                    try:
                        send_to_peer(ip, int(port), json.dumps(debug_data))
                    except Exception as e:
                        print(f"[Error sending debug request]: {e}")
            
        elif cmd.startswith("debug "):
            # Debug specific channel
            channel = cmd.split(" ", 1)[1]
            peers = self.register_to_tracker()
            debug_data = {
                "type": "debug_channel",
                "channel": channel
            }
            # Send to all peers
            for peer in peers:
                ip, port = peer["ip"], peer["port"]
                if int(port) != self.port:
                    try:
                        send_to_peer(ip, int(port), json.dumps(debug_data))
                    except Exception as e:
                        print(f"[Error sending debug request]: {e}")
            
        elif cmd.startswith("leave "):
            channel = cmd.split(" ", 1)[1]
            if channel in self.channels:
                self.channels.remove(channel)
                if channel in self.hosted_channels:
                    self.hosted_channels.remove(channel)
                    print(f"[Agent] Warning: You are leaving a channel you host. Messages have been backed up to server.")
                    self.sync_with_tracker(channel)
                
                self.save_local_data()
                
                peers = self.register_to_tracker()
                for peer in peers:
                    ip, port = peer["ip"], peer["port"]
                    if int(port) != self.port:
                        message = {
                            "type": "leave_channel",
                            "channel": channel,
                            "username": self.username or "visitor"
                        }
                        send_to_peer(ip, int(port), json.dumps(message))
                print(f"[Agent] Left channel: {channel}")
            else:
                print(f"[Agent] You are not in channel: {channel}")
                
        elif cmd.startswith("send "):
            parts = cmd.split(" ", 2)
            if len(parts) < 3:
                print("[Agent] Usage: send <channel> <message>")
                return
                
            channel = parts[1]
            message = parts[2]
            
            # Check visitor mode
            if not self.is_authenticated:
                print("[Agent] Visitors cannot send messages. Please login to send messages.")
                return
                
            if channel not in self.channels:
                print(f"[Agent] You are not in channel: {channel}")
                return
                
            # Add to channel data if host
            self.add_message_to_channel(channel, self.username, message)
            
            # If online, send to peers
            if self.status != "offline":
                peers = self.register_to_tracker()
                message_data = {
                    "type": "message",
                    "channel": channel,
                    "content": message,
                    "sender": self.username,
                    "timestamp": datetime.now().isoformat()
                }
                
                # Send to all peers
                success = False
                for peer in peers:
                    ip, port = peer["ip"], peer["port"]
                    if int(port) != self.port:
                        try:
                            send_to_peer(ip, int(port), json.dumps(message_data))
                            success = True
                        except Exception as e:
                            print(f"[Error sending to {peer['username']}]: {e}")
                
                if not success:
                    print("[Agent] Warning: Could not send message to any peers.")
                    # Ensure sync with tracker for backup
                    if channel in self.hosted_channels:
                        self.sync_with_tracker(channel)
            
            # Print message locally
            print(f"[{channel}] You: {message}")
                    
        elif cmd.startswith("create "):
            # Check visitor mode
            if not self.is_authenticated:
                print("[Agent] Visitors cannot create channels. Please login to create channels.")
                return
                
            channel = cmd.split(" ", 1)[1]
            if channel in self.channels:
                print(f"[Agent] Channel already exists: {channel}")
                return
                
            self.channels.add(channel)
            self.hosted_channels.add(channel)
            
            # Initialize channel data
            self.channel_data[channel] = {
                "name": channel,
                "host": self.username,
                "members": [self.username],
                "messages": []
            }
            
            self.save_local_data()
            
            # Sync with tracker
            if self.status != "offline":
                self.sync_with_tracker(channel)
                
                # Notify other peers
                peers = self.register_to_tracker()
                for peer in peers:
                    ip, port = peer["ip"], peer["port"]
                    if int(port) != self.port:
                        try:
                            create_message = {
                                "type": "create_channel",
                                "channel": channel,
                                "host": self.username
                            }
                            print(f"[DEBUG] Sending create channel notification to {peer['username']}: {channel} with host {self.username}")
                            send_to_peer(ip, int(port), json.dumps(create_message))
                        except Exception as e:
                            print(f"[Error notifying {peer['username']} about channel creation]: {e}")
            
            print(f"[Agent] Created channel: {channel} (you are the host)")
            
        elif cmd.startswith("status "):
            # Check visitor mode
            if not self.is_authenticated:
                print("[Agent] Visitors cannot change status. Please login to change status.")
                return
                
            new_status = cmd.split(" ", 1)[1].lower()
            old_status = self.status
            
            if new_status in ["online", "offline", "invisible"]:
                self.status = new_status
                
                # Handle transitions to/from offline
                if old_status == "offline" and new_status != "offline":
                    # Coming back online, sync offline messages
                    print("[Agent] Coming back online, syncing offline messages...")
                    self.sync_offline_messages()
                
                if self.status != "offline":
                    self.register_to_tracker()  # Update status with tracker
                
                self.save_local_data()
                print(f"[Agent] Status changed to: {new_status}")
            else:
                print("[Agent] Invalid status. Use: online, offline, or invisible")
                
        elif cmd.startswith("history "):
            channel = cmd.split(" ", 1)[1]
            if channel not in self.channels:
                print(f"[Agent] You are not in channel: {channel}")
                return
            
            self.request_channel_history(channel)
                
        elif cmd == "sync":
            # Check visitor mode
            if not self.is_authenticated:
                print("[Agent] Visitors cannot sync channels. Please login to sync channels.")
                return
                
            if self.status == "offline":
                print("[Agent] Cannot sync while offline")
                return
                
            if self.hosted_channels:
                print("[Agent] Syncing hosted channels with tracker...")
                self.sync_with_tracker()
            else:
                print("[Agent] You don't host any channels to sync")

    def request_channel_history(self, channel):
        """Request message history for a channel"""
        # Check if we have local data for this channel
        if channel in self.channel_data:
            messages = self.channel_data[channel]["messages"]
            print(f"\n[Message History for {channel}]")
            if not messages:
                print("No messages in history.")
            else:
                for msg in messages:
                    timestamp = datetime.fromisoformat(msg["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                    print(f"[{timestamp}] {msg['sender']}: {msg['content']}")
            print()
            return
            
        # If not, try to get from host or tracker
        if self.status == "offline":
            print("[Agent] Cannot fetch history while offline")
            return
            
        # Try to fetch from tracker
        channel_data = self.fetch_channel_from_tracker(channel)
        if channel_data:
            print(f"\n[Message History for {channel}]")
            if not channel_data["messages"]:
                print("No messages in history.")
            else:
                for msg in channel_data["messages"]:
                    timestamp = datetime.fromisoformat(msg["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                    print(f"[{timestamp}] {msg['sender']}: {msg['content']}")
            print()
            return
            
        # If tracker doesn't have it, request from peers
        peers = self.register_to_tracker()
        request_history = {
            "type": "request_history",
            "channel": channel,
            "username": self.username or "visitor"
        }
        
        host_found = False
        for peer in peers:
            ip, port = peer["ip"], peer["port"]
            if int(port) != self.port:
                try:
                    print(f"[Agent] Sending history request to {peer['username']} at {ip}:{port}")
                    send_to_peer(ip, int(port), json.dumps(request_history))
                    host_found = True
                except Exception as e:
                    print(f"[Error requesting history from {peer['username']}]: {e}")
        
        if not host_found:
            print(f"[Agent] Could not find any peers to request history from")
        else:
            print(f"[Agent] Requesting message history for channel: {channel}")
            print(f"[Agent] Waiting for response from host...")

    def update_username(self, new_username):
        """Update agent's username when logging in/out"""
        old_username = self.username
        self.username = new_username
        self.is_authenticated = bool(new_username)
        
        # When logging in/out, reset channel lists
        self.channels = set()
        self.hosted_channels = set()
        self.channel_data = {}
        
        # If logging in, load cached data
        if new_username:
            self.load_local_data()
            
        # Register new username with tracker
        self.register_to_tracker()
        
        print(f"[Agent] Updated username from {old_username or 'visitor'} to {new_username or 'visitor'}")

def agent_main(command_queue: Queue, my_port: int, username: str = "", status: str = "online"):
    print(f"[Agent] Running on port {my_port}...")
    
    agent = Agent(my_port, username, status)
    
    # Start peer server
    peer_server = Process(target=start_peer_server, args=(my_port, username), daemon=True)
    peer_server.start()
    time.sleep(1)  # wait for server to start
    
    # Register with tracker and get default username if needed
    peers = agent.register_to_tracker()
    if username:
        print(f"[Agent] Registered as {username} ({status})")
    else:
        print(f"[Agent] Running in visitor mode")
    
    # Sync offline messages if coming online
    if agent.offline_messages and status != "offline" and username:
        print("[Agent] Syncing offline messages...")
        agent.sync_offline_messages()
    
    # Set up periodic sync for hosted channels (every 30 seconds)
    last_sync = time.time()
    
    while True:
        if not command_queue.empty():
            cmd = command_queue.get()
            
            if cmd == "exit":
                print("[Agent] Exiting...")
                
                # Final sync before exit if hosting channels
                if agent.hosted_channels and agent.status != "offline":
                    print("[Agent] Final sync of hosted channels before exit...")
                    agent.sync_with_tracker()
                
                break
            elif cmd.startswith("login:"):
                # Handle login command
                new_username = cmd.split(":", 1)[1]
                agent.update_username(new_username)
                
                # Restart peer server with new username
                peer_server.terminate()
                peer_server.join()
                peer_server = Process(target=start_peer_server, args=(my_port, new_username), daemon=True)
                peer_server.start()
                
            elif cmd == "logout":
                # Handle logout command
                agent.update_username("")
                
                # Restart peer server in visitor mode
                peer_server.terminate()
                peer_server.join()
                peer_server = Process(target=start_peer_server, args=(my_port, ""), daemon=True)
                peer_server.start()
                
            else:
                agent.handle_command(cmd)
        
        # Periodic sync for hosted channels
        if agent.hosted_channels and agent.status != "offline" and time.time() - last_sync > 30:
            agent.sync_with_tracker()
            last_sync = time.time()
        
        time.sleep(1)

