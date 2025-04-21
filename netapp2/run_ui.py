import tkinter as tk
from tkinter import simpledialog, messagebox
from multiprocessing import Queue, Process
from agent import agent_main
from chat_ui import run_chat_ui

def ask_port_dialog():
    """Hiện dialog nhập port, trả về port hợp lệ hoặc None nếu cancel."""
    root = tk.Tk()
    root.withdraw()
    while True:
        port_str = simpledialog.askstring("Nhập Port", "Nhập port (1024-65535):", parent=root)
        if port_str is None:
            return None  # User cancel
        try:
            port = int(port_str)
            if 1024 <= port <= 65535:
                return port
            else:
                messagebox.showerror("Lỗi", "Port phải trong khoảng 1024-65535.", parent=root)
        except Exception:
            messagebox.showerror("Lỗi", "Vui lòng nhập số port hợp lệ.", parent=root)

def main():
    port = ask_port_dialog()
    if port is None:
        print("Chưa nhập port, thoát chương trình.")
        return

    username = ""
    status = "online"
    command_queue = Queue()
    response_queue = Queue()

    agent_proc = Process(target=agent_main, args=(command_queue, port, username, status, response_queue))
    agent_proc.start()

    run_chat_ui(command_queue, response_queue, port)  # Truyền port vào đây

    # Đảm bảo tiến trình agent được kết thúc hoàn toàn khi UI đóng
    if agent_proc.is_alive():
        agent_proc.terminate()
    agent_proc.join()

if __name__ == "__main__":
    main()
