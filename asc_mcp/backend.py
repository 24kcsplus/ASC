"""Serialized, cancellable subprocess bridge; no decompiler imports in MCP host."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import anyio

ROOT = Path(__file__).resolve().parent.parent


class AnalysisError(RuntimeError):
    pass


class Backend:
    def __init__(self, timeout=180):
        self.timeout = timeout
        self.process = None
        self.lock = asyncio.Lock()

    async def _start(self):
        if self.process is None or self.process.returncode is not None:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'asc_mcp.worker', cwd=ROOT,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=sys.stderr, limit=8*1024*1024, start_new_session=os.name == 'posix')

    async def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        # Also terminate search/decompiler children in this worker's process group.
        try:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGTERM)
            elif process.returncode is None:
                process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 3)
        except asyncio.TimeoutError:
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            await process.wait()

    async def call(self, op, **args):
        async with self.lock:
            await self._start()
            async def exchange():
                self.process.stdin.write((json.dumps({'op': op, 'args': args})+'\n').encode())
                await self.process.stdin.drain()
                line = await self.process.stdout.readline()
                if not line:
                    raise AnalysisError('分析进程已退出；请重新打开 APK')
                return json.loads(line)
            try:
                response = await asyncio.wait_for(exchange(), self.timeout)
            except (asyncio.CancelledError, Exception) as exc:
                with anyio.CancelScope(shield=True):
                    await self.close()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if isinstance(exc, asyncio.TimeoutError):
                    raise AnalysisError('分析超时，进程已回收；请重新打开 APK') from exc
                raise AnalysisError('分析进程通信失败：'+str(exc)) from exc
            if not response['ok']:
                raise AnalysisError(response['error'])
            return response['result']
