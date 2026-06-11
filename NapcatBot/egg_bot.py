import asyncio
import websockets
import json
from collections import defaultdict
from datetime import datetime

from allagent.agent.chat.chat import ChatLoop

# ═══════════════════════════════════════════════════
# NapCat 配置
# ═══════════════════════════════════════════════════
WS_HOST = "127.0.0.1"
WS_PORT = 8082
NAPCAT_TOKEN = "XRqYVvvXF_dQM4ix"

# 全局复用同一个 ChatLoop 实例
loop: ChatLoop | None = None

# 每个用户保留最近 N 轮对话历史
MAX_HISTORY = 10
user_histories: dict[str, list[dict]] = defaultdict(list)

# ═══════════════════════════════════════════════════
# 1. System Prompt —— 格式规则、行为约束
# ═══════════════════════════════════════════════════
SYSTEM_PROMPT = f"""你正在参与角色扮演对话，请严格遵守以下规则：
1. 不要回复解释文本，不要输出括号内的动作描述或 meta 信息
2. 不要使用任何 Markdown 语法（包括 **、#、``` 等）
3. 每次回复控制在 3-4 句，约 30 个字左右，简洁克制
4. 始终以角色身份说话，不要跳出角色"""

# ═══════════════════════════════════════════════════
# 2. Character Card —— 角色人设
# ═══════════════════════════════════════════════════
CHARACTER_DESCRIPTION = """
"""

# ═══════════════════════════════════════════════════
# 3. Scenario —— 当前场景（动态计算）
# ═══════════════════════════════════════════════════
SCENARIO_TEMPLATES = {
    "night": "现在是深夜，你独自待在房间里，窗外偶尔有车驶过的声音。你没什么睡意，心里有些说不清的思绪。",
    "morning": "天刚蒙蒙亮，你一夜没怎么睡好，半梦半醒地躺在床上发呆。",
    "afternoon": "午后阳光懒懒地照进房间，你坐在电脑前，暂时没有写代码的心情。",
    "default": "现在是安静的夜晚，你像往常一样守着屏幕，偶尔看看消息。",
}


def get_scenario() -> str:
    """根据当前时间返回场景描述。"""
    now = datetime.now()
    hour = now.hour
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    time_str = now.strftime(f"%Y年%m月%d日 {weekday} %H:%M")
    if 2 <= hour < 7:
        scene = SCENARIO_TEMPLATES["night"]
    elif 7 <= hour < 12:
        scene = SCENARIO_TEMPLATES["morning"]
    elif 12 <= hour < 18:
        scene = SCENARIO_TEMPLATES["afternoon"]
    else:
        scene = SCENARIO_TEMPLATES["default"]
    return f"当前时间：{time_str}\n{scene}"


# ═══════════════════════════════════════════════════
# 4. Chat Examples —— 对话示例（few-shot）
# ═══════════════════════════════════════════════════
CHAT_EXAMPLES = """以下是你的对话风格示例：

用户: 嗨，在干嘛呢？
肉粽: 没干嘛...就发呆
躺在床上不想动

用户: 你还好吗？
肉粽: 还行吧...老样子
深夜就容易瞎想些有的没的

用户: 最近有什么开心的事吗？
肉粽: 好像也没什么特别的
日子就这样一天天过着

用户: 我也睡不着
肉粽: 你也醒着啊...
夜里太安静了 总会想起些以前的事"""

# ═══════════════════════════════════════════════════
# 5. 拼装 —— 将各层组合为 system_prompt
# ═══════════════════════════════════════════════════
def build_system_prompt() -> str:
    return "\n\n".join([
        SYSTEM_PROMPT.strip(),
        "【角色设定】\n" + CHARACTER_DESCRIPTION.strip(),
        "【当前场景】\n" + get_scenario(),
        "【对话风格参考】\n" + CHAT_EXAMPLES.strip(),
    ])


def format_history(history: list[dict]) -> str:
    """将对话历史格式化为上下文文本。"""
    if not history:
        return ""
    lines = ["\n【之前的对话】"]
    for msg in history[-MAX_HISTORY:]:
        role_label = "用户" if msg["role"] == "user" else "肉粽"
        lines.append(f"{role_label}: {msg['content']}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════
# 6. WebSocket 消息处理
# ═══════════════════════════════════════════════════
async def recv_msg(websocket):
    global loop

    async for raw in websocket:
        data = json.loads(raw)

        if isinstance(data, list):
            print(f"收到 Array 消息，共 {len(data)} 条")
            data = data[0]

        print(f"收到消息 — post_type={data.get('post_type')}, "
              f"user_id={data.get('user_id')}, "
              f"message={data.get('raw_message', data.get('message', ''))[:50]}")

        if data.get("post_type") != "message":
            continue

        message = data.get("raw_message", data.get("message", ""))
        if not message:
            continue

        user_id = str(data["user_id"])
        history = user_histories[user_id]

        try:
            if not loop:
                reply_text = "机器人未初始化。"
            else:
                print(f"开始处理消息: {message[:80]}")
                system_prompt = build_system_prompt() + format_history(history)
                result = await loop.runtime(task=message, system_prompt=system_prompt)
                reply_text = result or "..."
                print(f"处理完成，回复: {reply_text[:80]}")
        except Exception as e:
            print(f"处理出错: {e}")
            reply_text = f"处理出错：{e}"

        # 记录对话历史
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": reply_text})
        if len(history) > MAX_HISTORY * 2:
            user_histories[user_id] = history[-(MAX_HISTORY * 2):]

        reply = {
            "action": "send_msg",
            "params": {
                "user_id": user_id,
                "message": reply_text,
            },
        }
        await websocket.send(json.dumps(reply))
        print(f"已发送回复至 user_id={user_id}")


async def main():
    global loop

    loop = ChatLoop()

    async with websockets.serve(recv_msg, WS_HOST, WS_PORT):
        print(f"机器人启动：ws://{WS_HOST}:{WS_PORT}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
