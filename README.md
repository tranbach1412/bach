# CodeHub 2.0 (Localhost)

Website luyện Competitive Programming: đăng ký/đăng nhập, contest, XP, bảng xếp hạng, trang Admin (tạo contest, duyệt kết quả, đặt lại mật khẩu người dùng).

## Chạy trên Windows
1. Cài Python 3.10 trở lên (tick **Add Python to PATH**).
2. Giải nén file zip, mở thư mục `CodeHub2_Localhost`, bấm thanh địa chỉ, gõ `cmd` rồi Enter.
3. Chạy lần lượt:
```bat
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements.txt
py app.py
```
4. Mở http://127.0.0.1:8000

## Admin lần đầu
Tên đăng nhập `admin`, mật khẩu `Admin@12345`. Hãy đổi mật khẩu ngay ở Bảng điều khiển.
Trang **Người dùng** (`/admin/users`) cho admin đặt lại mật khẩu bất kỳ tài khoản nào.

## Giữ dữ liệu cũ
Muốn giữ tài khoản/điểm/contest cũ, chép file `codehub.db` cũ vào cạnh `app.py` trước khi chạy. Không có file này thì app tự tạo DB mới.

## Đưa lên Internet
Đặt biến môi trường `CODEHUB_ENV=production` và `CODEHUB_SECRET` (chuỗi ngẫu nhiên dài), chạy bằng `gunicorn -w 2 -b 127.0.0.1:8000 app:app` (Linux) hoặc `waitress-serve --listen=127.0.0.1:8000 app:app` (Windows), đặt Nginx/Caddy có HTTPS phía trước. Đổi mật khẩu admin mặc định trước khi mở ra ngoài.
