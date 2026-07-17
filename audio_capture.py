"""音频采集：WASAPI loopback（系统声音）或麦克风，重采样到 16kHz 单声道 PCM16。

采集在 PyAudio 回调线程进行，处理后的 16k PCM16 字节块通过 on_chunk 回调交出
（由 GeminiClient 用 run_coroutine_threadsafe 投递到 asyncio 线程）。
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

import numpy as np

TARGET_RATE = 16000
CHUNK_MS = 100  # 每块约 100ms


class AudioCaptureError(Exception):
    pass


class AudioCapture:
    def __init__(self, source: str, on_chunk: Callable[[bytes], None]):
        self.source = source  # "system" | "mic"
        self.on_chunk = on_chunk
        self._pa = None
        self._stream = None
        self._lock = threading.Lock()
        self._device_rate = TARGET_RATE
        self._device_channels = 1
        self._resample_pos = 0.0  # 线性插值重采样的相位余量
        self._tail = np.zeros(0, dtype=np.float32)

    # ---- 设备选择 ----

    def _open(self):
        try:
            import pyaudiowpatch as pyaudio
        except ImportError as e:
            raise AudioCaptureError("缺少 PyAudioWPatch，请先 pip install -r requirements.txt") from e

        self._pa = pyaudio.PyAudio()
        if self.source == "system":
            try:
                wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
                default_out = self._pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
                device = None
                for loopback in self._pa.get_loopback_device_info_generator():
                    if default_out["name"] in loopback["name"]:
                        device = loopback
                        break
                if device is None:
                    device = next(self._pa.get_loopback_device_info_generator(), None)
                if device is None:
                    raise AudioCaptureError("未找到 WASAPI loopback 设备")
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
            if self._device_rate != TARGET_RATE:
                data = self._resample(data)
            if data.size:
                out = np.clip(data, -32768, 32767).astype(np.int16).tobytes()
                self.on_chunk(out)
        except Exception:
            pass  # 采集回调里绝不抛异常，否则流会停
        return (None, pyaudio.paContinue)

    def _resample(self, data: np.ndarray) -> np.ndarray:
        """流式线性插值重采样（块间保持相位连续）。"""
        src = np.concatenate([self._tail, data])
        if src.size < 2:
            self._tail = src
            return np.zeros(0, dtype=np.float32)
        step = self._device_rate / TARGET_RATE
        # 可取的输出采样点位置
        positions = np.arange(self._resample_pos, src.size - 1, step)
        out = np.interp(positions, np.arange(src.size), src).astype(np.float32)
        consumed = int(positions[-1]) if positions.size else 0
        self._resample_pos = (positions[-1] + step - consumed) if positions.size else self._resample_pos
        self._tail = src[consumed:]
        # 防止 tail 无限增长
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
