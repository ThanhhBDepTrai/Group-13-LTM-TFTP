"""Benchmark TFTP Pro theo blksize/windowsize (RFC 7440).

Script tự dựng một server tạm trong cùng tiến trình, tạo file kích thước xác
định, rồi đo download + upload cho từng cấu hình. Mọi lần chạy đều kiểm tra
SHA-256 để tránh kết luận "nhanh" nhưng mất dữ liệu.

Ví dụ:
    python scripts/benchmark_tftp.py --size 4MiB --runs 3
    python scripts/benchmark_tftp.py --json-out benchmark.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tftp_client import TFTPClient
from tftp_server import ServerConfig, TFTPServer

CASES = [
    ("A - Baseline", 512, 1),
    ("B", 512, 4),
    ("C", 1024, 4),
    ("D", 1456, 8),
    ("E", 1456, 16),
]


def parse_size(text: str) -> int:
    text = text.strip().lower()
    units = {"kib": 1024, "mib": 1024**2, "gib": 1024**3, "kb": 1000, "mb": 1000**2}
    for suffix, multiplier in units.items():
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * multiplier)
    return int(text)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def start_server(root: Path):
    config = ServerConfig(
        host="127.0.0.1",
        port=0,
        root=root,
        timeout=2.0,
        retries=6,
        max_clients=8,
        overwrite=True,
        blksize=512,
        windowsize=32,
        log_level="ERROR",
    )
    server = TFTPServer(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    while server.sock is None:
        time.sleep(0.01)
    return server, thread, server.sock.getsockname()[1]


def run_case(
    host: str,
    port: int,
    source: Path,
    root: Path,
    workdir: Path,
    blksize: int,
    windowsize: int,
    runs: int,
):
    expected_hash = sha256_file(source)
    rows = []

    for direction in ("download", "upload"):
        times: list[float] = []
        timeouts = 0
        retries = 0
        verified = 0

        for run in range(1, runs + 1):
            timeout_marker = {"count": 0}

            def on_log(message: str, marker=timeout_marker) -> None:
                if "Timeout" in message:
                    marker["count"] += 1

            client = TFTPClient(
                host,
                port,
                timeout=2.0,
                retries=6,
                blksize=blksize,
                windowsize=windowsize,
                on_log=on_log,
            )
            out = workdir / f"download-{blksize}-{windowsize}-{run}.bin"
            remote = f"upload-{blksize}-{windowsize}-{run}.bin"
            started = time.perf_counter()
            if direction == "download":
                client.download(source.name, out)
            else:
                client.upload(source, remote)
                out = root / remote
            elapsed = time.perf_counter() - started
            times.append(elapsed)
            timeouts += timeout_marker["count"]
            retries += timeout_marker["count"]
            got_hash = sha256_file(out)
            if got_hash == expected_hash:
                verified += 1
            out.unlink(missing_ok=True)

        total = sum(times)
        speed = (source.stat().st_size * runs / total) / (1024 * 1024) if total else 0
        rows.append(
            {
                "direction": direction,
                "blksize": blksize,
                "windowsize": windowsize,
                "runs": runs,
                "total_seconds": round(total, 4),
                "avg_seconds": round(total / runs, 4),
                "avg_mib_per_second": round(speed, 3),
                "timeouts": timeouts,
                "retries": retries,
                "sha256_verified": f"{verified}/{runs}",
            }
        )
    return rows


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="TFTP Pro benchmark")
    parser.add_argument("--size", default="2MiB", help="Kích thước file, ví dụ 512KiB, 2MiB")
    parser.add_argument("--runs", type=int, default=3, help="Số lần mỗi cấu hình")
    parser.add_argument("--json-out", help="Ghi kết quả JSON ra file")
    args = parser.parse_args(argv)

    if args.runs < 1:
        parser.error("--runs phải >= 1")
    size = parse_size(args.size)

    with tempfile.TemporaryDirectory(prefix="tftp-bench-") as temp:
        root = Path(temp) / "root"
        work = Path(temp) / "work"
        root.mkdir()
        work.mkdir()
        source = root / "benchmark.bin"
        payload = os.urandom(size)
        source.write_bytes(payload)
        expected = hashlib.sha256(payload).hexdigest()

        server, thread, port = start_server(root)
        try:
            print(f"File: {source.name} ({size} bytes), SHA-256={expected[:16]}...")
            print(f"Server: 127.0.0.1:{port}, runs={args.runs}")
            print()
            print(
                f"{'Case':<14}{'Dir':<10}{'Blk':>6}{'Win':>5}"
                f"{'Total(s)':>10}{'Avg(s)':>9}{'MiB/s':>9}{'Timeouts':>9}{'SHA':>9}"
            )
            all_rows = []
            for name, blksize, windowsize in CASES:
                rows = run_case(
                    "127.0.0.1",
                    port,
                    source,
                    root,
                    work,
                    blksize,
                    windowsize,
                    args.runs,
                )
                all_rows.extend([{"case": name, **row} for row in rows])
                for row in rows:
                    print(
                        f"{name:<14}{row['direction']:<10}{row['blksize']:>6}"
                        f"{row['windowsize']:>5}{row['total_seconds']:>10.3f}"
                        f"{row['avg_seconds']:>9.3f}{row['avg_mib_per_second']:>9.2f}"
                        f"{row['timeouts']:>9}{row['sha256_verified']:>9}"
                    )

            if args.json_out:
                Path(args.json_out).write_text(
                    json.dumps(
                        {
                            "file_size": size,
                            "sha256": expected,
                            "runs": args.runs,
                            "results": all_rows,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print(f"\nĐã ghi {args.json_out}")
        finally:
            server.stop()
            thread.join(timeout=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
