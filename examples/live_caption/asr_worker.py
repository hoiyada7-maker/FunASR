"""실시간 인식 워커 (2pass: 스트리밍 1차 + 오프라인 2차).

발화 구간을 두 번 인식한다.
  - 1차(중간 자막): StreamingSenseVoice 로 청크 단위 점진 인식 → 말하는 도중 매끄럽게 갱신
  - 2차(확정 자막): 발화가 끝나면 **오프라인 모델로 전체 구간을 다시** 인식 → 최고 정확도

1차(스트리밍)·2차(오프라인) 모두 SenseVoice(zh/en/ja/ko/yue) 를 사용한다.
한국어/영어 모두 공식 2pass(Paraformer)를 쓸 수 없어(한국어 미지원, 영어 Paraformer-en
모델은 현재 funasr/torch 버전과 호환 안 됨 → garbage 출력) SenseVoice 로 동일한
2pass 패턴(스트리밍 1차 + 오프라인 전체 재인식 2차)을 직접 조립했다.

콜백: on_result(text: str, is_final: bool)
"""

import os
import sys
import time
import queue
import threading
import collections

import numpy as np

from funasr import AutoModel
from funasr.utils.postprocess_utils import rich_transcription_postprocess

# 같은 폴더의 third_party_ssv 에 clone 된 streaming-sensevoice 패키지 사용
_SSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "third_party_ssv")
if _SSV_PATH not in sys.path:
    sys.path.insert(0, _SSV_PATH)

VAD_MODEL = "fsmn-vad"
ASR_MODEL = "iic/SenseVoiceSmall"
SR = 16000
VAD_CHUNK_MS = 200
VAD_STEP = VAD_CHUNK_MS * SR // 1000  # 3200 samples = 200ms
PREROLL_FRAMES = 4  # 발화 시작 직전 프레임(=VAD 감지 지연 보정)을 함께 먹임
MAX_UTT_SEC = 30  # 너무 긴 발화 버퍼 보호


class AsrWorker(threading.Thread):
    def __init__(
        self,
        on_result,
        language="ko",
        device_id="-1",
        textnorm=True,
        on_status=None,
        on_final=None,
    ):
        super().__init__(daemon=True)
        self.on_result = on_result
        self.on_status = on_status or (lambda msg: None)
        # on_final(start_epoch, text): 확정 자막 + 발화 시작 시각(wall-clock epoch, 파일 저장용)
        self.on_final = on_final or (lambda ts, text: None)
        self.language = language
        self.textnorm = textnorm
        self.device = "cpu" if str(device_id) == "-1" else f"cuda:{device_id}"

        self._q = queue.Queue()  # 입력 오디오 청크
        self._stop = threading.Event()
        self._ready = threading.Event()

        self._pending = np.zeros(0, dtype=np.float32)  # VAD 에 아직 안 먹인 잔여
        self._recent = collections.deque(maxlen=PREROLL_FRAMES)  # 프리롤용 최근 프레임
        self._in_speech = False
        self._last_text = ""  # 현재 발화의 마지막 중간자막
        self._utt = []  # 현재 발화의 전체 오디오 프레임 (2차 오프라인용)
        self._utt_start = 0.0  # 현재 발화 시작 시각 (wall-clock epoch)

    # ---- 외부 인터페이스 ----
    def feed(self, audio: np.ndarray):
        """캡처 스레드가 16k mono float32 청크를 넣는다."""
        self._q.put(audio)

    def stop(self):
        self._stop.set()
        self._q.put(None)

    def wait_ready(self, timeout=None):
        return self._ready.wait(timeout)

    # ---- 워커 스레드 ----
    def run(self):
        self.on_status("모델 로딩 중... (VAD)")
        self.vad = AutoModel(model=VAD_MODEL, disable_update=True, device=self.device)

        self.on_status("모델 로딩 중... (스트리밍 1차)")
        from streaming_sensevoice import StreamingSenseVoice

        self.ssv = StreamingSenseVoice(
            language=self.language,
            textnorm=self.textnorm,
            device=self.device,
            model=ASR_MODEL,
        )

        self.on_status("모델 로딩 중... (확정 2차: SenseVoice)")
        self.final = AutoModel(
            model=ASR_MODEL,
            trust_remote_code=False,
            disable_update=True,
            device=self.device,
        )

        self._vad_cache = {}
        self.on_status("준비 완료")
        self._ready.set()

        while not self._stop.is_set():
            try:
                chunk = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if chunk is None:
                break
            self._process(chunk)

    def _process(self, chunk: np.ndarray):
        self._pending = np.concatenate([self._pending, chunk])

        while len(self._pending) >= VAD_STEP:
            frame = self._pending[:VAD_STEP]
            self._pending = self._pending[VAD_STEP:]
            self._recent.append(frame)

            start_now, end_now = self._vad_step(frame)

            if start_now and not self._in_speech:
                self._in_speech = True
                self._last_text = ""
                self._utt_start = time.time()  # 발화 시작 시각(현재 시각)
                self.ssv.reset()
                audio = np.concatenate(list(self._recent))  # 프리롤 포함
                self._utt = [audio]
            elif self._in_speech:
                audio = frame
                self._utt.append(frame)
                if sum(len(a) for a in self._utt) > MAX_UTT_SEC * SR:
                    end_now = True  # 과도하게 길면 강제 확정
            else:
                continue

            # 1차: 스트리밍 중간자막
            text = self._stream_infer(audio, is_last=end_now)
            if not end_now:
                if text:
                    self._last_text = text
                    self.on_result(text, False)
                continue

            # 2차: 발화 끝 → 오프라인 전체 재인식(확정)
            full = np.concatenate(self._utt) if self._utt else audio
            final = self._final_recognize(full) or text or self._last_text
            if final:
                self.on_result(final, True)
                self.on_final(self._utt_start, final)
            self._in_speech = False
            self._last_text = ""
            self._utt = []

    def _vad_step(self, frame):
        """VAD 한 프레임 → (발화 시작?, 발화 끝?)."""
        res = self.vad.generate(
            input=frame,
            cache=self._vad_cache,
            is_final=False,
            chunk_size=VAD_CHUNK_MS,
            disable_pbar=True,
        )
        segs = res[0].get("value") if res else None
        start_now = end_now = False
        if segs:
            for beg_ms, end_ms in segs:
                if beg_ms != -1:
                    start_now = True
                if end_ms != -1:
                    end_now = True
        return start_now, end_now

    def _stream_infer(self, audio: np.ndarray, is_last: bool) -> str:
        """1차 스트리밍 인식 → 이번 호출의 마지막 텍스트 반환."""
        last = ""
        try:
            for res in self.ssv.streaming_inference(audio * 32768, is_last):
                t = res.get("text", "")
                if t:
                    last = t
        except Exception:
            pass
        return last

    def _final_recognize(self, audio: np.ndarray) -> str:
        """2차 오프라인 전체 인식(확정) — SenseVoice 전체 문맥."""
        try:
            res = self.final.generate(
                input=audio,
                cache={},
                language=self.language,
                use_itn=True,
                disable_pbar=True,
            )
            return rich_transcription_postprocess(res[0]["text"]) if res else ""
        except Exception:
            return ""
