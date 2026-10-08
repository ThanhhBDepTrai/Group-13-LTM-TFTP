# TFTP Protocol Visualizer — Nhóm 2

Học phần: Lập trình Mạng • Giao thức TFTP (RFC 1350) • Báo cáo 15 phút

## Cấu trúc repo (chia cho 3 thành viên)

```
/
├── core/               ← TV1 (Nhóm trưởng): logic + FSM + test
│   ├── tftpPackets.js  ← encode/decode 5 gói RRQ/WRQ/DATA/ACK/ERROR
│   ├── fsmEngine.js    ← mô phỏng Stop-and-Wait, Timeout, TID 69→ephemeral
│   └── core.test.js    ← unit test (node core/core.test.js)
├── demo/               ← demo chạy ngay của TV1 (TV2 nâng cấp sau)
│   └── index.html      ← mở bằng browser là chạy
├── ui/                 ← TV2: giao diện nâng cấp (dự kiến)
├── captures/           ← TV3: file .pcapng Wireshark + ảnh đối chiếu
├── README.md
└── slides/             ← TV3: slide thuyết trình
```

## Chạy demo (không cần cài gì)

1. Mở `demo/index.html` bằng Chrome/Edge (double-click là chạy).
2. Bấm **Bắt đầu tải file (RRQ)** → xem DATA/ACK bay qua lại, TID đổi 69 → 50001.
3. Tick **Giả lập mất gói** → xem TIMEOUT gửi lại.
4. Tick **File không tồn tại** → xem ERROR 1.
5. Thử file **1024B** → xem case đặc biệt: gói 0-byte kết thúc.
6. Click từng dòng sự kiện → khung Packet Inspector hiện Hex/Byte.

## Kiểm thử Core

```bash
node core/core.test.js
# expect: 14 test passed
```

## Chuẩn RFC 1350 đã cài

- Opcode: RRQ=1, WRQ=2, DATA=3, ACK=4, ERROR=5
- DATA: Block(2B) + payload 0..512B; `<512B` = gói cuối
- File chia hết cho 512 → thêm gói 0-byte kết thúc
- TID: Client → Server:69 lần đầu, sau đó Server dùng TID ngẫu nhiên, giải phóng 69
- Stop-and-Wait: gửi #n → chờ ACK #n → mới gửi #n+1; timeout → gửi lại

## Quy ước Git (TV1 quản trị)

```bash
main             # bản ổn định để thuyết trình
feature/core-fsm # TV1
feature/ui       # TV2
feature/capture  # TV3
```

Mỗi người commit vào nhánh mình rồi mở Pull Request vào `main`.
