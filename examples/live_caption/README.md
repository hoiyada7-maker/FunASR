# 한국어 라이브 자막 (Live Caption)

마이크 또는 시스템 소리(스피커 출력)를 받아 **한국어 음성을 실시간 인식**하고,
화면 위에 항상 떠 있는 **반투명 자막**으로 표시합니다.

## 동작 구조 (2pass: 스트리밍 1차 + 오프라인 2차)

```
[오디오 캡처]        [처리]                                          [표시]
마이크 ─┐                  ┌─ 1차: 스트리밍 인식 ──→ 중간 자막(흐림) ─┐
        ├→ 16k → 온라인 VAD┤                                          ├→ 오버레이 창
시스템 ─┘ (soxr) (시작/끝)  └─ 2차: 끝나면 전체 재인식 → 확정 자막(밝음)┘  (항상 위·반투명)
소리
```

발화 구간을 **두 번** 인식합니다:
- **1차(중간 자막)** — `streaming-sensevoice` 로 청크 단위 점진 인식. 말하는 도중
  자막이 매끄럽게 자랍니다(순수 torch, CPU 청크당 ~0.05s). truncated attention 이라
  정확도는 약간 낮지만 미리보기 용도.
- **2차(확정 자막)** — 발화가 끝나면 **오프라인 모델로 전체 구간을 다시** 인식.
  전체 문맥을 보므로 **가장 정확**합니다. 이게 화면에 고정되는 최종 자막.

1차·2차 모두 **SenseVoice**(zh/en/ja/ko/yue) 를 사용합니다.

| 언어 | 1차(스트리밍) | 2차(확정·오프라인) |
|---|---|---|
| 한국어·영어·일어·auto 등 | streaming-sensevoice | SenseVoice (전체 문맥) |

> 공식 2pass(Paraformer)는 한국어 미지원, 영어 전용 `paraformer-en` 은 현재
> funasr/torch 버전과 호환되지 않아(출력 깨짐) 사용할 수 없었습니다. 그래서 한국어·영어
> 모두 SenseVoice 로 같은 2pass 패턴(스트리밍 1차 + 오프라인 전체 재인식 2차)을 직접
> 조립했습니다. 실측상 영어 확정 정확도는 매우 좋습니다(예: 스트리밍의 "50 pieces of
> code" → 확정 "50 pieces of gold." 로 교정).

- **VAD**: `fsmn-vad`(온라인 스트리밍) 로 말이 시작/끝나는 지점을 찾습니다.

## 필요 패키지

```bash
pip install funasr modelscope pyaudiowpatch soxr PyQt6 \
            asr-decoder online-fbank
# 추론은 torch(CPU) 로 동작합니다. 최초 실행 시 모델을 자동 다운로드합니다.
```

streaming-sensevoice 는 PyPI 에 없고 setup.py 도 없어, **clone 해서 패키지를 가져옵니다**
(저장소에는 `.gitignore` 로 제외되어 있으니 새 PC 에서 별도 clone 필요):

```bash
# 저장소 루트(FunASR)에서 실행
git clone --depth 1 https://github.com/pengzhendong/streaming-sensevoice.git examples/live_caption/third_party_ssv
```

`asr_worker.py` 가 `third_party_ssv/` 를 자동으로 import 경로에 추가합니다.

> Windows 전용: 시스템 소리 캡처는 WASAPI 루프백(`pyaudiowpatch`)을 사용합니다.

## 실행

```bash
cd examples/live_caption
python live_caption.py
```

실행하면 순서대로 묻습니다:

1. **실행 장치** — GPU(CUDA) 감지 시 선택, 없으면 CPU 자동
2. **소리 선택** — 🎤 마이크 / 🔊 시스템 소리(루프백) 목록에서 선택
3. **언어** — 한국어(기본) / 자동 / 영어 / 일본어

모델 로딩(최초 실행은 모델 다운로드로 시간이 걸립니다) 후 자막창이 뜹니다.

## 조작

- **드래그**: 자막창 위치 이동 (옮긴 위치 유지)
- **트레이 아이콘 우클릭**: 설정 / 자막 보이기·숨기기 / 종료 (더블클릭 → 설정창)
- **ESC**: 종료

자막은 최대 N줄(기본 5줄)만 보이고, 넘치면 최신이 아래로 자동 스크롤됩니다.

## 설정창

트레이 아이콘 우클릭 → **설정...** 에서 즉시 적용됩니다(재실행 시 유지).

- **글자 크기** (10~80pt)
- **글자 색**
- **한번에 보이는 줄 수** (1~15줄)
- **자막 저장 폴더** (md 파일 저장 위치)

## 자막 자동 저장 (.md)

확정 자막이 나올 때마다 md 파일에 한 줄씩 자동 누적됩니다.

```
22:10:41 조금만 생각을 하면서 살면 훨씬 편할 거야.
22:11:05 다음 문장입니다.
```

- 형식: `hh:mm:ss 자막내용` — 타임스탬프는 **해당 발화가 시작된 현재 시각**(time-of-day).
- 실행할 때마다 `caption_YYYY-MM-DD_HH-MM-SS.md` 파일을 새로 만듭니다.
- 기본 저장 폴더: `%USERPROFILE%\Documents\FunASR` (설정창에서 변경 가능, 변경 시 새 폴더에 새 파일 생성).

## 구성 파일

| 파일 | 역할 |
|---|---|
| `live_caption.py` | 진입점 — 장치/언어 선택, 전체 연결 |
| `audio_source.py` | 마이크·시스템 소리 캡처 → 16kHz mono 변환 |
| `asr_worker.py` | 온라인 VAD + 2pass(스트리밍 1차 + 오프라인 2차) 인식 |
| `overlay.py` | PyQt6 반투명 오버레이 자막창 + 트레이 아이콘 + 설정창 |
| `caption_log.py` | 확정 자막을 md 파일로 저장 (타임스탬프 포함) |
| `third_party_ssv/` | clone 한 streaming-sensevoice (git 추적 제외) |

## 참고 / 한계

- GPU 가속을 쓰려면 CUDA 빌드 torch 설치가 필요합니다(현재 CPU 기준).
- 메모리: 1차(스트리밍)·2차(오프라인) 모델을 함께 올리므로 RAM 을 더 씁니다(대략 +1GB).
- 중간 자막(1차)은 정확도가 약간 낮을 수 있으나, 발화가 끝나면 2차 오프라인
  전체 재인식으로 **정확한 확정 자막**이 화면에 고정됩니다.
- 한국어 출력에 토큰 경계 공백("조 금만")이 보일 수 있습니다 — SenseVoice 한국어
  출력 특성으로, 1차·2차 모두 나타납니다.
- 잡음이 많거나 음악이 섞이면 인식 정확도가 떨어질 수 있습니다.
