# thread_server.py
import socket
import threading
import json
import os
from datetime import datetime
from thread_client import send_to_peer

# Tracker configuration
TRACKER_IP = "127.0.0.1"
TRACKER_PORT = 12345

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

class Channel:
    def __init__(self, name, host):
        self.name = name
        self.host = host
        self.messages = []
        self.members = set()
        self.visitors = set()  # Visitors who can read but not write
        # Automatically add host as member
        self.add_member(host)
        print(f"[DEBUG] Channel {name} created with host {host}")

    def add_message(self, message):
        self.messages.append(message)
        return message

    def add_member(self, username):
        if not username or username == "visitor":
            return
        self.members.add(username)
        # Remove from visitors if they were one
        self.visitors.discard(username)
        print(f"[DEBUG] Added {username} as member to channel {self.name}")

    def add_visitor(self, username):
        if not username:
            username = "visitor"
        # Only add as visitor if not already a member
        if username not in self.members:
            self.visitors.add(username)
            print(f"[DEBUG] Added {username} as visitor to channel {self.name}")

    def remove_member(self, username):
        if username in self.members:
            self.members.discard(username)
            print(f"[DEBUG] Removed {username} from members of channel {self.name}")
        if username in self.visitors:
            self.visitors.discard(username)
            print(f"[DEBUG] Removed {username} from visitors of channel {self.name}")

    def is_member(self, username):
        return username in self.members

    def is_visitor(self, username):
        return username in self.visitors
        
    def is_host(self, username):
        # Check if username is the host
        is_host = username == self.host
        print(f"[DEBUG] Checking host status for {username} in channel {self.name}")
        print(f"[DEBUG] Current host: {self.host}")
        print(f"[DEBUG] Is host: {is_host}")
        return is_host
        
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
        
    def debug_info(self):
        """Return debug info about channel"""
        return f"Channel {self.name}: Host={self.host}, Members={self.members}, Visitors={self.visitors}, Messages={len(self.messages)}"

# Global channel storage
channels = {}

