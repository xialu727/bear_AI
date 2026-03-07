from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
app.secret_key = "dev-secret-key"

# 允许前端(5173)跨域访问 /api/*
CORS(app, resources={r"/api/*": {"origins": "*"}})

USERS = {
    "admin": "123456",
    "test": "abc123"
}

# 简单内存会话（演示用）
sessions = {}


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    print(f"[LOGIN] username={username}")

    if USERS.get(username) == password:
        token = username
        sessions[token] = username
        return jsonify({"success": True, "token": token, "username": username})

    return jsonify({"success": False, "message": "用户名或密码错误"}), 401


@app.route("/api/home", methods=["GET"])
def home():
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    print(f"[HOME] token={token}")

    if token in sessions:
        username = sessions[token]
        return jsonify({"success": True, "username": username})

    return jsonify({"success": False, "message": "未登录或令牌无效"}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    print(f"[LOGOUT] token={token}")

    if token in sessions:
        del sessions[token]

    return jsonify({"success": True})


@app.route("/api/test", methods=["GET"])
def test():
    return jsonify({"success": True, "message": "API 正常工作"})


if __name__ == "__main__":
    print("=" * 50)
    print("后端服务启动中...")
    print("访问地址: http://localhost:5001")
    print("API:")
    print("  GET  /api/test")
    print("  POST /api/login")
    print("  GET  /api/home")
    print("  POST /api/logout")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)
