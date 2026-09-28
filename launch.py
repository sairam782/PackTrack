"""Launch PackTrack's API and dashboard; demo by default, or --config for real cameras."""
import argparse
import os
from pathlib import Path
import signal
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import requests

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="measured camera config; disables synthetic demo")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--dashboard-port", type=int, default=8501)
    parser.add_argument("--phone", action="store_true", help="enable paired phones on the local Wi-Fi network")
    parser.add_argument("--phone-port", type=int, default=8020)
    parser.add_argument("--phone-ip", help="LAN IP printed in the pairing link (auto-detected by default)")
    parser.add_argument("--locations", type=Path, default=ROOT / "examples/phone-locations.demo.json")
    parser.add_argument("--phone-cert", type=Path, help="trusted TLS certificate for optional continuous live camera")
    parser.add_argument("--phone-key", type=Path, help="TLS key for --phone-cert")
    args = parser.parse_args()
    ports = [args.api_port, args.dashboard_port] + ([args.phone_port] if args.phone else [])
    if len(set(ports)) != len(ports):
        parser.error("API, dashboard and phone ports must differ")
    if bool(args.phone_cert) != bool(args.phone_key):
        parser.error("--phone-cert and --phone-key must be supplied together")
    if args.phone:
        from phone_scanner import PhoneScanner
        args.locations = args.locations.resolve()
        PhoneScanner(args.locations, "http://127.0.0.1")
    if args.config:
        args.config = args.config.resolve()
        from camera_tracking import Camera
        Camera.load_all(args.config)  # Fail before starting services if config is invalid.
    for port in ports:
        if not 1 <= port <= 65535:
            parser.error("ports must be between 1 and 65535")
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("0.0.0.0" if args.phone and port == args.phone_port else "127.0.0.1", port))
            except OSError:
                parser.error(f"port {port} is in use. Stop the old app or choose --api-port 8010 --dashboard-port 8510")
    if not (ROOT / "assets/qr/test-labels.zip").exists():
        from tools.make_labels import generate
        generate()
    live_mode = bool(args.config or args.phone)
    temporary = tempfile.TemporaryDirectory(prefix="packtrack-demo-") if not live_mode else None
    env = {**os.environ, "PACKTRACK_API_BASE_URL": f"http://127.0.0.1:{args.api_port}",
           "PACKTRACK_DEMO": "0" if live_mode else "1"}
    if args.phone:
        ip = args.phone_ip
        if not ip:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                    # Route selection only; no packet or data is sent.
                    probe.connect(("192.0.2.1", 80))
                    ip = probe.getsockname()[0]
            except OSError:
                parser.error("Could not identify Wi-Fi address; supply --phone-ip with this computer's LAN IP")
        scheme = "https" if args.phone_cert else "http"
        env["PACKTRACK_PHONE_TOKEN"] = secrets.token_urlsafe(32)
        env["PACKTRACK_PHONE_URL"] = f"{scheme}://{ip}:{args.phone_port}/phone"
        env["PACKTRACK_PHONE_LOCATIONS"] = str(args.locations)
        env.setdefault("PACKTRACK_DB_URL", f"sqlite:///{ROOT / 'packtrack-phone.db'}")
        env.setdefault("PACKTRACK_STALE_AFTER", "3600")
    else:
        for key in ("PACKTRACK_PHONE_TOKEN", "PACKTRACK_PHONE_URL", "PACKTRACK_PHONE_LOCATIONS"):
            env.pop(key, None)
    if temporary:
        env["PACKTRACK_DB_URL"] = f"sqlite:///{temporary.name}/demo.db"
        env["PACKTRACK_STALE_AFTER"] = "60"
    phone_labels = None
    if args.phone:
        from tools.make_location_labels import generate
        phone_labels = tempfile.TemporaryDirectory(prefix="packtrack-phone-labels-")
        generate(args.locations, phone_labels.name)
        env["PACKTRACK_PHONE_MARKER_DIR"] = phone_labels.name
    processes = []
    def stop_signal(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_signal)
    try:
        processes.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "api:app", "--host", "127.0.0.1",
                          "--port", str(args.api_port), "--log-level", "warning"], cwd=ROOT, env=env))
        for _ in range(80):
            if processes[0].poll() is not None:
                raise RuntimeError("Tracking API stopped during startup")
            try:
                requests.get(env["PACKTRACK_API_BASE_URL"] + "/dashboard", timeout=1).raise_for_status()
                break
            except requests.RequestException:
                time.sleep(.25)
        else:
            raise RuntimeError("Tracking API did not start")
        processes.append(subprocess.Popen([sys.executable, "-m", "streamlit", "run", "dashboard.py",
                          "--server.address", "127.0.0.1", "--server.port", str(args.dashboard_port),
                          "--server.headless", "true", "--browser.gatherUsageStats", "false"], cwd=ROOT, env=env))
        if args.phone:
            command = [sys.executable, "-m", "uvicorn", "phone_server:app", "--host", "0.0.0.0",
                       "--port", str(args.phone_port), "--log-level", "warning"]
            if args.phone_cert:
                command.extend(["--ssl-certfile", str(args.phone_cert.resolve()), "--ssl-keyfile", str(args.phone_key.resolve())])
            processes.append(subprocess.Popen(command, cwd=ROOT, env=env))
        if args.config:
            processes.append(subprocess.Popen([sys.executable, "track.py", "--config", str(args.config)], cwd=ROOT, env=env))
        url = f"http://127.0.0.1:{args.dashboard_port}"
        for _ in range(80):
            if processes[1].poll() is not None:
                raise RuntimeError("Dashboard stopped during startup")
            try:
                requests.get(url + "/_stcore/health", timeout=1).raise_for_status()
                break
            except requests.RequestException:
                time.sleep(.25)
        else:
            raise RuntimeError("Dashboard did not start")
        print(f"\nPackTrack is ready: {url}", flush=True)
        print("Open this URL in your browser. Keep this terminal running. Ctrl-C stops all services.", flush=True)
        if temporary:
            print("Demo: use 'Load into truck' or 'Auto-play pickups'. Data is temporary; no webcam is opened.\n", flush=True)
        else:
            print("Live mode: using real scans and a persistent application database.\n", flush=True)
        if args.phone:
            print("Phone scanning is enabled. Open 'Connect phone' on the dashboard and scan its pairing QR.", flush=True)
            print(f"Both devices must be on the same Wi-Fi. Phone page: {env['PACKTRACK_PHONE_URL']}", flush=True)
        while all(p.poll() is None for p in processes):
            time.sleep(1)
        raise RuntimeError("A PackTrack service stopped. See its output above.")
    except KeyboardInterrupt:
        print("\nStopping PackTrack.", flush=True)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if temporary:
            temporary.cleanup()
        if phone_labels:
            phone_labels.cleanup()


if __name__ == "__main__":
    main()