def handle_peer(conn, username):
    is_authenticated = bool(username) and username != "visitor"  # Empty username or visitor means visitor mode
    
    try:
        while True:
            data = conn.recv(1024).decode()
            if not data:
                break

            try:
                message_data = json.loads(data)
                if message_data["type"] == "message":
                    channel_name = message_data["channel"]
                    content = message_data["content"]
                    sender = message_data["sender"]
                    
                    if channel_name not in channels:
                        print(f"[ERROR] Channel {channel_name} does not exist")
                        continue
                    
                    channel = channels[channel_name]
                    print(f"[DEBUG] Processing message in {channel_name} from {sender}")
                    print(f"[DEBUG] Channel state: {channel.debug_info()}")
                    
                    # Only process message if sender can write
                    # For non-members, automatically add them if authenticated
                    if sender and sender != "visitor" and not channel.is_member(sender):
                        channel.add_member(sender)
                        print(f"[DEBUG] Auto-added {sender} as member to channel {channel_name}")
                    
                    if channel.can_write(sender):
                        message = Message(sender, content, channel_name)
                        
                        # If we're the host, store the message
                        if channel.is_host(username):
                            channel.add_message(message)
                            print(f"[{channel_name}] {sender}: {content} (stored)")
                            
                            # As host, forward to all other members/visitors
                            try:
                                # Get updated peer list
                                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                s.connect((TRACKER_IP, TRACKER_PORT))
                                s.send(b"get_list\n")
                                peers = json.loads(s.recv(4096).decode())
                                s.close()
                                
                                # Forward to all users in channel
                                for recipient in channel.get_all_users():
                                    if recipient != username and recipient != sender:
                                        # Find recipient in peer list
                                        for peer in peers:
                                            if peer["username"] == recipient:
                                                try:
                                                    send_to_peer(peer["ip"], int(peer["port"]), json.dumps(message_data))
                                                    print(f"[DEBUG] Host forwarded message to {recipient}")
                                                except Exception as e:
                                                    print(f"[Error forwarding message to {recipient}]: {e}")
                                                break
                            except Exception as e:
                                print(f"[Error forwarding messages]: {e}")
                            
                            # Sync with tracker
                            try:
                                sync_data = {
                                    "type": "sync_with_tracker",
                                    "channel": channel_name
                                }
                                # Send to self to trigger tracker sync
                                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                s.connect(("127.0.0.1", int(conn.getsockname()[1])))
                                s.send(json.dumps(sync_data).encode())
                                s.close()
                            except Exception as e:
                                print(f"[Error triggering tracker sync]: {e}")
                                
                        # If sender is host, store the message
                        elif channel.is_host(sender):
                            channel.add_message(message)
                            print(f"[{channel_name}] {sender}: {content} (stored from host)")
                        # Otherwise just display it
                        else:
                            print(f"[{channel_name}] {sender}: {content}")
                    else:
                        print(f"[ERROR] User {sender} does not have permission to send messages in {channel_name}")
                    
                elif message_data["type"] == "join_channel":
                    channel_name = message_data["channel"]
                    joining_user = message_data["username"]
                    is_visitor = message_data.get("as_visitor", not is_authenticated)
                    
                    print(f"[DEBUG] User {joining_user} joining channel {channel_name} as {'visitor' if is_visitor else 'member'}")
                    
                    # Create channel if it doesn't exist yet
                    if channel_name not in channels:
                        print(f"[DEBUG] Creating new channel {channel_name} on join request")
                        channels[channel_name] = Channel(channel_name, joining_user)
                        
                    channel = channels[channel_name]
                    if is_visitor:
                        channel.add_visitor(joining_user)
                        print(f"{joining_user} joined channel {channel_name} as visitor (read-only)")
                    else:
                        channel.add_member(joining_user)
                        print(f"{joining_user} joined channel {channel_name} as member")
                    
                    print(f"[DEBUG] Channel state after join: {channel.debug_info()}")
                    
                    # Request message history if we're the host
                    if channel.is_host(username):
                        # Request message history from current host
                        request_history = {
                            "type": "request_history",
                            "channel": channel_name,
                            "username": username
                        }
                        # Send request to the current host
                        host = channel.host
                        # Find host information from tracker
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.connect((TRACKER_IP, TRACKER_PORT))
                        s.send(b"get_list\n")
                        peers = json.loads(s.recv(4096).decode())
                        s.close()
                        
                        for peer in peers:
                            if peer["username"] == host:
                                try:
                                    send_to_peer(peer["ip"], int(peer["port"]), json.dumps(request_history))
                                except Exception as e:
                                    print(f"[Error requesting history from {host}]: {e}")
                                break
                    
                elif message_data["type"] == "leave_channel":
                    channel_name = message_data["channel"]
                    leaving_user = message_data["username"]
                    if channel_name in channels:
                        channels[channel_name].remove_member(leaving_user)
                        print(f"{leaving_user} left channel {channel_name}")
                        print(f"[DEBUG] Channel state after leave: {channels[channel_name].debug_info()}")
                        
                elif message_data["type"] == "create_channel":
                    channel_name = message_data["channel"]
                    host = message_data["host"]
                    
                    print(f"[DEBUG] Received create channel notification: {channel_name} with host {host}")
                    
                    # Update or create new channel with specified host
                    if channel_name in channels:
                        # If channel already exists, update host
                        channels[channel_name].host = host
                        # Ensure host is a member
                        channels[channel_name].add_member(host)
                        print(f"[DEBUG] Updated host for channel {channel_name} to {host}")
                    else:
                        # Create new channel with specified host
                        channels[channel_name] = Channel(channel_name, host)
                        print(f"[DEBUG] Created new channel {channel_name} with host {host}")
                    
                    print(f"[DEBUG] Channel state after create: {channels[channel_name].debug_info()}")
                        
                elif message_data["type"] == "request_history":
                    channel_name = message_data["channel"]
                    requester = message_data["username"]
                    
                    print(f"[DEBUG] Received history request for channel {channel_name} from {requester}")
                    print(f"[DEBUG] Current user: {username}, is host: {channel_name in channels and channels[channel_name].is_host(username)}")
                    
                    if channel_name in channels:
                        channel = channels[channel_name]
                        print(f"[DEBUG] Channel host: {channel.host}")
                        
                        # Check if we're the host and the requester has read permission
                        if channel.is_host(username) and (channel.can_read(requester) or not requester):
                            # Send message history to requester
                            history_data = {
                                "type": "message_history",
                                "channel": channel_name,
                                "messages": [msg.to_dict() for msg in channel.messages]
                            }
                            
                            print(f"[DEBUG] Sending {len(channel.messages)} messages to {requester}")
                            
                            # Find requester information from tracker
                            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            s.connect((TRACKER_IP, TRACKER_PORT))
                            s.send(b"get_list\n")
                            peers = json.loads(s.recv(4096).decode())
                            s.close()
                            
                            for peer in peers:
                                if peer["username"] == requester:
                                    try:
                                        send_to_peer(peer["ip"], int(peer["port"]), json.dumps(history_data))
                                        print(f"[DEBUG] History sent to {requester} at {peer['ip']}:{peer['port']}")
                                    except Exception as e:
                                        print(f"[Error sending history to {requester}]: {e}")
                                    break
                        elif channel.is_host(username):
                            print(f"[DEBUG] Requester {requester} does not have permission to view channel history")
                                
                elif message_data["type"] == "message_history":
                    channel_name = message_data["channel"]
                    print(f"[DEBUG] Received message history for channel {channel_name}")
                    print(f"[DEBUG] Number of messages: {len(message_data['messages'])}")
                    
                    if channel_name in channels and not channels[channel_name].is_host(username):
                        # Update message history from host
                        channels[channel_name].messages = [Message.from_dict(msg) for msg in message_data["messages"]]
                        
                        # Display message history
                        print(f"\n[Message History for {channel_name}]")
                        if len(channels[channel_name].messages) == 0:
                            print("No messages in history.")
                        else:
                            for msg in channels[channel_name].messages:
                                timestamp = datetime.fromisoformat(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
                                print(f"[{timestamp}] {msg.sender}: {msg.content}")
                        print()
                
                elif message_data["type"] == "sync_with_tracker":
                    # Request to fetch data from tracker
                    channel_name = message_data["channel"]
                    if channel_name in channels and channels[channel_name].is_host(username):
                        # We're the host, sync our data with tracker
                        try:
                            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            s.connect((TRACKER_IP, TRACKER_PORT))
                            
                            # Prepare channel data
                            channel = channels[channel_name]
                            channel_data = {
                                "name": channel.name,
                                "host": channel.host,
                                "members": list(channel.members),
                                "messages": [msg.to_dict() for msg in channel.messages]
                            }
                            
                            # Send sync request
                            s.send(f"sync_channel {json.dumps(channel_data)}\n".encode())
                            response = s.recv(1024).decode()
                            s.close()
                            
                            print(f"[DEBUG] Synced channel {channel_name} with tracker: {response}")
                        except Exception as e:
                            print(f"[Error syncing with tracker]: {e}")
                elif message_data["type"] == "debug_channel":
                    # Debug command to show channel state
                    if "channel" in message_data:
                        channel_name = message_data["channel"]
                        if channel_name in channels:
                            print(f"[DEBUG] {channels[channel_name].debug_info()}")
                        else:
                            print(f"[DEBUG] Channel {channel_name} does not exist")
                    else:
                        # Show all channels
                        print("[DEBUG] All channels:")
                        for name, channel in channels.items():
                            print(f"[DEBUG] {channel.debug_info()}")

            except json.JSONDecodeError:
                print(f"[Error] Invalid message format from {username}")
            except Exception as err:
                print(f"[Error processing message]: {err}")

    except Exception as e:
        print(f"[Server Error]: {e}")
    finally:
        conn.close()

def start_peer_server(port, username):
    # Create data directory if it doesn't exist
    os.makedirs("data", exist_ok=True)
    
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('', port))
    server.listen()
    print(f"[Peer server] Listening on port {port} as {'visitor' if not username else username}...")
    
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_peer, args=(conn, username), daemon=True).start()
