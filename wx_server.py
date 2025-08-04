# wx_server.py
import json
import logging

from flask import Flask, request, make_response

from wx_service import WxService

logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/wx_callback/<bot_id>', methods=['GET', 'POST'])
def wx_callback(bot_id):
    # logger.info("Received request: %s", request.method)
    wx_service = WxService(bot_id)
    if request.method == 'GET':
        # 验证URL有效性
        signature = request.args.get('msg_signature')
        timestamp = request.args.get('timestamp')
        nonce = request.args.get('nonce')
        echostr = request.args.get('echostr')
        # 解密并验证echostr
        plain_echostr = wx_service.verify_signature(signature, timestamp, nonce, echostr)
        if plain_echostr:
            return make_response(plain_echostr)
        else:
            return make_response('fail')

    elif request.method == 'POST':
        # 处理消息
        msg_signature = request.args.get('msg_signature')
        timestamp = request.args.get('timestamp')
        nonce = request.args.get('nonce')
        # 获取请求数据
        data = request.data.decode('utf-8')
        # logger.info(f"receive——data: {data}")
        try:
            # 解密消息内容
            message = wx_service.decrypt_message(msg_signature, timestamp, nonce, data)
            # logger.info(f"receive——message: {message}")

            # 获取消息信息
            msg_obj = json.loads(message)
            from_user = msg_obj.get("from").get('userid')
            chattype = msg_obj.get('chattype')
            msgid = msg_obj.get('msgid')
            msg_type = msg_obj.get('msgtype')
            if msg_type == 'text':
                # 接收到的文本消息
                content = msg_obj.get("text").get('content')
                # 加密回复消息
                result = wx_service.msg_forward(from_user, bot_id, content, nonce)
                logger.info(f"Received message: {content}, from: {from_user}, type: {msg_type}, chattype: {chattype}")
            elif msg_type == 'stream':
                # 接收到的消息类型为stream，需要获取后续的流式输出，时间范围：6分钟
                steam_id = msg_obj.get("stream").get('id')
                result = wx_service.get_stream_msg(steam_id, nonce)
            else:
                result = wx_service.encrypt_message('抱歉，我无法理解此消息。请重新输入。', from_user, msgid, nonce)
            return make_response(result)
        except Exception as e:
            logger.error(f"处理消息时出错: {e}", exc_info=True)
            return make_response('fail')


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)