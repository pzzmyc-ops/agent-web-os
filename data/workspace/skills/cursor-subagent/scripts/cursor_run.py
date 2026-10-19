#!/usr/bin/env python
"""把任务交给 Cursor agent 做，并把过程实时写进日志（异步跑 + 中途查看进度）。

用法示例：
  python cursor_run.py --cwd D:/proj "把这个仓库的 README 更新，补上安装步骤，并跑一次测试"
  python cursor_run.py --cwd D:/proj --background-hint
  python cursor_run.py --resume agent-xxxx "再补一句：把变更写进 CHANGELOG"

设计要点（给调用方/agent 用）：
- 每行带 [HH:MM:SS] 时间戳；过程事件（thinking / 工具 / 状态）用 '·' 前缀，正文用 '│' 前缀。
- 日志同时落盘（--log，默认 workspace/cursor-re/runs/cursor-<时间戳>.log），方便在后台跑时随时 tail。
- 头部会立刻打印 AGENT_ID=...，中途可以用它 resume / 观察。
- 结束打印 FINAL: 和 RUN_DONE status=... elapsed=... 与 LOG=...，便于脚本判定成功。
"""
from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time

# ------------------------------------------------------------------ Windows 补丁
def _patch_bridge() -> None:
    try:
        import cursor_sdk._bridge as b
        from cursor_sdk.errors import CursorSDKError
    except Exception:
        return
    if getattr(b, '_cursor_subagent_patched', False):
        return

    def _read_discovery_win(process, timeout: float):
        if process.stderr is None:
            raise CursorSDKError('Bridge process stderr is unavailable')
        q: 'queue.Queue' = queue.Queue()

        def reader() -> None:
            try:
                while True:
                    ch = process.stderr.read(1)
                    if not ch:
                        break
                    q.put(ch)
            except Exception:
                pass
            q.put(None)

        threading.Thread(target=reader, daemon=True).start()
        pending, tail = '', ''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                ch = q.get(timeout=0.1)
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if ch is None:
                break
            pending += ch
            tail = (tail + ch)[-4000:]
            while '\n' in pending:
                line, pending = pending.split('\n', 1)
                found = b.parse_discovery_line(line + '\n')
                if found is not None:
                    return found
        raise CursorSDKError('Bridge not ready in %.0fs (exit=%s) %s' % (timeout, process.poll(), tail[-400:]))

    b._read_discovery = _read_discovery_win
    b._cursor_subagent_patched = True


def _api_key(explicit: str | None) -> str:
    key = (explicit or os.environ.get('CURSOR_API_KEY') or '').strip()
    if key:
        return key
    for p in (r'D:/mafagent/data/workspace/cursor-re/data/cursor_api_key.txt',
              os.path.join(os.path.expanduser('~'), '.cursor', 'api_key.txt')):
        try:
            with open(p, encoding='utf-8') as f:
                k = f.read().strip()
            if k:
                return k
        except Exception:
            continue
    raise SystemExit('找不到 Cursor API key（set CURSOR_API_KEY 或放到 cursor-re/data/cursor_api_key.txt）')


def _model_selection(model: str, effort: str | None, fast: bool, context: str | None):
    # SDK 要求 params 是 [{"id": ..., "value": ...}] 列表，不是字典
    params = []
    if effort:
        params.append({'id': 'reasoning_effort', 'value': effort})
    if fast:
        params.append({'id': 'fast', 'value': 'true'})
    if context:
        params.append({'id': 'context', 'value': context})
    return {'id': model, 'params': params} if params else model


