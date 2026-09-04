# 숏폼 자동 제작 웹사이트 (Web Only)

이 프로젝트는 **로컬 Helper 없이** 브라우저에서 여러 TXT를 한 번에 올리고,

- 숏폼 대본 변환
- 대본 미리보기, 자동 카운트다운 및 수정 후 즉시 진행
- Gemini TTS 생성
- 자막 SRT 생성
- 대사별 이미지 생성(선택)
- 최종 MP4 렌더링
- ZIP 자동 다운로드

를 순차적으로 수행하는 **웹사이트 MVP**입니다.

## 이 버전에서 중요한 점

사용자가 “로컬은 복잡해서 싫다”고 했기 때문에, 이 버전은 **CapCut 데스크톱 자동 조작을 제거**했습니다.
대신 서버가 **최종 MP4를 직접 렌더링**합니다.

즉, 이 버전의 출력은:
- `final.mp4`
- `voice.wav`
- `subtitles.srt`
- `script.txt`
- `images/*.png` (이미지 생성 켠 경우)
- `manifest.json`

입니다.

> 진짜 CapCut Draft 파일 자동 생성/자동 Export까지 하려면 다시 Windows Helper 버전으로 가야 합니다.

---

## 지원 기능

- TXT 여러 개 동시 업로드
- **하나 완료되면 다음 TXT 자동 처리**
- 처리 중 중지(현재 API 호출이 끝나는 안전 지점에서 정지)
- 대본/이미지/캐릭터/TTS 프롬프트 직접 편집
- 외부 API를 호출하지 않는 개발용 더미 모드
- 배경 설정
  - 단색 배경
  - 사용자 배경 이미지 업로드
- 이미지 위치 설정
  - X / Y / Scale
- 자막 위치 설정
  - 정렬(top/middle/bottom)
  - Y 좌표
- 자막 폰트 설정
  - 폰트 이름
  - 폰트 크기
  - 외곽선 두께
- 자동 다운로드
  - MP4 자동 다운로드
  - ZIP 자동 다운로드
  - 전체 결과 ZIP 다운로드

---

## 필요 환경

- Python 3.10+
- ffmpeg 설치
- Gemini API Key
- (선택) OpenAI API Key

### API 키 역할과 보안

이 버전은 **브라우저 입력창으로 API 키를 넣을 수 있음**.
입력한 키는 이번 배치 처리에만 사용하고 서버 디스크에는 저장하지 않도록 만들었다.

- `Gemini API Key`: Gemini TTS 필수
- `OpenAI API Key`: ChatGPT 이미지 생성용
- 서버 `.env`에 미리 넣어두는 방식도 가능
- 브라우저 입력 키는 런타임 메모리에만 보관하며 `status.json`, 로그, 결과 ZIP, DB에 쓰지 않음
- 완료/오류/중지 시 런타임 메모리에서 제거

---

## 빠른 실행 (로컬 테스트)

```bash
cd server
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # macOS/Linux는 cp .env.example .env
# .env에 API 키 입력

uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

브라우저에서:

```text
http://127.0.0.1:8000
```

접속.

---

## ffmpeg 설치

### Windows
- https://www.gyan.dev/ffmpeg/builds/ 같은 배포판 설치 후 `ffmpeg.exe`가 PATH에 잡히게 설정

### macOS
```bash
brew install ffmpeg
```

### Ubuntu / Debian
```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

---

## 배포 추천

사용자 입장에서 “설치 없이 웹사이트처럼” 쓰려면 서버를 올리면 됩니다.

추천:
- **Render**
- **Railway**
- **Fly.io**
- **VPS + Nginx + systemd**

주의:
- ffmpeg가 설치 가능한 환경이어야 함
- 이미지 생성이 많으면 시간/비용 증가
- 자동 다운로드는 브라우저가 다중 다운로드 허용을 요구할 수 있음

---

## 폴더 구조

```text
shorts_factory_web/
├─ README_KO.md
├─ START_HERE.txt
├─ server/
│  ├─ app.py
│  ├─ ai.py
│  ├─ audio_utils.py
│  ├─ script_utils.py
│  ├─ video_utils.py
│  ├─ requirements.txt
│  ├─ .env.example
│  ├─ data/
│  │  └─ batches/
│  └─ static/
│     ├─ index.html
│     ├─ app.js
│     └─ style.css
├─ setup_web.ps1
└─ start_web.bat
```

---

## 한계 / 주의사항

1. **CapCut 데스크톱 draft 자동 생성은 이 버전에 없음**
   - 이유: 사용자가 “로컬 복잡성 제거”를 원했기 때문

2. **브라우저의 완전한 무인 자동 다운로드는 브라우저 설정 영향**
   - 보통 자동 다운로드 가능
   - 다중 다운로드는 한 번 허용 팝업이 뜰 수 있음

3. **폰트는 서버에 설치된 폰트 기준**
   - `Pretendard`라고 써도 서버에 없으면 대체 폰트로 렌더링될 수 있음

4. **이미지는 기본적으로 ChatGPT(OpenAI)로 생성**
   - 화면의 OpenAI API Key 입력창에 키를 넣으면 바로 사용 가능
   - 필요하면 드롭다운에서 Gemini 이미지 생성으로 변경 가능

---

## 테스트

```bash
pip install pytest httpx
pytest -q
```

더미 모드 통합 테스트는 외부 AI API 없이 업로드, 음성 타이밍, SRT, 다운로드 API, ZIP과 키 비저장을 검사합니다.

## 다음 버전에서 붙일 수 있는 것

- 프로젝트별 프리셋 저장
- BGM 업로드/볼륨 조절
- 썸네일 자동 생성
- ZIP 대신 Google Drive/S3 저장
- 진짜 CapCut Draft 생성 모드 추가


---

## 배포 준비 완료

루트에 아래 파일이 추가되어 있습니다.

- `Dockerfile` : Python + ffmpeg + 한글 폰트가 들어간 서버 이미지
- `render.yaml` : Render Blueprint 설정
- `.dockerignore`
- `DEPLOY_RENDER.md`

클라우드 서버에서는 `Noto Sans CJK KR` 폰트가 가장 확실합니다.
웹 화면의 자막 폰트 칸에 `Noto Sans CJK KR`를 입력하면 Docker 서버에 포함된 한글 폰트를 사용할 수 있습니다.
