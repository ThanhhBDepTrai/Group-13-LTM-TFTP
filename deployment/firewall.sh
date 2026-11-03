#!/usr/bin/env bash
# Chỉ mở API/TFTP cho mạng LAN tin cậy (đổi CIDR cho phù hợp)
LAN="${1:-192.168.1.0/24}"
ufw allow from "$LAN" to any port 6971 proto tcp
ufw allow from "$LAN" to any port 6969 proto udp
