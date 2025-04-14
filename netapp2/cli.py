# cli.py
from multiprocessing import Queue
from agent import agent_main
from multiprocessing import Process
import json
import os
import getpass
import hashlib
from datetime import datetime

# User authentication data
USER_DATA_FILE = "data/users.json"

def print_help(is_authenticated=True):
    if is_authenticated:
        print("""
Available commands:
- help: Show this help message
- list: List all peers
- channels: List all channels
- list_all: List all available channels
- join <channel>: Join a channel
- leave <channel>: Leave a channel
- send <channel> <message>: Send message to a channel
- create <channel>: Create a new channel (you become the host)
- history <channel>: Request message history from channel host
- status <online|offline|invisible>: Change your status
- sync: Force synchronization with the tracker server
- logout: Log out and switch to visitor mode
- exit/quit: Exit the program

Note: Messages in channels are stored by the channel host (creator).
When you create a channel, you become the host and are responsible for storing messages.
When you join a channel, you'll receive message history from the host.
You can use the 'history' command to request message history at any time.
""")
    else:
        # Visitor mode help - limited commands
        print("""
Available commands (Visitor Mode):
- help: Show this help message
- list: List all peers
- list_all: List all available channels
- join <channel>: Join a channel (read-only)
- history <channel>: Request message history from channel host (if permitted)
- login: Log in to access full features
- register: Create a new account
- exit/quit: Exit the program

Note: In visitor mode, you can view content but cannot create or modify channels.
Use 'login' to access full features.
""")

def create_user_data_dir():
    # Create data directory if it doesn't exist
    try:
        os.makedirs("data", exist_ok=True)
        
        # Create user data file if it doesn't exist
        if not os.path.exists(USER_DATA_FILE):
            with open(USER_DATA_FILE, "w") as f:
                json.dump({}, f)
            print(f"[Auth] Created new user database at {USER_DATA_FILE}")
        return True
    except Exception as e:
        print(f"[Auth] Error creating data directory: {e}")
        return False

def hash_password(password):
    # Simple password hashing
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username):
    """Simplified registration - just check if username is valid"""
    if not username or username.strip() == "":
        return False
    return True

def authenticate_user(username):
    """Simplified authentication - just check if username is valid"""
    if not username or username.strip() == "":
        return False
    return True

def visitor_mode_cli(command_queue: Queue):
    """CLI for visitor mode (unauthenticated users)"""
    print("==== Visitor Mode ====")
    print_help(is_authenticated=False)
    
    while True:
        try:
            cmd = input("[Visitor] >> ").strip()
            if not cmd:
                continue
                
            if cmd in ["exit", "quit"]:
                command_queue.put("exit")
                break
            elif cmd == "help":
                print_help(is_authenticated=False)
            elif cmd == "login":
                username = input("Username: ").strip()
                
                if authenticate_user(username):
                    print(f"Login successful. Welcome, {username}!")
                    # Send login command to agent
                    command_queue.put(f"login:{username}")
                    return username  # Return username to switch to authenticated mode
                else:
                    print("Invalid username. Username cannot be empty.")
            elif cmd == "register":
                username = input("Choose a username: ").strip()
                
                if register_user(username):
                    print(f"Registration successful. Welcome, {username}!")
                    # Send login command to agent
                    command_queue.put(f"login:{username}")
                    return username  # Return username to switch to authenticated mode
                else:
                    print("Invalid username. Username cannot be empty.")
            elif cmd.startswith("join ") or cmd == "list" or cmd == "list_all" or cmd.startswith("history "):
                # Limited set of commands allowed in visitor mode
                command_queue.put(cmd)
            else:
                print("Command not available in visitor mode. Please login to access full features.")
        except KeyboardInterrupt:
            print("\nExiting...")
            command_queue.put("exit")
            break
        except Exception as e:
            print(f"Error: {e}")
    
    return None  # No authentication happened

def authenticated_cli_loop(command_queue: Queue, username: str):
    """CLI for authenticated users"""
    print(f"==== Authenticated as {username} ====")
    print_help(is_authenticated=True)
    
    while True:
        try:
            cmd = input(f"[{username}] >> ").strip()
            if not cmd:
                continue
                
            if cmd in ["exit", "quit"]:
                command_queue.put("exit")
                break
            elif cmd == "help":
                print_help(is_authenticated=True)
            elif cmd == "logout":
                print("Logging out...")
                # Send logout command to agent
                command_queue.put("logout")
                return False  # Return to visitor mode
            else:
                command_queue.put(cmd)
        except KeyboardInterrupt:
            print("\nExiting...")
            command_queue.put("exit")
            break
        except Exception as e:
            print(f"Error: {e}")
    
    return True  # Exit the program

def cli_loop(command_queue: Queue):
    print("==== CLI Started ====")
    
    # Start in visitor mode
    username = visitor_mode_cli(command_queue)
    
    # If login/register successful, switch to authenticated mode
    if username:
        exit_program = authenticated_cli_loop(command_queue, username)
        
        # If user logged out, go back to visitor mode
        while not exit_program:
            username = visitor_mode_cli(command_queue)
            if username:
                exit_program = authenticated_cli_loop(command_queue, username)
            else:
                break

def main():
    port = int(input("Enter port for your node: "))
    
    # Start in visitor mode by default
    username = ""
    status = "online"
    
    # Create agent command queue
    command_queue = Queue()

    # Create agent as a separate process
    agent_proc = Process(target=agent_main, args=(command_queue, port, username, status))
    agent_proc.start()

    # CLI handles command input
    cli_loop(command_queue)

    agent_proc.join()

if __name__ == "__main__":
    main()
