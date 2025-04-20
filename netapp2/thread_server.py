# thread_server.py
import socket
import threading
import json
import os
from datetime import datetime
from thread_client import send_to_peer
from data_manager import DataManager, Message

# Tracker configuration
TRACKER_IP = "127.0.0.1"
TRACKER_PORT = 12345

# Global data manager
data_manager = DataManager()

def handle_peer(conn, username_fn):
    # Get current username (might change if user logs in/out)
    username = username_fn() if callable(username_fn) else username_fn
    is_authenticated = bool(username) and username != "visitor"  # Empty username or visitor means visitor mode
    
    try:
        buffer = ""
        while True:
            # Receive data in chunks
            chunk = conn.recv(4096).decode()
            if not chunk:
                break
                
            # Add to buffer
            buffer += chunk
            
            # Process complete messages (each message ends with a newline)
            if '\n' in buffer:
                messages = buffer.split('\n')
                # Last element might be an incomplete message
                buffer = messages.pop()
                
                for data in messages:
                    if not data.strip():
                        continue
                        
                    # Always get the latest username before processing each message
                    username = username_fn() if callable(username_fn) else username_fn
                    is_authenticated = bool(username) and username != "visitor"

                    # Xử lý lệnh ping từ tracker
                    if data.strip() == "ping":
                        print(f"[Server] Received ping from tracker, sending pong")
                        conn.send(b"pong\n")
                        continue
                    
                    try:
                        message_data = json.loads(data)
                        if message_data["type"] == "message":
                            channel_name = message_data["channel"]
                            content = message_data["content"]
                            sender = message_data["sender"]
                            # Extract timestamp from the message data if available
                            timestamp = message_data.get("timestamp")
                            
                            channel = data_manager.get_channel(channel_name)
                            if not channel:
                                print(f"[ERROR] Channel {channel_name} does not exist")
                                continue
                            
                            print(f"[DEBUG] Processing message in {channel_name} from {sender}")
                            print(f"[DEBUG] Channel state: {channel.debug_info()}")
                            
                            # Only process message if sender can write
                            # For non-members, automatically add them if authenticated
                            if sender and sender != "visitor" and not channel.is_member(sender):
                                data_manager.join_channel(channel_name, sender)
                                print(f"[DEBUG] Auto-added {sender} as member to channel {channel_name}")
                            
                            if channel.can_write(sender):
                                # Use the provided timestamp to maintain consistency across clients
                                message = data_manager.add_message(channel_name, sender, content, timestamp)
                                
                                # If we're the host, store the message
                                if channel.is_host(username):
                                    print(f"[{channel_name}] {sender}: {content} (stored) [timestamp: {timestamp}]")
                                    
                                    # As host, forward to all other members/visitors with the same timestamp
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
                                                            # Make sure we're forwarding the original message_data 
                                                            # with its timestamp preserved
                                                            send_to_peer(peer["ip"], int(peer["port"]), json.dumps(message_data))
                                                            print(f"[DEBUG] Host forwarded message to {recipient} with timestamp {timestamp}")
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
                                    print(f"[{channel_name}] {sender}: {content} (stored from host)")
                                # Otherwise just display it
                                else:
                                    print(f"[{channel_name}] {sender}: {content}")
                            else:
                                print(f"[ERROR] User {sender} does not have permission to send messages in {channel_name}")
                            
                        elif message_data["type"] == "join_channel":
                            channel_name = message_data["channel"]
                            visitor_username = message_data.get("username", "visitor")
                            
                            # Visitors join as read-only
                            as_visitor = visitor_username == "visitor" or not is_authenticated
                            
                            if data_manager.join_channel(channel_name, visitor_username, as_visitor):
                                print(f"[DEBUG] {'Visitor' if as_visitor else 'User'} {visitor_username} joined channel {channel_name}")
                                
                                # If we're the host, send welcome message and channel history
                                channel = data_manager.get_channel(channel_name)
                                if channel and channel.is_host(username):
                                    # Find peer in peer list
                                    try:
                                        # Send welcome message
                                        welcome_data = {
                                            "type": "message",
                                            "channel": channel_name,
                                            "content": f"Welcome to channel {channel_name}! You joined as a {'visitor (read-only)' if as_visitor else 'member'}.",
                                            "sender": channel.host
                                        }
                                        
                                        # Get peer information
                                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                        s.connect((TRACKER_IP, TRACKER_PORT))
                                        s.send(b"get_list\n")
                                        peers = json.loads(s.recv(4096).decode())
                                        s.close()
                                        
                                        for peer in peers:
                                            if peer["username"] == visitor_username:
                                                try:
                                                    send_to_peer(peer["ip"], int(peer["port"]), json.dumps(welcome_data))
                                                    print(f"[DEBUG] Sent welcome message to {visitor_username}")
                                                    
                                                    # Send channel history
                                                    if channel.messages:
                                                        history_data = {
                                                            "type": "channel_history",
                                                            "channel": channel_name,
                                                            "messages": [msg.to_dict() for msg in channel.messages]
                                                        }
                                                        send_to_peer(peer["ip"], int(peer["port"]), json.dumps(history_data))
                                                        print(f"[DEBUG] Sent history ({len(channel.messages)} messages) to {visitor_username}")
                                                except Exception as e:
                                                    print(f"[Error sending welcome/history to {visitor_username}]: {e}")
                                                break
                                    except Exception as e:
                                        print(f"[Error processing join event]: {e}")
                            else:
                                print(f"[ERROR] Failed to add {'visitor' if as_visitor else 'user'} {visitor_username} to channel {channel_name}")
                            
                        elif message_data["type"] == "leave_channel":
                            channel_name = message_data["channel"]
                            leaver_username = message_data.get("username", "visitor")
                            
                            if data_manager.leave_channel(channel_name, leaver_username):
                                print(f"[DEBUG] User {leaver_username} left channel {channel_name}")
                            else:
                                print(f"[ERROR] Failed to remove user {leaver_username} from channel {channel_name}")
                            
                        elif message_data["type"] == "request_history":
                            channel_name = message_data["channel"]
                            requester = message_data.get("username", "visitor")
                            
                            channel = data_manager.get_channel(channel_name)
                            if not channel:
                                print(f"[ERROR] Cannot find channel {channel_name} for history request")
                                continue
                            
                            if not channel.can_read(requester):
                                print(f"[ERROR] User {requester} does not have permission to read channel {channel_name}")
                                continue
                                
                            # Only respond if we're the host
                            if channel.is_host(username):
                                # Send channel history to requester
                                try:
                                    # Get peer information
                                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                    s.connect((TRACKER_IP, TRACKER_PORT))
                                    s.send(b"get_list\n")
                                    peers = json.loads(s.recv(4096).decode())
                                    s.close()
                                    
                                    # Find requester in peer list
                                    for peer in peers:
                                        if peer["username"] == requester:
                                            history_data = {
                                                "type": "channel_history",
                                                "channel": channel_name,
                                                "messages": [msg.to_dict() for msg in channel.messages]
                                            }
                                            send_to_peer(peer["ip"], int(peer["port"]), json.dumps(history_data))
                                            print(f"[DEBUG] Sent history ({len(channel.messages)} messages) to {requester}")
                                            break
                                except Exception as e:
                                    print(f"[Error sending history to {requester}]: {e}")
                        
                        elif message_data["type"] == "channel_history":
                            channel_name = message_data["channel"]
                            messages = message_data["messages"]
                            
                            channel = data_manager.get_channel(channel_name)
                            if not channel:
                                # Create channel if it doesn't exist
                                data_manager.create_channel(channel_name, "unknown")
                                channel = data_manager.get_channel(channel_name)
                            
                            if channel and messages:
                                # Clear existing messages and add new ones
                                channel.messages = []
                                for msg_data in messages:
                                    message = Message.from_dict(msg_data)
                                    channel.add_message(message)
                                
                                # Save channel to disk
                                data_manager.save_channel(channel_name)
                                print(f"[DEBUG] Updated channel {channel_name} with {len(messages)} messages from history")
                                
                                # Display the history to the user
                                print(f"\n[Message History for {channel_name}]")
                                for msg in channel.messages:
                                    # Format the timestamp for better display
                                    try:
                                        timestamp = datetime.fromisoformat(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
                                    except:
                                        timestamp = msg.timestamp
                                    print(f"[{timestamp}] {msg.sender}: {msg.content}")
                                print()
                            
                        elif message_data["type"] == "sync_with_tracker":
                            # This is a request for the local agent to sync a channel with the tracker
                            # We just ignore it in the server handler, as the agent will handle it separately
                            pass
                        
                    except json.JSONDecodeError:
                        print(f"[Error] Invalid JSON data received: {data}")
                    except Exception as e:
                        print(f"[Error handling message]: {e}")
    except Exception as e:
        print(f"[Error in peer connection]: {e}")
    finally:
        conn.close()

def start_peer_server(port, username_fn):
    # Create data directory if it doesn't exist
    os.makedirs("data", exist_ok=True)
    
    host = '0.0.0.0'
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((host, port))
    server_socket.listen(5)
    print(f"[Server] Peer server started on port {port}")
    
    while True:
        conn, addr = server_socket.accept()
        print(f"[Server] Connected with {addr[0]}:{addr[1]}")
        client_thread = threading.Thread(target=handle_peer, args=(conn, username_fn))
        client_thread.daemon = True
        client_thread.start()
