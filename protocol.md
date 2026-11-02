# Ghi chú giao thức TFTP & API `tftp_packet.py`

Tài liệu này mô tả định dạng gói tin, mã lỗi, option và API công khai của
`tftp_packet.py` (Thành viên 1). Tham chiếu: RFC 1350, 2347, 2348, 2349.

## 1. Định dạng gói tin

Mọi số nguyên là **big-endian** (`!` trong `struct`); `H` = unsigned short 2 byte.
Chuỗi kết thúc bằng một byte `0x00`.

| Loại  | Opcode | Cấu trúc                                                              | Chiều             |
|-------|--------|-----------------------------------------------------------------------|-------------------|
| RRQ   | 1      | `!H` + filename + `0` + mode + `0` + [option `0` value `0`]…          | Client → Server   |
| WRQ   | 2      | giống RRQ                                                             | Client → Server   |
| DATA  | 3      | `!HH` (opcode, block) + dữ liệu (0 … blksize byte)                    | hai chiều         |
| ACK   | 4      | `!HH` (opcode, block) — **đúng 4 byte**                               | hai chiều         |
| ERROR | 5      | `!HH` (opcode, mã lỗi) + thông báo + `0`                              | hai chiều         |
| OACK  | 6      | `!H` + (option `0` value `0`)… — ít nhất một option                   | Server → Client   |

Ví dụ byte thực tế:

```
RRQ  a.txt           00 01 'a.txt' 00 'octet' 00
DATA block 1 "abc"   00 03 00 01 'a' 'b' 'c'
ACK  block 0         00 04 00 00
ERROR 1 "x"          00 05 00 01 'x' 00
OACK blksize=1024    00 06 'blksize' 00 '1024' 00
```

Quy ước của TFTP Pro:

- Tên file mã hóa **UTF-8** (TFTP gốc dùng ASCII nên client khác có thể không hiểu tên tiếng Việt).
- Mode khi **gửi** luôn là `octet`. Khi **nhận**, parser chấp nhận `netascii`/`octet`/`mail`
  (không phân biệt hoa thường) và từ chối mode khác bằng lỗi 4; việc có hỗ trợ `netascii`
  hay không do server/client quyết định (v1.0.0: không).
- Tên option không phân biệt hoa thường; luôn được chuẩn hóa về chữ thường.
- Block number 16-bit: 0…65535. File kết thúc khi gói DATA có độ dài **< blksize** (kể cả 0 byte),
  nên file có kích thước đúng bội số blksize phải gửi thêm một DATA rỗng.

## 2. Mã lỗi

| Mã | Tên chuẩn                 | Dùng trong `tftp_packet.py` khi…                                  |
|----|---------------------------|-------------------------------------------------------------------|
| 0  | Not defined               | (dành cho server/client: exception bất ngờ)                       |
| 1  | File not found            | (server)                                                          |
| 2  | Access violation          | tên file rỗng / chứa NUL / không mã hóa được UTF-8 khi đóng gói   |
| 3  | Disk full                 | (server)                                                          |
| 4  | Illegal TFTP operation    | gói quá ngắn, sai cấu trúc, opcode lạ, mode lạ, block ngoài 0…65535, DATA quá lớn |
| 5  | Unknown transfer ID       | (server/client: gói đến từ IP/port lạ)                            |
| 6  | File already exists       | (server)                                                          |
| 7  | No such user              | không dùng                                                        |
| 8  | Option negotiation failed | option sai tên/giá trị, trùng lặp, không phải ASCII, OACK rỗng    |

`unpack_packet()` **chỉ ném `TFTPError`** với mọi đầu vào `bytes` (đã được fuzz 3000 gói ngẫu nhiên
trong test), nên server có thể `try/except TFTPError` rồi gửi `pack_error(exc.code, exc.message)`.

## 3. Option mở rộng (RFC 2347 / 2348 / 2349)

| Option   | RFC  | Giá trị hợp lệ        | Mặc định | Ghi chú                                                   |
|----------|------|-----------------------|----------|-----------------------------------------------------------|
| blksize  | 2348 | 8 … 65464             | 512      | 1460 an toàn với MTU Ethernet; 8192 có thể bị phân mảnh IP |
| timeout  | 2349 | 1 … 255 (giây)        | 3        | thời gian chờ trước khi gửi lại                           |
| tsize    | 2349 | số nguyên ≥ 0         | —        | RRQ gửi `0`; server trả kích thước thật trong OACK        |

