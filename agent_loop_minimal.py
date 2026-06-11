"""
agent_runner_loop 最小化实现（不含 _hook 插件系统）
—— 包含所有必需的类和函数，可直接运行。
"""
import json, re, os
from dataclasses import dataclass
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════════
# 1. StepOutcome —— 工具执行结果的数据载体
# ═══════════════════════════════════════════════════════════════
@dataclass
class StepOutcome:
    data: Any                                # 工具返回值
    next_prompt: Optional[str] = None        # 下一轮追加的 prompt，None 表示任务完成
    should_exit: bool = False                # True 表示立即退出


# ═══════════════════════════════════════════════════════════════
# 2. try_call_generator —— 包装 do_xxx 方法，自动展开 generator
# ═══════════════════════════════════════════════════════════════
def try_call_generator(func, *args, **kwargs):
    """调用 func，如果返回值是 generator 则 yield from 展开，否则直接返回。"""
    ret = func(*args, **kwargs)
    if hasattr(ret, '__iter__') and not isinstance(ret, (str, bytes, dict, list)):
        ret = yield from ret
    return ret


# ═══════════════════════════════════════════════════════════════
# 3. json_default —— json.dumps 的 default 回调
# ═══════════════════════════════════════════════════════════════
def json_default(o):
    """处理 json.dumps 无法直接序列化的类型（set 等）。"""
    return list(o) if isinstance(o, set) else str(o)


# ═══════════════════════════════════════════════════════════════
# 4. exhaust —— 消费 generator，捕获 StopIteration 返回值
# ═══════════════════════════════════════════════════════════════
def exhaust(g):
    """消费完整个 generator，返回 StopIteration.value。"""
    try:
        while True:
            next(g)
    except StopIteration as e:
        return e.value


# ═══════════════════════════════════════════════════════════════
# 5. get_pretty_json —— verbose 模式格式化工具参数
# ═══════════════════════════════════════════════════════════════
def get_pretty_json(data):
    """格式化 JSON，对 script 参数做换行美化。"""
    if isinstance(data, dict) and "script" in data:
        data = data.copy()
        data["script"] = data["script"].replace("; ", ";\n  ")
    return json.dumps(data, indent=2, ensure_ascii=False).replace('\\n', '\n')


# ═══════════════════════════════════════════════════════════════
# 6. _compact_tool_args —— 非 verbose 模式压缩工具参数展示
# ═══════════════════════════════════════════════════════════════
def _compact_tool_args(name, args):
    """生成工具调用的短摘要，避免终端输出过长。"""
    a = {k: v for k, v in args.items() if k != '_index'}
    for k in ('path',):
        if k in a:
            a[k] = os.path.basename(a[k])
    if name == 'update_working_checkpoint':
        s = a.get('key_info', '')
        return (s[:60] + '...') if len(s) > 60 else s
    if name == 'ask_user':
        q = str(a.get('question', ''))
        cs = a.get('candidates') or []
        if cs:
            q += '\ncandidates:\n' + '\n'.join(f'- {c}' for c in cs)
        return q
    s = json.dumps(a, ensure_ascii=False)
    return (s[:120] + '...') if len(s) > 120 else s


# ═══════════════════════════════════════════════════════════════
# 7. _clean_content —— 非 verbose 模式压缩 LLM 回复中的长内容
# ═══════════════════════════════════════════════════════════════
def _clean_content(text):
    """压缩代码块和长标签内容，避免终端输出过长。"""
    if not text:
        return ''

    def _shrink_code(m):
        lines = m.group(0).split('\n')
        lang = lines[0].replace('```', '').strip()
        body = [l for l in lines[1:-1] if l.strip()]
        if len(body) <= 6:
            return m.group(0)
        preview = '\n'.join(body[:5])
        return f'```{lang}\n{preview}\n  ... ({len(body)} lines)\n```'

    text = re.sub(r'```[\s\S]*?```', _shrink_code, text)
    for p in [r'<file_content>[\s\S]*?</file_content>',
              r'<tool_(?:use|call)>[\s\S]*?</tool_(?:use|call)>',
              r'(\r?\n){3,}']:
        text = re.sub(p, '\n\n' if '\\n' in p else '', text)
    return text.strip()


# ═══════════════════════════════════════════════════════════════
# 8. BaseHandler —— 工具反射分发器
# ═══════════════════════════════════════════════════════════════
class BaseHandler:
    """
    工具调度基类 —— 约定优于配置：
    子类只需定义 do_{tool_name} 方法，LLM 调用该工具时会自动反射路由。
    """
    def turn_end_callback(self, response, tool_calls, tool_results, turn, next_prompt, exit_reason):
        """每轮结束的回调，子类可覆写做日志/历史裁剪。默认透传 next_prompt。"""
        return next_prompt

    def dispatch(self, tool_name, args, response, index=0, tool_num=1):
        """
        根据 tool_name 反射到 self.do_{tool_name} 方法。
        注入 _index / _tool_num 后通过 try_call_generator 执行。
        """
        method_name = f"do_{tool_name}"
        if hasattr(self, method_name):
            args['_index'] = index
            args['_tool_num'] = tool_num
            ret = yield from try_call_generator(getattr(self, method_name), args, response)
            return ret
        elif tool_name == 'bad_json':
            return StepOutcome(None, next_prompt=args.get('msg', 'bad_json'), should_exit=False)
        else:
            yield f"未知工具: {tool_name}\n"
            return StepOutcome(None, next_prompt=f"未知工具 {tool_name}", should_exit=False)


