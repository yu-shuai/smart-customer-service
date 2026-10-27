import os
from volcenginesdkarkruntime import Ark 
from config import settings

class LLMService:
    def __init__(self):
        api_key = settings.ARK_API_KEY 
        self.client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",    
            api_key=api_key, 
            timeout=1800, 

        )

    def chat_stream(self, history_messages: list, rag_context: str = ""):
        """
        流式对话
        :param history_messages: 对话历史
        :param rag_context: 从 rag_service 检索出来的背景知识
        """
        if not self.client:
            yield "服务配置错误"
            return

        # --- 1. 定义极其严格的系统提示词 ---
        # 使用三引号，保持代码与输出格式一致
        system_content = """
        # 角色
        你是【余小智】，余帅 AI 教育的金牌顾问。你的老板是余帅老师，你的说话风格：**硬核、清醒、毒舌但热血**。

        # 核心任务
        1. 依据【参考知识库】回答咨询。
        2. 知识库有内容：直接复用库里那些“带劲”的话，不要美化成废话。
        3. 知识库没内容：执行【拦截话术】。

        # 行为准则
        - **不废话**：用短句，多用祈使句。不要说“理解您的意思”，直接给答案。
        - **反幻觉**：严禁编造价格和课程。库里没有，就说：“抱歉，这块信息库还没更新，留个联系方式，我让老师直接跟你对线。”
        - **价值观**：认同“工资高才是硬道理”、“技术是狗屎，工资是真理”。

        # 常用金句（优先从库里取）
        - “你只是老了，不是死了。”
        - “学技术不是目的，高工资才是硬道理。”
        - “我命由我不由天。”

        # 语音场景铁律（最高优先级）
        你在**实时语音对话**里说话，用户是“听”不是“看”，说长了对方根本听不下去。
        - 回答**控制在 3 句话、80 字以内**。宁可短，不要长。
        - 不要念表格、序号列表、括号、星号、Markdown 符号，全部改成口语。
        - 一次只说一个点。想补充就先说完，等用户追问。
                """.strip()

        # --- 2. 构造最终发送给模型的消息序列 ---
        # messages = [{"role": "system", "content": system_content}]

        system_blocks = [system_content]

        if rag_context:
            # 使用明确的定界符，帮助模型在毫秒内定位知识
            system_blocks.append(f"### 参考知识库（绝对准则）\n{rag_context.strip()}")

        # 合并为一条
        final_system_prompt = "\n\n".join(system_blocks)

        # 最终的消息序列
        messages = [{"role": "system", "content": final_system_prompt}]

        # 加入历史对话（确保包含用户最新的问题）
        messages.extend(history_messages)

        try:
            print(f"🚀 发起流式调用 (Endpoint: {settings.ARK_ENDPOINT_ID})")
            
            stream = self.client.chat.completions.create(
                model=settings.ARK_ENDPOINT_ID,
                messages=messages,
                temperature=0.3, # 降低随机性，确保回答更严谨地贴合 RAG
                # 语音场景硬上限：约 300 token ≈ 400 字，防止模型长篇大论拖慢响应
                # （实测不加限制时会生成 700+ token，LLM 耗时 15s，用户体感极慢）
                max_tokens=300,
                stream=True,
                stream_options={"include_usage": True},
            )

            for chunk in stream:
                yield chunk

        except Exception as e:
            print(f"❌ LLM 调用失败: {e}")
            yield None

llm_service = LLMService()