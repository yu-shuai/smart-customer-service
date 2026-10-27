import uuid
import time
import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any

from config import settings
from services.llm_service import llm_service
from services.token_build import AccessToken, PRIVILEGES
from services.utils import Signer  # 确保 utils.py 已移动到 services 目录

from fastapi.responses import JSONResponse

from fastapi import Request
from fastapi.responses import StreamingResponse  # <--- 必须导入这个
import json
from services.rag_service import rag_service  # <--- 新增这行

# 在你的 settings.py 或 main.py 顶部
from dotenv import load_dotenv

load_dotenv()  # 必须先执行这一行，后面的 settings 才能读到值

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- 1. 获取场景 (前端展示用) ---

# 房间 / 用户 ID 常量：Token、RTCConfig、StartVoiceChat 三处必须完全一致
ROOM_ID = "ChatRoom01"
USER_ID = "Huoshan01"

# 火山「语音技术」的应用 AppId，用于 ASR(语音识别) / TTS(语音合成)。
# 注意：它和 RTC AppId 是两回事，填错不会让 StartVoiceChat 报错，
# 但 Agent 会因为无法识别/合成语音而静默失败，前端表现就是一直停在「AI 准备中」。
# 当前值取自项目内其它两处场景配置（Server/scenes/Custom.json、server_python/scenes/Custom.json）的官方公共值。
# 若要用自己的语音应用，请到 https://console.volcengine.com/speech/app 复制 AppId 替换。
VOLC_SPEECH_APP_ID = "1035261036"


def build_rtc_token() -> str:
    """
    签发前端加入房间所需的 RTC Token。

    优先级：
      1. .env 中显式配置的 RTC_TOKEN（现成 Token，直接用）
      2. 用 RTC_APP_ID + RTC_APP_KEY 动态签发

    注意：Token 内部前 24 位就是 AppId，必须与下发给前端的 AppId 一致，
    否则 RTC 会直接返回 token_error / RoomJoin._joinRoomFailed。
    """
    if settings.RTC_TOKEN:
        return settings.RTC_TOKEN

    app_key = settings.RTC_APP_KEY or ""
    # AppKey 若是 "001" + 24位AppId + base64 的形态，说明填错成了 Token
    if app_key.startswith("001") and app_key[3:27] == (settings.RTC_APP_ID or ""):
        print(
            "⚠️  RTC_APP_KEY 看起来是一个 Token（格式为 001+AppId+base64），而不是真实 AppKey。\n"
            "   请到 https://console.volcengine.com/rtc/aigc/listRTC 复制真实 AppKey，"
            "或把现成 Token 填到 .env 的 RTC_TOKEN。\n"
            "   否则动态签发的 Token 签名不合法，前端仍会报 token_error。"
        )

    token_builder = AccessToken(settings.RTC_APP_ID, app_key, ROOM_ID, USER_ID)
    token_builder.add_privilege(PRIVILEGES["PrivSubscribeStream"], 0)
    token_builder.add_privilege(PRIVILEGES["PrivPublishStream"], 0)
    token_builder.expire_time(int(time.time()) + 3600 * 24)
    return token_builder.serialize()


@app.post("/getScenes")
async def get_scenes(request: Request):
    # 签发 RTC Token
    token = build_rtc_token()

    # 构造返回结构
    return {
        "ResponseMetadata": {"Action": "getScenes"},
        "Result": {
            "scenes": [
                {
                    "scene": {
                        # --- 补全的核心字段 ---
                        "id": "Custom",  # 建议改为 Custom，通常前端会根据这个 ID 做特殊处理
                        "name": "自定义助手",
                        "botName": "AiAgent",
                        "icon": "https://lf3-rtc-demo.volccdn.com/obj/rtc-aigc-assets/DoubaoAvatar.png",  # 补全图标
                        # --- 功能开关 ---
                        "isInterruptMode": True,  # 是否支持打断
                        "isVision": False,  # 补全：是否开启视觉（摄像头）
                        "isScreenMode": False,  # 补全：是否开启屏幕共享
                        # --- 数字人相关 (无数字人时设为 None/null) ---
                        "isAvatarScene": None,
                        "avatarBgUrl": None,
                    },
                    "rtc": {
                        "AppId": settings.RTC_APP_ID,
                        "RoomId": ROOM_ID,
                        "UserId": USER_ID,
                        # 必须用上面签发出来的 Token，且其内嵌 AppId 要与 AppId 一致
                        "Token": token,
                    },
                    # 这里的配置主要是为了兼容前端透传，实际生效主要看 proxy
                    "VoiceChat": {},
                }
            ]
        },
    }


# --- 2. 拦截前端的 StartVoiceChat 请求 (核心配置下发) ---
# main.py 核心修改
# rag_llm_server/main.py


