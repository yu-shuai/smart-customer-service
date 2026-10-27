import os
import httpx
from config import settings
from services.utils import Signer
import json



class RagService:
    def __init__(self):
        # 1. 加载基础鉴权配置
        self.ak = settings.VOLC_AK
        self.sk = settings.VOLC_SK
        
        # 2. 知识库特有配置（统一从 config.py 读，即 .env）
        self.collection_name = settings.KB_COLLECTION_NAME   # 知识库集合名称
        self.project_name = settings.KB_PROJECT_NAME or "default"  # 项目名称
        self.account_id = settings.VOLC_ACCOUNT_ID           # 火山引擎主账号 ID（对应 V-Account-Id，必填）

        # 启动时打印一次配置摘要，避免"静默无知识库"却查不出原因
        print(
            f"📚 [RagService] 知识库配置: collection={self.collection_name or '(空!)'} "
            f"project={self.project_name} account_id={self.account_id or '(空!)'} "
            f"ak={'已配置' if self.ak else '(空!)'}"
        )
        if not self.collection_name or not self.account_id:
            print(
                "⚠️  [RagService] 知识库未配置完整：请在 rag_llm_server/.env 填 "
                "KB_COLLECTION_NAME 与 VOLC_ACCOUNT_ID，否则每轮都会走「知识库没内容」的拦截话术。"
            )

        # 知识库服务的固定配置
        self.host = "api-knowledgebase.mlp.cn-beijing.volces.com"
        self.region = "cn-north-1" # 示例代码中使用的是 cn-north-1
        self.service = "air"       # 知识库服务的 Service 名通常为 air

    async def retrieve(self, query: str) -> str:
        """
        根据用户问题检索知识库
        :param query: 用户查询语句
        :return: 整合后的上下文文本
        """
        # 基础校验
        if not self.ak or not self.sk or not self.account_id:
            print(f"⚠️ [RagService] 配置缺失: 请检查 VOLC_AK, VOLC_SK, VOLC_ACCOUNT_ID(当前: {self.account_id})")
            return ""

        path = "/api/knowledge/collection/search_knowledge"
        
        # 3. 构造请求体 (参考官方示例)
        body = {
            "project": self.project_name,
            "name": self.collection_name,
            "query": query,
            "limit": 3, # 获取相关度最高的前 3 条（语音场景不要设 1，容易漏召回）
            "pre_processing": {
                "need_instruction": True,
                "return_token_usage": True,
                "messages": [{"role": "user", "content": query}]
            },
            "post_processing": {
                "get_attachment_link": True
            }
        }

        # 4. 构造 Header
        # 注意：V-Account-Id 是知识库接口必须的
        headers = {
            "Host": self.host,
            "Content-Type": "application/json",
            "V-Account-Id": self.account_id 
        }

        # 构造待签名的请求数据
        request_data = {
            "method": "POST",
            "path": path,
            "headers": headers,
            "body": body,
            "params": {}
        }



        try:
            # 5. 计算签名 (复用 utils.py 中的 Signer)
            # 知识库使用的是 air / cn-north-1
            signer = Signer(request_data, service=self.service, region=self.region)
            signer.add_authorization({
                "accessKeyId": self.ak,
                "secretKey": self.sk
            })
            
            # 6. 发送异步请求
            url = f"http://{self.host}{path}"
            
            async with httpx.AsyncClient() as client:
                # request_data['headers'] 已经被 signer 修改，包含了 Authorization 字段
                resp = await client.post(
                    url,
                    headers=request_data["headers"],
                    # 签名与实际发送必须是同一份字节，因此直接用 signer.body_str；
                    # 用 json=body 会让 httpx 重新序列化（带空格 + 中文转 \uXXXX），
                    # 与签名时的字节不一致，火山会返回 403 / 1000001 check sign not match。
                    content=signer.body_str.encode("utf-8"),
                    timeout=10.0,
                )

            # 6. 解析响应内容
            if resp.status_code != 200:
                text = resp.text
                print(f"❌ [RagService] 请求失败: {resp.status_code}, {text}")
                # 常见错误码给出人话提示，避免对着一串 JSON 猜
                if "300001" in text:
                    print("   → 知识库服务未开通/未下单：去火山引擎控制台开通知识库后重试（代码不用改）。")
                elif "1000001" in text or "check sign" in text:
                    print("   → 签名校验失败：检查 AK/SK 是否正确、VOLC_ACCOUNT_ID 是否是主账号 ID。")
                elif "not found" in text.lower():
                    print(
                        f"   → 集合不存在：确认 .env 的 KB_COLLECTION_NAME（当前 {self.collection_name}）"
                        " 与控制台列表里的集合名完全一致。"
                    )
                return ""

            data = resp.json()
            
            # --- 核心提取逻辑 ---
            # 1. 按照层级定位到 result_list
            # 使用 .get() 级联获取，防止中间某个 Key 缺失导致报错
            result_list = data.get("data", {}).get("result_list", [])
            
            # 2. 提取所有 item 中的 content
            # 兼容多条数据：遍历列表，只取 content 字段不为空的部分
            contents = [item.get("content", "") for item in result_list if item.get("content")]
            
            
            if not contents:
                print(f"⚠️ [RagService] 未检索到匹配的知识内容")
                return ""

            # 3. 将多条 content 拼接成一个完整的字符串返回
            # 使用双换行符分隔不同的知识块，方便 LLM 区分
            context_text = "\n\n".join(contents)
            
            print(f"✅ [RagService] 成功提取 {len(contents)} 条知识内容")
            print(f"【传给LLM的,上下文内容】:\n{context_text}")
            return context_text


        except Exception as e:
            print(f"❌ [RagService] 异常: {e}")
            return ""

# 实例化单例
rag_service = RagService()