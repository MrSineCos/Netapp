# Netapp

## Mô tả ứng dụng

**Netapp** là một ứng dụng chat phân tán theo mô hình peer-to-peer (P2P) có hỗ trợ tracker trung tâm để quản lý trạng thái các peer, kênh chat và đồng bộ tin nhắn. Ứng dụng cho phép nhiều người dùng đăng nhập, tạo/join các kênh chat, gửi/nhận tin nhắn, đồng bộ lịch sử tin nhắn giữa các peer và tracker, và hỗ trợ cả giao diện dòng lệnh (CLI) lẫn giao diện đồ họa (Tkinter UI).

## Cách chạy ứng dụng

### 1. Chuẩn bị môi trường

- Cài đặt Python 3.x.
- Đảm bảo các thư viện tiêu chuẩn Python đã có sẵn (tkinter, multiprocessing, socket, threading, ...).

### 2. Chạy tracker server (bắt buộc)

Ứng dụng cần tracker server để hoạt động. Nếu bạn đã có mã nguồn tracker, hãy chạy tracker trước khi chạy các peer.

Ví dụ (giả sử bạn có file `tracker.py`):

```bash
python tracker.py
```

- Tracker mặc định lắng nghe tại địa chỉ `127.0.0.1` và port `12345`.
- Đảm bảo tracker chạy trước khi khởi động các peer.

### 3. Chạy ứng dụng peer

**Chạy bằng giao diện đồ họa (UI):**

Từ thư mục `Sample_code`, bạn có thể chạy lệnh sau trong terminal/cmd:

```bash
python run_ui.py
```

**Chạy bằng giao diện dòng lệnh (CLI):**

Nếu muốn sử dụng giao diện dòng lệnh, bạn có thể chạy lệnh sau:

```bash
python netapp2\cli.py
```

- Khi được hỏi `Enter port for your node:`, nhập một số port chưa bị chiếm (ví dụ: 5001, 5002, ...).
- Giao diện chat sẽ hiện ra, bạn có thể đăng nhập, tạo/join kênh, gửi tin nhắn, đồng bộ, v.v.

**Lưu ý:**  
- `run_ui.py` là file khởi động nhanh giao diện đồ họa (Tkinter UI) cho ứng dụng peer, giúp đơn giản hóa thao tác chạy app mà không cần chỉnh sửa file `cli.py`.
- Bạn chỉ cần chạy `python run_ui.py` để sử dụng giao diện đồ họa.

**Chạy nhiều peer trên cùng máy:**

- Mỗi cửa sổ terminal chạy một lệnh như trên, chọn port khác nhau cho mỗi peer.
- Có thể đăng nhập bằng các username khác nhau.
- Để mô phỏng nhiều người dùng: tạo các bản copy của app (copy và paste thư mục), rồi tiến hành mở các cửa sổ terminal tương ứng với từng thư mục và chạy app.

**Chạy bằng CLI (nếu muốn):**

- Mở file `cli.py`, bỏ comment dòng `cli_loop(command_queue, response_queue)` và comment dòng `run_chat_ui(command_queue, response_queue)`.
- Chạy lại như trên, bạn sẽ thao tác qua dòng lệnh thay vì giao diện đồ họa.

### 4. Thư mục dữ liệu

- Dữ liệu kênh, tin nhắn được lưu trong thư mục `data/` dưới dạng file JSON.
- Nếu chưa có thư mục này, chương trình sẽ tự tạo khi chạy.

## Cách thức vận hành

- **Tracker**: Một server trung tâm quản lý danh sách peer, trạng thái online/offline, danh sách kênh, host của từng kênh, v.v.
- **Peer/Agent**: Mỗi người dùng chạy một tiến trình agent (node) trên máy của mình, có thể đăng nhập, tạo/join kênh, gửi/nhận tin nhắn.
- **Giao tiếp**: Các peer giao tiếp trực tiếp với nhau qua TCP socket để gửi tin nhắn, đồng bộ lịch sử, hoặc thông qua tracker để lấy danh sách peer/kênh.
- **Đồng bộ**: Tin nhắn được lưu cục bộ tại host của kênh và có thể đồng bộ lên tracker hoặc tới các peer khác khi online.
- **Giao diện**: Có thể sử dụng CLI hoặc UI Tkinter để thao tác với agent.

## Các file và chức năng chính

