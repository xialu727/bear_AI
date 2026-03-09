import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent
BACKEND_FILE = BASE_DIR / "backend.py"
FRONTEND_DIR = BASE_DIR

BACKEND_PORT = 5001
FRONTEND_PORT = 5173


def install_dependencies():
    try:
        import flask_cors  # noqa: F401
        print("[OK] flask-cors 已经装好了")
    except ImportError:
        print("[i] 正在安装 flask-cors ...")
        subprocess.run([sys.executable, "-m", "pip", "install", "flask-cors"], check=True)
        print("[OK] flask-cors 安装完成")


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
        print(f"[OK] {name} 已停止")
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
        print(f"[OK] {name} 被强制关闭")


def main():
    install_dependencies()

    if not BACKEND_FILE.exists():
        print(f"[X] 找不到 backend.py: {BACKEND_FILE}")
        sys.exit(1)

    if is_port_in_use(BACKEND_PORT):
        print(f"[X] 端口 {BACKEND_PORT} 已经被占用了，先关掉之前的进程吧")
        sys.exit(1)

    if is_port_in_use(FRONTEND_PORT):
        print(f"[X] 端口 {FRONTEND_PORT} 已经被占用了，先关掉之前的进程吧")
        sys.exit(1)

    print("=" * 48)
    print("[i] 正在启动前后端分离的登录演示")
    print(f"[i] 后端文件: {BACKEND_FILE}")
    print(f"[i] 前端目录: {FRONTEND_DIR}")
    print(f"[i] 后端地址: http://localhost:{BACKEND_PORT}")
    print(f"[i] 前端地址: http://localhost:{FRONTEND_PORT}/index.html")
    print("[i] 按 Ctrl+C 可以停止服务")
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
            print(f"[X] 后端没能在端口 {BACKEND_PORT} 上启动起来")
        if not frontend_ok:
            print(f"[X] 前端没能在端口 {FRONTEND_PORT} 上启动起来")

        if backend_ok and frontend_ok:
            print("[OK] 服务都启动好啦！")
            webbrowser.open(f"http://localhost:{FRONTEND_PORT}/index.html")
        else:
            print("[X] 启动没完成，正在关闭进程...")
            stop_process(frontend_proc, "前端")
            stop_process(backend_proc, "后端")
            sys.exit(1)

        while True:
            if backend_proc.poll() is not None:
                print("[X] 后端进程意外退出了")
                break
            if frontend_proc.poll() is not None:
                print("[X] 前端进程意外退出了")
                break
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[i] 正在停止服务...")
    finally:
        stop_process(frontend_proc, "前端")
        stop_process(backend_proc, "后端")
        print("[OK] 所有服务都停掉了")


if __name__ == "__main__":
    main()