@app.post("/proxy")
async def proxy(request: Request):
    """
    完全硬编码的代理接口，用于测试链路是否畅通
    """
    action = request.query_params.get("Action")
    version = request.query_params.get("Version", "2024-12-01")

    # 打印前端实际传过来的数据，方便观察
    try:
        incoming_body = await request.json()
        print(f"DEBUG: 收到前端请求 {action}, Body: {incoming_body}")
    except:
        pass

    # --- 开始硬编码数据 ---
    # 注意：这里的 AppId, RoomId, UserId 必须与 /getScenes 下发给前端的一致，
    # 否则 AI Agent 会被拉进另一个房间，前端听不到声音。
    target_app_id = settings.RTC_APP_ID
    target_room_id = ROOM_ID
    target_user_id = USER_ID

    request_body = {}

    print(f"RTCCCCC  callback {settings.SERVER_URL}/api/chat_callback")
    if action == "StartVoiceChat":
        request_body = {
            "AppId": target_app_id,
            "RoomId": target_room_id,
            "TaskId": "ChatTask01",
            "AgentConfig": {
                "TargetUserId": [target_user_id],
                "WelcomeMessage": "我是余小智，你的专属课程顾问，有什么问题尽管问我吧，我比余帅更强",
                "UserId": "AiAgent",
                "EnableConversationStateCallback": True, 
            },
            "Config": {
                "ASRConfig": {
                    "Provider": "volcano",
                    "ProviderParams": {
                        "Mode": "smallmodel",
                        "AppId": VOLC_SPEECH_APP_ID,
                        "Cluster": "volcengine_streaming_common",
                    },
                },
                "TTSConfig": {
                    "Provider": "volcano",
                    "ProviderParams": {
                        "app": {"appid": VOLC_SPEECH_APP_ID, "cluster": "volcano_tts"},
                        "audio": {
                            "voice_type": "BV001_streaming",
                            "speed_ratio": 1,
                            "pitch_ratio": 1,
                            "volume_ratio": 1,
                        },
                    },
                },
                "LLMConfig": {
                    # 先用 Custom 模式测试你的回调地址
                    "Mode": "CustomLLM",
                    "Url": f"{settings.SERVER_URL}/api/chat_callback",
                    "Method": "POST",
                    "ApiType": "https"
                    if str(settings.SERVER_URL).startswith("https")
                    else "http",
                },
                "InterruptMode": 0,
            },
        }
    elif action == "StopVoiceChat":
        request_body = {
            "AppId": target_app_id,
            "RoomId": target_room_id,
            "TaskId": "ChatTask01",
        }
    else:
        # 其他 Action 直接返回前端传的内容
        request_body = incoming_body

    # --- 签名与发送 ---
    host = "rtc.volcengineapi.com"
    open_api_request_data = {
        "method": "POST",
        "path": "/",
        "params": {"Action": action, "Version": version},
        "headers": {"Host": host, "Content-Type": "application/json"},
        "body": request_body,
    }

    # 这里的 AK/SK 必须拥有调用 RTC OpenAPI 的权限
    account_config = {"accessKeyId": settings.VOLC_AK, "secretKey": settings.VOLC_SK}

    signer = Signer(open_api_request_data, "rtc")
    signer.add_authorization(account_config)

    url = f"https://{host}?Action={action}&Version={version}"

    # print(f"DEBUG: 发送请求到 {url} callback rtc")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            headers=open_api_request_data["headers"],
            # 签名与实际发送必须是同一份字节, 因此直接用 signer.body_str
            content=signer.body_str.encode("utf-8"),
            timeout=30.0,
        )
        result = resp.json()
        print(f"DEBUG: 火山引擎返回结果: {result}")
        return result


# --- 3. 业务回调接口 (RTC -> 这里) ---


# ... 其他代码 ...


@app.post("/api/chat_callback")
async def chat_callback(request: Request):
    try:
        data = await request.json()
    except:
        return {"text": ""}

    print(f"======================== 流式请求", data)

    messages = data.get("messages", [])

    # 校验逻辑 (保持不变)
    if not messages or messages[-1].get("role") != "user":
        print("⚠️ 忽略：非用户主动发言")
        return {"text": ""}

    # --- 定义 SSE 生成器 ---
    async def generate_sse():
        # 1. 调用 LLM 的流式方法
        # 注意：这里是同步生成器还是异步取决于 SDK，Ark SDK 默认是同步 iterator，
        # 但在 FastAPI 的 async def 中，通常可以直接遍历

        rag_content = await rag_service.retrieve(messages[-1].get("content", ""))

        stream_iterator = llm_service.chat_stream(messages, rag_content)

        for chunk in stream_iterator:
            if chunk:
                # Ark SDK 的 chunk 是一个对象 (ChatCompletionChunk)
                # 我们直接用 model_dump_json() 把它转成 JSON 字符串
                # 这完全符合 RTC 要求的 OpenAI 兼容格式
                chunk_json = chunk.model_dump_json()

                # 2. 构造 SSE 协议格式： "data: {json数据}\n\n"
                yield f"data: {chunk_json}\n\n"

        # 3. 循环结束后，必须发送结束符 (RTC 要求的)
        yield "data: [DONE]\n\n"

    # --- 返回流式响应 ---
    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",  # <--- 必须是这个 Header
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 如果存在跨域问题，可以加上 Access-Control-Allow-Origin
            "Access-Control-Allow-Origin": "*",
        },
    )


