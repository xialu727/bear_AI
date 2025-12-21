import os
import dashscope
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from dashscope import Generation

app = Flask(__name__)
app.config['SECRET_KEY'] = 'simple-key'
socketio = SocketIO(app, cors_allowed_origins="*")
dashscope.api_key = os.getenv('qinwen_api_key')

# 用于存储用户会话的生成任务和对话历史
user_generations = {}
user_histories = {}  # 存储用户对话历史，最多保留5轮对话

# 前端页面
@app.route('/')
def index():
    return render_template('index.html')

# WebSocket核心事件：处理AI提问
@socketio.on('ask_ai')
def handle_ask_ai(data):
    user_id = request.sid
    user_input = data.get('message')

    if not user_input:
        emit('ai_response', {'content': '请输入问题'})
        return

    # 清除之前的生成任务
    if user_id in user_generations:
        user_generations[user_id]['stop'] = True

    # 创建新的生成任务
    user_generations[user_id] = {'stop': False}

    # 初始化用户对话历史
    if user_id not in user_histories:
        user_histories[user_id] = []

    # 构建消息列表：系统消息 + 历史对话 + 当前问题
    messages = [{'role': 'system', 'content': "You are a helpful assistant."}]
    
    # 添加历史对话（最多5轮）
    messages.extend(user_histories[user_id])
    
    # 添加当前用户问题
    messages.append({'role': 'user', 'content': user_input})

    # 调用通义千问流式接口
    responses = Generation.call(
        model="qwen-turbo",
        messages=messages,
        result_format='message',
        stream=True,
        incremental_output=True
    )

    # 收集完整的AI回答
    full_answer = ""

    # 逐段推送AI回答
    for res in responses:
        # 检查是否需要停止
        if user_id in user_generations and user_generations[user_id]['stop']:
            break

        if res.status_code == 200:
            content = res.output.choices[0].message.content
            full_answer += content
            safe_content = content.replace("\n", "\\n").replace("\r", "\\r")
            emit('ai_response', {'content': safe_content})
            socketio.sleep(0.05)
        else:
            emit('ai_response', {'content': '出错了'})
            break

    # 将对话加入历史（只在正常结束时）
    if user_id in user_generations and not user_generations[user_id]['stop']:
        user_histories[user_id].append({'role': 'user', 'content': user_input})
        user_histories[user_id].append({'role': 'assistant', 'content': full_answer})
        
        # 保挅10条消息（5轮对话）
        if len(user_histories[user_id]) > 10:
            user_histories[user_id] = user_histories[user_id][-10:]

    emit('ai_response', {'content': '[DONE]'})

# WebSocket事件：停止生成
@socketio.on('stop_generation')
def handle_stop_generation():
    user_id = request.sid
    if user_id in user_generations:
        user_generations[user_id]['stop'] = True

# WebSocket断开连接时清理资源
@socketio.on('disconnect')
def handle_disconnect():
    user_id = request.sid
    if user_id in user_generations:
        del user_generations[user_id]
    # 注意：不删除 user_histories，保留对话历史直到用户关闭浏览器

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)