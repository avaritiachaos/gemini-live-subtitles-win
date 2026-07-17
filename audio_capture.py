"""音频采集：WASAPI loopback（系统声音）或麦克风，重采样到 16kHz 单声道 PCM16。

采集在 PyAudio 回调线程进行，处理后的 16k PCM16 字节块通过 on_chunk 回调交出
（由 GeminiClient 用 run_coroutine_threadsafe 投递到 asyncio 线程）。

支持：
- 指定输出设备（配合虚拟声卡实现静音观看）
- VAD 静音门控：低于阈值的静音段不上传，节省 TPM 配额
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

import numpy as np

TARGET_RATE = 16000
CHUNK_MS = 100  # 每块约 100ms
VAD_HANGOVER_CHUNKS = 8  # 语音结束后继续上传 ~0.8s，避免截掉句尾


class AudioCaptureError(Exception):
    pass


def list_loopback_devices() -> list[str]:
    """枚举可监听的输出设备名（loopback），供设置界面选择。"""
    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        return []
    names: list[str] = []
    try:
        pa = pyaudio.PyAudio()
    except OSError:
        return []
    try:
        for dev in pa.get_loopback_device_info_generator():
            name = str(dev.get("name", "")).replace(" [Loopback]", "")
            if name and name not in names:
                names.append(name)
    except OSError:
        pass
    finally:
        pa.terminate()
    return names


class AudioCapture:
    def __init__(
        self,
        source: str,
        on_chunk: Callable[[bytes], None],
        device_name: str = "",
        vad_enabled: bool = False,
        vad_threshold: int = 200,
    ):
        self.source = source  # "system" | "mic"
        self.on_chunk = on_chunk
        self.device_name = device_name.strip()
        self.vad_enabled = vad_enabled
        self.vad_threshold = max(1, vad_threshold)
        self._voice_hold = 0  # 剩余的 hangover 块数
        self._pa = None
        self._stream = None
        self._lock = threading.Lock()
        self._device_rate = TARGET_RATE
        self._device_channels = 1
        self._resample_pos = 0.0  # 线性插值重采样的相位余量
        self._tail = np.zeros(0, dtype=np.float32)

    # ---- 设备选择 ----

    def _pick_system_device(self, pa):
        """选 loopback 设备：优先用户指定名，其次默认输出，最后任意一个。"""
        loopbacks = list(pa.get_loopback_device_info_generator())
        if not loopbacks:
            raise AudioCaptureError("未找到 WASAPI loopback 设备")
        if self.device_name:
            for dev in loopbacks:
                if self.device_name in str(dev.get("name", "")):
                    return dev
            raise AudioCaptureError(
                f"找不到设备「{self.device_name}」，请到设置里重新选择"
            )
        try:
            import pyaudiowpatch as pyaudio
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_out = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            for dev in loopbacks:
                if default_out["name"] in dev["name"]:
                    return dev
        except OSError:
            pass
        return loopbacks[0]

    def _open(self):
        try:
            import pyaudiowpatch as pyaudio
        except ImportError as e:
            raise AudioCaptureError("缺少 PyAudioWPatch，请先 pip install -r requirements.txt") from e

        self._pa = pyaudio.PyAudio()
        if self.source == "system":
            try:
                device = self._pick_system_device(self._pa)
            except OSError as e:
                raise AudioCaptureError(f"WASAPI 初始化失败: {e}") from e
        else:
            try:
                device = self._pa.get_default_input_device_info()
            except OSError as e:
                raise AudioCaptureError("未找到麦克风设备") from e

        self._device_rate = int(device["defaultSampleRate"])
        self._device_channels = max(1, int(device["maxInputChannels"]))
        frames = int(self._device_rate * CHUNK_MS / 1000)
        try:
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=self._device_channels,
                rate=self._device_rate,
                input=True,
                input_device_index=device["index"],
                frames_per_buffer=frames,
                stream_callback=self._callback,
            )
        except OSError as e:
            raise AudioCaptureError(f"打开音频流失败: {e}") from e

    def _callback(self, in_data, frame_count, time_info, status):
        import pyaudiowpatch as pyaudio
        try:
            pcm = np.frombuffer(in_data, dtype=np.int16)
            if self._device_channels > 1:
                pcm = pcm.reshape(-1, self._device_channels).mean(axis=1)
            data = pcm.astype(np.float32)
            if self._gate(data):
                if self._device_rate != TARGET_RATE:
                    data = self._resample(data)
                if data.size:
                    out = np.clip(data, -32768, 32767).astype(np.int16).tobytes()
                    self.on_chunk(out)
        except Exception:
            pass  # 采集回调里绝不抛异常，否则流会停
        return (None, pyaudio.paContinue)

    def _gate(self, data: np.ndarray) -> bool:
        """VAD 门控：返回 False 表示该块为持续静音，跳过上传。"""
        if not self.vad_enabled or data.size == 0:
            return True
        rms = float(np.sqrt(np.mean(np.square(data))))
        if rms >= self.vad_threshold:
            self._voice_hold = VAD_HANGOVER_CHUNKS
            return True
        if self._voice_hold > 0:
            self._voice_hold -= 1
            return True
        # 长静音：把重采样相位也复位，避免恢复时残留旧尾巴
        self._tail = np.zeros(0, dtype=np.float32)
        self._resample_pos = 0.0
        return False

    def _resample(self, data: np.ndarray) -> np.ndarray:
        """流式线性插值重采样（块间保持相位连续）。"""
        src = np.concatenate([self._tail, data])
        if src.size < 2:
            self._tail = src
            return np.zeros(0, dtype=np.float32)
        step = self._device_rate / TARGET_RATE
        positions = np.arange(self._resample_pos, src.size - 1, step)
        out = np.interp(positions, np.arange(src.size), src).astype(np.float32)
        consumed = int(positions[-1]) if positions.size else 0
        self._resample_pos = (positions[-1] + step - consumed) if positions.size else self._resample_pos
        self._tail = src[consumed:]
        if self._tail.size > self._device_rate:
            self._tail = self._tail[-2:]
            self._resample_pos = 0.0
        return out

    # ---- 生命周期 ----

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            try:
                self._open()
                self._stream.start_stream()
            except Exception:
                self._cleanup()
                raise

    def stop(self) -> None:
        with self._lock:
            self._cleanup()

    def _cleanup(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except OSError:
                pass
            self._stream = None
        if self._pa is not None:
            try:
                self._pa.terminate()
            except OSError:
                pass
            self._pa = None
        self._tail = np.zeros(0, dtype=np.float32)
        self._resample_pos = 0.0
        self._voice_hold = 0
