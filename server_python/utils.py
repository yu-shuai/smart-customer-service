# Server/utils.py
import os
import json
import hashlib
import hmac
import datetime
import urllib.parse
from fastapi.responses import JSONResponse

# --- 签名工具类 (严格对齐官方 @volcengine/openapi 的 Signer 实现) ---
# 参考: Server/node_modules/@volcengine/openapi/lib/base/sign.js
# 注意: content-type 在官方实现里属于「不参与签名」的 header,
#       这里必须保持一致, 否则服务端重建的 CanonicalRequest 会与客户端不同。
UNSIGNABLE_HEADERS = [
    'authorization',
    'content-type',
    'content-length',
    'user-agent',
    'presigned-expires',
    'expect',
]

ALGORITHM = 'HMAC-SHA256'
V4_IDENTIFIER = 'request'
DATE_HEADER = 'X-Date'
CONTENT_SHA256_HEADER = 'X-Content-Sha256'


def uri_escape(value):
    """对齐官方 uriEscape: 除 A-Za-z0-9_.~- 外的字符全部百分号编码"""
    return urllib.parse.quote(str(value), safe='')


def serialize_body(body):
    """对齐 JS 的 JSON.stringify: 紧凑分隔符 + 非 ASCII 原样输出(UTF-8)

    必须与 httpx 的 json= 序列化结果一致 (httpx 使用 separators=(',', ':'), ensure_ascii=False),
    否则签名用的 body 哈希与实际发送的 body 不一致, 服务端会返回 SignatureDoesNotMatch。
    """
    if body is None:
        return ''
    if isinstance(body, str):
        return body
    return json.dumps(body, separators=(',', ':'), ensure_ascii=False)


class Signer:
    def __init__(self, request_data, service, region='cn-north-1'):
        self.method = request_data.get('method', 'POST').upper()
        self.path = request_data.get('path', '/')
        # 官方 SDK 在构造时会按 key 排序, 并剔除值为 undefined/null 的参数
        self.params = {
            k: v
            for k, v in sorted(request_data.get('params', {}).items())
            if v is not None
        }
        self.headers = request_data.get('headers', {})
        self.body = request_data.get('body', {})
        self.service = service
        self.region = region
        # 关键: 签名与实际发送必须使用同一份字节, 这里只序列化一次
        self.body_str = serialize_body(self.body)
        self.body_hash = hashlib.sha256(self.body_str.encode('utf-8')).hexdigest()

    def _signable_headers(self):
        """返回 (小写key, 值) 列表, 按小写 key 升序"""
        items = [
            (k.lower(), str(v))
            for k, v in self.headers.items()
            if k.lower() not in UNSIGNABLE_HEADERS
        ]
        return sorted(items, key=lambda item: item[0])

    def _canonical_query_string(self):
        return '&'.join(
            f'{uri_escape(k)}={uri_escape(v)}' for k, v in self.params.items()
        )

    def _canonical_headers(self, signable_headers):
        # 值需要折叠连续空白并去掉首尾空白
        return '\n'.join(
            f'{k}:{" ".join(v.split())}' for k, v in signable_headers
        )

    def add_authorization(self, account_config, date=None):
        ak = account_config.get('accessKeyId')
        sk = account_config.get('secretKey')
        if not ak or not sk:
            return

        # 1. 准备时间 (UTC)
        now = date or datetime.datetime.now(datetime.timezone.utc)
        if now.tzinfo is not None:
            now = now.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        date_str = now.strftime('%Y%m%d')
        ts = now.strftime('%Y%m%dT%H%M%SZ')
        self.headers[DATE_HEADER] = ts
        self.headers[CONTENT_SHA256_HEADER] = self.body_hash

        # 2. 规范化请求 (CanonicalRequest)
        signable_headers = self._signable_headers()
        signed_headers_str = ';'.join(k for k, _ in signable_headers)
        canonical_request = '\n'.join([
            self.method,
            self.path,
            self._canonical_query_string(),
            self._canonical_headers(signable_headers) + '\n',
            signed_headers_str,
            self.body_hash,
        ])

        # 3. StringToSign
        credential_scope = f'{date_str}/{self.region}/{self.service}/{V4_IDENTIFIER}'
        string_to_sign = '\n'.join([
            ALGORITHM,
            ts,
            credential_scope,
            hashlib.sha256(canonical_request.encode('utf-8')).hexdigest(),
        ])

        # 4. 计算签名 Key
        k_date = hmac_sha256(sk.encode('utf-8'), date_str)
        k_region = hmac_sha256(k_date, self.region)
        k_service = hmac_sha256(k_region, self.service)
        k_signing = hmac_sha256(k_service, V4_IDENTIFIER)

        # 5. 计算最终签名
        signature = hmac.new(k_signing, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

        # 6. 构造 Authorization 头
        self.headers['Authorization'] = (
            f'{ALGORITHM} Credential={ak}/{credential_scope}, '
            f'SignedHeaders={signed_headers_str}, Signature={signature}'
        )

def hmac_sha256(key, msg):
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

def key_map(lower_key, headers):
    """找到原始 header key"""
    for k in headers.keys():
        if k.lower() == lower_key:
            return k
    return lower_key

# --- 业务工具函数 ---

def read_files(directory, suffix='.json'):
    scenes = {}
    abs_dir = os.path.join(os.path.dirname(__file__), directory)
    if not os.path.exists(abs_dir):
        return scenes
        
    for filename in os.listdir(abs_dir):
        if filename.endswith(suffix):
            filepath = os.path.join(abs_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    key = filename.replace(suffix, '')
                    scenes[key] = data
            except Exception as e:
                print(f"Error reading {filename}: {e}")
    return scenes


# 成功 {
#     "ResponseMetadata": { "Action": "getScenes" },
#     "Result": { "scenes": [...] } # logic_func 返回的实际数据
# }

# 失败 {
#     "ResponseMetadata": {
#         "Action": "getScenes",
#         "Error": {
#             "Code": -1,
#             "Message": "Custom 不存在, 请先在 Server/scenes 下定义该场景的 JSON." # 异常的具体描述
#         }
#     }
# }

# 统一响应封装 (替代原 wrapper)
async def response_wrapper(api_name, logic_func, contain_metadata=True):
    response_metadata = {"Action": api_name}
    try:
        res = await logic_func()
        if contain_metadata:
            return {"ResponseMetadata": response_metadata, "Result": res}
        return res
    except Exception as e:
        print(f"\x1b[31mError in {api_name}: {e}\x1b[0m")
        response_metadata["Error"] = {
            "Code": -1,
            "Message": str(e)
        }
        return JSONResponse(content={"ResponseMetadata": response_metadata})

def assert_val(expression, msg):
    if not expression or (isinstance(expression, str) and ' ' in expression):
        print(f"\x1b[31m校验失败: {msg}\x1b[0m")
        raise ValueError(msg)