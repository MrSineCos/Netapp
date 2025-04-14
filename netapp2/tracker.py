# tracker.py
import socket
import threading
import json
import os
from datetime import datetime

class Peer:
    def __init__(self, ip, port, username, status):
        self.ip = ip
        self.port = port
        self.username = username
        self.status = status

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
        print(f"[Tracker] Created channel {name} with host {host}")

    def add_message(self, message_data):
        message = Message(
            message_data["sender"], 
            message_data["content"], 
            message_data["channel"],
            message_data.get("timestamp")
        )
        self.messages.append(message)
        print(f"[Tracker] Added message from {message.sender} to channel {self.name}")
        self.save_to_disk()
        return message

    def add_member(self, username):
        if username and username != "visitor":
            self.members.add(username)
            print(f"[Tracker] Added member {username} to channel {self.name}")
            self.save_to_disk()

    def remove_member(self, username):
        if username in self.members:
            self.members.discard(username)
            print(f"[Tracker] Removed member {username} from channel {self.name}")
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

    @classmethod
    def load_from_disk(cls, channel_name):
        try:
            with open(f"data/{channel_name}.json", "r") as f:
                data = json.load(f)
                channel = cls(data["name"], data["host"])
                channel.members = set(data["members"])
                channel.messages = [Message.from_dict(m) for m in data["messages"]]
                return channel
        except (FileNotFoundError, json.JSONDecodeError):
            return None

peer_list = []
channels = {}  # Store channels on the tracker

# Load saved channels from disk on startup
def load_channels():
    global channels
    if not os.path.exists("data"):
        os.makedirs("data", exist_ok=True)
        print("[Tracker] Created data directory")
        return
        
    try:
        # Load all channel files
        for filename in os.listdir("data"):
            if filename.endswith(".json") and not filename.startswith("user_"):
                channel_name = filename[:-5]  # Remove .json extension
                channel = Channel.load_from_disk(channel_name)
                if channel:
                    channels[channel_name] = channel
                    print(f"[Tracker] Loaded channel {channel_name} with {len(channel.messages)} messages")
    except Exception as e:
        print(f"[Tracker] Error loading channels: {e}")

def handle_client(conn):
    global peer_list, channels
    try:
        while True:
            data = conn.recv(1024).decode()
            if not data:
                break
            parts = data.strip().split()
            if not parts:
                continue
            cmd = parts[0]
            
            if cmd == "send_info":
                ip, port, username, status = parts[1], parts[2], parts[3], parts[4]
                peer = Peer(ip, port, username, status)
                # Update or add peer
                for i, p in enumerate(peer_list):
                    if p.ip == ip and p.port == port:
                        peer_list[i] = peer
                        break
                else:
                    peer_list.append(peer)
                conn.send(b"OK\n")
                print(f"[Tracker] Registered peer {username} at {ip}:{port} with status {status}")
                
            elif cmd == "get_list":
                # Send peer list as JSON
                peer_data = [p.to_dict() for p in peer_list]
                conn.send(json.dumps(peer_data).encode() + b'\n')
                
            elif cmd == "sync_channel":
                # Receive channel data from a host for backup
                try:
                    channel_json = " ".join(parts[1:])
                    channel_data = json.loads(channel_json)
                    channel_name = channel_data["name"]
                    
                    print(f"[Tracker] Received sync request for channel {channel_name}")
                    
                    if channel_name not in channels:
                        print(f"[Tracker] Creating new channel {channel_name} from sync")
                        channels[channel_name] = Channel(channel_name, channel_data["host"])
                    
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
                        for msg in channel_data["messages"]:
                            # Check if message is already in channel by timestamp
                            timestamps = [m.timestamp for m in channels[channel_name].messages]
                            if msg.get("timestamp") not in timestamps:
                                channels[channel_name].add_message(msg)
                                print(f"[Tracker] Added message from {msg['sender']} to channel {channel_name}")
                    
                    # Save to disk
                    channels[channel_name].save_to_disk()
                    conn.send(b"OK\n")
                    print(f"[Tracker] Synced channel {channel_name} with {len(channels[channel_name].messages)} messages")
                except json.JSONDecodeError as e:
                    print(f"[Tracker] JSON decode error: {e}")
                    print(f"[Tracker] Received data: {' '.join(parts[1:])}")
                    conn.send(b"ERROR: Invalid JSON\n")
                except Exception as e:
                    print(f"[Tracker] Error during sync: {str(e)}")
                    conn.send(f"ERROR: {str(e)}\n".encode())
                    
            elif cmd == "get_channel":
                # Send channel data to a peer
                try:
                    channel_name = parts[1]
                    if channel_name in channels:
                        channel_data = channels[channel_name].to_dict()
                        conn.send(json.dumps(channel_data).encode() + b'\n')
                        print(f"[Tracker] Sent channel {channel_name} data with {len(channel_data['messages'])} messages")
                    else:
                        conn.send(b"ERROR: Channel not found\n")
                        print(f"[Tracker] Channel {channel_name} not found on request")
                except Exception as e:
                    print(f"[Tracker] Error sending channel data: {str(e)}")
                    conn.send(f"ERROR: {str(e)}\n".encode())
                    
            elif cmd == "list_channels":
                # Send list of available channels
                channel_list = []
                for name, channel in channels.items():
                    channel_list.append({
                        "name": name,
                        "host": channel.host,
                        "members": len(channel.members),
                        "messages": len(channel.messages)
                    })
                conn.send(json.dumps(channel_list).encode() + b'\n')
                print(f"[Tracker] Sent list of {len(channel_list)} channels")
                
            elif cmd == "debug":
                # Debug command to list all channels
                debug_info = []
                for name, channel in channels.items():
                    debug_info.append({
                        "name": name,
                        "host": channel.host,
                        "members": list(channel.members),
                        "message_count": len(channel.messages)
                    })
                conn.send(json.dumps(debug_info).encode() + b'\n')
                print(f"[Tracker] Sent debug info for {len(debug_info)} channels")
                
    except Exception as e:
        print(f"[Tracker] Client handling error: {e}")
    finally:
        conn.close()

def main():
    # Load channels from disk
    load_channels()
    
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # Avoid bind errors
    server.bind(("0.0.0.0", 12345))
    server.listen()
    print("Tracker is running on port 12345...")

    try:
        while True:
            conn, addr = server.accept()
            threading.Thread(target=handle_client, args=(conn,), daemon=True).start()
    except KeyboardInterrupt:
        print("\n[Tracker] Shutting down gracefully...")
        server.close()

if __name__ == "__main__":
    main()