### 1. `agent.py`
- **Agent**: Lớp trung tâm quản lý trạng thái người dùng, xử lý các lệnh từ UI/CLI, giao tiếp với tracker và các peer khác.
- **Các hàm chính**:
  - `handle_command`: Xử lý lệnh từ UI/CLI (login, logout, join, send, sync, ...).
  - `register_to_tracker`: Đăng ký peer lên tracker, lấy danh sách peer/kênh.
  - `sync_all`: Đồng bộ tin nhắn và kênh với tracker và các peer.
  - `fetch_channel_from_tracker`: Lấy dữ liệu kênh từ tracker.
  - `add_message_to_channel`, `add_message_direct`: Thêm tin nhắn vào kênh cục bộ.
  - `check_online_status`, `check_peer_status`: Kiểm tra trạng thái online của bản thân hoặc peer khác.
  - `request_channel_history`, `get_history_from_tracker`: Yêu cầu lịch sử tin nhắn từ host/tracker.
- **agent_main**: Hàm khởi tạo agent, chạy server peer, lắng nghe lệnh từ UI/CLI qua Queue.

### 2. `chat_ui.py`
- **ChatUI**: Lớp giao diện đồ họa Tkinter cho phép người dùng đăng nhập, tạo/join kênh, gửi tin nhắn, xem trạng thái, đồng bộ, v.v.
- **Các hàm chính**:
  - `login`, `logout`: Đăng nhập/đăng xuất, cập nhật trạng thái giao diện.
  - `refresh_channels`, `refresh_users`, `refresh_chat`: Cập nhật danh sách kênh, user, tin nhắn trên UI.
  - `send_message`, `create_channel`, `leave_channel`, `sync`: Gửi lệnh tương ứng tới agent.
  - `send_command_and_handle_response`: Gửi lệnh tới agent qua Queue, nhận và xử lý phản hồi.
  - `update_user_label`, `update_status_label`: Cập nhật nhãn tên người dùng và trạng thái trên UI.
  - `update_ui_loop`: Luồng nền cập nhật thông báo và phản hồi UI.

### 3. `thread_server.py`
- **Peer Server**: Server TCP cho phép peer nhận tin nhắn, yêu cầu lịch sử, join/leave channel từ các peer khác.
- **Các hàm chính**:
  - `start_peer_server`: Khởi động server lắng nghe kết nối từ peer khác.
  - `handle_peer`: Xử lý từng kết nối đến, nhận/gửi tin nhắn, join/leave, gửi lịch sử, v.v.

### 4. `thread_client.py`
- **send_to_peer**: Hàm gửi một message (dạng JSON) tới peer khác qua TCP socket.

### 5. `cli.py`
- **CLI**: Giao diện dòng lệnh cho phép người dùng thao tác với agent (có thể thay thế bằng UI).
- **Các hàm chính**:
  - `cli_loop`, `visitor_mode_cli`, `authenticated_cli_loop`: Vòng lặp nhận lệnh từ người dùng, gửi tới agent, xử lý phản hồi.
  - `send_command_and_wait`: Gửi lệnh tới agent và chờ phản hồi.
  - `print_help`: Hiển thị hướng dẫn sử dụng.

### 6. `data_manager.py` (không đính kèm ở đây)
- **DataManager**: Quản lý dữ liệu cục bộ về kênh, tin nhắn, thành viên, lưu/đọc file JSON.
- **Message**: Lớp đại diện cho một tin nhắn.

### 7. `run_ui.py`
- **run_ui.py**: File khởi động nhanh giao diện đồ họa Tkinter cho ứng dụng peer.
- Khi chạy `python run_ui.py`, chương trình sẽ tự động khởi tạo các thành phần cần thiết và mở giao diện chat UI mà không cần chỉnh sửa các file khác.
- Thích hợp cho người dùng muốn sử dụng giao diện đồ họa mà không cần thao tác với CLI.

## Quy trình hoạt động cơ bản

1. Người dùng khởi động app, nhập port, agent được tạo và đăng ký với tracker.
2. Người dùng đăng nhập qua UI/CLI, agent cập nhật trạng thái và đồng bộ dữ liệu với tracker.
3. Người dùng tạo/join kênh, gửi tin nhắn, các thao tác này được gửi tới agent, agent xử lý và đồng bộ với các peer/tracker.
4. Tin nhắn được gửi trực tiếp tới các peer trong kênh (nếu online) hoặc lưu pending để gửi sau.
5. Lịch sử tin nhắn được đồng bộ giữa host, tracker và các peer khi cần thiết.
6. Trạng thái online/offline của các peer được cập nhật liên tục qua tracker.

## Lưu ý

- Mỗi peer là một tiến trình độc lập, có thể chạy trên nhiều máy khác nhau.
- Tracker là thành phần trung tâm, có thể được chạy trước hoặc sau các peer (nếu chạy sau thì lúc này các peer được coi là đang offline)
- Dữ liệu kênh/tin nhắn được lưu cục bộ dưới dạng file JSON trong thư mục `data/`.
- Ứng dụng hỗ trợ cả chế độ visitor (khách) và authenticated (đăng nhập).
