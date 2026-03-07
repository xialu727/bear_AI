import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# 临时写死你的项目目录
BASE_DIR = Path(__file__).resolve().parent
BACKEND_FILE = BASE_DIR / "backend.py"
FRONTEND_DIR = BASE_DIR




BACKEND_PORT = 5001
FRONTEND_PORT = 5173


def install_dependencies():
    try:
        import flask_cors  # noqa: F401
        print("[OK] flask-cors already installed")
    except ImportError:
        print("[INFO] Installing flask-cors ...")
        subprocess.run([sys.executable, "-m", "pip", "install", "flask-cors"], check=True)
        print("[OK] flask-cors installed")


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def wait_port_ready(port: int, timeout: float = 10.0, host: str = "127.0.0.1") -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_port_in_use(port, host):
            return True
        time.sleep(0.2)
    return False


def stop_process(proc: subprocess.Popen, name: str):
    if proc is None:
        return
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
        print(f"[OK] {name} terminated")
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
        print(f"[OK] {name} killed")


def main():
    install_dependencies()

    if not BACKEND_FILE.exists():
        print(f"[ERR] backend.py not found: {BACKEND_FILE}")
        sys.exit(1)

    if is_port_in_use(BACKEND_PORT):
        print(f"[ERR] Port {BACKEND_PORT} is already in use. Please stop old process first.")
        sys.exit(1)

    if is_port_in_use(FRONTEND_PORT):
        print(f"[ERR] Port {FRONTEND_PORT} is already in use. Please stop old process first.")
        sys.exit(1)

    print("=" * 48)
    print("[INFO] Starting separated frontend/backend login demo")
    print(f"[INFO] Backend file: {BACKEND_FILE}")
    print(f"[INFO] Frontend dir : {FRONTEND_DIR}")
    print(f"[INFO] Backend URL  : http://localhost:{BACKEND_PORT}")
    print(f"[INFO] Frontend URL : http://localhost:{FRONTEND_PORT}/index.html")
    print("[INFO] Press Ctrl+C to stop")
    print("=" * 48)

    backend_proc = None
    frontend_proc = None

    try:
        backend_proc = subprocess.Popen(
            [sys.executable, str(BACKEND_FILE)],
            cwd=str(BASE_DIR),
        )

        frontend_proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "http.server",
                str(FRONTEND_PORT),
                "--bind",
                "127.0.0.1",
                "--directory",
                str(FRONTEND_DIR),
            ],
            cwd=str(BASE_DIR),
        )

        backend_ok = wait_port_ready(BACKEND_PORT, timeout=10)
        frontend_ok = wait_port_ready(FRONTEND_PORT, timeout=10)

        if not backend_ok:
            print(f"[ERR] Backend failed to start on port {BACKEND_PORT}")
        if not frontend_ok:
            print(f"[ERR] Frontend failed to start on port {FRONTEND_PORT}")

        if backend_ok and frontend_ok:
            print("[OK] Services started")
            webbrowser.open(f"http://localhost:{FRONTEND_PORT}/index.html")
        else:
            print("[ERR] Startup incomplete, stopping processes...")
            stop_process(frontend_proc, "frontend")
            stop_process(backend_proc, "backend")
            sys.exit(1)

        while True:
            if backend_proc.poll() is not None:
                print("[ERR] Backend process exited unexpectedly")
                break
            if frontend_proc.poll() is not None:
                print("[ERR] Frontend process exited unexpectedly")
                break
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[INFO] Stopping services...")
    finally:
        stop_process(frontend_proc, "frontend")
        stop_process(backend_proc, "backend")
        print("[OK] All services stopped")


if __name__ == "__main__":
    main()
