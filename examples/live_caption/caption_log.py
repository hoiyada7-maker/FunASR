"""자막을 md 파일로 저장.

확정 자막이 나올 때마다 `hh:mm:ss 자막내용` 한 줄씩 추가한다. 타임스탬프는 해당
발화가 시작된 **현재 시각**(time-of-day)이다. 세션마다 새 파일을 만든다.
저장 폴더는 런타임에 바꿀 수 있다(설정창).
"""

import os
import threading
import datetime


def default_folder() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents", "FunASR")


def _fmt_ts(epoch: float) -> str:
    """wall-clock epoch(초) → 현재 시각 hh:mm:ss."""
    return datetime.datetime.fromtimestamp(epoch).strftime("%H:%M:%S")


class CaptionLogger:
    def __init__(self, folder: str = None):
        self._lock = threading.Lock()
        self.folder = folder or default_folder()
        self.path = None
        self._start_session()

    def _start_session(self):
        os.makedirs(self.folder, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.path = os.path.join(self.folder, f"caption_{stamp}.md")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(f"# 자막 기록 {stamp}\n\n")

    def set_folder(self, folder: str):
        """저장 폴더 변경 → 새 폴더에 새 세션 파일을 만든다."""
        with self._lock:
            if not folder or folder == self.folder:
                return
            self.folder = folder
            self._start_session()

    def append(self, epoch: float, text: str):
        """확정 자막 한 줄 추가 (마크다운 줄바꿈을 위해 끝에 공백 2개)."""
        text = (text or "").strip()
        if not text:
            return
        line = f"{_fmt_ts(epoch)} {text}  \n"
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
