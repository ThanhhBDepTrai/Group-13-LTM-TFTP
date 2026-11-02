# Lab PXE với TFTP Pro

Tài liệu này hướng dẫn dựng lab PXE dùng TFTP Pro và DHCP Server độc lập trong
mạng cô lập. TFTP Pro **không** chạy DHCP Server; nó chỉ cung cấp boot file qua
TFTP và quản lý boot profile qua Admin API/GUI.

> Cảnh báo: Chỉ bật DHCP trong mạng Host-only/isolated hoặc môi trường được quản
> lý. Không bật DHCP trên Wi-Fi dùng chung, mạng gia đình hoặc mạng lớp học.
> DHCP sai cấu hình có thể làm gián đoạn toàn mạng.

## 1. Kiến trúc lab

```text
┌──────────────┐     DHCP (IP + next-server + bootfile)      ┌──────────────┐
│ PXE client   │ ◄────────────────────────────────────────── │ DHCP Server  │
│ (VM BIOS/UEFI)│                                            │ độc lập      │
└──────┬───────┘                                            └──────────────┘
       │ TFTP (boot file)
       ▼
┌──────────────┐     Admin API (localhost)                    ┌──────────────┐
│ TFTP Pro     │ ◄────────────────────────────────────────── │ GUI/Admin    │
│ :6969 + :6971│                                             │              │
└──────────────┘                                             └──────────────┘
```

DHCP trả IP và thông tin boot; TFTP trả boot file; TFTP Pro quản lý file/profile.

## 2. Chuẩn bị

- Máy/VM chạy TFTP Pro (Windows/Linux có Python 3.10+).
- DHCP Server độc lập trong mạng cô lập:
  - Windows Server DHCP, RouterOS, pfSense, hoặc `dnsmasq` trên Linux.
  - VMware/VirtualBox Host-only network hoặc mạng NAT cô lập.
- Máy ảo PXE Client với firmware BIOS hoặc UEFI.
- Boot file phù hợp:
  - BIOS: `undionly.kpxe` (iPXE), `pxelinux.0`, hoặc boot file khác.
  - UEFI: `ipxe.efi`, `snponly.efi`, `grubx64.efi`, ...
- File cấu hình iPXE/script nếu dùng iPXE.

TFTP Pro phải bind TFTP trên interface mà PXE client truy cập được:

```powershell
python tftp_pro.py --host 0.0.0.0 --port 6969
```

Admin API giữ mặc định localhost:

```powershell
python tftp_pro.py --host 0.0.0.0 --admin-host 127.0.0.1 --admin-port 6971
```

Nếu Admin API cần truy cập từ máy khác, bind có kiểm soát và đặt mật khẩu mạnh;
không mở ra Internet.

## 3. Chuẩn bị boot file

Thư mục mặc định trong `server_storage`:

```text
server_storage/
  pxe/
    boot/      # boot file BIOS
    efi/       # boot file UEFI
    config/    # script/cfg
```

Tải boot file thật từ dự án iPXE/GRUB/PXELINUX, sau đó upload qua File Manager
hoặc đặt trực tiếp vào thư mục chia sẻ. Không tạo file giả; profile sẽ báo thiếu
file nếu chưa upload.

Ví dụ upload bằng Admin API:

```bash
curl -X POST "http://127.0.0.1:6971/api/files/upload?path=pxe/boot/undionly.kpxe" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @undionly.kpxe
```

## 4. Tạo profile PXE

Profile mặc định `lab-default` được tạo tự động:

```json
{
  "name": "lab-default",
  "description": "Profile PXE mẫu cho lab BIOS/UEFI (iPXE).",
  "bios_file": "pxe/boot/undionly.kpxe",
  "uefi_file": "pxe/efi/ipxe.efi",
  "default": true
}
```

Tạo/chỉnh sửa qua API:

```http
POST /api/pxe/profiles
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "ipxe-lab",
  "description": "iPXE BIOS + UEFI",
  "bios_file": "pxe/boot/undionly.kpxe",
  "uefi_file": "pxe/efi/ipxe.efi",
  "default": true
}
```

Dùng `require_files=1` để bắt buộc boot file tồn tại tại thời điểm lưu:

```http
POST /api/pxe/profiles?require_files=1
```

Chọn profile theo kiến trúc:

```http
GET /api/pxe/select?arch=bios
GET /api/pxe/select?arch=uefi&name=ipxe-lab
```

Phản hồi có `boot_file` và `boot_file_exists`. Kiểm tra tất cả profile:

```http
GET /api/pxe/profiles
```

Trả `issues` cho boot file đang thiếu. Có thể tạo thư mục PXE nếu cần:

```python
PxeManager(storage, "pxe_profiles.json").ensure_directories()
```

hoặc gọi `storage.ensure_layout()` khi khởi động.

