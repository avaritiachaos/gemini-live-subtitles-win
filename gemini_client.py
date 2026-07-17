"""Gemini Live Translate WebSocket 客户端。

在后台线程跑 asyncio loop；音频回调线程通过 send_audio() 用
run_coroutine_threadsafe 投递 PCM16 块。字幕文本经 Qt Signal 发回主线程。
"""
from __future__ import annotations

import asyncio
import base64
import json
import threading
from typing import Optional

import websockets
from PySide6.QtCore import QObject, Signal

WS_PATH = "/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
SETUP_TIMEOUT_SEC = 10.0
MAX_PENDING_SENDS = 20
MAX_SETUP_FAILURES = 3
MAX_RECONNECT_DELAY = 30.0
RECONNECT_STABLE_SEC = 10.0
# 值得自动重连的 close code；其余（如鉴权失败）视为永久错误
RECONNECTABLE_CLOSE_CODES = {1000, 1001, 1006, 1011, 1012, 1013, 1014}

# 状态种类，HUD 据此上色
KIND_IDLE = "idle"
KIND_CONNECTING = "connecting"
KIND_CONNECTED = "connected"
KIND_ERROR = "error"
KIND_INFO = "info"


class _GoAway(Exception):
    """服务端发来 goAway：会话时长到期，需要立即重连（非故障）。"""


