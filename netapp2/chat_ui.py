import tkinter as tk
from tkinter import messagebox, simpledialog, scrolledtext
import threading
import queue
import time
import os
import json

DATA_DIR = "data"

class ChatUI:
    def __init__(self, master, command_queue, response_queue):
        self.master = master
        self.command_queue = command_queue
        self.response_queue = response_queue
        self.current_channel = None
        self.channels = []
        self.users = []
        self.username = ""
        self.status = "offline"
        self.notifications = queue.Queue()

        master.title("Chat App")

        # Top panel: User info and login/logout
        self.top_frame = tk.Frame(master)
        self.top_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=2)
        self.user_label = tk.Label(self.top_frame, text="User: (not logged in)", font=("Arial", 10, "bold"))
        self.user_label.pack(side=tk.LEFT, padx=2)
        self.login_btn = tk.Button(self.top_frame, text="Login", command=self.login)
        self.login_btn.pack(side=tk.LEFT, padx=2)
        self.logout_btn = tk.Button(self.top_frame, text="Logout", command=self.logout, state=tk.DISABLED)
        self.logout_btn.pack(side=tk.LEFT, padx=2)

        # Left panel: Channel list
        self.channel_frame = tk.Frame(master)
        self.channel_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        tk.Label(self.channel_frame, text="Channels").pack()
        self.channel_listbox = tk.Listbox(self.channel_frame, width=25)
        self.channel_listbox.pack(fill=tk.Y, expand=True)
        self.channel_listbox.bind('<<ListboxSelect>>', self.on_channel_select)
        self.create_channel_btn = tk.Button(self.channel_frame, text="Create Channel", command=self.create_channel)
        self.create_channel_btn.pack(fill=tk.X, pady=2)
        self.leave_channel_btn = tk.Button(self.channel_frame, text="Leave Channel", command=self.leave_channel)
        self.leave_channel_btn.pack(fill=tk.X, pady=2)

        # Center panel: Chat
        self.center_frame = tk.Frame(master)
        self.center_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.channel_title = tk.Label(self.center_frame, text="No channel selected", font=("Arial", 14, "bold"))
        self.channel_title.pack()
        self.chat_display = scrolledtext.ScrolledText(self.center_frame, state='disabled', height=20)
        self.chat_display.pack(fill=tk.BOTH, expand=True)
        self.message_entry = tk.Entry(self.center_frame)
        self.message_entry.pack(fill=tk.X, pady=2)
        self.message_entry.bind('<Return>', self.send_message)
        self.send_btn = tk.Button(self.center_frame, text="Send", command=self.send_message)
        self.send_btn.pack(fill=tk.X, pady=2)

        # Right panel: User list
        self.user_frame = tk.Frame(master)
        self.user_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        tk.Label(self.user_frame, text="Users").pack()
        self.user_listbox = tk.Listbox(self.user_frame, width=20)
        self.user_listbox.pack(fill=tk.Y, expand=True)
        self.status_label = tk.Label(self.user_frame, text="Status: offline")
        self.status_label.pack(pady=2)
        self.sync_btn = tk.Button(self.user_frame, text="Sync", command=self.sync)
        self.sync_btn.pack(fill=tk.X, pady=2)

        # Bottom: Notifications
        self.notification_label = tk.Label(master, text="", fg="blue")
        self.notification_label.pack(fill=tk.X, side=tk.BOTTOM)

        # Start background thread to update UI
        self.running = True
        threading.Thread(target=self.update_ui_loop, daemon=True).start()
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)

        # Initial load
        self.refresh_channels()
        self.refresh_users()
        self.update_user_label()
        self.update_status_label()

    def update_user_label(self):
        if self.username:
            self.user_label.config(text=f"User: {self.username}")
            self.logout_btn.config(state=tk.NORMAL)
            self.login_btn.config(state=tk.DISABLED)
        else:
            self.user_label.config(text="User: (not logged in)")
            self.logout_btn.config(state=tk.DISABLED)
            self.login_btn.config(state=tk.NORMAL)

    def update_status_label(self):
        self.status_label.config(text=f"Status: {self.status}")

    def send_command_and_handle_response(self, cmd, wait_time=5):
        """Gửi command tới agent, chờ response và xử lý kết quả"""
        self.command_queue.put(cmd)
        start = time.time()
        response = None
        while time.time() - start < wait_time:
            try:
                if not self.response_queue.empty():
                    response = self.response_queue.get()
                    break
            except Exception:
                break
            time.sleep(0.05)
        # Nếu không có response, trả về None
        if response is None:
            self.notifications.put("No response from agent.")
            return None
        # Cập nhật trạng thái nếu có trong response
        if "status_value" in response:
            self.status = response["status_value"]
            self.update_status_label()
        # Xử lý response
        status = response.get("status", "")
        msg = response.get("message", "")
        if status == "ok":
            self.notifications.put(msg)
        elif status == "error":
            self.notifications.put(f"Error: {msg}")
            messagebox.showerror("Error", msg)
        elif status == "exit":
            self.notifications.put(msg)
        else:
            self.notifications.put(str(response))
        return response

    def login(self):
        if self.username:
            messagebox.showinfo("Already logged in", f"Already logged in as {self.username}")
            return
        name = simpledialog.askstring("Login", "Enter username:")
        if name:
            resp = self.send_command_and_handle_response(f"login:{name}")
            if resp and resp.get("status") == "ok":
                agent_username = resp.get("username") if "username" in resp else name
                self.username = agent_username
                # Cập nhật trạng thái nếu agent trả về
                if "status_value" in resp:
                    self.status = resp["status_value"]
                else:
                    self.status = "online"
                self.notifications.put(f"Logged in as {self.username}.")
                self.update_user_label()
                self.update_status_label()
                self.refresh_channels()
                self.refresh_users()
            else:
                self.notifications.put("Login failed.")

    def logout(self):
        if not self.username:
            messagebox.showinfo("Not logged in", "You are not logged in.")
            return
        resp = self.send_command_and_handle_response("logout")
        if resp and resp.get("status") == "ok":
            self.notifications.put(f"Logged out user {self.username}.")
            self.username = ""
            # Cập nhật trạng thái nếu agent trả về
            if "status_value" in resp:
                self.status = resp["status_value"]
            else:
                self.status = "offline"
            self.update_user_label()
            self.update_status_label()
            self.refresh_channels()
            self.refresh_users()
        else:
            self.notifications.put("Logout failed.")

    def get_local_channels(self):
        """Lấy danh sách kênh cục bộ từ thư mục data/"""
        channels = []
        try:
            for filename in os.listdir(DATA_DIR):
                if filename.endswith(".json") and not filename.startswith("user_"):
                    channel_name = filename[:-5]
                    # Đọc host từ file
                    try:
                        with open(os.path.join(DATA_DIR, filename), "r") as f:
                            data = json.load(f)
                            host = data.get("host", "unknown")
                    except Exception:
                        host = "unknown"
                    channels.append({"name": channel_name, "host": host})
        except Exception as e:
            print(f"[UI] Error loading local channels: {e}")
        return channels

    def on_channel_select(self, event):
        selection = self.channel_listbox.curselection()
        if selection:
            idx = selection[0]
            self.current_channel = self.channels[idx]
            self.channel_title.config(text=f"Channel: {self.current_channel['name']}")
            self.refresh_chat()
            self.refresh_users()
        else:
            self.current_channel = None
            self.channel_title.config(text="No channel selected")
            self.chat_display.config(state='normal')
            self.chat_display.delete(1.0, tk.END)
            self.chat_display.config(state='disabled')

    def create_channel(self):
        name = simpledialog.askstring("Create Channel", "Enter channel name:")
        if name:
            resp = self.send_command_and_handle_response(f"create {name}")
            self.refresh_channels()
            if resp and resp.get("status") == "ok":
                self.notifications.put(f"Created channel '{name}' successfully.")
            else:
                self.notifications.put(f"Failed to create channel '{name}'.")

    def leave_channel(self):
        if self.current_channel:
            resp = self.send_command_and_handle_response(f"leave {self.current_channel['name']}")
            self.refresh_channels()
            if resp and resp.get("status") == "ok":
                self.notifications.put(f"Left channel '{self.current_channel['name']}'.")
            else:
                self.notifications.put(f"Failed to leave channel '{self.current_channel['name']}'.")

    def send_message(self, event=None):
        msg = self.message_entry.get().strip()
        if msg and self.current_channel:
            resp = self.send_command_and_handle_response(f"send {self.current_channel['name']} {msg}")
            self.message_entry.delete(0, tk.END)
            self.refresh_chat()
            if resp and resp.get("status") == "ok":
                self.notifications.put(f"Message sent to {self.current_channel['name']}.")
            else:
                self.notifications.put(f"Failed to send message to {self.current_channel['name']}.")

    def sync(self):
        resp = self.send_command_and_handle_response("sync")
        if resp and resp.get("status") == "ok":
            self.notifications.put("Sync successful.")
        else:
            self.notifications.put("Sync failed.")

    def refresh_channels(self):
        # Lấy danh sách kênh cục bộ
        self.channels = self.get_local_channels()
        self.channel_listbox.delete(0, tk.END)
        for ch in self.channels:
            self.channel_listbox.insert(tk.END, f"{ch['name']} (Host: {ch['host']})")

    def refresh_users(self):
        # Hiển thị danh sách user của kênh đang chọn, trạng thái lấy từ tracker
        self.user_listbox.delete(0, tk.END)
        users = []
        if self.current_channel:
            channel_name = self.current_channel['name']
            filepath = os.path.join(DATA_DIR, f"{channel_name}.json")
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                    members = data.get("members", [])
                    visitors = data.get("visitors", [])
                    users = list(set(members + visitors))
            except Exception as e:
                print(f"[UI] Error loading users for channel {channel_name}: {e}")
        else:
            return

        # Gửi lệnh "list" đến agent để lấy danh sách peers online từ tracker
        resp = self.send_command_and_handle_response("list", wait_time=2)
        online_users = set()
        if resp and resp.get("status") == "ok" and "peers" in resp:
            # Lấy danh sách user online thực sự từ response của agent
            for peer in resp["peers"]:
                if peer.get("status") == "online":
                    online_users.add(peer.get("username"))
        # Hiển thị user và trạng thái (bằng chữ)
        for user in users:
            status_text = "online" if user in online_users else "offline"
            self.user_listbox.insert(tk.END, f"{user} ({status_text})")

    def refresh_chat(self):
        # Hiển thị lịch sử tin nhắn cục bộ của kênh được chọn
        messages = []
        if self.current_channel:
            channel_name = self.current_channel['name']
            filepath = os.path.join(DATA_DIR, f"{channel_name}.json")
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                    messages = data.get("messages", [])
            except Exception as e:
                print(f"[UI] Error loading messages for channel {channel_name}: {e}")
        self.chat_display.config(state='normal')
        self.chat_display.delete(1.0, tk.END)
        for msg in messages:
            status_icon = {"pending": "⌛", "sent": "✅", "received": "📥"}.get(msg.get("status", ""), "")
            ts = msg.get("timestamp", "")
            sender = msg.get("sender", "")
            content = msg.get("content", "")
            self.chat_display.insert(tk.END, f"[{ts}] {sender}: {content} {status_icon}\n")
        self.chat_display.config(state='disabled')

    def update_ui_loop(self):
        while self.running:
            # Handle notifications
            try:
                note = self.notifications.get_nowait()
                self.notification_label.config(text=note)
            except queue.Empty:
                pass
            # Xử lý các response còn lại trong response_queue (nếu có)
            try:
                while not self.response_queue.empty():
                    resp = self.response_queue.get_nowait()
                    # Có thể mở rộng xử lý các loại response khác ở đây nếu muốn
            except Exception:
                pass
            time.sleep(0.2)

    def on_close(self):
        try:
            self.command_queue.put("exit")
        except Exception:
            pass
        self.running = False
        self.master.destroy()

def run_chat_ui(command_queue, response_queue):
    root = tk.Tk()
    app = ChatUI(root, command_queue, response_queue)
    root.mainloop()