## 5. Cấu hình DHCP

DHCP phải trỏ PXE client tới TFTP Pro. Các option thường dùng:

- Option 66 / `next-server`: IP máy chạy TFTP Pro (ví dụ `192.168.56.10`).
- Option 67 / `bootfile-name`: tên boot file (ví dụ `pxe/boot/undionly.kpxe`).
- Nếu dùng iPXE chainloading, DHCP có thể trả `undionly.kpxe` cho BIOS và
  `ipxe.efi` cho UEFI theo architecture.

### Ví dụ dnsmasq (Linux)

```conf
# /etc/dnsmasq.d/pxe-lab.conf
interface=eth1
bind-interfaces
dhcp-range=192.168.56.100,192.168.56.200,255.255.255.0,1h
dhcp-option=option:router,192.168.56.1
dhcp-boot=pxe/boot/undionly.kpxe,pxeserver,192.168.56.10

# UEFI (vendor class matching) - ví dụ đơn giản
dhcp-match=set:efi-x86_64,option:client-arch,7
dhcp-boot=tag:efi-x86_64,pxe/efi/ipxe.efi,pxeserver,192.168.56.10
```

Khởi động lại `dnsmasq` sau khi sửa. Chỉ chạy trên interface lab.

### Windows Server DHCP

Trong DHCP Console:

1. Tạo scope cho mạng lab.
2. Scope Options:
   - 066 Boot Server Host Name: IP TFTP Pro.
   - 067 Bootfile Name: `pxe/boot/undionly.kpxe` (hoặc file UEFI).
3. Nếu cần phân biệt BIOS/UEFI, dùng policy/vendor class tương ứng.

### VirtualBox/VMware

Có thể dùng DHCP built-in của hypervisor cho IP, nhưng boot filename thường cần
DHCP server riêng hoặc cấu hình nâng cao. Trong lab, dễ nhất là dùng `dnsmasq`
trên host-only network.

## 6. Thử nghiệm PXE client

1. Tạo VM với firmware BIOS hoặc UEFI.
2. Kết nối VM vào mạng lab.
3. Bật network boot (PXE) trong firmware.
4. Quan sát:
   - VM nhận IP từ DHCP.
   - VM tải boot file từ TFTP Pro (log TFTP server).
   - Boot file chạy và có thể chainload cấu hình iPXE.
5. Kiểm tra SHA-256 boot file nếu cần qua Admin API.

Với iPXE, có thể để `pxe/config/boot.ipxe` và cấu hình script iPXE để tải kernel/
initrd tiếp theo từ TFTP hoặc HTTP.

## 7. Kiểm tra và khắc phục sự cố

| Triệu chứng | Kiểm tra |
|---|---|
| VM không nhận IP | DHCP server scope/interface, firewall, mạng host-only |
| VM nhận IP nhưng không tải file | Option 66/67, IP TFTP, port UDP 6969, firewall |
| TFTP timeout | TFTP Pro đang chạy, bind đúng interface, client/server cùng subnet |
| File không tồn tại | `GET /api/pxe/profiles` -> `issues`, đường dẫn profile |
| Profile không hợp lệ | Tên profile, ít nhất một boot file, path traversal |
| Boot file sai kiến trúc | Dùng `arch=bios` hoặc `arch=uefi` trong `/api/pxe/select` |
| DHCP ảnh hưởng mạng thật | Dừng DHCP ngay; chỉ chạy trên interface lab |

Log TFTP server cho biết RRQ/WRQ và lỗi file. Packet Analyzer hiển thị RRQ/OACK/
DATA/ACK nếu server và client dùng chung tiến trình.

## 8. Bảo mật lab

- Chỉ chạy DHCP trên mạng cô lập.
- Không expose TFTP/Admin API ra Internet.
- Admin API mặc định localhost; nếu mở LAN, dùng mật khẩu mạnh và firewall.
- Boot file có thể chứa OS installer; kiểm soát truy cập thư mục chia sẻ.
- SHA-256 chỉ xác nhận nội dung file, không thay thế xác thực/mã hóa.

## 9. Checklist nghiệm thu

- [ ] TFTP Pro chạy và Admin API trả `GET /api/health`.
- [ ] Boot file BIOS/UEFI đã upload vào `server_storage/pxe/...`.
- [ ] Profile tồn tại và `boot_file_exists = true`.
- [ ] DHCP trả đúng `next-server` và `bootfile-name`.
- [ ] VM PXE tải boot file thành công.
- [ ] Có log TFTP và kết quả trong tài liệu lab.

## 10. Kiểm thử liên quan

```powershell
python -m pytest tests/test_pxe.py tests/test_storage.py -q
```

Các test kiểm tra profile schema, path validation, chọn BIOS/UEFI, báo file thiếu
và không cho path traversal.