# ═══════════════════════════════════════════════════════════════
# 9. agent_runner_loop —— Agent 主循环（generator）
# ═══════════════════════════════════════════════════════════════
def agent_runner_loop(client, system_prompt, user_input, handler, tools_schema,
                      max_turns=40, verbose=True, initial_user_content=None, yield_info=False):
    """
    Agent 主循环：
    1. 构建初始 messages
    2. 循环：调用 LLM → 解析 tool_calls → dispatch 到 handler → 收集 StepOutcome → 组装下一轮消息
    3. 直到任务完成 / 退出 / 达到最大轮次
    """
    # --- 初始化 ---
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": initial_user_content if initial_user_content is not None else user_input}
    ]
    turn = 0
    handler.max_turns = max_turns

    while turn < handler.max_turns:
        turn += 1
        turnstr = f'LLM Running (Turn {turn}) ...'
        if getattr(getattr(handler, 'parent', None), 'task_dir', None):
            turnstr = f'Turn {turn} ...'
        if verbose:
            turnstr = f'**{turnstr}**'
        if yield_info:
            yield {'turn': turn}
        yield f"\n\n{turnstr}\n\n"

        # 每10轮重置 LLM 工具缓存
        if turn % 10 == 0:
            client.last_tools = ''

        # --- 调用 LLM ---
        response_gen = client.chat(messages=messages, tools=tools_schema)
        if verbose:
            response = yield from response_gen
            yield '\n\n'
        else:
            response = exhaust(response_gen)
            cleaned = _clean_content(response.content)
            if cleaned:
                yield cleaned + '\n'

        # --- 解析 tool_calls ---
        if not response.tool_calls:
            tool_calls = [{'tool_name': 'no_tool', 'args': {}}]
        else:
            tool_calls = [
                {
                    'tool_name': tc.function.name,
                    'args': json.loads(tc.function.arguments),
                    'id': tc.id
                }
                for tc in response.tool_calls
            ]

        # --- 逐个执行工具 ---
        tool_results = []
        next_prompts = set()
        exit_reason = {}

        for ii, tc in enumerate(tool_calls):
            tool_name, args, tid = tc['tool_name'], tc['args'], tc.get('id', '')

            if tool_name == 'no_tool':
                pass
            else:
                if verbose:
                    yield f"🛠️ Tool: `{tool_name}`  📥 args:\n````text\n{get_pretty_json(args)}\n````\n"
                else:
                    yield f"🛠️ {tool_name}({_compact_tool_args(tool_name, args)})\n\n\n"

            handler.current_turn = turn
            gen = handler.dispatch(tool_name, args, response, index=ii, tool_num=len(tool_calls))

            try:
                v = next(gen)

                def proxy():
                    yield v
                    return (yield from gen)

                if verbose:
                    yield '`````\n'
                outcome = (yield from proxy()) if verbose else exhaust(proxy())
                if verbose:
                    yield '`````\n'
            except StopIteration as e:
                outcome = e.value

            # --- 判断退出条件 ---
            if outcome.should_exit:
                exit_reason = {'result': 'EXITED', 'data': outcome.data}
                break
            if not outcome.next_prompt:
                exit_reason = {'result': 'CURRENT_TASK_DONE', 'data': outcome.data}
                break

            if outcome.next_prompt.startswith('未知工具'):
                client.last_tools = ''

            # --- 收集工具结果和 next_prompt ---
            if outcome.data is not None and tool_name != 'no_tool':
                datastr = (
                    json.dumps(outcome.data, ensure_ascii=False, default=json_default)
                    if isinstance(outcome.data, (dict, list))
                    else str(outcome.data)
                )
                tool_results.append({'tool_use_id': tid, 'content': datastr})

            next_prompts.add(outcome.next_prompt)

        # --- 检查 _done_hooks（收尾操作）---
        if len(next_prompts) == 0 or exit_reason:
            done_hooks = getattr(handler, '_done_hooks', [])
            if len(done_hooks) == 0 or exit_reason.get('result', '') == 'EXITED':
                break
            next_prompts.add(done_hooks.pop(0))

        # --- 组装下一轮消息 ---
        next_prompt = handler.turn_end_callback(
            response, tool_calls, tool_results, turn,
            '\n'.join(next_prompts), exit_reason
        )
        messages = [{"role": "user", "content": next_prompt, "tool_results": tool_results}]

    # --- 循环结束 ---
    if exit_reason:
        handler.turn_end_callback(response, tool_calls, tool_results, turn, '', exit_reason)
    return exit_reason or {'result': 'MAX_TURNS_EXCEEDED'}