class GeminiClient(QObject):
    subtitle = Signal(str)        # 增量译文文本
    original = Signal(str)        # 增量原文转写（双语字幕用）
    turnComplete = Signal()       # 一句结束
    status = Signal(str, str)     # (kind, message)
    stopped = Signal()            # 会话彻底结束（用户停止或永久错误）

    def __init__(self) -> None:
        super().__init__()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ws = None
        self._running = False
        self._pending = 0
        self._dropped = 0
        self._lock = threading.Lock()
        # 会话参数（start 时快照）
        self._api_key = ""
        self._api_base = ""
        self._model = ""
        self._target_lang = "zh-CN"

    # ---- 公共接口（主线程调用） ----

    def start(self, api_key: str, api_base: str, model: str, target_lang: str) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self._api_key = api_key
        self._api_base = api_base
        self._model = model
        self._target_lang = target_lang
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="gemini-ws")
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(lambda: None)  # 唤醒 recv 等待

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def send_audio(self, pcm16: bytes) -> None:
        """音频采集线程调用；连接未就绪或积压过多时丢弃。"""
        loop = self._loop
        if loop is None or not self.running:
            return
        if self._pending >= MAX_PENDING_SENDS:
            self._dropped += 1
            return
        self._pending += 1
        try:
            asyncio.run_coroutine_threadsafe(self._send_audio_async(pcm16), loop)
        except RuntimeError:
            self._pending -= 1

    # ---- asyncio 线程 ----

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._session_loop())
        finally:
            self._loop.close()
            self._loop = None
            with self._lock:
                self._running = False
            self.status.emit(KIND_IDLE, "已停止")
            self.stopped.emit()

    def _ws_url(self) -> str:
        base = self._api_base.replace("https://", "wss://").replace("http://", "ws://")
        return f"{base}{WS_PATH}?key={self._api_key}"

    async def _session_loop(self) -> None:
        setup_failures = 0
        delay = 1.0
        while self.running:
            connected_at = None
            try:
                self.status.emit(KIND_CONNECTING, "连接中…")
                async with websockets.connect(
                    self._ws_url(), max_size=16 * 1024 * 1024, ping_interval=20
                ) as ws:
                    self._ws = ws
                    await self._send_setup(ws)
                    await asyncio.wait_for(self._wait_setup_complete(ws), SETUP_TIMEOUT_SEC)
                    setup_failures = 0
                    connected_at = asyncio.get_event_loop().time()
                    self.status.emit(KIND_CONNECTED, "已连接")
                    await self._recv_loop(ws)
            except asyncio.TimeoutError:
                setup_failures += 1
                if setup_failures >= MAX_SETUP_FAILURES:
                    self.status.emit(KIND_ERROR, "连接握手连续超时，请检查网络/代理与模型名")
                    return
                self.status.emit(KIND_ERROR, f"握手超时，重试 {setup_failures}/{MAX_SETUP_FAILURES}")
            except websockets.exceptions.InvalidStatus as e:
                code = getattr(getattr(e, "response", None), "status_code", None)
                if code in (401, 403):
                    self.status.emit(KIND_ERROR, "API key 无效或无权限")
                    return
                self.status.emit(KIND_ERROR, f"连接被拒绝 (HTTP {code})")
            except _GoAway:
                self.status.emit(KIND_INFO, "会话时长到期，正在续连…")
                delay = 1.0
            except websockets.exceptions.ConnectionClosed as e:
                frame = getattr(e, "rcvd", None)
                code = getattr(frame, "code", None)
                reason = str(getattr(frame, "reason", "") or "")
                # 未处理 goAway 时服务端会以 1008 关闭，同样按会话到期续连
                session_expired = code == 1008 and (
                    "GoAway" in reason or "session" in reason.lower()
                )
                if (
                    code is not None
                    and code not in RECONNECTABLE_CLOSE_CODES
                    and not session_expired
                ):
                    self.status.emit(KIND_ERROR, f"连接被关闭 ({code}) {reason}")
                    return
                self.status.emit(KIND_INFO, "连接断开，准备重连")
            except RuntimeError as e:
                msg = str(e)
                # 免费层 TPM 限流等配额错误：退避重连而不是停死
                lowered = msg.lower()
                if any(k in lowered for k in ("resource_exhausted", "quota", "rate limit", "exhausted")):
                    self.status.emit(KIND_INFO, "触发限流，稍后自动重连")
                else:
                    self.status.emit(KIND_ERROR, msg)
                    return
            except OSError as e:
                self.status.emit(KIND_ERROR, f"网络错误: {e}")
            finally:
                self._ws = None
                self._pending = 0

            if not self.running:
                return
            # 稳定连接过一段时间则重置退避
            now = asyncio.get_event_loop().time()
            if connected_at is not None and now - connected_at >= RECONNECT_STABLE_SEC:
                delay = 1.0
            # 带倒计时的等待（每秒醒来，用户点停止能及时退出）
            remaining = delay
            while remaining > 0 and self.running:
                if delay >= 2:
                    self.status.emit(KIND_INFO, f"{int(remaining)} 秒后重连…")
                await asyncio.sleep(min(1.0, remaining))
                remaining -= 1.0
            delay = min(delay * 2, MAX_RECONNECT_DELAY)

    async def _send_setup(self, ws) -> None:
        setup = {
            "model": self._model,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "translationConfig": {
                    "targetLanguageCode": self._target_lang,
                    "echoTargetLanguage": False,
                },
            },
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            "contextWindowCompression": {
                "triggerTokens": "0",
                "slidingWindow": {"targetTokens": "0"},
            },
        }
        await ws.send(json.dumps({"setup": setup}))

    async def _wait_setup_complete(self, ws) -> None:
        while True:
            root = self._parse(await ws.recv())
            if root is None:
                continue
            err = root.get("error")
            if isinstance(err, dict):
                raise RuntimeError(f"Gemini 错误: {err.get('message', '未知')}")
            if "setupComplete" in root:
                return
            self._handle_content(root)

    async def _recv_loop(self, ws) -> None:
        while self.running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue  # 定期醒来检查 running
            root = self._parse(raw)
            if root is None:
                continue
            if "goAway" in root:
                raise _GoAway()
            err = root.get("error")
            if isinstance(err, dict):
                raise RuntimeError(f"Gemini 错误: {err.get('message', '未知')}")
            self._handle_content(root)
        # 用户停止：优雅关闭
        try:
            await ws.close()
        except Exception:
            pass

    @staticmethod
    def _parse(raw) -> Optional[dict]:
        try:
            root = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            return None
        return root if isinstance(root, dict) else None

    def _handle_content(self, root: dict) -> None:
        content = root.get("serverContent")
        if not isinstance(content, dict):
            return
        out = content.get("outputTranscription")
        if isinstance(out, dict):
            text = out.get("text")
            if text:
                self.subtitle.emit(text)
        inp = content.get("inputTranscription")
        if isinstance(inp, dict):
            text = inp.get("text")
            if text:
                self.original.emit(text)
        if content.get("turnComplete") or content.get("generationComplete"):
            self.turnComplete.emit()

    async def _send_audio_async(self, pcm16: bytes) -> None:
        try:
            ws = self._ws
            if ws is None:
                return
            msg = {
                "realtimeInput": {
                    "audio": {
                        "mimeType": "audio/pcm;rate=16000",
                        "data": base64.b64encode(pcm16).decode("ascii"),
                    }
                }
            }
            await ws.send(json.dumps(msg))
        except Exception:
            pass  # 断线由 recv_loop 统一处理
        finally:
            self._pending = max(0, self._pending - 1)
