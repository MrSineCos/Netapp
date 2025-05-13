import tkinter as tk
from tkinter import messagebox, simpledialog, scrolledtext
import threading
import queue
import time
import os
import json
import socket

DATA_DIR = "data"

class ChatUI:
    def __init__(self, master, command_queue, response_queue, port=None):
        self.master = master
        self.command_queue = command_queue
        self.response_queue = response_queue
        self.current_channel = None
        self.channels = []
        self.users = []
        self.username = ""
        self.status = "offline"
        self.notifications = queue.Queue()
        self.last_response = None  # Lưu response cuối cùng
        self.last_response_lock = threading.Lock()  # Thêm lock cho last_response

        # --- Thêm thuộc tính lưu IP, invisible mode, port ---
        self.my_ip = self.get_local_ip()
        self.invisible_mode = False
        self.my_port = port  # Gán port truyền vào khi khởi tạo
        self._port_locked = False  # Thêm biến này để kiểm soát việc khóa port
        self.settings_visible = False  # Thêm biến trạng thái hiển thị settings panel
        # ----------------------------------------------------

        # Nếu đã có port truyền vào thì khóa trường nhập port trong settings
        if self.my_port:
            self._port_locked = True

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
        # --- Thêm nút Cài đặt ---
        self.settings_btn = tk.Button(self.top_frame, text="Cài đặt", command=self.toggle_settings_panel)
        self.settings_btn.pack(side=tk.RIGHT, padx=2)
        # ------------------------

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
        # --- Thêm ô nhập tên kênh và nút Join Channel ---
        self.join_channel_entry = tk.Entry(self.channel_frame)
        self.join_channel_entry.pack(fill=tk.X, pady=2)
        self.join_channel_btn = tk.Button(self.channel_frame, text="Join Channel", command=self.join_channel_by_name)
        self.join_channel_btn.pack(fill=tk.X, pady=2)
        # ------------------------------------------------

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

        # --- Thêm frame settings (ẩn mặc định) ---
        self.settings_frame = tk.Frame(master, borderwidth=2, relief=tk.GROOVE)
        # Các widget settings sẽ được tạo trong self.build_settings_panel()
        # self.build_settings_panel()
        # -----------------------------------------

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
        threading.Thread(target=self.handle_response_loop, daemon=True).start()  # Thread xử lý response
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)

        # Khóa ô nhập chat và nút tạo kênh khi khởi động (visitor mode)
        self.update_chat_input_state()
        self.update_create_channel_btn_state()

        # Sau khi khởi tạo xong các panel:
        self.send_command("status check")  # hoặc lệnh phù hợp để agent trả về trạng thái thực tế
        # --- Hiển thị panel settings khi khởi động, ẩn các panel chat ---
        self.show_settings_panel(startup=True)
        # --------------------------------------------------------------

    def get_local_ip(self):
        """Lấy địa chỉ IP cục bộ"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def build_settings_panel(self):
        """Tạo các widget cho panel cài đặt (settings_frame)"""
        # Xóa các widget cũ nếu có
        for widget in self.settings_frame.winfo_children():
            widget.destroy()

        row = 0
        # IP address (readonly)
        tk.Label(self.settings_frame, text="Địa chỉ IP:").grid(row=row, column=0, sticky="w", padx=5, pady=5)
        self.ip_entry = tk.Entry(self.settings_frame)
        self.ip_entry.grid(row=row, column=1, padx=5, pady=5)
        self.ip_entry.insert(0, self.my_ip)
        self.ip_entry.config(state="readonly")
        row += 1

        # Port (editable lần đầu, sau đó readonly)
        tk.Label(self.settings_frame, text="Port:").grid(row=row, column=0, sticky="w", padx=5, pady=5)
        self.port_entry = tk.Entry(self.settings_frame)
        self.port_entry.grid(row=row, column=1, padx=5, pady=5)
        if self.my_port:
            self.port_entry.insert(0, str(self.my_port))
        if self._port_locked:
            self.port_entry.config(state="readonly")
        row += 1

        # Invisible mode (switch button)
        self.invisible_switch_btn = tk.Button(
            self.settings_frame,
            text=self.get_invisible_switch_text(),
            command=self.on_invisible_switch
        )
        self.invisible_switch_btn.grid(row=row, column=0, columnspan=2, padx=5, pady=5)
        row += 1

        # Save button
        self.save_btn = tk.Button(self.settings_frame, text="Lưu", command=self.save_settings)
        self.save_btn.grid(row=row, column=0, columnspan=2, pady=10)

    def get_invisible_switch_text(self):
        """Trả về text cho nút switch invisible mode"""
        return "Invisible mode: ON" if self.invisible_mode else "Invisible mode: OFF"

    def on_invisible_switch(self):
        """Callback khi nhấn nút switch invisible mode"""
        self.invisible_mode = not self.invisible_mode
        # Cập nhật lại text nút
        self.invisible_switch_btn.config(text=self.get_invisible_switch_text())
        print(f"[UI] [on_invisible_switch] invisible_mode: {self.invisible_mode}")
        self.update_status_label()

    def toggle_settings_panel(self):
        """Bật/tắt panel cài đặt khi bấm nút Cài đặt"""
        if self.settings_visible:
            self.hide_settings_panel()
        else:
            self.show_settings_panel()

    def show_settings_panel(self, startup=False):
        """Hiển thị panel cài đặt, ẩn vùng chat"""
        if self.settings_visible and not startup:
            return
        self.settings_visible = True

        # Ẩn vùng chat
        self.center_frame.pack_forget()

        print(f"[UI] [show_settings_panel] invisible_mode: {self.invisible_mode}")
        
        # Hiện panel settings
        self.settings_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.build_settings_panel()

        if startup:
            # ẩn luôn các panel khác lúc khởi động
            self.channel_frame.pack_forget()
            self.user_frame.pack_forget()
            self.top_frame.pack_forget()
            self.notification_label.pack_forget()

    def hide_settings_panel(self):
        """Ẩn panel cài đặt, hiện lại vùng chat"""
        if not self.settings_visible:
            return
        self.settings_visible = False
        self.settings_frame.pack_forget()
        self.center_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

    def save_settings(self):
        """Lưu cài đặt từ panel settings"""
        # Nếu đã khóa port thì không cho chỉnh sửa nữa
        if self._port_locked:
            port = self.my_port
        else:
            port_str = self.port_entry.get().strip()
            try:
                port = int(port_str)
                if not (1024 <= port <= 65535):
                    raise ValueError
            except Exception:
                messagebox.showerror("Lỗi", "Vui lòng nhập port hợp lệ (1024-65535).")
                return
            self.my_port = port

        print("[UI] [save_settings] Đang lưu cài đặt...")
        if self.invisible_mode:
            if self.status == "online":
                print("[UI] [save_settings] Đang bật invisible mode")
                self.send_command("status invisible")
            else:
                self.send_command("status online")
        self.update_status_label()
        self.notifications.put("Đã lưu cài đặt.")

        # Nếu là lần đầu khởi động, truyền port cho agent và khóa port
        if not hasattr(self, "_settings_initialized"):
            self._settings_initialized = True
            self._port_locked = True  # Khóa port sau lần đầu lưu
            # Gửi lệnh set_port cho agent
            self.send_command(f"set_port {self.my_port}")
            # Hiện lại các panel
            self.top_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=2)
            self.channel_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
            self.user_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
            self.notification_label.pack(fill=tk.X, side=tk.BOTTOM)
            # Ẩn settings, hiện chat
            self.hide_settings_panel()
            # --- Sửa tại đây: reset current_channel và refresh lại danh sách kênh ---
            self.current_channel = None
            self.refresh_channels()
            self.refresh_users()
            self.update_user_label()
            self.update_status_label()
        else:
            # Nếu chỉ là mở settings từ nút, ẩn settings, hiện chat
            self.hide_settings_panel()

    def update_chat_input_state(self):
        """Enable/disable chat input and send button based on login state."""
        if self.username:
            self.message_entry.config(state="normal")
            self.send_btn.config(state="normal")
        else:
            self.message_entry.config(state="disabled")
            self.send_btn.config(state="disabled")

    def update_create_channel_btn_state(self):
        """Enable/disable create channel button based on login state."""
        if self.username:
            self.create_channel_btn.config(state="normal")
        else:
            self.create_channel_btn.config(state="disabled")

    def update_user_label(self):
        if self.username:
            self.user_label.config(text=f"User: {self.username}")
            self.logout_btn.config(state=tk.NORMAL)
            self.login_btn.config(state=tk.DISABLED)
        else:
            self.user_label.config(text="User: (not logged in)")
            self.logout_btn.config(state=tk.DISABLED)
            self.login_btn.config(state=tk.NORMAL)
        # Cập nhật trạng thái ô nhập chat và nút tạo kênh mỗi khi cập nhật user label
        self.update_chat_input_state()
        self.update_create_channel_btn_state()

    def update_status_label(self):
        # Hiển thị trạng thái invisible nếu đang bật
        if self.invisible_mode:
            self.status_label.config(text="Status: invisible")
        else:
            self.status_label.config(text=f"Status: {self.status}")

    def send_command(self, cmd):
        """Chỉ gửi command tới agent (không chờ response)"""
        self.command_queue.put(cmd)

    def handle_response_loop(self):
        """Luôn chạy, lấy và xử lý response từ agent"""
        while self.running:
            try:
                # Chỉ chạy khi response_queue không rỗng
                resp = self.response_queue.get(block=True)  # Chờ đến khi có phần tử trong queue
                with self.last_response_lock:
                    self.last_response = resp  # Lưu response cuối cùng
                # Xử lý response
                if isinstance(resp, dict):
                    if "status_value" in resp:
                        self.status = resp["status_value"]
                        self.update_status_label()
                    status = resp.get("status", "")
                    msg = resp.get("message", "")
                    if status == "ok":
                        self.notifications.put(msg)
                    elif status == "error":
                        self.notifications.put(f"Error: {msg}")
                        messagebox.showerror("Error", msg)
                    elif status == "exit":
                        self.notifications.put(msg)
                    else:
                        self.notifications.put(str(resp))
                else:
                    self.notifications.put(str(resp))
            except Exception:
                pass
            time.sleep(0.05)

    def login(self):
        if self.username:
            messagebox.showinfo("Already logged in", f"Already logged in as {self.username}")
            return
        name = simpledialog.askstring("Login", "Enter username:")
        if name:
            self.send_command(f"login:{name}")
            # Đợi response trong 5s
            start = time.time()
            resp = None
            while time.time() - start < 5:
                with self.last_response_lock:
                    last_resp = self.last_response
                if last_resp and isinstance(last_resp, dict) and last_resp.get("username") == name:
                    resp = last_resp
                    break
                time.sleep(0.05)
            if resp and resp.get("status") == "ok":
                self.username = resp.get("username", name)
                self.status = resp.get("status_value", "online")
                self.notifications.put(f"Logged in as {self.username}.")
                self.update_user_label()
                self.update_status_label()
                self.current_channel = None
                self.refresh_channels()
                self.refresh_users()
                self.update_chat_input_state()
                self.update_create_channel_btn_state()
            else:
                self.notifications.put("Login failed.")

    def logout(self):
        if not self.username:
            messagebox.showinfo("Not logged in", "You are not logged in.")
            return
        self.send_command("logout")
        # Đợi response trong 3s
        start = time.time()
        resp = None
        while time.time() - start < 3:
            with self.last_response_lock:
                last_resp = self.last_response
            if last_resp and isinstance(last_resp, dict) and last_resp.get("status") == "ok":
                resp = last_resp
                break
            time.sleep(0.05)
        if resp and resp.get("status") == "ok":
            self.notifications.put(f"Logged out user {self.username}.")
            self.username = ""
            if "status_value" in resp:
                self.status = resp["status_value"]
            else:
                self.status = "offline"
            self.update_user_label()
            self.update_status_label()
            self.refresh_channels()
            self.refresh_users()
            self.update_chat_input_state()
            self.update_create_channel_btn_state()
        else:
            self.notifications.put("Logout failed.")

    def create_channel(self):
        name = simpledialog.askstring("Create Channel", "Enter channel name:")
        if name:
            self.send_command(f"create {name}")
            # Đợi response trong 3s
            start = time.time()
            resp = None
            while time.time() - start < 3:
                with self.last_response_lock:
                    last_resp = self.last_response
                if last_resp and isinstance(last_resp, dict) and last_resp.get("status") in ("ok", "error"):
                    resp = last_resp
                    break
                time.sleep(0.05)
            if resp and resp.get("status") == "ok":
                self.notifications.put(f"Created channel '{name}' successfully.")
                self.sync()
                self.refresh_channels()
            else:
                self.notifications.put(f"Failed to create channel '{name}'.")

    def leave_channel(self):
        if self.current_channel:
            self.send_command(f"leave {self.current_channel['name']}")
            # Đợi response trong 3s
            start = time.time()
            resp = None
            while time.time() - start < 3:
                with self.last_response_lock:
                    last_resp = self.last_response
                if last_resp and isinstance(last_resp, dict) and last_resp.get("status") in ("ok", "error"):
                    resp = last_resp
                    break
                time.sleep(0.05)
            self.refresh_channels()
            if resp and resp.get("status") == "ok":
                self.notifications.put(f"Left channel '{self.current_channel['name']}'.")
            else:
                self.notifications.put(f"Failed to leave channel '{self.current_channel['name']}'.")

    def send_message(self, event=None):
        msg = self.message_entry.get().strip()
        if msg and self.current_channel:
            self.send_command(f"send {self.current_channel['name']} {msg}")
            # Đợi response trong 3s
            start = time.time()
            resp = None
            while time.time() - start < 3:
                with self.last_response_lock:
                    last_resp = self.last_response
                if last_resp and isinstance(last_resp, dict) and last_resp.get("status") in ("ok", "error"):
                    resp = last_resp
                    break
                time.sleep(0.05)
            self.message_entry.delete(0, tk.END)
            self.refresh_chat()
            if resp and resp.get("status") == "ok":
                self.notifications.put(f"Message sent to {self.current_channel['name']}.")
            else:
                self.notifications.put(f"Failed to send message to {self.current_channel['name']}.")

    def sync(self):
        self.send_command("sync")
        # Đợi response trong 3s
        start = time.time()
        resp = None
        while time.time() - start < 3:
            with self.last_response_lock:
                last_resp = self.last_response
            if last_resp and isinstance(last_resp, dict) and last_resp.get("status") in ("ok", "error"):
                resp = last_resp
                break
            time.sleep(0.05)
        if resp and resp.get("status") == "ok":
            self.notifications.put("Sync successful.")
        else:
            self.notifications.put("Sync failed.")

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
            # Kiểm tra chỉ số hợp lệ
            if idx < len(self.channels):
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
        else:
            self.current_channel = None
            self.channel_title.config(text="No channel selected")
            self.chat_display.config(state='normal')
            self.chat_display.delete(1.0, tk.END)
            self.chat_display.config(state='disabled')

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
        self.send_command("list")
        # Đợi response trong 2s
        start = time.time()
        resp = None
        while time.time() - start < 2:
            with self.last_response_lock:
                last_resp = self.last_response
            if last_resp and isinstance(last_resp, dict) and last_resp.get("status") == "ok":
                resp = last_resp
                break
            time.sleep(0.05)
        # --- Sửa tại đây: gom trạng thái user ---
        user_status_map = {}
        if resp and "peers" in resp:
            for peer in resp["peers"]:
                uname = peer.get("username")
                status = peer.get("status")
                if uname:
                    # Nếu status là invisible thì coi như offline
                    if status == "invisible":
                        print(f"[UI] [refresh_users] invisible user: {uname}")
                        user_status_map[uname] = "offline"
                    elif status == "online":
                        user_status_map[uname] = "online"
                    else:
                        user_status_map[uname] = "offline"
        # Hiển thị user và trạng thái (bằng chữ)
        for user in users:
            status_text = user_status_map.get(user, "offline")
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
        last_channel = None
        last_message_count = 0
        last_message_statuses = []
        last_status = self.status  # Lưu trạng thái user lần trước
        while True:
            if not self.running:
                break
            try:
                # Handle notifications
                note = self.notifications.get_nowait()
                self.notification_label.config(text=note)
            except queue.Empty:
                pass
            except Exception:
                break  # UI đã bị destroy

            try:
                # Xử lý các response còn lại trong response_queue (nếu có)
                while not self.response_queue.empty():
                    resp = self.response_queue.get_nowait()
                    # --- BẮT THÔNG BÁO TẠO KÊNH TỪ PEER KHÁC ---
                    if isinstance(resp, dict) and resp.get("type") == "join_channel":
                        peer_username = resp.get("username")
                        channel_name = resp.get("channel")
                        # Chỉ thông báo nếu không phải chính mình tạo
                        if peer_username and channel_name and peer_username != self.username:
                            self.notifications.put(f"Người dùng '{peer_username}' đã tạo hoặc tham gia kênh '{channel_name}'.")
                            self.refresh_channels()
                        continue

            except Exception:
                break  # UI đã bị destroy

            # --- Tự động refresh chat khi có thay đổi ---
            if self.current_channel:
                channel_name = self.current_channel['name']
                filepath = os.path.join(DATA_DIR, f"{channel_name}.json")
                try:
                    with open(filepath, "r") as f:
                        data = json.load(f)
                        messages = data.get("messages", [])
                        message_statuses = [(msg.get("timestamp", ""), msg.get("status", "")) for msg in messages]
                        if (
                            channel_name != last_channel or
                            len(messages) != last_message_count or
                            message_statuses != last_message_statuses
                        ):
                            self.refresh_chat()
                            last_channel = channel_name
                            last_message_count = len(messages)
                            last_message_statuses = message_statuses
                except Exception:
                    pass
            else:
                last_channel = None
                last_message_count = 0
                last_message_statuses = []
            # -------------------------------------------

            # --- Chỉ refresh trạng thái user khi status thay đổi ---
            if self.status != last_status:
                self.refresh_users()
                last_status = self.status
            # ------------------------------------------------------

            time.sleep(0.2)

    def on_close(self):
        try:
            self.send_command("exit")
        except Exception:
            pass
        self.running = False
        try:
            self.master.quit()
        except Exception:
            pass
        try:
            self.master.destroy()
        except Exception:
            pass

    def join_channel_by_name(self):
        channel_name = self.join_channel_entry.get().strip()
        if not channel_name:
            messagebox.showwarning("Join Channel", "Please enter a channel name.")
            return
        self.send_command(f"join {channel_name}")
        # Đợi response trong 3s
        start = time.time()
        resp = None
        while time.time() - start < 3:
            with self.last_response_lock:
                last_resp = self.last_response
            if last_resp and isinstance(last_resp, dict) and last_resp.get("status") in ("ok", "error"):
                resp = last_resp
                break
            time.sleep(0.05)
        if resp and resp.get("status") == "ok":
            self.notifications.put(f"Joined channel '{channel_name}'.")
            self.sync()
            self.refresh_channels()
        else:
            self.notifications.put(f"Failed to join channel '{channel_name}'.")

def run_chat_ui(command_queue, response_queue, port=None):
    root = tk.Tk()
    app = ChatUI(root, command_queue, response_queue, port=port)
    root.mainloop()