def _describe(obj, depth: int = 0, width: int = 200) -> str:
    """尽量把 SDK 的消息/工具调用对象描述成一行可读文本。"""
    if obj is None:
        return 'None'
    if isinstance(obj, (str, int, float, bool)):
        return str(obj)[:width]
    try:
        import dataclasses
        if dataclasses.is_dataclass(obj):
            parts = []
            for f in dataclasses.fields(obj):
                v = getattr(obj, f.name, None)
                if v in (None, [], {}, ''):
                    continue
                parts.append('%s=%s' % (f.name, _describe(v, depth + 1, 120) if depth < 1 else str(v)[:120]))
            return '{%s}' % ', '.join(parts)[:width]
    except Exception:
        pass
    if isinstance(obj, dict):
        return '{%s}' % ', '.join('%s=%s' % (k, str(v)[:120]) for k, v in list(obj.items())[:8])[:width]
    for attr in ('case', 'value', 'name', 'tool_name', 'tool', 'args', 'input', 'arguments', 'text', 'content'):
        if hasattr(obj, attr):
            v = getattr(obj, attr)
            if v not in (None, '', [], {}):
                return '%s(%s=%s)' % (type(obj).__name__, attr, _describe(v, depth + 1, width - 40))
    return '%s(%s)' % (type(obj).__name__, str(obj)[:width - 40])


