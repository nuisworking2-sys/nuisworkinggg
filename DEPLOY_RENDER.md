# Render 배포

이 프로젝트는 Docker 배포용으로 준비되어 있습니다.

## 서버에 포함되는 것
- Python 3.12
- ffmpeg
- Noto CJK 한글 폰트
- FastAPI 웹 서버

따라서 사용자 PC에는 별도 프로그램을 설치할 필요가 없습니다.

## 필수 환경변수
- `GEMINI_API_KEY`: Gemini TTS

## 이미지 생성까지 사용할 때
- `OPENAI_API_KEY`: 현재 MVP의 이미지 자동 생성

## Render 권장 플랜
영상 렌더링과 이미지/TTS 처리 때문에 sleep이 있거나 CPU/RAM이 매우 작은 무료 플랜은 추천하지 않습니다.
`render.yaml`은 `starter`로 설정되어 있습니다.

## 저장소 주의사항
생성 결과는 서버 로컬 디스크에 일시 저장됩니다. Render 재배포/재시작 때 사라질 수 있습니다.
현재 용도는 생성 즉시 브라우저로 MP4/ZIP을 다운로드하는 방식입니다.

## Render Blueprint
GitHub 저장소 루트에 `render.yaml`이 있으면 Blueprint로 서비스 생성이 가능합니다.
Dockerfile도 루트에 준비되어 있습니다.