Luồng: client thêm option vào RRQ/WRQ → server trả **OACK** với các option chấp nhận →
client gửi **ACK #0** → truyền với `blksize` đã thống nhất. Nếu server không hiểu option,
nó bỏ qua và trả DATA #1 (RRQ) hoặc ACK #0 (WRQ); client phải xử lý cả hai trường hợp.

`validate_options(options)` kiểm tra giá trị của ba option trên, trả về dict đã chuẩn hóa
(`"0512"` → `"512"`), **bỏ qua** option lạ (đúng RFC 2347) và ném `TFTPError(8)` khi giá trị sai.
Chỉ chấp nhận chữ số ASCII thuần — `"+512"`, `" 512"`, `"-1"`, chữ số Unicode đều bị từ chối.

## 4. API công khai

```python
BLOCK_SIZE = 512
MAX_DATA_SIZE = 65464              # = MAX_BLKSIZE
MAX_PACKET_SIZE = 65468            # buffer đủ cho recvfrom() mọi gói hợp lệ
OP_RRQ, OP_WRQ, OP_DATA, OP_ACK, OP_ERROR, OP_OACK = 1, 2, 3, 4, 5, 6

class TFTPError(Exception):        # .code: int, .message: str

@dataclass
class Packet:                      # opcode, filename, mode, block, data,
    ...                            # error_code, error_msg, options

pack_rrq(filename, options=None) -> bytes
pack_wrq(filename, options=None) -> bytes
pack_data(block, data)           -> bytes
pack_ack(block)                  -> bytes
pack_error(code, message=None)   -> bytes
pack_oack(options)               -> bytes
unpack_packet(raw)               -> Packet
validate_options(options)        -> dict[str, str]     # bổ sung cho W3.1
```

Phần bổ sung so với `docs/interface.md` (cần TV2/TV3 đồng ý khi tạo PR):

- Hằng số `MIN_BLKSIZE`, `MAX_BLKSIZE`, `MIN_TIMEOUT`, `MAX_TIMEOUT`, `ERROR_CODES`, `__version__`.
- Hàm `validate_options()` để server và client dùng chung một luật kiểm tra option.
- `MAX_PACKET_SIZE` = 65468 (theo Bước 5.2) thay vì 516, để server nhận được cả gói blksize lớn.

### Quy tắc kiểm tra khi đóng gói / giải mã

| Hàm              | Điều kiện bị từ chối                                              | Mã |
|------------------|-------------------------------------------------------------------|----|
| `pack_rrq/wrq`   | filename rỗng, chứa `\x00`, không mã hóa được UTF-8               | 2  |
| `pack_rrq/wrq`   | option: tên rỗng, có `\x00`, không ASCII, trùng tên (không phân biệt hoa thường) | 8 |
| `pack_data`      | block ngoài 0…65535 hoặc không phải `int`; dữ liệu > 65464 byte   | 4  |
| `pack_ack`       | block ngoài 0…65535 hoặc không phải `int`                         | 4  |
| `pack_error`     | mã lỗi ngoài 0…65535                                              | 4  |
| `pack_oack`      | không có option nào                                               | 8  |
| `unpack_packet`  | < 2 byte; opcode lạ; ACK ≠ 4 byte; DATA < 4 byte; ERROR < 5 byte; request/OACK thiếu NUL cuối; request thiếu filename/mode; mode lạ; filename không phải UTF-8; option lẻ cặp | 4 |
| `unpack_packet`  | OACK rỗng; tên option rỗng; option trùng; option không phải ASCII | 8  |

`pack_error()` tự loại bỏ `\x00` trong thông báo để gói không bị cắt sớm;
`unpack_packet()` đọc thông báo ERROR đến NUL đầu tiên và vẫn đọc được nếu thiếu NUL cuối.

## 5. Luồng truyền, retry, TID và boundary

### 5.1. RRQ (download)

