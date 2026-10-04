# DID — 디스코드 봇 만들기 (AI 도도봇)

프롬프트만 적으면 AI 도도봇이 discord.py 봇을 만들어 주고, 켜기/끄기·서버 관리·업데이트까지 웹에서 하는 FastAPI 앱입니다.

## 실행
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # ANTHROPIC_API_KEY 입력
python app.py               # http://localhost:8000
```

## 사용 순서
1. `/login`에서 회원가입 → `/app` 패널 접속
2. [디스코드 개발자 포털](https://discord.com/developers/applications)에서 내 계정으로 봇을 만들고 **Application ID**와 **봇 토큰** 복사 (Bot 메뉴에서 필요한 Privileged Intents 켜기)
3. DID에 붙여넣고, 명령어·기능을 프롬프트에 전부 적어 도도봇에게 생성 요청
4. 봇 목록에서 봇 클릭 → 켜기/끄기, 서버 목록, 추방, 서버별 사용 차단, 업데이트(수정 요청·직접 수정·버전 되돌리기)

## 환경변수
| 이름 | 설명 |
|---|---|
| `ANTHROPIC_API_KEY` | 필수. 도도봇(AI) 코딩용 |
| `DID_SITE_URL` | 사이트 주소 (sitemap/SEO용, 예: https://did.example.com) |
| `PORT` | 포트 (기본 8000) |
| `DID_ALLOW_SIGNUP` | `0`이면 회원가입 차단 |
| `DID_MAX_BOTS` | 사용자당 최대 봇 수 (기본 5) |
| `DID_SECRET_KEY` | 토큰 암호화 키(Fernet). 잃어버리면 저장된 토큰 복구 불가 |

## 보안 주의
- AI가 만든 코드는 서버에서 그대로 실행됩니다. **신뢰할 수 있는 사용자에게만** 공개하거나 Docker 등으로 격리하세요.
- `data/`(DB, 암호화 키, 로그)와 `.env`는 저장소에 올리지 마세요 (`.gitignore`에 포함됨).
- 서버를 재시작하면 켜져 있던 봇은 꺼집니다.

## 검색 노출
`/`(랜딩, meta·구조화 데이터), `/sitemap.xml`, `/robots.txt` 제공. Google Search Console·네이버 서치어드바이저에 등록하세요.
