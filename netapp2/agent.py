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
            s.connect((TRACKER_IP, TRACKER_PORT))
            s.send(f"get_channel {channel_name}\n".encode())
            response = s.recv(4096).decode()
            s.close()
            
            if response.startswith("ERROR"):
                print(f"[Agent] Error fetching channel {channel_name}: {response}")
                return None
            
            channel_data = json.loads(response)
            
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
                
                # Add messages
                for msg_data in channel_data.get("messages", []):
                    if isinstance(msg_data, dict):
                        self.data_manager.add_message(
                            channel_name, 
                            msg_data["sender"], 
                            msg_data["content"], 
                            msg_data.get("timestamp")
                        )
            
            print(f"[Agent] Fetched channel {channel_name} from tracker")
            return channel
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
            
        return True

    def sync_offline_messages(self):
        """Sync any messages that were stored while offline"""
        if not self.is_authenticated:
            print("[Agent] Cannot sync in visitor mode")
            return False
            
        if self.status == "offline":
            print("[Agent] Cannot sync while offline")
            return False
            
        # Get offline messages
        offline_messages = self.data_manager.get_offline_messages(self.username)
        if not offline_messages:
            print("[Agent] No offline messages to sync")
            return True
            
        # Process each message
        for channel_name, messages in offline_messages.items():
            print(f"[Agent] Processing {len(messages)} offline messages for channel {channel_name}")
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
                if status in ["online", "offline", "invisible"]:
                    self.status = status
                    
                    # Update tracker
                    if status != "invisible":
                        self.register_to_tracker()
                        
                    # If coming back online, sync offline messages
                    if status == "online":
                        self.sync_offline_messages()
                        
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
                parts = params.split(' ', 1)
                if len(parts) < 2:
                    response = {"status": "error", "message": "Invalid send command. Use 'send <channel> <message>'"}
                else:
                    channel_name = parts[0].strip()
                    message_content = parts[1].strip()
                    
                    # Verify channel exists and user is member
                    user_channels = self.data_manager.get_user_channels(self.username)
                    if channel_name not in user_channels:
                        response = {"status": "error", "message": f"You are not a member of channel {channel_name}"}
                    else:
                        # Add message locally
                        self.add_message_to_channel(channel_name, self.username, message_content)
                        
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
                            for peer in peers:
                                if peer["username"] != self.username and peer["username"] in channel.get_all_users():
                                    try:
                                        send_to_peer(peer["ip"], int(peer["port"]), json.dumps(message_data))
                                    except Exception as e:
                                        print(f"[Agent] Error sending message to peer {peer['username']}: {e}")
                        
                        response = {"status": "ok", "message": f"Message sent to channel {channel_name}"}
            
            elif action == "history" and params:
                channel_name = params.strip()
                
                # Request message history
                if self.request_channel_history(channel_name):
                    response = {"status": "ok", "message": f"Requested message history for channel {channel_name}"}
                else:
                    response = {"status": "error", "message": f"Failed to request message history for channel {channel_name}"}
            
            elif action == "sync":
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
                    print(f"\n[Message History for {channel_name}]")
                    for msg in channel.messages:
                        # Format the timestamp for better display
                        try:
                            timestamp = datetime.fromisoformat(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
                        except:
                            timestamp = msg.timestamp
                        print(f"[{timestamp}] {msg.sender}: {msg.content}")
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
                print(f"\n[Message History for {channel_name} (from tracker)]")
                for msg in channel.messages:
                    try:
                        timestamp = datetime.fromisoformat(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        timestamp = msg.timestamp
                    print(f"[{timestamp}] {msg.sender}: {msg.content}")
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
                    
            # Brief sleep to prevent CPU hogging
            time.sleep(0.1)
            
        except KeyboardInterrupt:
            print("\n[Agent] Received interrupt, shutting down...")
            running = False
        except Exception as e:
            print(f"[Agent] Error: {e}")
    
    print("[Agent] Shutting down")
    # Cleanup operations here if needed