| Bước | Bên gửi | Gói                     | Nếu gói bị mất                                   |
|------|---------|-------------------------|--------------------------------------------------|
| 1    | Client  | RRQ(filename, octet, options?) | Client hết timeout → gửi lại RRQ          |
| 2    | Server  | [OACK] hoặc DATA #1     | Server hết timeout → gửi lại                     |
| 3    | Client  | [ACK #0 nếu có OACK] → ACK #n | Server thấy không có ACK → gửi lại DATA #n |
| 4    | Server  | DATA #n …               | lặp lại cơ chế timeout/retry                     |
| 5    | Server  | DATA cuối (< blksize)   | Client re-ACK nếu nhận lại DATA cuối             |

### 5.2. WRQ (upload)

| Bước | Bên gửi | Gói                     | Nếu gói bị mất                                   |
|------|---------|-------------------------|--------------------------------------------------|
| 1    | Client  | WRQ(filename, octet, options?) | Client hết timeout → gửi lại WRQ          |
| 2    | Server  | ACK #0 hoặc OACK        | Client không thấy → gửi lại WRQ                  |
| 3    | Client  | DATA #1 …               | Client hết timeout → gửi lại DATA #n             |
| 4    | Server  | ACK #n                  | Server thấy DATA trùng → gửi lại ACK #n          |
| 5    | Client  | DATA cuối (< blksize)   | Server giữ phiên thêm 1–2 s để re-ACK            |

### 5.3. Timeout và retry (stop-and-wait)

- Gửi **một** gói DATA, chờ ACK đúng block đó, rồi mới gửi gói kế tiếp.
- Hết `timeout` (mặc định 3 s) mà chưa có phản hồi → gửi lại đúng gói vừa gửi,
  tối đa `retries` lần (mặc định 5). Quá số lần → báo `TimeoutError`, hủy phiên.
- **Bên nhận** khi timeout gửi lại **ACK gần nhất** (`last_ack`). Chỉ khi chưa nhận được
  DATA #1 thì client mới gửi lại RRQ — gửi lại RRQ ở block > 1 sẽ mở phiên mới.
- ACK/DATA **trùng hoặc cũ** (block ≠ block đang chờ): không gửi block mới;
  bên nhận chỉ re-ACK `last_ack`, không ghi lặp dữ liệu.
- Gói không giải mã được (`TFTPError` từ `unpack_packet`) **không được làm sập phiên**:
  bỏ qua hoặc gửi ERROR, rồi tiếp tục vòng chờ.
- Block tăng theo `(block + 1) & 0xFFFF`: sau 65535 quay về 0. Với blksize 512 nghĩa là
  file lớn hơn khoảng 32 MB cần blksize lớn hơn hoặc kiểm thử riêng.

### 5.4. TID (Transfer ID)

- Mỗi phiên dùng một cặp (IP, cổng UDP) riêng ở mỗi đầu. Server nhận yêu cầu ở cổng chính
  (6969), sau đó **trả lời từ một socket mới bind cổng 0** — cổng đó là TID của server.
- Client ghi lại địa chỉ của gói trả lời đầu tiên làm `tid` và từ đó **chỉ nhận gói từ `tid`**.
- Gói đến từ (IP, cổng) khác `tid` → gửi `ERROR 5 (Unknown transfer ID)` **cho bên gửi gói đó**,
  rồi bỏ qua; phiên chính không bị ảnh hưởng.
- Nhờ TID, nhiều client tải file cùng lúc không bị lẫn gói.

### 5.5. Boundary 0 / 511 / 512 / bội số 512

Quy tắc: **file kết thúc khi gói DATA có độ dài < blksize** (kể cả 0 byte).

| Kích thước file | Các gói DATA (blksize 512)                | Số block |
|-----------------|-------------------------------------------|----------|
| 0 B             | DATA #1 rỗng (0 B)                        | 1        |
| 1 B / 511 B     | DATA #1 (1 B / 511 B)                     | 1        |
| 512 B           | DATA #1 (512 B) + **DATA #2 rỗng (0 B)**  | 2        |
| 513 B           | DATA #1 (512 B) + DATA #2 (1 B)           | 2        |
| 1024 B          | DATA #1, #2 (512 B) + **DATA #3 rỗng**    | 3        |

Hệ quả cho TV2/TV3: dùng điều kiện dừng `len(chunk) < blksize` và **luôn gửi DATA cuối**
dù nó rỗng. Nếu bỏ qua gói rỗng, bên nhận sẽ chờ mãi file có kích thước đúng bội số blksize.
`tftp_packet.py` đã được kiểm thử với DATA 0 / 1 / 511 / 512 / 513 / 1460 / 65464 byte.

## 6. Chạy test

```powershell
pytest tests/test_packet.py -q
ruff check tftp_packet.py tests/test_packet.py
```
