# Xử lý Mảng/Danh sách quản lý IP
# 1. Danh sách các Client đang kết nối
active_ips = ["192.168.1.2", "192.168.1.5"]

# 2. Client mới kết nối vào Server
new_client = "192.168.1.10"
active_ips.append(new_client)

# 3. Duyệt và kiểm tra danh sách kết nối
print("=== DANH SÁCH CLIENT ONLINE ===")
for index, ip in enumerate(active_ips, start=1):
    print(f"Client #{index}: {ip}")
