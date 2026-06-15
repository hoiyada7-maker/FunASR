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
    if "?" in name:
        return name.replace("?", "")
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

        # 루프백 장치 여부 판별
        loopback_indices = set()
        try:
            for lb in self._p.get_loopback_device_info_generator():
                loopback_indices.add(lb["index"])
        except Exception:
            pass
        is_loopback = device_index in loopback_indices

        # 루프백: 장치 네이티브 채널 수 사용 / 마이크: 모노 강제(안정적)
        self.channels = int(info["maxInputChannels"]) if is_loopback else 1
        self.native_rate = int(info["defaultSampleRate"])

        self._resampler = (
            soxr.ResampleStream(self.native_rate, TARGET_RATE, 1, dtype="float32")
            if self.native_rate != TARGET_RATE
            else None
        )

        frames = max(1, int(self.native_rate * chunk_ms / 1000))

        kind_str = "루프백(시스템 소리)" if is_loopback else "마이크"
        print(
            f"[오디오] {kind_str} | 채널:{self.channels} | {self.native_rate}Hz",
            flush=True,
        )

        # WASAPI 마이크는 Float32 가 네이티브인 경우가 많음 → Float32 우선 시도
        self._use_float32 = False
        opened = False
        for fmt in (pyaudio.paFloat32, pyaudio.paInt16):
            try:
                self._stream = self._p.open(
                    format=fmt,
                    channels=self.channels,
                    rate=self.native_rate,
                    input=True,
                    input_device_index=device_index,
                    frames_per_buffer=frames,
                    stream_callback=self._callback,
                    start=False,
                )
                self._use_float32 = fmt == pyaudio.paFloat32
                print(
                    f"[오디오] 형식: {'Float32' if self._use_float32 else 'Int16'}",
                    flush=True,
                )
                opened = True
                break
            except Exception as e:
                print(f"[오디오] {'Float32' if fmt == pyaudio.paFloat32 else 'Int16'} 실패: {e}", flush=True)

        if not opened:
            raise RuntimeError(f"오디오 스트림을 열 수 없습니다 (device_index={device_index})")

        self._first_chunk = True

    def _callback(self, in_data, frame_count, time_info, status):
        # 오디오 디코딩/리샘플링 — 실패 시 이번 청크만 스킵
        try:
            if self._use_float32:
                audio = np.frombuffer(in_data, dtype=np.float32).copy()
            else:
                audio = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
            if self.channels > 1:
                audio = audio.reshape(-1, self.channels).mean(axis=1)
            if self._resampler is not None:
                audio = self._resampler.resample_chunk(audio)
        except Exception as e:
            print(f"[audio] decode error: {e}", flush=True)
            return (None, pyaudio.paContinue)

        if audio.size:
            if self._first_chunk:
                self._first_chunk = False
                rms = float(np.sqrt(np.mean(audio ** 2)))
                print(f"[audio] first chunk ok  RMS={rms:.5f}", flush=True)
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
