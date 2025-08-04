# wx_service.py
import json
import logging
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import requests

from apps.extensions import redis_client
from utils.wx.WXBizMsgCrypt import WXBizJsonMsgCrypt

logger = logging.getLogger(__name__)

stream_msg_map = {}

# 创建全局线程池，最大并发5个线程
thread_pool_executor = ThreadPoolExecutor(max_workers=5)


class WxService:
    def __init__(self, bot_id):
        # config = WeworkBotConfigService.get_bot_config(bot_id)
        self.token = 'your_token'
        self.encoding_aes_key = 'your_encoding_aes_key'
        self.corp_id = 'your_corp_id'
        self.ww_api_key = 'your_dify_app_api_key'
        # 加解密库要求传receiveid（即corp_id）参数，企业自建智能机器人的使用场景里，receiveid直接传空字符串即可
        self.receive_id = self.corp_id  # 请替换为您的企业ID
        self.wx_cpt = WXBizJsonMsgCrypt(self.token, self.encoding_aes_key, self.receive_id)

    def verify_signature(self, msg_signature, timestamp, nonce, encrypt_msg):
        ret, echo_str = self.wx_cpt.VerifyURL(msg_signature, timestamp, nonce, encrypt_msg)
        if ret != 0:
            logger.info(f"Received === 验证失败，{ret}")
            raise Exception('verify signature failed')
        return echo_str

    def decrypt_message(self, msg_signature, timestamp, nonce, encrypt_msg):
        ret, message = self.wx_cpt.DecryptMsg(encrypt_msg, msg_signature, timestamp, nonce)
        if ret != 0:
            logger.info(f"Received === 验证失败，{ret}")
            raise Exception('decrypt message failed')
        return message

    def encrypt_message(self, content, nonce, finish_stats, stream_id=None):
        steam_dict = {
            "finish": finish_stats,
            "content": content,
            "msg_item": []
            # "msg_item": [
            #     {
            #         "msgtype": "image",
            #         "image": {
            #             "base64": "BASE64",
            #             "md5": "MD5"
            #         }
            #     }
            # ],
        }
        if stream_id:
            steam_dict['id'] = stream_id
        stream_msg = {
            "msgtype": "stream",
            "stream": steam_dict
        }
        # logger.info(f"=====》stream_msg: {stream_msg}")
        ret, s_encrypt_msg = self.wx_cpt.EncryptMsg(json.dumps(stream_msg, ensure_ascii=False), nonce)
        if ret != 0:
            logger.info(f"Received === 验证失败，{ret}")
            raise Exception('encrypt message failed')
        return s_encrypt_msg

    def msg_forward(self, user_name, bot_id, content, nonce):
        """
        消息转发到Dify ai应用
        """
        msg_stream_id = str(uuid.uuid4())
        # 使用线程池异步调用ww_ai_api
        thread_pool_executor.submit(WwAppService.ww_ai_api, self.ww_api_key, content, msg_stream_id, user_name, bot_id)
        return self.encrypt_message('', nonce, False, msg_stream_id)

    def get_stream_msg(self, stream_id, nonce):
        """
        根据stream_id获取消息
        """
        finish_stats = False
        msg = ''
        try:
            # 获取队列长度
            msg_list = []
            items = redis_client.lrange(stream_id, 0, -1)
            for item in items:
                decoded_item = item.decode('utf-8') if isinstance(item, bytes) else str(item)
                # 检查是否为结束标志
                if decoded_item == 'stream_msg_finish':
                    finish_stats = True
                    redis_client.expire(stream_id, 30)
                    break
                else:
                    msg_list.append(decoded_item)
            if msg_list:
                msg = ''.join(msg_list)

            # logger.info(f"从缓存中读取key={stream_id}的数据，{msg}")
        except Exception as e:
            logger.info(f"从缓存中读取key={stream_id}的数据失败，{e}")
            finish_stats = True
        return self.encrypt_message(content=msg, nonce=nonce, finish_stats=finish_stats, stream_id=None)


class WwAppService:

    @classmethod
    def get_conversation_id(cls, user_name, bot_id):
        result = redis_client.get(f"{bot_id}:{user_name}")
        return result.decode('utf-8') if result else ""

    @classmethod
    def set_conversation_id(cls, user_name, bot_id, conversation_id):
        redis_client.set(f"{bot_id}:{user_name}", conversation_id, ex=24 * 60 * 60)

    @classmethod
    def ww_ai_api(cls, api_key, query, redis_key, user_name, bot_id):
        url = 'http://127.0.0.1:5001/v1/chat-messages'
        req_data = {
            "inputs": {},
            "query": query,
            "response_mode": "streaming",
            "conversation_id": cls.get_conversation_id(user_name, bot_id),
            "user": "abc-123",
        }
        req_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        try:
            with requests.post(url, json=req_data, headers=req_headers, stream=True) as response:
                conversation_cache_stats = False
                stream_msg_finish_stats = False
                for chunk in response.iter_lines(decode_unicode=True):
                    if chunk:
                        # 解析每一块数据（假设是JSON格式）
                        try:
                            data = json.loads(chunk.replace('data:', ''))
                            if not conversation_cache_stats:
                                cls.set_conversation_id(user_name, bot_id, data.get('conversation_id'))
                                conversation_cache_stats = True
                            # 将数据存入 Redis 队列
                            if data.get('event') == 'message_end':
                                redis_client.rpush(redis_key, 'stream_msg_finish')
                                stream_msg_finish_stats = True
                            else:
                                redis_client.rpush(redis_key, data.get('answer', ''))
                            # logger.info(f"Received stream data: {data.get('answer')}")
                        except Exception as e:
                            logger.error(f"Failed to parse or store stream data：{chunk}，error：{e}")
        except Exception as e:
            logger.error(f"DifyAi对话异常: {e}")
            redis_client.rpush(redis_key, 'stream_msg_finish')
            stream_msg_finish_stats = True
        if not stream_msg_finish_stats:
            redis_client.rpush(redis_key, 'stream_msg_finish')


