"""Repeatable loopback throughput comparison; no TUN or system proxy changes.

Run with .venv/Scripts/python scripts/benchmark_traffic.py --output artifacts/traffic.json.
The HTTPS origin and transfer clients run in separate processes so the Python
monitor cannot hold their GIL. The managed modes use Core; they do not render
the GUI. This measures HTTPS transport overhead, not Internet/VPN server capacity.
"""

import argparse
import json
import multiprocessing
import ssl
import statistics
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import yaml

from nodera.core import CREATE_FLAGS, Core
from nodera.flow_monitor import FlowMonitor
from nodera.geodata import prepare_session_geodata
from nodera.models import Profile, Settings
from nodera.ports import choose_ports

ROOT = Path(__file__).resolve().parents[1]
BLOCK = bytes(range(256)) * 4096


class Origin(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        length = int(self.path.strip("/"))
        self.send_response(200)
        self.send_header("Content-Length", str(length))
        self.end_headers()
        while length:
            part = BLOCK[: min(length, len(BLOCK))]
            self.wfile.write(part)
            length -= len(part)

    def do_POST(self):
        remaining = int(self.headers["Content-Length"])
        while remaining:
            part = self.rfile.read(min(remaining, len(BLOCK)))
            if not part:
                return
            if part != BLOCK[: len(part)]:
                self.send_error(400, "Uploaded content differs")
                return
            remaining -= len(part)
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@contextmanager
def monitoring(core, enabled):
    stop = threading.Event()
    ready = threading.Event()
    errors, timings = [], []
    monitor = FlowMonitor()

    def poll():
        next_status = 0
        while not stop.is_set():
            started = time.perf_counter()
            try:
                monitor.update(core.connection_snapshot())
                if started >= next_status:
                    core.status()
                    core.groups()
                    next_status = started + 3
                core.drain_logs()
            except Exception as exc:
                errors.append(type(exc).__name__)
            timings.append(time.perf_counter() - started)
            ready.set()
            stop.wait(max(0, 1 - (time.perf_counter() - started)))

    thread = threading.Thread(target=poll, daemon=True)
    if enabled:
        thread.start()
        if not ready.wait(10):
            stop.set()
            thread.join(timeout=5)
            raise RuntimeError("Monitoring did not start")
    try:
        yield {"errors": errors, "poll_seconds": timings}
    finally:
        stop.set()
        if enabled:
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError("Monitoring thread did not stop")


def transfer(url, proxy, trust, length, direction):
    context = ssl.create_default_context(cafile=trust)
    with httpx.Client(proxy=proxy, verify=context, trust_env=False, timeout=30) as client:
        if direction == "download":
            received = 0
            with client.stream("GET", f"{url}/{length}") as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(len(BLOCK)):
                    if chunk != BLOCK[: len(chunk)]:
                        raise RuntimeError("Downloaded content differs")
                    received += len(chunk)
            if received != length:
                raise RuntimeError("Incomplete download")
        else:

            def chunks():
                remaining = length
                while remaining:
                    chunk = BLOCK[: min(remaining, len(BLOCK))]
                    yield chunk
                    remaining -= len(chunk)

            response = client.post(url, content=chunks(), headers={"Content-Length": str(length)})
            response.raise_for_status()


def run_origin(pipe, stop):
    origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    fixtures = ROOT / "tests/fixtures"
    tls.load_cert_chain(fixtures / "localhost-cert.pem", fixtures / "localhost-key.pem")
    origin.socket = tls.wrap_socket(origin.socket, server_side=True)
    thread = threading.Thread(target=origin.serve_forever, daemon=True)
    thread.start()
    pipe.send(origin.server_port)
    pipe.close()
    try:
        stop.wait()
    finally:
        origin.shutdown()
        origin.server_close()
        thread.join(timeout=5)


@contextmanager
def standalone_core(core, root):
    """Same binary and runtime config, without the application's process manager."""
    root.mkdir()
    prepare_session_geodata(root)
    config = yaml.safe_load((core.runtime / "config.yaml").read_text(encoding="utf-8"))
    mixed, controller = choose_ports(0)
    config.update({"mixed-port": mixed, "external-controller": f"127.0.0.1:{controller}"})
    config_path = root / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    process = subprocess.Popen(
        [core.process.args[0], "-d", str(root), "-f", str(config_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_FLAGS,
    )
    try:
        with httpx.Client(trust_env=False, timeout=1) as client:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Standalone core exited")
                try:
                    response = client.get(
                        f"http://127.0.0.1:{controller}/version",
                        headers={"Authorization": f"Bearer {config['secret']}"},
                    )
                    response.raise_for_status()
                    break
                except httpx.HTTPError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Standalone core did not start") from None
                    time.sleep(0.1)
        yield f"http://127.0.0.1:{mixed}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mib", type=int, default=512, help="MiB per stream and direction")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--streams", type=int, nargs="+", default=[1, 4])
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/traffic-benchmark.json")
    args = parser.parse_args()
    if min(args.mib, args.repeats, *args.streams) <= 0:
        parser.error("All sizes and counts must be positive")
    fixtures = ROOT / "tests/fixtures"
    trust = str(fixtures / "localhost-cert.pem")
    context = multiprocessing.get_context("spawn")
    pipe, child_pipe = context.Pipe(duplex=False)
    stop = context.Event()
    server = context.Process(target=run_origin, args=(child_pipe, stop), daemon=True)
    server.start()
    results = []
    try:
        if not pipe.poll(15):
            raise RuntimeError("Benchmark origin did not start")
        url = f"https://127.0.0.1:{pipe.recv()}"
        pipe.close()
        with tempfile.TemporaryDirectory(prefix="nodera-benchmark-") as temporary:
            core = Core(Path(temporary) / "runtime")
            profile = Profile(
                "Loopback benchmark",
                "",
                {"proxies": [{"name": "Local", "type": "direct"}], "rules": ["MATCH,DIRECT"]},
                "",
            )
            try:
                core.start(profile, Settings(tun=False, system_proxy=False))
                proxy = f"http://127.0.0.1:{core.mixed_port}"
                with (
                    standalone_core(core, Path(temporary) / "standalone") as raw_proxy,
                    ProcessPoolExecutor(
                        max_workers=max(args.streams), mp_context=context
                    ) as executor,
                ):
                    routes = {
                        "direct": None,
                        "mihomo": raw_proxy,
                        "nodera": proxy,
                        "nodera_monitored": proxy,
                    }
                    for concurrency in args.streams:
                        for direction in ("download", "upload"):
                            samples = {name: [] for name in routes}
                            for repeat in range(args.repeats):
                                # Rotate ordering to reduce warmup and background-load bias.
                                names = list(samples)
                                offset = repeat % len(names)
                                names = names[offset:] + names[:offset]
                                for name in names:
                                    route = routes[name]
                                    # Start all client processes before the timed transfer.
                                    warmups = [
                                        executor.submit(
                                            transfer, url, route, trust, len(BLOCK), direction
                                        )
                                        for _ in range(max(args.streams))
                                    ]
                                    for future in warmups:
                                        future.result()
                                    with monitoring(core, name == "nodera_monitored") as metrics:
                                        started = time.perf_counter()
                                        futures = [
                                            executor.submit(
                                                transfer,
                                                url,
                                                route,
                                                trust,
                                                args.mib * 1024 * 1024,
                                                direction,
                                            )
                                            for _ in range(concurrency)
                                        ]
                                        for future in futures:
                                            future.result()
                                        elapsed = time.perf_counter() - started
                                    if metrics["errors"]:
                                        raise RuntimeError(f"Monitor failed: {metrics['errors']}")
                                    mbps = args.mib * 1024 * 1024 * concurrency * 8 / elapsed / 1e6
                                    samples[name].append(round(mbps, 2))
                                    print(
                                        f"{direction} streams={concurrency} {name} {mbps:.1f} Mbps",
                                        flush=True,
                                    )
                            results.append(
                                {
                                    "direction": direction,
                                    "streams": concurrency,
                                    "samples_mbps": samples,
                                    "median_mbps": {
                                        k: statistics.median(v) for k, v in samples.items()
                                    },
                                }
                            )
            finally:
                core.stop("benchmark complete")
            if core.running or core.runtime is not None:
                raise RuntimeError("Core cleanup incomplete")
    finally:
        stop.set()
        server.join(timeout=10)
        if server.is_alive():
            server.terminate()
            server.join(timeout=5)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "transport": "HTTPS loopback, DIRECT via mixed proxy; TUN not tested",
                "monitoring": "Flow snapshots every 1s; status, journals and groups every 3s; no GUI",
                "mib_per_stream": args.mib,
                "repeats": args.repeats,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
