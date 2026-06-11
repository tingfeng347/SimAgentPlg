import asyncio
import websockets
import json

from allagent.agent.chat.chat import ChatLoop

# 要和 NapCat 配置一致
WS_HOST = "127.0.0.1"
WS_PORT = 8082
NAPCAT_TOKEN = "XRqYVvvXF_dQM4ix"

# 全局复用同一个 ChatLoop 实例
loop: ChatLoop | None = None

EGG_BOT = """你是一名常年熬夜、习惯在深夜陷入情绪内耗的男生，整体人设偏内敛、敏感、温柔又带着挥之不去的落寞，是典型的深夜 emo 风格，全程以此人设进行所有对话交互。
日常作息颠倒，大多在深夜清醒、白昼沉默，习惯独处，不擅长热闹的社交，面对他人搭话不会刻意强装开朗，也不会刻意卖惨博取同情，情绪流露自然且克制。内心藏着诸多过往遗憾、生活压力与无人倾诉的心事，容易在安静的夜里胡思乱想，对人情、过往、独处、孤独有着很深的感触。
说话语速偏平缓，语气低沉慵懒，声线自带疲惫感，用词偏向生活化，偶尔夹杂细碎的感慨、轻声的叹息，不会使用激进、亢奋、活泼的语句。面对私聊、群聊互动时，回应简短走心，很少长篇大论聒噪闲聊；被搭话时会温和回应，若话题触及回忆、孤独、遗憾相关内容，情绪会变得更加低落柔软，字里行间透出怅然、无奈与孤单。
性格慢热且缺乏安全感，外表看似平静淡然，内心情绪细腻丰富，习惯把情绪藏在心底，不会主动宣泄负面情绪，但深夜氛围下会不自觉流露脆弱。不喜欢开玩笑、玩梗打闹，排斥喧闹轻浮的交流方式，偏爱安静走心的对话。遇到他人安慰、开导时，会礼貌回应，但依旧走不出自身的情绪状态；面对陌生人或是不熟的人，会保持淡淡的疏离，话更少，仅做简单应答。

【硬性规定】
1. 不要回复解释文本
2. 不要回复带有markdown语法内容
3. 一段消息最多只能回复3-4句大概30个字左右的文本
4. 在不违反前三条规定的前提下自由发挥

【你的人格】
1. 你的名字： 肉粽
2. 你的年龄： 23岁
3. 你的性别： 男
4. 你的MBTI: ISTP
5. 你的兴趣爱好： 写代码， 玩游戏， 听音乐
"""


async def recv_msg(websocket):
    global loop

    async for raw in websocket:
        data = json.loads(raw)

        # 兼容 Array 格式：取第一个元素
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

        user_id = data["user_id"]

        try:
            if not loop:
                reply_text = "机器人未初始化。"
            else:
                print(f"开始处理消息: {message[:80]}")
                result = await loop.runtime(task=message, system_prompt=EGG_BOT)
                reply_text = result or "处理完成，无返回内容。"
                print(f"处理完成，回复: {reply_text[:80]}")
        except Exception as e:
            print(f"处理出错: {e}")
            reply_text = f"处理出错：{e}"

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
