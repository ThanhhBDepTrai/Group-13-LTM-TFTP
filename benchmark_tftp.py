"""Benchmark TFTP Pro theo blksize/windowsize (RFC 7440).

Có hai cách dùng:

1. CLI: tự dựng một server tạm trong cùng tiến trình, tạo file kích thước xác
   định, rồi đo download + upload cho từng cấu hình (``CASES``).
2. Thư viện: ``run_benchmark_suite(host, port, ...)`` chạy trên một server đang
   chạy sẵn và trả về dữ liệu thuần (list dict) để GUI vẽ biểu đồ cột.

Mọi lần chạy đều kiểm tra SHA-256 để tránh kết luận "nhanh" nhưng mất dữ liệu.

Ví dụ:
    python scripts/benchmark_tftp.py --size 4MiB --runs 3
    python scripts/benchmark_tftp.py --suite --size 2MiB
    python scripts/benchmark_tftp.py --json-out benchmark.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tftp_client import TFTPClient

logger = logging.getLogger(__name__)

CASES = [
    ("A - Baseline", 512, 1),
    ("B", 512, 4),
    ("C", 1024, 4),
    ("D", 1456, 8),
    ("E", 1456, 16),
]

DEFAULT_WINDOW_SIZES = (1, 4, 8, 16)


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
    # Import muộn để GUI chỉ cần gọi run_benchmark_suite() mà không phải nạp
    # toàn bộ server khi không dựng server tạm.
    from tftp_server import ServerConfig, TFTPServer

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


def run_benchmark_suite(
    host: str,
    port: int,
    file_size_mb: float = 2,
    window_sizes: Sequence[int] | None = None,
    *,
    blksize: int = 1456,
    timeout: float = 2.0,
    retries: int = 6,
    authorize: Callable[[str, str], None] | None = None,
    cleanup: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> list[dict]:
    """Chạy benchmark theo windowsize trên server ``host:port`` đang chạy.

    Với mỗi windowsize: upload một file ngẫu nhiên ``file_size_mb`` MiB rồi
    download lại chính file đó và so SHA-256 với bản gốc.

    Trả về list dict thuần (GUI có thể vẽ biểu đồ cột trực tiếp)::

        [{"windowsize": 1, "duration_s": 0.52, "throughput_mbps": 64.5,
          "retries": 0, "sha256_status": "VERIFIED"}, ...]

    - ``duration_s``: tổng thời gian upload + download (không tính thời gian
      xin vé).
    - ``throughput_mbps``: megabit/giây = (2 × kích thước file × 8) / 1e6 /
      ``duration_s``; bằng 0.0 nếu lượt chạy thất bại.
    - ``retries``: số lần Timeout/gửi lại mà client ghi nhận.
    - ``sha256_status``: ``VERIFIED``, ``MISMATCH`` hoặc ``FAILED`` (khi có
      lỗi truyền; khi đó dict có thêm khóa ``error``).

    Tham số tùy chọn:
    - ``authorize(filename, mode)``: gọi trước mỗi lần truyền (mode là
      ``"write"`` hoặc ``"read"``), dùng để xin vé 1 lần qua
      ``POST /api/v1/transfers/authorize``.
    - ``cleanup(remote_name)``: dọn file benchmark trên server sau mỗi lượt
      (TFTP không có lệnh xóa nên mặc định file vẫn nằm lại trong root).
    - ``on_progress(index, total, windowsize)``: báo tiến độ cho thanh tiến
      trình GUI trước mỗi lượt.
    """
    sizes = [int(value) for value in (window_sizes or DEFAULT_WINDOW_SIZES)]
    if any(value < 1 for value in sizes):
        raise ValueError("window_sizes phải gồm các số nguyên >= 1")
    size_bytes = int(float(file_size_mb) * 1024 * 1024)
    if size_bytes <= 0:
        raise ValueError("file_size_mb phải > 0")

    payload = os.urandom(size_bytes)
    expected_hash = hashlib.sha256(payload).hexdigest()
    run_id = uuid.uuid4().hex[:8]
    results: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="tftp-suite-") as temp:
        work = Path(temp)
        source = work / "source.bin"
        source.write_bytes(payload)

        for index, windowsize in enumerate(sizes):
            if on_progress is not None:
                on_progress(index, len(sizes), windowsize)

            remote = f"bench-{run_id}-w{windowsize}.bin"
            downloaded = work / f"download-w{windowsize}.bin"
            marker = {"count": 0}

            def on_log(message: str, marker=marker) -> None:
                if "Timeout" in message:
                    marker["count"] += 1

            client = TFTPClient(
                host,
                port,
                timeout=timeout,
                retries=retries,
                blksize=blksize,
                windowsize=windowsize,
                on_log=on_log,
            )

            elapsed = 0.0
            status = "VERIFIED"
            error: str | None = None
            try:
                if authorize is not None:
                    authorize(remote, "write")
                started = time.perf_counter()
                client.upload(source, remote)
                elapsed += time.perf_counter() - started

                if authorize is not None:
                    authorize(remote, "read")
                started = time.perf_counter()
                client.download(remote, downloaded)
                elapsed += time.perf_counter() - started

                if sha256_file(downloaded) != expected_hash:
                    status = "MISMATCH"
            except Exception as exc:  # noqa: BLE001 - báo lỗi về cho GUI
                status = "FAILED"
                error = f"{type(exc).__name__}: {exc}"
            finally:
                downloaded.unlink(missing_ok=True)
                if cleanup is not None:
                    try:
                        cleanup(remote)
                    except Exception as exc:  # noqa: BLE001 - dọn dẹp không được làm hỏng kết quả
                        logger.debug("Bỏ qua lỗi dọn dẹp %s: %s", remote, exc)

            throughput = 0.0
            if status != "FAILED" and elapsed > 0:
                throughput = size_bytes * 2 * 8 / 1_000_000 / elapsed
            row = {
                "windowsize": windowsize,
                "duration_s": round(elapsed, 4),
                "throughput_mbps": round(throughput, 3),
                "retries": marker["count"],
                "sha256_status": status,
            }
            if error is not None:
                row["error"] = error
            results.append(row)

    return results


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


def _print_suite(results: list[dict]) -> None:
    print(
        f"{'Win':>5}{'Duration(s)':>13}{'Mbit/s':>10}{'Retries':>9}{'SHA-256':>11}"
    )
    for row in results:
        print(
            f"{row['windowsize']:>5}{row['duration_s']:>13.3f}"
            f"{row['throughput_mbps']:>10.2f}{row['retries']:>9}"
            f"{row['sha256_status']:>11}"
        )
        if "error" in row:
            print(f"      ! {row['error']}")


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
    parser.add_argument(
        "--suite",
        action="store_true",
        help="Chạy run_benchmark_suite() (windowsize 1/4/8/16) thay cho CASES",
    )
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
            if args.suite:
                print(f"Server: 127.0.0.1:{port}, file={size} bytes")
                print()
                results = run_benchmark_suite(
                    "127.0.0.1", port, file_size_mb=size / (1024 * 1024)
                )
                _print_suite(results)
                if args.json_out:
                    Path(args.json_out).write_text(
                        json.dumps(
                            {"file_size": size, "results": results}, indent=2
                        ),
                        encoding="utf-8",
                    )
                    print(f"\nĐã ghi {args.json_out}")
                return 0 if all(
                    row["sha256_status"] == "VERIFIED" for row in results
                ) else 1

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
