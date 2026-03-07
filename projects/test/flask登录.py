from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)

# 模拟数据库中的用户信息
users = {
    "admin": "123456"
}

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if username in users and users[username] == password:
            return "登录成功！"
        else:
            return "用户名或密码错误！"

    # 渲染 HTML 页面
    return '''
        <html>
            <head>
                <meta charset="UTF-8">
                <title>登录页面</title>
            </head>
            <body>
                <h2>用户登录</h2>
                <form method="post">
                    <label for="username">用户名：</label><br>
                    <input type="text" id="username" name="username"><br><br>

                    <label for="password">密码：</label><br>
                    <input type="password" id="password" name="password"><br><br>

                    <input type="submit" value="登录">
                </form>
            </body>
        </html>
    '''

if __name__ == '__main__':
    app.run(debug=True, port=8001)
