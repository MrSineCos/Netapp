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
import logging

# Thiết lập logging để ghi ra file app.log dùng chung
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

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
        
        self._auto_sync = True  # Mặc định bật tự động đồng bộ
        
        self.data_manager = DataManager()
        
        self.is_authenticated = False
        
        self.auth_key = ""
        
        if self.is_authenticated:
            self.data_manager.load_user_data(username)
            
            self.data_manager.sort_all_channels_messages()
            logging.info("[Agent] Sorted messages in all channels on startup")

    def register_to_tracker(self, get_peers=False):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if get_peers:
                s.connect((TRACKER_IP, TRACKER_PORT))
                s.send(f"send_info {MY_IP} {self.port} {self.username or 'visitor'} {self.status} get_peers\n".encode())
                data = s.recv(4096).decode()
                s.close()
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
                
                if self.is_authenticated:
                    logging.info("[Agent] First successful connection to tracker, performing full sync")
                    self.sync_all(sync_type="reconnect")
                
                return data
        except Exception as e:
            logging.error(f"[Agent] Error connecting to tracker: {e}")
            return []

    def sync_all(self, channel_name=None, sync_type="normal"):
        logging.info(f"[Agent] Current status before sync: {self.status}")
        
        check_online = sync_type not in ["offline", "reconnect"]
        if check_online:
            is_online = self.check_online_status()
            logging.info(f"[Agent] Online check result: {is_online}")
            
            if not is_online:
                logging.warning("[Agent] Không thể đồng bộ khi offline") 
                return False
        
        try:
            logging.info("[Agent] Requesting channel list from tracker")
            available_channels = self.list_available_channels()
            if available_channels:
                logging.info(f"[Agent] Found {len(available_channels)} channels on tracker")
                local_channels = self.data_manager.get_all_channels()
                
                new_channels_count = 0
                new_hosted_channels = []
                
                for channel_info in available_channels:
                    channel_name_on_tracker = channel_info.get("name")
                    if not channel_name_on_tracker:
                        continue
                        
                    if channel_name_on_tracker not in local_channels:
                        logging.info(f"[Agent] Discovered new channel: {channel_name_on_tracker}")
                        
                        host = channel_info.get("host", "unknown")
                        
                        self.data_manager.add_channel(channel_name_on_tracker, host)
                        logging.info(f"[Agent] Created local record for channel {channel_name_on_tracker} (host: {host})")
                        new_channels_count += 1
                        
                        if self.username == host or self.username in channel_info.get("members", []):
                            new_hosted_channels.append(channel_name_on_tracker)
                
                if new_channels_count > 0:
                    logging.info(f"[Agent] Added {new_channels_count} new channels to local database")
        except Exception as e:
            logging.error(f"[Agent] Error while checking for new channels: {e}")
        
        sync_success = True
        channels_synced = 0
        messages_synced = 0
        
        channels_to_sync = []
        if channel_name:
            channel = self.data_manager.get_channel(channel_name)
            if channel:
                channels_to_sync.append(channel)
            else:
                logging.warning(f"[Agent] Kênh {channel_name} không tồn tại")
                return False
        else:
            user_channels = self.data_manager.get_user_channels(self.username)
            if not user_channels:
                logging.info(f"[Agent] User {self.username} hasn't joined any channels")
                if new_hosted_channels:
                    logging.info(f"[Agent] User {self.username} is host of new channels, syncing them")
                    for ch_name in new_hosted_channels:
                        channel = self.data_manager.get_channel(ch_name)
                        if channel:
                            channels_to_sync.append(channel) 
                
            logging.info(f"[Agent] Found {len(user_channels)} channels joined by user {self.username}")
            for ch_name in user_channels:
                channel = self.data_manager.get_channel(ch_name)
                if channel:
                    channels_to_sync.append(channel)
            
            for ch_name in getattr(locals(), "new_hosted_channels", []):
                if ch_name not in user_channels:
                    channel = self.data_manager.get_channel(ch_name)
                    if channel and channel not in channels_to_sync:
                        logging.info(f"[Agent] Adding newly hosted channel {ch_name} to sync list")
                        channels_to_sync.append(channel)
        
        logging.info(f"[Agent] Preparing to sync {len(channels_to_sync)} channels")
        
        for channel in channels_to_sync:
            channel_name = channel.name
            is_host = channel.host == self.username
            
            logging.info(f"[Agent] Processing channel {channel_name} (host: {is_host})")
            
            pending_messages = []
            for msg in channel.messages:
                if hasattr(msg, "status") and msg.status == "pending":
                    pending_messages.append(msg)
            
            logging.info(f"[Agent] Found {len(pending_messages)} pending messages in channel {channel_name}")
            
            if pending_messages:
                tracker_sync_success = False
                try:
                    logging.info(f"[Agent] Sending pending messages to tracker for channel {channel_name}")
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
                    
                    try:
                        sync_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sync_socket.settimeout(10)
                        sync_socket.connect((TRACKER_IP, TRACKER_PORT))
                        
                        json_data = json.dumps(channel_data)
                        sync_socket.send(f"sync_channel {json_data}\n".encode())
                        
                        response = sync_socket.recv(1024).decode().strip()
                        sync_socket.close()
                        
                        if response.startswith("OK"):
                            logging.info(f"[Agent] Successfully synced channel {channel_name} with tracker")
                            tracker_sync_success = True
                        else:
                            logging.warning(f"[Agent] Failed to sync channel {channel_name} with tracker: {response}")
                    except Exception as e:
                        logging.error(f"[Agent] Error syncing channel {channel_name} with tracker: {e}")
                except Exception as e:
                    logging.error(f"[Agent] Error preparing data for channel {channel_name}: {e}")
                
                peers_sync_success = False
                try:
                    peers = self.register_to_tracker(get_peers=True)
                    if peers:
                        channel_members = channel.get_all_users()
                        channel_peers = [p for p in peers if p["username"] in channel_members and p["username"] != self.username]
                        
                        logging.info(f"[Agent] Found {len(channel_peers)} online members in channel {channel_name}")
                        
                        for pending_msg in pending_messages:
                            message_data = {
                                "type": "message",
                                "channel": channel_name,
                                "content": pending_msg.content,
                                "sender": pending_msg.sender,
                                "timestamp": pending_msg.timestamp
                            }
                            
                            success_count = 0
                            for peer in channel_peers:
                                try:
                                    send_to_peer(peer["ip"], int(peer["port"]), json.dumps(message_data))
                                    success_count += 1
                                except Exception as e:
                                    logging.error(f"[Agent] Error sending message to peer {peer['username']}: {e}")
                            
                            if success_count > 0:
                                logging.info(f"[Agent] Message sent to {success_count} peers")
                                peers_sync_success = True
                except Exception as e:
                    logging.error(f"[Agent] Error synchronizing with peers: {e}")
                
                if tracker_sync_success or peers_sync_success:
                    for msg in pending_messages:
                        msg.status = "sent"
                        messages_synced += 1
                    
                    self.data_manager.save_channel(channel_name)
                    logging.info(f"[Agent] Updated status of {len(pending_messages)} messages to 'sent'")
            
            if is_host:
                try:
                    logging.info(f"[Agent] Fetching message history from tracker for channel {channel_name} (as host)")
                    updated_channel = self.fetch_channel_from_tracker(channel_name)
                    if updated_channel:
                        logging.info(f"[Agent] Successfully fetched latest data for channel {channel_name} from tracker")
                        channels_synced += 1
                except Exception as e:
                    logging.error(f"[Agent] Error fetching channel history from tracker: {e}")
                    sync_success = False
            else:
                host_username = channel.host
                
                if not host_username or host_username == "unknown" or host_username == "visitor":
                    logging.info(f"[Agent] Channel {channel_name} has no valid host, fetching from tracker")
                    try:
                        updated_channel = self.fetch_channel_from_tracker(channel_name)
                        if updated_channel:
                            logging.info(f"[Agent] Successfully fetched channel {channel_name} data from tracker")
                            channels_synced += 1
                    except Exception as e:
                        logging.error(f"[Agent] Error fetching channel from tracker: {e}")
                        sync_success = False
                else:
                    host_status = self.check_peer_status(host_username)
                    logging.info(f"[Agent] Host {host_username} status for channel {channel_name}: {host_status}")
                    
                    if host_status == "online":
                        logging.info(f"[Agent] Requesting message history from host {host_username}")
                        
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
                                
                                test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                test_socket.settimeout(2)
                                
                                try:
                                    test_socket.connect((host_peer["ip"], int(host_peer["port"])))
                                    test_socket.close()
                                    
                                    send_to_peer(host_peer["ip"], int(host_peer["port"]), json.dumps(request_data))
                                    logging.info(f"[Agent] History request sent to host {host_username}")
                                    channels_synced += 1
                                except Exception:
                                    logging.warning(f"[Agent] Host {host_username} is not responsive, falling back to tracker")
                                    updated_channel = self.fetch_channel_from_tracker(channel_name)
                                    if updated_channel:
                                        logging.info(f"[Agent] Successfully fetched channel {channel_name} data from tracker")
                                        channels_synced += 1
                            except Exception as e:
                                logging.error(f"[Agent] Error requesting history from host: {e}")
                                sync_success = False
                        else:
                            logging.warning(f"[Agent] Could not find host {host_username} in peer list, falling back to tracker")
                            updated_channel = self.fetch_channel_from_tracker(channel_name)
                            if updated_channel:
                                logging.info(f"[Agent] Successfully fetched channel {channel_name} data from tracker")
                                channels_synced += 1
                    else:
                        logging.info(f"[Agent] Host {host_username} is offline, fetching from tracker")
                        try:
                            updated_channel = self.fetch_channel_from_tracker(channel_name)
                            if updated_channel:
                                logging.info(f"[Agent] Successfully fetched channel {channel_name} data from tracker")
                                channels_synced += 1
                        except Exception as e:
                            logging.error(f"[Agent] Error fetching channel from tracker: {e}")
                            sync_success = False
        
        self.last_sync = datetime.now()
        
        logging.info(f"[Agent] Sync complete: {channels_synced} channels synchronized, {messages_synced} messages updated")
        return sync_success
        
    def fetch_channel_from_tracker(self, channel_name):
        try:
            logging.info(f"[Agent] Fetching channel {channel_name} data from tracker")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10)
            s.connect((TRACKER_IP, TRACKER_PORT))
            s.send(f"get_channel {channel_name}\n".encode())
            logging.info(f"[Agent] Sent get_channel request to tracker for {channel_name}")
            
            buffer = ""
            while True:
                try:
                    chunk = s.recv(4096).decode()
                    if not chunk:
                        logging.warning(f"[Agent] Connection closed by tracker while fetching {channel_name}")
                        break
                    
                    buffer += chunk
                    logging.info(f"[Agent] Received {len(chunk)} bytes chunk for channel {channel_name}")
                    
                    if buffer.count('{') == buffer.count('}') and '{' in buffer:
                        logging.info(f"[Agent] JSON data for {channel_name} appears complete")
                        break
                except socket.timeout:
                    logging.warning(f"[Agent] Timeout receiving data for channel {channel_name}")
                    break
            
            s.close()
            
            if not buffer:
                logging.warning(f"[Agent] No data received for channel {channel_name}")
                return None
                
            if buffer.startswith("ERROR"):
                logging.error(f"[Agent] Error fetching channel {channel_name}: {buffer}")
                return None
            
            try:
                logging.info(f"[Agent] Parsing JSON data for channel {channel_name}")
                channel_data = json.loads(buffer)
                
                channel = self.data_manager.get_channel(channel_name)
                if not channel:
                    logging.info(f"[Agent] Creating new local channel {channel_name}")
                    self.data_manager.create_channel(channel_name, channel_data["host"])
                    channel = self.data_manager.get_channel(channel_name)
                else:
                    logging.info(f"[Agent] Updating existing local channel {channel_name}")
                
                if channel:
                    if not channel.host and "host" in channel_data and channel_data["host"]:
                        logging.info(f"[Agent] Updating channel {channel_name} host to {channel_data['host']}")
                        channel.host = channel_data["host"]
                        
                    members_added = 0
                    for member in channel_data.get("members", []):
                        if member not in channel.members:
                            members_added += 1
                            self.data_manager.join_channel(channel_name, member)
                    
                    if members_added > 0:
                        logging.info(f"[Agent] Added {members_added} new members to channel {channel_name}")
                    
                    existing_timestamps = set()
                    for msg in channel.messages:
                        existing_timestamps.add(msg.timestamp)
                    
                    msg_count = 0
                    message_list = channel_data.get("messages", [])
                    logging.info(f"[Agent] Processing {len(message_list)} messages from tracker for channel {channel_name}")
                    
                    for msg_data in message_list:
                        if isinstance(msg_data, dict):
                            timestamp = msg_data.get("timestamp")
                            if timestamp is None:
                                logging.warning(f"[Agent] Warning: Message without timestamp found, skipping")
                                continue
                                
                            if timestamp not in existing_timestamps:
                                try:
                                    self.add_message_direct(
                                        channel_name, 
                                        msg_data["sender"], 
                                        msg_data["content"], 
                                        timestamp,
                                        status="received"
                                    )
                                    msg_count += 1
                                except KeyError as e:
                                    logging.error(f"[Agent] Error adding message: Missing field {e}")
                            else:
                                logging.info(f"[Agent] Skipping message with timestamp {timestamp} (already exists locally)")
                    
                    if msg_count > 0:
                        logging.info(f"[Agent] Saving channel {channel_name} after adding {msg_count} messages")
                        self.data_manager.save_channel(channel_name)
                        
                        self.data_manager.sort_channel_messages(channel)
                        logging.info(f"[Agent] Sorted messages in channel {channel_name} after adding new messages from tracker")
                    
                    logging.info(f"[Agent] Fetched channel {channel_name} from tracker: {msg_count} new messages added")
                    if msg_count == 0:
                        logging.info(f"[Agent] No new messages for channel {channel_name}")
                    
                    return channel
                else:
                    logging.error(f"[Agent] Failed to create/update channel {channel_name}")
                    return None
                    
            except json.JSONDecodeError as e:
                logging.error(f"[Agent] JSON decode error: {e}")
                logging.error(f"[Agent] Received data starts with: {buffer[:100]}...")
                return None
                
        except ConnectionRefusedError:
            logging.error(f"[Agent] Connection refused by tracker. Make sure tracker is running.")
            return None
        except socket.timeout:
            logging.error(f"[Agent] Connection to tracker timed out")
            return None
        except Exception as e:
            logging.error(f"[Agent] Error fetching channel from tracker: {e}")
            return None

    def add_message_direct(self, channel_name, sender, content, timestamp=None, status="pending"):
        try:
            channel = self.data_manager.get_channel(channel_name)
            if not channel:
                logging.warning(f"[Agent] Channel {channel_name} not found")
                return None
            
            for existing_msg in channel.messages:
                if existing_msg.timestamp == timestamp and existing_msg.sender == sender and existing_msg.content == content:
                    logging.info(f"[Agent] Duplicate message detected, not adding: {sender}/{timestamp}")
                    return existing_msg
            
            message = Message(sender, content, channel_name, timestamp, status)
            channel.add_message(message)
            
            self.data_manager.sort_channel_messages(channel)
            
            logging.info(f"[Agent] Directly added message from {sender} to channel {channel_name} and sorted by timestamp")
            return message
        except Exception as e:
            logging.error(f"[Agent] Error in add_message_direct: {e}")
            return None

    def add_message_to_channel(self, sender, content, channel_name):
        logging.info(f"[Agent] Adding message to channel {channel_name}")
        
        channel = self.data_manager.get_channel(channel_name)
        if not channel:
            logging.warning(f"[Agent] Channel {channel_name} does not exist")
            return False
        
        message = Message(sender, content, channel_name)
        message.update_status("pending")
        
        channel.add_message(message)
        
        self.data_manager.save_channel(channel_name)
        logging.info(f"[Agent] Message saved locally with status 'pending'")
        
        return message

    def list_available_channels(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((TRACKER_IP, TRACKER_PORT))
            s.send(b"list_channels\n")
            data = s.recv(4096).decode()
            s.close()
            
            channels = json.loads(data)
            return channels
        except Exception as e:
            logging.error(f"[Agent] Error listing channels: {e}")
            return []

    def check_peer_status(self, username):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((TRACKER_IP, TRACKER_PORT))
            s.send(f"check_status {username}\n".encode())
            response = s.recv(1024).decode()
            s.close()
            
            logging.info(f"[Agent] Tracker response for {username} status: {response}")
            
            if response.startswith("STATUS:"):
                status = response.split(":", 1)[1].strip()
                return status
            else:
                return f"Error: {response}"
        except Exception as e:
            logging.error(f"[Agent] Error checking peer status: {e}")
            return f"Error: {str(e)}"

    def handle_command(self, cmd):
        response = {"status": "error", "message": "Unknown command", "username": self.username, "status_value": self.status}
        
        try:
            if not cmd:
                return response

            if cmd.startswith("set_port "):
                try:
                    new_port = int(cmd.split(" ", 1)[1])
                    self.port = new_port
                    logging.info(f"[Agent] Port updated to {new_port} from UI")
                    response = {
                        "status": "ok",
                        "message": f"Port updated to {new_port}",
                        "username": self.username,
                        "status_value": self.status
                    }
                except Exception as e:
                    response = {
                        "status": "error",
                        "message": f"Invalid port: {e}",
                        "username": self.username,
                        "status_value": self.status
                    }
                return response

            cmd_parts = cmd.split(' ', 1)
            action = cmd_parts[0].lower()
            params = cmd_parts[1] if len(cmd_parts) > 1 else ""
            
            if action == "exit" or action == "quit":
                logging.info("[Agent] Shutting down...")

                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(3)
                    s.connect((TRACKER_IP, TRACKER_PORT))
                    offline_username = self.username or "visitor"
                    s.send(f"send_info {MY_IP} {self.port} {offline_username} offline\n".encode())
                    s.close()
                    logging.info(f"[Agent] Notified tracker: {offline_username} is offline")
                except Exception as e:
                    logging.error(f"[Agent] Error notifying tracker about offline status: {e}")

                response = {"status": "exit", "message": "Agent shutting down", "username": self.username, "status_value": self.status}
            
            elif action.startswith("login:"):
                username = action.split(":", 1)[1]
                try:
                    self.data_manager.load_user_data(username)
                    self.username = username
                    self.is_authenticated = True
                    logging.info(f"[Agent] Logged in as {username}")
                    
                    self.register_to_tracker()
                    
                    if self.status != "offline":
                        logging.info(f"[Agent] Performing initial data synchronization for user {username}")
                        sync_result = self.sync_all(sync_type="offline")
                        if sync_result:
                            logging.info("[Agent] Initial synchronization successful")
                        else:
                            logging.warning("[Agent] Warning: Issues occurred during initial synchronization")
                    
                    response = {
                        "status": "ok",
                        "message": f"Logged in as {self.username}",
                        "username": self.username,
                        "status_value": self.status
                    }
                except Exception as e:
                    logging.error(f"[Agent] Login failed for {username}: {e}")
                    self.is_authenticated = False
                    self.username = ""
                    response = {
                        "status": "error",
                        "message": f"Login failed: {e}",
                        "username": "",
                        "status_value": self.status
                    }
                return response
            
            elif action == "list":
                peers = self.register_to_tracker(get_peers=True)
                if peers:
                    logging.info(f"[Agent] Listing peers: {peers}")
                    for peer in peers:
                        status_icon = "🟢" if peer.get("status") == "online" else "🔴" if peer.get("status") == "offline" else "⚪"
                        logging.info(f"[Agent] {peer.get('username')} ({peer.get('ip')}:{peer.get('port')})")
                    response = {
                        "status": "ok",
                        "message": "Listed peers",
                        "peers": peers,
                        "username": self.username,
                        "status_value": self.status
                    }
                else:
                    logging.info("[Agent] No peers available")
                    response = {
                        "status": "ok",
                        "message": "No peers available",
                        "peers": [],
                        "username": self.username,
                        "status_value": self.status
                    }
            
            elif action == "list_all":
                channels = self.list_available_channels()
                if channels:
                    logging.info(f"[Agent] Listing available channels: {channels}")
                    for channel in channels:
                        members = channel.get('members', [])
                        if not isinstance(members, list):
                            members = []
                        logging.info(f"📢 {channel['name']} (Host: {channel['host']}, Members: {len(members)})")
                else:
                    logging.info("[Agent] No channels available")
                response = {
                    "status": "ok",
                    "message": "Listed all channels",
                    "username": self.username,
                    "status_value": self.status
                }
            
            elif action == "join" and params:
                if not self.check_online_status():
                    logging.warning("[Agent] Cannot join channel while offline")
                    return {"status": "error", "message": "Cannot join channel while offline", "username": self.username, "status_value": self.status}
                channel_name = params.strip()
                
                as_visitor = not self.is_authenticated
                
                logging.info(f"[Agent] Attempting to join channel: {channel_name} as {'visitor' if as_visitor else 'member'}")
                
                if self.data_manager.join_channel(channel_name, self.username or "visitor", as_visitor):
                    logging.info(f"[Agent] Successfully joined channel {channel_name} as {'visitor (read-only)' if as_visitor else 'member'}")
                    
                    peers = self.register_to_tracker(get_peers=True)
                    logging.info(f"[Agent] Notifying {len(peers)} peers about joining channel")
                    
                    join_data = {
                        "type": "join_channel",
                        "channel": channel_name,
                        "username": self.username or "visitor"
                    }
                    
                    notify_count = 0
                    for peer in peers:
                        if peer["username"] != self.username:
                            try:
                                send_to_peer(peer["ip"], int(peer["port"]), json.dumps(join_data))
                                notify_count += 1
                            except Exception as e:
                                logging.error(f"[Agent] Error notifying peer {peer['username']} about join: {e}")
                    
                    logging.info(f"[Agent] Notified {notify_count} peers about join")
                    
                    try:
                        tracker_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        tracker_sock.connect((TRACKER_IP, TRACKER_PORT))
                        tracker_sock.send((json.dumps(join_data) + "\n").encode())
                        tracker_sock.close()
                        logging.info("[Agent] Notified tracker about joining channel")
                    except Exception as e:
                        logging.error(f"[Agent] Error notifying tracker about join: {e}")
                                
                    if self.is_authenticated:
                        logging.info(f"[Agent] Requesting channel history for {channel_name}")
                        self.request_channel_history(channel_name)
                    
                    response = {
                        "status": "ok",
                        "message": f"Joined channel {channel_name}",
                        "username": self.username,
                        "status_value": self.status
                    }
                else:
                    logging.warning(f"[Agent] Failed to join channel {channel_name}")
                    response = {
                        "status": "error",
                        "message": f"Failed to join channel {channel_name}",
                        "username": self.username,
                        "status_value": self.status
                    }
            
            elif action == "leave" and params:
                channel_name = params.strip()
                
                if self.data_manager.leave_channel(channel_name, self.username):
                    logging.info(f"[Agent] Left channel {channel_name}")
                    
                    peers = self.register_to_tracker(get_peers=True)
                    
                    leave_data = {
                        "type": "leave_channel",
                        "channel": channel_name,
                        "username": self.username
                    }
                    
                    for peer in peers:
                        if peer["username"] != self.username:
                            try:
                                send_to_peer(peer["ip"], int(peer["port"]), json.dumps(leave_data))
                            except Exception as e:
                                logging.error(f"[Agent] Error notifying peer {peer['username']} about leaving: {e}")
                    
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

            elif action == "status" and params:
                print(f"[Agent] [handle_command] Handling status: {params}")
                if params.startswith("check "):
                    target_username = params.split(' ', 1)[1].strip()
                    status = self.check_peer_status(target_username)
                    logging.info(f"Status of {target_username}: {status}")
                    response = {
                        "status": "ok",
                        "message": f"Checked status of {target_username}",
                        "username": self.username,
                        "status_value": self.status
                    }
                    return response
                
                status = params.strip().lower()
                old_status = self.status
                if status in ["online", "offline", "invisible"]:
                    self.status = status
                    print(f"[Agent] [handle_command] Status: {status}")
                    # if status != "invisible":
                    self.register_to_tracker()
                        
                    if old_status == "offline" and status == "online":
                        logging.info("[Agent] Status changed from offline to online, syncing messages...")
                        sync_result = self.sync_all(sync_type="offline")
                        if sync_result:
                            logging.info("[Agent] Successfully synchronized offline messages with peers and tracker")
                        else:
                            logging.warning("[Agent] Some issues occurred during offline message synchronization")
                    elif old_status == "online" and status == "invisible":
                        logging.info("[Agent] Status changed from online to invisible, syncing messages...")
                        sync_result = self.sync_all(sync_type="offline")
                        if sync_result:
                            logging.info("[Agent] Successfully synchronized offline messages with peers and tracker")
                        else:
                            logging.warning("[Agent] Some issues occurred during offline message synchronization")
                    logging.info(f"[Agent] Status changed to {status}")
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

            #---------------------------------------------------------------
            # Handle command actions only if authenticated
            #---------------------------------------------------------------
            elif not self.is_authenticated:
                response = {
                    "status": "error",
                    "message": "This command requires authentication. Please login first.",
                    "username": self.username,
                    "status_value": self.status
                }
            
            elif action == "logout":
                logging.info(f"[Agent] Logging out user {self.username}")
                
                self.data_manager.clear_user_data(self.username)
                
                old_username = self.username
                self.username = ""
                self.is_authenticated = False
                
                self.register_to_tracker()
                
                response = {
                    "status": "ok",
                    "message": f"Logged out user {old_username}",
                    "username": self.username,
                    "status_value": self.status
                }
            
            
            elif action == "channels":
                user_channels = self.data_manager.get_user_channels(self.username)
                hosted_channels = self.data_manager.get_hosted_channels(self.username)
                
                if user_channels:
                    logging.info(f"[Agent] Listing your channels: {user_channels}")
                    for channel_name in user_channels:
                        channel = self.data_manager.get_channel(channel_name)
                        if channel:
                            if channel_name in hosted_channels:
                                logging.info(f"🔑 {channel_name} (Host: You, Members: {len(channel.members)})")
                            else:
                                logging.info(f"📢 {channel_name} (Host: {channel.host}, Members: {len(channel.members)})")
                else:
                    logging.info("[Agent] You haven't joined any channels")
                
                response = {
                    "status": "ok",
                    "message": "Listed channels",
                    "username": self.username,
                    "status_value": self.status
                }
            
            elif action == "create" and params:
                if self.check_online_status():
                    channel_name = params.strip()
                    
                    channel = self.data_manager.create_channel(channel_name, self.username)
                    if channel:
                        logging.info(f"[Agent] Created channel {channel_name} (you are the host)")
                        
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
                                logging.info(f"[Agent] Tracker created channel {channel_name} successfully")
                            else:
                                logging.warning(f"[Agent] Tracker failed to create channel {channel_name}: {response}")
                        except Exception as e:
                            logging.error(f"[Agent] Error notifying tracker to create channel: {e}")
                        
                        self.sync_all(channel_name=channel_name, sync_type="normal")
                        
                        peers = self.register_to_tracker(get_peers=True)
                        
                        create_data = {
                            "type": "join_channel",
                            "channel": channel_name,
                            "username": self.username
                        }
                        
                        for peer in peers:
                            if peer["username"] != self.username:
                                try:
                                    send_to_peer(peer["ip"], int(peer["port"]), json.dumps(create_data))
                                except Exception as e:
                                    logging.error(f"[Agent] Error notifying peer {peer['username']} about channel creation: {e}")
                        
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
                    logging.warning("[Agent] Cannot create channel while offline")
                    response = {
                        "status": "error",
                        "message": "Cannot create channel while offline",
                        "username": self.username,
                        "status_value": self.status
                    }
            
            elif action == "send" and params:
                try:
                    if self.is_authenticated:
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
                            
                            logging.info(f"[Agent] Trying to send message '{message_content}' to channel '{channel_name}'")
                            
                            user_channels = self.data_manager.get_user_channels(self.username)
                            if channel_name not in user_channels:
                                logging.warning(f"[Agent] Cannot send message: not in channel {channel_name}")
                                response = {
                                    "status": "error",
                                    "message": f"You are not a member of channel {channel_name}",
                                    "username": self.username,
                                    "status_value": self.status
                                }
                            else:
                                message = self.add_message_to_channel(self.username, message_content, channel_name)
                                if not message:
                                    logging.warning(f"[Agent] Failed to add message to local channel {channel_name}")
                                    response = {
                                        "status": "error",
                                        "message": f"Failed to add message to channel {channel_name}",
                                        "username": self.username,
                                        "status_value": self.status
                                    }
                                    return response
                                
                                sent_successfully = False
                                
                                try:
                                    channel = self.data_manager.get_channel(channel_name)
                                    if not channel:
                                        logging.warning(f"[Agent] Could not retrieve channel {channel_name} for message sending")
                                        response = {
                                            "status": "error",
                                            "message": f"Failed to retrieve channel for message sending",
                                            "username": self.username,
                                            "status_value": self.status
                                        }
                                        return response
                                    
                                    message_data = {
                                        "type": "message",
                                        "channel": channel_name,
                                        "content": message_content,
                                        "sender": self.username,
                                        "timestamp": message.timestamp
                                    }
                                    
                                    logging.info(f"[Agent] Checking online status before sending message...")
                                    logging.info(f"[Agent] Current status: {self.status}")
                                    is_online = self.check_online_status()
                                    logging.info(f"[Agent] Online check result: {is_online}")
                                    
                                    if is_online:
                                        peers = self.register_to_tracker(get_peers=True)
                                        
                                        sent_count = 0
                                        for peer in peers:
                                            if peer["username"] != self.username and peer["username"] in channel.get_all_users():
                                                try:
                                                    send_to_peer(peer["ip"], int(peer["port"]), json.dumps(message_data))
                                                    sent_count += 1
                                                    sent_successfully = True
                                                except Exception as e:
                                                    logging.error(f"[Agent] Error sending message to peer {peer['username']}: {e}")
                                        
                                        logging.info(f"[Agent] Message sent to {sent_count} peers in channel {channel_name}")
                                        
                                        if channel.host == self.username or sent_count == 0:
                                            try:
                                                logging.info(f"[Agent] Attempting to sync channel {channel_name} with tracker")
                                                if channel.host == self.username:
                                                    tracker_sync = self.sync_all(channel_name=channel_name, sync_type="normal")
                                                else:
                                                    tracker_sync = self.sync_all(channel_name=channel_name, sync_type="non_hosted")
                                                    
                                                if tracker_sync:
                                                    sent_successfully = True
                                                    logging.info(f"[Agent] Successfully synced message with tracker")
                                                else:
                                                    logging.warning(f"[Agent] Failed to sync message with tracker")
                                            except Exception as e:
                                                logging.error(f"[Agent] Error syncing with tracker: {e}")
                                    else:
                                        logging.info(f"[Agent] Agent is offline. Message will remain in 'pending' status.")
                                    
                                    if sent_successfully:
                                        message.update_status("sent")
                                        self.data_manager.save_channel(channel_name)
                                        logging.info(f"[Agent] Message status updated to 'sent'")
                                    
                                except Exception as e:
                                    logging.error(f"[Agent] Error sending message to peers: {e}")
                                
                                response = {
                                    "status": "ok",
                                    "message": f"Message {'sent' if sent_successfully else 'queued'} to channel {channel_name}",
                                    "username": self.username,
                                    "status_value": self.status
                                }
                    else:
                        logging.warning("Cannot send message in visitor mode")
                except Exception as e:
                    logging.error(f"[Agent] Error processing send command: {e}")
                    response = {
                        "status": "error",
                        "message": f"Error processing send command: {e}",
                        "username": self.username,
                        "status_value": self.status
                    }
            
            elif action == "history" and params:
                channel_name = params.strip()
                
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
                if params.strip() == "auto":
                    logging.info("[Agent] Enabling automatic sync with tracker")
                    response = {
                        "status": "ok",
                        "message": "Automatic sync enabled",
                        "username": self.username,
                        "status_value": self.status
                    }
                    self._auto_sync = True
                elif params.strip() == "manual":
                    logging.info("[Agent] Disabling automatic sync with tracker")
                    response = {
                        "status": "ok",
                        "message": "Automatic sync disabled",
                        "username": self.username,
                        "status_value": self.status
                    }
                    self._auto_sync = False
                else:
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
                response = {
                    "status": "ok",
                    "message": "Help displayed",
                    "username": self.username,
                    "status_value": self.status
                }
                
            else:
                logging.warning(f"[Agent] Unknown command: {action}")
                response = {
                    "status": "error",
                    "message": f"Unknown command: {action}",
                    "username": self.username,
                    "status_value": self.status
                }
                
        except Exception as e:
            logging.error(f"[Agent] Error handling command: {e}")
            response = {
                "status": "error",
                "message": f"Error: {e}",
                "username": self.username,
                "status_value": self.status
            }
            
        return response

    def request_channel_history(self, channel_name):
        try:
            channel = self.data_manager.get_channel(channel_name)
            if not channel:
                channel = self.fetch_channel_from_tracker(channel_name)
                if not channel:
                    logging.warning(f"[Agent] Channel {channel_name} not found")
                    return False
            
            if channel.is_host(self.username):
                logging.info(f"[Agent] You are the host of channel {channel_name}, displaying message history:")
                if not channel.messages:
                    logging.info("[No messages in this channel yet]")
                else:
                    displayed_messages = set()
                    
                    logging.info(f"[Message History for {channel_name}]")
                    
                    sorted_messages = sorted(channel.messages, key=lambda x: x.timestamp)
                    
                    for msg in sorted_messages:
                        msg_id = f"{msg.timestamp}:{msg.sender}:{msg.content}"
                        
                        if msg_id not in displayed_messages:
                            try:
                                timestamp = datetime.fromisoformat(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
                            except:
                                timestamp = msg.timestamp
                            status_icon = ""
                            if hasattr(msg, 'status'):
                                if msg.status == "pending":
                                    status_icon = "⌛"
                                elif msg.status == "sent":
                                    status_icon = "✅"
                                elif msg.status == "received":
                                    status_icon = "📥"
                                else:
                                    status_icon = "❓"
                            
                            logging.info(f"[{timestamp}] {msg.sender}: {msg.content} {status_icon}")
                            displayed_messages.add(msg_id)
                return True
            
            host = channel.host
            if not host or host == "unknown" or host == "visitor":
                logging.warning(f"[Agent] Channel {channel_name} has no valid host")
                return self.get_history_from_tracker(channel_name)
                
            peers = self.register_to_tracker(get_peers=True)
            host_peer = None
            for peer in peers:
                if peer["username"] == host:
                    host_peer = peer
                    break
                    
            if not host_peer:
                logging.warning(f"[Agent] Host {host} for channel {channel_name} is not online")
                return self.get_history_from_tracker(channel_name)
                
            try:
                request_data = {
                    "type": "request_history",
                    "channel": channel_name,
                    "username": self.username or "visitor"
                }
                
                logging.info(f"[Agent] Requesting history for channel {channel_name} from host {host}")
                
                test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_socket.settimeout(2)
                
                try:
                    test_socket.connect((host_peer["ip"], int(host_peer["port"])))
                    test_socket.close()
                    
                    success = send_to_peer(host_peer["ip"], int(host_peer["port"]), json.dumps(request_data))
                    
                    if not success:
                        logging.warning(f"[Agent] Could not send request to host {host}. Trying tracker instead.")
                        return self.get_history_from_tracker(channel_name)
                        
                    logging.info(f"[Agent] History request sent to host {host}. Please wait for response...")
                    return True
                    
                except (socket.timeout, ConnectionRefusedError, ConnectionError):
                    test_socket.close()
                    logging.warning(f"[Agent] Host {host} is not responsive. Trying tracker instead.")
                    return self.get_history_from_tracker(channel_name)
                    
            except Exception as e:
                logging.error(f"[Agent] Error connecting to host {host}: {e}")
                return self.get_history_from_tracker(channel_name)
            
        except Exception as e:
            logging.error(f"[Agent] Error requesting channel history: {e}")
            return False
            
    def get_history_from_tracker(self, channel_name):
        logging.info(f"[Agent] Attempting to fetch history from tracker for channel {channel_name}")
        success = self.fetch_channel_from_tracker(channel_name) is not None
        if success:
            logging.info(f"[Agent] Successfully retrieved channel data from tracker")
            channel = self.data_manager.get_channel(channel_name)
            if channel and channel.messages:
                displayed_messages = set()
                
                logging.info(f"[Message History for {channel_name} (from tracker)]")
                
                sorted_messages = sorted(channel.messages, key=lambda x: x.timestamp)
                
                for msg in sorted_messages:
                    msg_id = f"{msg.timestamp}:{msg.sender}:{msg.content}"
                    
                    if msg_id not in displayed_messages:
                        try:
                            timestamp = datetime.fromisoformat(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
                        except:
                            timestamp = msg.timestamp
                            
                        status_icon = ""
                        if hasattr(msg, 'status'):
                            if msg.status == "pending":
                                status_icon = "⌛"
                            elif msg.status == "sent":
                                status_icon = "✅"
                            elif msg.status == "received":
                                status_icon = "📥"
                            else:
                                status_icon = "❓"
                                
                        logging.info(f"[{timestamp}] {msg.sender}: {msg.content} {status_icon}")
                        displayed_messages.add(msg_id)
            else:
                logging.info("[No messages in this channel yet]")
        return success

    def update_username(self, new_username):
        if not new_username:
            logging.warning("[Agent] Invalid username")
            return False
            
        old_username = self.username
        self.username = new_username
        self.is_authenticated = True
        
        self.register_to_tracker()
        
        logging.info(f"[Agent] Username changed from {old_username or 'visitor'} to {new_username}")
        return True

    def check_online_status(self):
        try:    
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
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
            logging.error(f"[Agent] Error checking online status: {e}")
            self.status = "offline"
            return False

def agent_main(command_queue: Queue, my_port: int, username: str = "", status: str = "online", response_queue: Queue = None):
    agent = Agent(my_port, username, status)
    
    server_username = [username]
    
    def get_current_username():
        return server_username[0]
    
    server_thread = threading.Thread(target=start_peer_server, args=(my_port, get_current_username))
    server_thread.daemon = True
    server_thread.start()
    
    logging.info(f"[Agent] Started on port {my_port}")
    
    initial_status = agent.status
    try:
        is_online = agent.check_online_status()
        if is_online:
            logging.info(f"[Agent] Initially online, connected to tracker")
        else:
            logging.info(f"[Agent] Initially offline, cannot connect to tracker")
        
        agent.register_to_tracker()
    except Exception as e:
        logging.error(f"[Agent] Error during startup: {e}")
    
    tracker_connected = agent.check_online_status()
    last_connection_check = time.time()
    last_auto_sync = time.time()
    
    if not hasattr(agent, '_auto_sync'):
        agent._auto_sync = True
    
    running = True
    while running:
        try:
            current_time = time.time()
            if not command_queue.empty():
                cmd = command_queue.get()
                logging.info(f"[Agent] Received command: {cmd}")
                
                result = agent.handle_command(cmd)
                
                if response_queue:
                    response_queue.put(result)
                
                if result["status"] == "exit":
                    running = False
                    break
                    
                if cmd.startswith("login:"):
                    new_username = cmd.split(":", 1)[1]
                    server_username[0] = new_username
                    logging.info(f"[Agent] Updated server thread username to {new_username}")
            
            
            elif current_time - last_connection_check > 10:
                last_connection_check = current_time
                current_connection = agent.check_online_status()
                logging.info(f"[Agent] Checking tracker connection status: {current_connection}")
                if not tracker_connected and current_connection:
                    logging.info("[Agent] Tracker connection re-established!")
                    if agent.status == "offline":
                        logging.info("[Agent] Updating status from offline to online")
                        result = agent.handle_command("status online")
                        if response_queue:
                            if isinstance(result, dict):
                                result["auto"] = True
                            response_queue.put(result)
                        agent.register_to_tracker()
                    agent.sync_all(sync_type="reconnect")
                elif tracker_connected and not current_connection:
                    logging.warning("[Agent] Lost connection to tracker!")
                    result = agent.handle_command("status offline")
                    if response_queue:
                        if isinstance(result, dict):
                            result["auto"] = True
                        response_queue.put(result)
                tracker_connected = current_connection
        
            if agent._auto_sync and current_time - last_auto_sync > 60 and agent.status != "offline" and agent.is_authenticated and tracker_connected:
                last_auto_sync = current_time
                logging.info("[Agent] Performing scheduled automatic sync...")
                agent.sync_all(sync_type="reconnect")
                    
            time.sleep(0.1)
            
        except KeyboardInterrupt:
            logging.info("[Agent] Received interrupt, shutting down...")
            running = False
        except Exception as e:
            logging.error(f"[Agent] Error: {e}")
    
    logging.info("[Agent] Shutting down")

