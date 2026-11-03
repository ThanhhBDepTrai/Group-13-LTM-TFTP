# Benchmark TFTP Pro

`scripts/benchmark_tftp.py` đo hiệu năng upload/download của TFTP Pro theo
`blksize` và `windowsize` (RFC 7440). Script tự dựng server tạm trong cùng tiến
trình, tạo file ngẫu nhiên, chạy từng cấu hình nhiều lần và kiểm tra SHA-256 sau
mỗi lần truyền.

Mục tiêu không phải tìm ra "cấu hình nhanh nhất" một cách tuyệt đối, mà tạo
phép đo lặp lại trên cùng máy, cùng mạng và cùng kích thước file để so sánh
tương đối.

## 1. Cách chạy

```powershell
# Mặc định: file 2 MiB, mỗi cấu hình 3 lần
python scripts/benchmark_tftp.py

# File 4 MiB, 5 lần mỗi cấu hình, lưu JSON
python scripts/benchmark_tftp.py --size 4MiB --runs 5 --json-out benchmark.json

# File nhỏ để smoke test
python scripts/benchmark_tftp.py --size 256KiB --runs 1
```

| Option | Mặc định | Mô tả |
|---|---|---|
| `--size` | `2MiB` | Kích thước file; hỗ trợ `KiB`, `MiB`, `GiB`, `KB`, `MB` hoặc số byte |
| `--runs` | `3` | Số lần mỗi chiều cho mỗi cấu hình (phải ≥ 1) |
| `--json-out` | — | Ghi kết quả JSON |

Script không cần server bên ngoài. Nó bind server trên `127.0.0.1` với cổng
ngẫu nhiên, `timeout = 2 s`, `retries = 6`, `max_clients = 8`,
`windowsize tối đa = 32`.

## 2. Các ca kiểm thử

| Ca | Block size | Window size | Mục đích |
|---|---:|---:|---|
| A – Baseline | 512 | 1 | Tương đương stop-and-wait RFC 1350 |
| B | 512 | 4 | Tác động của window khi giữ block nhỏ |
| C | 1024 | 4 | Block lớn hơn + window |
| D | 1456 | 8 | Gần MTU Ethernet, window vừa |
| E | 1456 | 16 | Gần MTU Ethernet, window lớn |

Mỗi ca chạy cả **download** và **upload**. Với mỗi lần:

1. Tạo file nguồn ngẫu nhiên và tính SHA-256.
2. Download file từ server tạm hoặc upload lên server tạm.
3. Tính SHA-256 file nhận và so với file gốc.
4. Ghi thời gian, số timeout/retry.
5. Xóa file tạm trước lần kế tiếp.

## 3. Đọc kết quả

Ví dụ định dạng (số liệu chỉ minh họa):

```text
File: benchmark.bin (2097152 bytes), SHA-256=...
Server: 127.0.0.1:54321, runs=3

Case          Dir            Blk  Win  Total(s)   Avg(s)    MiB/s Timeouts      SHA
A - Baseline  download       512    1     2.100    0.700     2.86        0      3/3
A - Baseline  upload         512    1     2.250    0.750     2.67        0      3/3
B             download       512    4     1.050    0.350     5.71        0      3/3
...
```

| Cột | Ý nghĩa |
|---|---|
| `Case` | Tên ca A–E |
| `Dir` | `download` hoặc `upload` |
| `Blk` | Block size (byte) |
| `Win` | Window size |
| `Total(s)` | Tổng thời gian các lần chạy |
| `Avg(s)` | Thời gian trung bình mỗi lần |
| `MiB/s` | Tốc độ trung bình = tổng byte / tổng thời gian |
| `Timeouts` | Số log chứa "Timeout" |
| `SHA` | Số lần hash khớp / tổng số lần |

`SHA` phải là `runs/runs` cho mọi dòng. Nếu không, kết quả hiệu năng không hợp
lệ để so sánh cho đến khi tìm ra nguyên nhân mất dữ liệu.

## 4. JSON output

```json
{
  "file_size": 2097152,
  "sha256": "…",
  "runs": 3,
  "results": [
    {
      "case": "A - Baseline",
      "direction": "download",
      "blksize": 512,
      "windowsize": 1,
      "runs": 3,
      "total_seconds": 2.1,
      "avg_seconds": 0.7,
      "avg_mib_per_second": 2.86,
      "timeouts": 0,
      "retries": 0,
      "sha256_verified": "3/3"
    }
  ]
}
```

Dùng JSON để vẽ biểu đồ hoặc lưu minh chứng demo. Luôn lưu kèm thông tin máy,
hệ điều hành, loại mạng và thời điểm chạy.

## 5. Cách so sánh hợp lệ

- Chạy trên cùng máy, cùng interface mạng, cùng file size và cùng số lần.
- Chạy khi mạng lab không có tải khác; tránh Wi-Fi chập chờn nếu muốn số liệu ổn
  định.
- Chạy ít nhất 3 lần mỗi cấu hình; lặp lại cả bộ nếu kết quả dao động mạnh.
- So sánh A với B để thấy tác động window, B/C để thấy block size, D/E để thấy
  window lớn gần MTU.
- Kiểm tra `Timeouts` và `SHA` trước khi kết luận cấu hình nào nhanh hơn.
- Loopback (`127.0.0.1`) loại bỏ phần lớn ảnh hưởng mạng thật; kết quả không đại
  diện cho LAN/Wi-Fi. Muốn đo LAN, chạy client/server trên hai máy và ghi rõ
  điều kiện.

## 6. Giới hạn

- Script dùng server nội bộ, không kiểm tra GUI/Admin API/PXE.
- Chỉ đo một file mỗi lần, không mô phỏng nhiều client đồng thời.
- Không đo packet loss chủ động; chỉ ghi timeout/retry thực tế.
- `windowsize` lớn hơn khả năng xử lý của thiết bị/mạng có thể làm tăng retry;
  server mặc định chỉ chấp nhận tối đa 16 block/cửa sổ.
- SHA-256 xác nhận nội dung, không xác nhận bảo mật đường truyền.

## 7. Kiểm thử liên quan

```powershell
python -m pytest tests/test_windowsize.py tests/test_config.py -q
```

Các test kiểm tra thương lượng `windowsize`, fallback về 1, ACK tích lũy,
timeout/duplicate và cấu hình mặc định dùng chung.