from typing import List, Optional


# 1. 定义消息模型
class ChatMessage(BaseModel):
    role: str  # "user" 或 "assistant"
    content: str


class DebugRequest(BaseModel):
    history: Optional[List[ChatMessage]] = []
    question: str


# 2. 调试接口
@app.post("/debug/chat")
async def debug_chat(request: DebugRequest):


    # 构造当前发送给 LLM 的消息列表
    current_messages = []
    for msg in request.history:
        current_messages.append({"role": msg.role, "content": msg.content})

    # 放入用户最新问题
    current_messages.append({"role": "user", "content": request.question})

    async def generate_text():
        full_ai_response = ""
        total_usage = None

            # 1. 记录总开始时间
        start_t = time.time()
        # 查询知识库
        rag_content = await rag_service.retrieve(request.question)

        rag_duration = time.time() - start_t

        print(f"DEBUG: 知识库查询耗时: {rag_duration:.2f}s")
        # print(f"DEBUG: 知识库返回检索内容: {rag_content}")

        # 2. 记录 LLM 调用开始时间
        llm_start_t = time.time()

        # 调用 llm_service
        stream = llm_service.chat_stream(current_messages, rag_content)

        for chunk in stream:
            if chunk and chunk.choices:
                delta = chunk.choices[0].delta
                if delta.content:
                    content = delta.content
                    full_ai_response += content  # 累积 AI 的回答
                    yield content
            # 记录 Token 消耗
            if hasattr(chunk, "usage") and chunk.usage:
                total_usage = chunk.usage

        # 3. 记录 LLM 调用耗时
        llm_duration = time.time() - llm_start_t
        print(f"DEBUG: LLM 调用耗时: {llm_duration:.2f}s")

        if total_usage:
            print(
                f"🎫 Token 统计: Total={total_usage.total_tokens} (P:{total_usage.prompt_tokens}, C:{total_usage.completion_tokens})"
            )

        # --- 重点：在流结束后构造并打印 history 结构 ---
        # 构造完整的 history 列表
        new_history = []
        # 添加旧历史
        for m in request.history:
            new_history.append({"role": m.role, "content": m.content})
        # 添加最新的一轮对话
        new_history.append({"role": "user", "content": request.question})
        new_history.append({"role": "assistant", "content": full_ai_response})

        # 打印到控制台，方便你直接复制
        print("\n" + "=" * 50)
        print("🐞 调试完成！以下是可用于下次请求的 history 结构：")
        print(json.dumps({"history": new_history}, ensure_ascii=False, indent=2))
        print("=" * 50 + "\n")

    return StreamingResponse(generate_text(), media_type="text/plain")


# ... 其他导入保持不变 ...
from services.rag_service import rag_service  # 确保已导入 rag_service


# --- 新增：知识库调试接口 ---
@app.get("/debug/rag")
async def debug_rag(query: str):
    """
    调试接口：直接返回知识库检索到的原始文本内容
    用法：浏览器访问 http://127.0.0.1:8000/debug/rag?query=你的问题
    """
    if not query:
        return {"error": "请提供 query 参数"}

    print(f"🔍 [Debug] 正在检索知识库: {query}")

    # 调用我们在 rag_service.py 中实现的异步 retrieve 方法
    context = await rag_service.retrieve(query)

    return {
        "query": query,
        "retrieved_context": context,
        "length": len(context) if context else 0,
        "status": "success" if context else "no_results_or_error",
    }






if __name__ == "__main__":
    import uvicorn

    print(f"🚀 Server running at {settings.SERVER_URL}")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=3001,
        reload=True,
        reload_dirs=[".", "services"],
        # 依然建议排除缓存文件，防止编译行为触发重启
        reload_excludes=[
            "*/__pycache__/*",
            "*.pyc",
            ".venv/*",  # 排除根目录下的虚拟环境
            "*/.venv/*",
        ],
    )
