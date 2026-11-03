"""Benchmark TFTP: so sánh blksize / windowsize / mất gói / nhiều client.

Chạy từ thư mục gốc của repo::

    python scripts/benchmark_tftp.py                       # server loopback tự dựng
    python scripts/benchmark_tftp.py --size-mb 32 --windowsizes 1 8 32
    python scripts/benchmark_tftp.py --loss 0 0.02 --timeout 0.05
    python scripts/benchmark_tftp.py --clients 8           # 8 client tải đồng thời
    python scripts/benchmark_tftp.py --host 192.168.1.5 --port 69 --file boot.img
    python scripts/benchmark_tftp.py --csv ket_qua.csv --json ket_qua.json

Mặc định server và client chạy chung một máy qua loopback, nên số liệu phản ánh
chi phí của chính cài đặt (Python), không phải băng thông mạng thật.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tftp_client import TFTPClient  # noqa: E402
from tftp_config import ClientConfig, ServerConfig  # noqa: E402
from tftp_server import TFTPServer  # noqa: E402


@dataclass
class Result:
    direction: str
    blksize: int
    windowsize: int
    loss: float
    runs: int
    mbps_median: float
    mbps_best: float
    seconds_median: float
    retransmits_median: int
    ok: bool


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_matrix(
    host: str,
    port: int,
    source: Path,
    remote_name: str,
    workdir: Path,
    *,
    directions: list[str],
    blksizes: list[int],
    windowsizes: list[int],
    losses: list[float],
    runs: int,
    timeout: float,
    retries: int,
    upload_prefix: str = "bench_up_",
) -> list[Result]:
    expected = sha256(source)
    results: list[Result] = []
    for direction in directions:
        for loss in losses:
            for blksize in blksizes:
                for window in windowsize_list(windowsizes):
                    config = ClientConfig(
                        timeout=timeout, retries=retries, blksize=blksize,
                        windowsize=window, simulate_loss=loss, linger=0.0,
                    )
                    client = TFTPClient(host, port, config)
                    mbps: list[float] = []
                    seconds: list[float] = []
                    retrans: list[int] = []
                    ok = True
                    for i in range(runs):
                        try:
                            if direction == "get":
                                out = workdir / f"dl_{blksize}_{window}_{i}.bin"
                                stats = client.get(remote_name, out)
                                ok &= sha256(out) == expected
                                out.unlink(missing_ok=True)
                            else:
                                name = f"{upload_prefix}{blksize}_{window}_{i}.bin"
                                stats = client.put(source, name)
                        except Exception as exc:  # noqa: BLE001
                            print(f"  lỗi: {exc}", file=sys.stderr)
                            ok = False
                            continue
                        mbps.append(stats.throughput_mbps)
                        seconds.append(stats.duration)
                        retrans.append(stats.retransmits)
                    if mbps:
                        results.append(Result(
                            direction, blksize, window, loss, len(mbps),
                            round(statistics.median(mbps), 2), round(max(mbps), 2),
                            round(statistics.median(seconds), 4),
                            int(statistics.median(retrans)), ok,
                        ))
                        print_row(results[-1])
    return results


def windowsize_list(values: list[int]) -> list[int]:
    return sorted(set(values))


HEADER = f"{'dir':<4} {'blksize':>7} {'window':>6} {'loss':>5} {'Mbit/s':>9} {'best':>9} {'sec':>8} {'retx':>6}  ok"


def print_row(r: Result) -> None:
    print(
        f"{r.direction:<4} {r.blksize:>7} {r.windowsize:>6} {r.loss:>5.2f} "
        f"{r.mbps_median:>9.1f} {r.mbps_best:>9.1f} {r.seconds_median:>8.3f} "
        f"{r.retransmits_median:>6}  {'OK' if r.ok else 'FAIL'}"
    )


def summarize(results: list[Result]) -> None:
    baseline = next((r for r in results if r.blksize == 512 and r.windowsize == 1
                     and r.loss == 0 and r.direction == "get"), None)
    best = max(results, key=lambda r: r.mbps_median, default=None)
    if baseline and best:
        gain = best.mbps_median / baseline.mbps_median if baseline.mbps_median else 0
        print(f"\nTổng kết: nhanh nhất {best.mbps_median:.1f} Mbit/s "
              f"(blksize={best.blksize}, window={best.windowsize}, {best.direction}); "
              f"mốc 512/1 = {baseline.mbps_median:.1f} Mbit/s, gấp {gain:.1f} lần.")


def run_concurrent(host: str, port: int, remote_name: str, workdir: Path, expected: str,
                   clients: int, blksize: int, window: int, timeout: float, retries: int) -> bool:
    """Nhiều client tải cùng một file; kiểm tra hash và tổng thông lượng."""
    outcomes: list[tuple[bool, float, int]] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        config = ClientConfig(timeout=timeout, retries=retries, blksize=blksize,
                              windowsize=window, linger=0.0)
        out = workdir / f"multi_{index}.bin"
        try:
            stats = TFTPClient(host, port, config).get(remote_name, out)
            good = sha256(out) == expected
            with lock:
                outcomes.append((good, stats.duration, stats.bytes))
        except Exception as exc:  # noqa: BLE001
            print(f"  client {index} lỗi: {exc}", file=sys.stderr)
            with lock:
                outcomes.append((False, 0.0, 0))
        finally:
            out.unlink(missing_ok=True)

    import time
    started = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - started
    good = sum(1 for ok, _, _ in outcomes if ok)
    total = sum(b for ok, _, b in outcomes if ok)
    print(f"\n{clients} client đồng thời (blksize={blksize}, window={window}): "
          f"{good}/{clients} đúng hash, {elapsed:.2f}s, "
          f"tổng {total * 8 / elapsed / 1e6:.1f} Mbit/s")
    return good == clients


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--host", help="server ngoài; bỏ trống để tự dựng server loopback")
    p.add_argument("--port", type=int, default=69)
    p.add_argument("--file", help="file có sẵn trên server (chế độ server ngoài)")
    p.add_argument("--size-mb", type=float, default=8.0)
    p.add_argument("--blksizes", type=int, nargs="+", default=[512, 1428, 8192])
    p.add_argument("--windowsizes", type=int, nargs="+", default=[1, 4, 16])
    p.add_argument("--loss", type=float, nargs="+", default=[0.0], help="tỉ lệ mất gói giả lập")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--timeout", type=float, default=0.5)
    p.add_argument("--retries", type=int, default=20)
    p.add_argument("--direction", choices=["get", "put", "both"], default="get")
    p.add_argument("--clients", type=int, default=0, help="chạy thêm bài test N client đồng thời")
    p.add_argument("--csv")
    p.add_argument("--json")
    args = p.parse_args(argv)
    directions = ["get", "put"] if args.direction == "both" else [args.direction]

    with tempfile.TemporaryDirectory(prefix="tftp-bench-") as tmp:
        tmp_path = Path(tmp)
        server = None
        if args.host:
            if not args.file:
                p.error("--file là bắt buộc khi dùng server ngoài")
            host, port, remote = args.host, args.port, args.file
            source = tmp_path / "reference.bin"
            TFTPClient(host, port, ClientConfig(timeout=args.timeout)).get(remote, source)
        else:
            root = tmp_path / "root"
            root.mkdir()
            source = root / "bench.bin"
            source.write_bytes(os.urandom(int(args.size_mb * 1024 * 1024)))
            config = ServerConfig(
                host="127.0.0.1", port=0, root=root, timeout=args.timeout, retries=args.retries,
                max_blksize=65464, max_windowsize=1024, allow_overwrite=True, linger=0.0,
            )
            server = TFTPServer(config).start()
            host, port = server.address
            remote = "bench.bin"

        print(f"File thử: {source.stat().st_size / 1e6:.1f} MB, server {host}:{port}\n")
        print(HEADER)
        try:
            results = run_matrix(
                host, port, source, remote, tmp_path,
                directions=directions, blksizes=args.blksizes, windowsizes=args.windowsizes,
                losses=args.loss, runs=args.runs, timeout=args.timeout, retries=args.retries,
            )
            summarize(results)
            all_ok = all(r.ok for r in results)
            if args.clients:
                all_ok &= run_concurrent(
                    host, port, remote, tmp_path, sha256(source), args.clients,
                    max(args.blksizes), max(args.windowsizes), args.timeout, args.retries,
                )
        finally:
            if server:
                server.stop()

        if args.csv:
            with open(args.csv, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(asdict(results[0])))
                writer.writeheader()
                writer.writerows(asdict(r) for r in results)
            print(f"Đã ghi {args.csv}")
        if args.json:
            with open(args.json, "w", encoding="utf-8") as handle:
                json.dump([asdict(r) for r in results], handle, ensure_ascii=False, indent=2)
            print(f"Đã ghi {args.json}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