def main() -> int:
    ap = argparse.ArgumentParser(description='把任务交给 Cursor agent，过程写入日志')
    ap.add_argument('task', nargs='?', help='任务描述（中文即可）')
    ap.add_argument('--task', dest='task_opt', help='任务描述（显式指定）')
    ap.add_argument('--cwd', default=os.getcwd(), help='agent 的工作目录（默认当前目录）')
    ap.add_argument('--model', default='grok-4.7')
    ap.add_argument('--effort', choices=['low', 'medium', 'high', 'xhigh'], default=None)
    ap.add_argument('--fast', action='store_true')
    ap.add_argument('--context', choices=['256k', '500k'], default=None)
    ap.add_argument('--resume', default='', help='已有 agent id：接着这个会话继续发消息')
    ap.add_argument('--timeout', type=float, default=3600, help='本次 run 的软超时（秒）')
    ap.add_argument('--log', default='', help='日志文件路径')
    ap.add_argument('--api-key', default='')
    ap.add_argument('--quiet', action='store_true', help='只写日志，不在 stdout 打正文分片')
    args = ap.parse_args()

    task = (args.task_opt or args.task or '').strip()
    if not task:
        ap.error('任务不能为空')

    log_path = args.log or os.path.join(
        r'D:/mafagent/data/workspace/cursor-re/runs',
        'cursor-%s.log' % time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logf = open(log_path, 'a', encoding='utf-8', buffering=1)

    def emit(line: str, stdout: bool = True) -> None:
        logf.write(line + '\n')
        if stdout:
            print(line, flush=True)

    def ts() -> str:
        return time.strftime('%H:%M:%S')

    _patch_bridge()
    from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

    cwd = os.path.abspath(args.cwd)
    os.makedirs(cwd, exist_ok=True)
    key = _api_key(args.api_key or None)
    selection = _model_selection(args.model, args.effort, args.fast, args.context)

    emit('[%s] START model=%s cwd=%s' % (ts(), selection, cwd))
    emit('[%s] TASK %s' % (ts(), task))
    emit('[%s] LOG %s' % (ts(), log_path))
    t0 = time.time()

    opts = AgentOptions(model=selection, api_key=key, local=LocalAgentOptions(cwd=cwd))
    text_parts: list[str] = []
    usage = None
    status = 'unknown'
    agent_id = ''
    run_id = ''
    last_type = None
    try:
        if args.resume:
            agent = Agent.resume(args.resume, AgentOptions(
                api_key=key, model=selection, local=LocalAgentOptions(cwd=cwd)))
        else:
            agent = Agent.create(opts)
        with agent:
            agent_id = str(getattr(agent, 'agent_id', '') or getattr(agent, 'id', '') or args.resume)
            emit('[%s] AGENT_ID=%s' % (ts(), agent_id))
            run = agent.send(task)
            run_id = str(getattr(run, 'id', '') or getattr(run, 'run_id', '') or '')
            emit('[%s] RUN_ID=%s' % (ts(), run_id))
            for msg in run.messages():
                ty = getattr(msg, 'type', 'unknown')
                if ty == 'assistant':
                    try:
                        for block in msg.message.content:
                            bt = getattr(block, 'type', None)
                            if bt == 'text' and getattr(block, 'text', None):
                                chunk = block.text
                                text_parts.append(chunk)
                                emit('│' + chunk, stdout=not args.quiet)
                            elif bt in ('tool_call', 'tool_use', 'tool', 'tool_call_delta'):
                                emit('[%s] · tool-block %s' % (ts(), _describe(block)))
                            elif bt in ('reasoning', 'thinking'):
                                t = getattr(block, 'text', None) or getattr(block, 'content', None) or ''
                                emit('[%s] · reasoning %s' % (ts(), str(t)[:300]))
                            else:
                                emit('[%s] · block %s %s' % (ts(), bt, _describe(block)))
                    except Exception:
                        pass
                elif ty == 'thinking':
                    last_type = ty
                    emit('[%s] · thinking %s' % (ts(), str(getattr(msg, 'text', '') or '')[:300].replace('\n', ' ')))
                elif ty == 'tool_call':
                    last_type = ty
                    name = getattr(msg, 'name', '?')
                    st = getattr(msg, 'status', '')
                    tool_args = getattr(msg, 'args', None)
                    tool_res = getattr(msg, 'result', None)
                    line = '[%s] · tool %s %s args=%s' % (ts(), name, st, str(tool_args)[:300].replace('\n', ' '))
                    if tool_res is not None:
                        line += ' result=%s' % str(tool_res)[:300].replace('\n', ' ')
                    emit(line)
                elif ty == 'usage':
                    u = getattr(msg, 'usage', None)
                    usage = u
                    emit('[%s] · usage %s' % (ts(), str(u)[:240]))
                elif ty == 'status':
                    last_type = ty
                    st = getattr(msg, 'status', '')
                    m2 = getattr(msg, 'message', '')
                    if st and st != 'RUNNING':
                        emit('[%s] · status %s %s' % (ts(), st, str(m2)[:200]))
                else:
                    d = getattr(msg, 'message', None) or getattr(msg, 'data', None)
                    emit('[%s] · %s %s' % (ts(), ty, _describe(d)))
                if time.time() - t0 > args.timeout:
                    emit('[%s] TIMEOUT soft-limit, cancelling' % ts())
                    try:
                        run.cancel()
                    except Exception:
                        pass
                    break
            res = run.wait()
            status = str(getattr(res, 'status', '') or '')
            final = getattr(res, 'result', None)
            if final and not ''.join(text_parts).strip():
                text_parts.append(str(final))
            u = getattr(res, 'usage', None)
            if u is not None:
                usage = u
    except Exception as exc:  # noqa: BLE001
        status = 'error'
        emit('[%s] ERROR %s: %s' % (ts(), type(exc).__name__, str(exc)[:800]))
        try:
            import traceback
            emit('[%s] TRACE %s' % (ts(), traceback.format_exc()[-1500:].replace('\n', ' | ')))
        except Exception:
            pass
        emit('RUN_DONE status=error elapsed=%.1fs agent_id=%s log=%s' % (time.time() - t0, agent_id, log_path))
        logf.close()
        return 2

    text = ''.join(text_parts).strip()
    emit('[%s] DONE status=%s elapsed=%.1fs usage=%s' % (ts(), status, time.time() - t0, str(usage)[:200]))
    emit('FINAL: %s' % text)
    emit('RUN_DONE status=%s elapsed=%.1fs agent_id=%s run_id=%s log=%s'
         % (status, time.time() - t0, agent_id, run_id, log_path))
    logf.close()
    return 0 if status in ('finished', 'success', 'completed') else 3


if __name__ == '__main__':
    sys.exit(main())
