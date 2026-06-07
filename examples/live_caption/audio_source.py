"""오디오 캡처 모듈.

마이크 또는 시스템 소리(WASAPI 루프백)를 받아 16kHz mono float32 로 변환해
콜백으로 흘려보낸다. 마이크와 루프백을 같은 PyAudio(pyaudiowpatch) API 로 통일해
다룬다.
"""

import numpy as np
import soxr
import pyaudiowpatch as pyaudio

TARGET_RATE = 16000


def _clean_name(name: str) -> str:
    """Windows 한글 장치명이 깨져 들어오는 경우를 위한 best-effort 정리."""
    if "�" in name:  # U+FFFD (디코딩 실패 문자) 가 섞이면 복구 불가
        return name.replace("�", "")
    return name


def list_devices():
    """선택 가능한 입력 장치 목록을 반환한다.

    각 항목: dict(index, name, channels, rate, kind)  kind ∈ {"mic", "loopback"}
    """
    p = pyaudio.PyAudio()
    devices = []
    try:
        # 시스템 소리(루프백) 장치
        loopback_indices = set()
        try:
            for lb in p.get_loopback_device_info_generator():
                loopback_indices.add(lb["index"])
                devices.append(
                    {
                        "index": lb["index"],
                        "name": _clean_name(lb["name"]),
                        "channels": int(lb["maxInputChannels"]),
                        "rate": int(lb["defaultSampleRate"]),
                        "kind": "loopback",
                    }
                )
        except Exception:
            pass

        # 마이크(일반 입력) 장치 - 중복/루프백 제외
        seen = set()
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info["maxInputChannels"] <= 0:
                continue
            if i in loopback_indices:
                continue
            name = _clean_name(info["name"])
            key = (name, int(info["maxInputChannels"]))
            if key in seen:
                continue
            seen.add(key)
            devices.append(
                {
                    "index": i,
                    "name": name,
                    "channels": int(info["maxInputChannels"]),
                    "rate": int(info["defaultSampleRate"]),
                    "kind": "mic",
                }
            )
    finally:
        p.terminate()
    return devices


def default_loopback_index():
    """기본 스피커의 루프백 장치 index 를 반환 (없으면 None)."""
    p = pyaudio.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        for lb in p.get_loopback_device_info_generator():
            if default_out["name"] in lb["name"]:
                return lb["index"]
    except Exception:
        return None
    finally:
        p.terminate()
    return None


class AudioSource:
    """선택한 장치에서 16kHz mono float32 오디오를 콜백으로 전달한다."""

    def __init__(self, device_index, on_audio, chunk_ms=100):
        self.on_audio = on_audio
        self._p = pyaudio.PyAudio()
        info = self._p.get_device_info_by_index(device_index)
        self.channels = int(info["maxInputChannels"])
        self.native_rate = int(info["defaultSampleRate"])
        self._resampler = (
            soxr.ResampleStream(self.native_rate, TARGET_RATE, 1, dtype="float32")
            if self.native_rate != TARGET_RATE
            else None
        )
        frames = max(1, int(self.native_rate * chunk_ms / 1000))
        self._stream = self._p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.native_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=frames,
            stream_callback=self._callback,
            start=False,
        )

    def _callback(self, in_data, frame_count, time_info, status):
        audio = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
        if self.channels > 1:
            audio = audio.reshape(-1, self.channels).mean(axis=1)
        if self._resampler is not None:
            audio = self._resampler.resample_chunk(audio)
        if audio.size:
            self.on_audio(audio)
        return (None, pyaudio.paContinue)

    def start(self):
        self._stream.start_stream()

    def stop(self):
        try:
            if self._stream.is_active():
                self._stream.stop_stream()
            self._stream.close()
        finally:
            self._p.terminate()
