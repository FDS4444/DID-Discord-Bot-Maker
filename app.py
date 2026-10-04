"""DID - Discord bot Maker. 실행: python app.py   (필요한 패키지는 처음 실행할 때 자동 설치됩니다)
환경변수: ANTHROPIC_API_KEY(필수), DID_SITE_URL(예: https://did.example.com),
선택: DID_SECRET_KEY(Fernet 키, 없으면 data/secret.key 자동생성), DID_ALLOW_SIGNUP(1/0), DID_MAX_BOTS(기본 5)
"""
# ── 필요한 패키지 (requirements 통합): 없으면 자동 설치 ──
REQUIREMENTS = {"fastapi": "fastapi", "uvicorn": "uvicorn", "anthropic": "anthropic",
                "requests": "requests", "discord": "discord.py>=2.3", "cryptography": "cryptography"}
import importlib.util, subprocess, sys
_missing = [pkg for mod, pkg in REQUIREMENTS.items() if importlib.util.find_spec(mod) is None]
if _missing:
    print("[DID] 패키지 설치 중:", ", ".join(_missing), flush=True)
    subprocess.check_call([sys.executable, "-m", "pip", "install", *_missing])
import os, sys, ast, json, re, sqlite3, subprocess, secrets
from pathlib import Path
import requests, anthropic
import hashlib
from contextvars import ContextVar
from cryptography.fernet import Fernet
from fastapi import FastAPI, Depends, HTTPException, Cookie
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, JSONResponse, RedirectResponse

BASE = Path(__file__).parent
if (BASE / ".env").exists():  # .env 파일 지원 (이미 설정된 환경변수가 우선)
    for _l in (BASE / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in _l and not _l.strip().startswith("#"):
            _k, _v = _l.split("=", 1); os.environ.setdefault(_k.strip(), _v.strip().strip('"'))
DATA = BASE / "data"; DATA.mkdir(exist_ok=True)
(DATA / "bots").mkdir(exist_ok=True)
DB = str(DATA / "did.db")
SITE = os.environ.get("DID_SITE_URL", "http://localhost:8000")
MODEL = os.environ.get("DID_MODEL", "claude-sonnet-5-5")
API = "https://discord.com/api/v10"
procs = {}
app = FastAPI(title="DID")
CUR = ContextVar("cur", default=0)
MAX_BOTS = int(os.environ.get("DID_MAX_BOTS", "5"))
KF = DATA / "secret.key"
if not os.environ.get("DID_SECRET_KEY") and not KF.exists(): KF.write_bytes(Fernet.generate_key())
FER = Fernet(os.environ.get("DID_SECRET_KEY") or KF.read_bytes())
enc = lambda t: FER.encrypt(t.encode()).decode()
dec = lambda t: FER.decrypt(t.encode()).decode()

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS bots(id INTEGER PRIMARY KEY, name TEXT, token TEXT,
      app_id TEXT, invite TEXT, prompt TEXT, code TEXT, status TEXT DEFAULT 'ok', owner INTEGER DEFAULT 0)""")
    c.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, salt TEXT, pw TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS sessions(sid TEXT PRIMARY KEY, user_id INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY, bot_id INTEGER, code TEXT, note TEXT, ts TEXT DEFAULT CURRENT_TIMESTAMP)")
    try: c.execute("ALTER TABLE bots ADD COLUMN owner INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    return c

def user_of(sid):
    if not sid: return 0
    with db() as c:
        r = c.execute("select user_id from sessions where sid=?", (sid,)).fetchone()
    return r[0] if r else 0

async def auth(did_sid: str = Cookie(None)):
    u = user_of(did_sid)
    if not u: raise HTTPException(401, "로그인이 필요합니다")
    CUR.set(u)

FEATURES = {
 "moderation": "kick/ban/timeout/clear 명령어와 권한 검사", "automod": "욕설·링크·도배 자동 삭제와 경고 누적",
 "welcome": "입장/퇴장 환영 메시지와 자동 역할", "reaction_roles": "버튼으로 역할 받기",
 "tickets": "버튼으로 문의 채널 생성·닫기", "leveling": "채팅 경험치·레벨·랭킹",
 "economy": "출석 보상, 상점, 송금", "polls": "투표 생성과 결과 집계", "giveaway": "추첨 이벤트 생성·종료",
 "logging": "메시지 삭제/수정, 입퇴장 로그 채널", "reminders": "시간 지정 알림", "games": "가위바위보, 주사위, 퀴즈",
 "utility": "서버/유저 정보, 아바타, ping, 계산기", "embeds": "관리자용 임베드 메시지 작성",
 "suggestions": "건의 채널과 추천/비추천 투표", "verify": "버튼 인증 후 역할 지급",
 "starboard": "별 이모지 많이 받은 글 하이라이트", "birthdays": "생일 등록과 축하 메시지",
 "counting": "숫자 세기 채널", "afk": "자리비움 상태와 멘션 안내",
 "voice_rooms": "입장 시 개인 음성 채널 자동 생성", "stats": "서버 통계 채널(멤버 수 등)",
 "slowmode": "채널 슬로우모드 설정", "lockdown": "채널 잠금/해제", "warn_system": "경고 누적 시 자동 제재",
 "mute_timer": "기간제 뮤트/언뮤트", "anti_raid": "대량 입장 감지·자동 방어", "anti_spam": "도배·멘션 폭탄 방지",
 "invite_tracker": "초대 링크별 입장자 추적", "auto_thread": "특정 채널 글에 스레드 자동 생성",
 "sticky_message": "채널 하단 고정 메시지", "announce": "공지 예약/반복 전송", "custom_responses": "키워드 자동 응답 관리",
 "role_menu": "드롭다운 역할 선택 메뉴", "temp_roles": "기간제 역할 부여", "xp_roles": "레벨별 역할 자동 지급",
 "daily_quests": "일일 퀘스트와 보상", "shop_inventory": "상점·인벤토리·아이템 사용", "gambling": "슬롯·블랙잭 미니게임",
 "trivia": "퀴즈 대결과 점수판", "hangman": "행맨 게임", "tictactoe": "틱택토 2인 대전", "wordchain": "끝말잇기 게임",
 "pets": "가상 펫 키우기", "profile_cards": "프로필 카드와 자기소개", "leaderboards": "서버 랭킹(레벨·돈·활동)",
 "message_stats": "유저/채널별 메시지 통계", "snipe": "삭제 메시지 확인(관리자)", "audit_log": "관리 행동 기록 채널",
 "ticket_transcripts": "티켓 대화 기록 저장", "feedback_forms": "모달 입력 설문/신청서", "staff_apply": "스태프 지원서 접수와 심사",
 "event_calendar": "서버 일정 등록과 알림", "color_roles": "색상 역할 선택", "bump_reminder": "서버 홍보 리마인더",
 "todo_lists": "개인 할 일 목록", "notes": "개인 메모장", "timers": "타이머/스톱워치", "multi_polls": "복수 선택 투표",
 "random_tools": "랜덤 뽑기·팀 나누기", "welcome_dm": "입장 시 DM 안내", "vc_stats": "음성 채널 이용 시간 통계",
 "word_filter": "금지어 사전 관리", "link_guard": "허용 도메인만 링크 허용", "report_system": "유저 신고 접수",
}

def need(sub):
    def f(code):
        if sub not in code: raise ValueError(f"{sub} 필요")
    return f

def ai_loop(system, first, check):
    cl = anthropic.Anthropic(); msgs = [{"role": "user", "content": first}]; err = ""
    for _ in range(3):
        txt = cl.messages.create(model=MODEL, max_tokens=8000, system=system, messages=msgs).content[0].text
        m = re.search(r"```(?:python)?\n(.*?)```", txt, re.S); code = m.group(1) if m else txt
        try:
            ast.parse(code); check(code); compile(code, "x.py", "exec"); return code
        except Exception as e:
            err = str(e)
            msgs += [{"role": "assistant", "content": txt}, {"role": "user", "content": f"오류: {err}\n수정한 전체 코드를 다시 주세요."}]
    raise HTTPException(500, f"도도봇이 3회 시도했지만 실패: {err}")

def save_code(i, code, note):
    with db() as c:
        c.execute("update bots set code=? where id=? and owner=?", (code, i, CUR.get()))
        c.execute("insert into versions(bot_id,code,note) values(?,?,?)", (i, code, note))

@app.get("/api/features", dependencies=[Depends(auth)])
def features(): return FEATURES

# 차단된 서버에서는 이벤트/명령어를 모두 무시하는 실행 래퍼
RUNNER = '''import os, sys, json, runpy, discord
from discord import app_commands
def BL():
    try: return set(json.load(open(os.environ["DID_BLOCK"])))
    except Exception: return set()
def _bl(a):
    bl = BL()
    if not bl: return False
    for x in a:
        g = getattr(x, "guild", None)
        gid = getattr(g, "id", None) or getattr(x, "guild_id", None) or (x.id if isinstance(x, discord.Guild) else None)
        if gid and str(gid) in bl: return True
    return False
_o = discord.Client.dispatch
def _d(self, event, *a, **k):
    if _bl(a): return
    return _o(self, event, *a, **k)
discord.Client.dispatch = _d
from discord.ext import commands
if "dispatch" in vars(commands.BotBase):
    _ob = commands.BotBase.dispatch
    def _bd(self, event, *a, **k):
        if _bl(a): return
        return _ob(self, event, *a, **k)
    commands.BotBase.dispatch = _bd
async def _ic(self, interaction): return str(interaction.guild_id) not in BL()
app_commands.CommandTree.interaction_check = _ic
discord.ui.View.interaction_check = _ic
runpy.run_path(sys.argv[1], run_name="__main__")
'''
(DATA / "runner.py").write_text(RUNNER, encoding="utf-8")

SYSTEM = """당신은 '도도봇', discord.py 2.x 전문 개발 AI입니다. 요구사항대로 완전히 동작하는 단일 파일 봇을 만드세요.
규칙: 토큰은 반드시 os.environ["BOT_TOKEN"]에서 읽기(하드코딩 금지). 필요한 intents 설정,
슬래시 명령어는 setup_hook에서 tree.sync(), 모든 명령어에 에러 처리, 표준 라이브러리와 discord.py만 사용,
데이터 저장이 필요하면 sqlite3 사용. 반드시 commands.Bot(변수명 bot)을 만들고 슬래시 명령어는 bot.tree로 등록. 설명 없이 ```python 코드블록 하나만 출력."""

def dodobot_generate(prompt, app_id, intents=None):
    cl = anthropic.Anthropic()
    ie = ""
    if intents:
        ie = (f"\n허용된 특권 인텐트(True만 사용 가능): {intents}. False인 인텐트는 코드에서 절대 켜지 마세요"
              "(켜면 PrivilegedIntentsRequired로 접속 실패). 그 인텐트가 필요한 기능은 슬래시 명령어 등으로 대체하세요.")
    msgs = [{"role": "user", "content": f"애플리케이션 ID: {app_id}\n요구사항:\n{prompt}{ie}"}]
    err = ""
    for _ in range(3):
        r = cl.messages.create(model=MODEL, max_tokens=8000, system=SYSTEM, messages=msgs)
        txt = r.content[0].text
        m = re.search(r"```(?:python)?\n(.*?)```", txt, re.S)
        code = m.group(1) if m else txt
        try:
            ast.parse(code)
            if "BOT_TOKEN" not in code: raise ValueError('os.environ["BOT_TOKEN"] 사용 필요')
            compile(code, "bot.py", "exec")
            return code
        except Exception as e:
            err = str(e)
            msgs += [{"role": "assistant", "content": txt},
                     {"role": "user", "content": f"오류가 있습니다: {err}\n수정한 전체 코드를 다시 주세요."}]
    raise HTTPException(500, f"도도봇이 3회 시도했지만 실패: {err}")

def get_bot(i):
    with db() as c:
        b = c.execute("select * from bots where id=? and owner=?", (i, CUR.get())).fetchone()
    if not b: raise HTTPException(404, "봇 없음")
    b = dict(b)
    try: b["token"] = dec(b["token"])
    except Exception: pass
    return b

def running(i):
    p = procs.get(i); return bool(p and p.poll() is None)

def blockfile(i): return DATA / f"block_{i}.json"
def blocked(i):
    try: return set(json.loads(blockfile(i).read_text()))
    except Exception: return set()

def discord_req(method, path, token):
    r = requests.request(method, API + path, headers={"Authorization": f"Bot {token}"}, timeout=15)
    if r.status_code >= 400: raise HTTPException(r.status_code, f"Discord 오류: {r.text[:200]}")
    return r.json() if r.content else {}

INTENT_BITS = {"message_content": (1 << 18) | (1 << 19), "members": (1 << 14) | (1 << 15), "presences": (1 << 12) | (1 << 13)}

def invite_url(app_id):
    return f"https://discord.com/oauth2/authorize?client_id={app_id}&scope=bot%20applications.commands&permissions=8"

def intents_of(token):
    f = discord_req("GET", "/oauth2/applications/@me", token).get("flags", 0)
    return {k: bool(f & v) for k, v in INTENT_BITS.items()}

@app.post("/api/bots", dependencies=[Depends(auth)])
def create(d: dict):
    for k in ("name", "token", "app_id"):
        if not str(d.get(k, "")).strip(): raise HTTPException(400, f"{k} 필요")
    feats = [f for f in d.get("features", []) if f in FEATURES]
    if not str(d.get("prompt", "")).strip() and not feats: raise HTTPException(400, "프롬프트나 기능 모듈이 필요합니다")
    with db() as c:
        n = c.execute("select count(*) from bots where owner=?", (CUR.get(),)).fetchone()[0]
    if n >= MAX_BOTS: raise HTTPException(400, f"봇은 최대 {MAX_BOTS}개까지 만들 수 있어요")
    me = discord_req("GET", "/users/@me", d["token"])  # 토큰 유효성 검사(AI 비용 낭비 방지)
    if me.get("id") != str(d["app_id"]).strip(): raise HTTPException(400, "애플리케이션 ID가 토큰의 봇과 다릅니다")
    full = d.get("prompt", "") + "".join(f"\n- 모듈 [{f}]: {FEATURES[f]}" for f in feats)
    it = intents_of(d["token"])
    code = dodobot_generate(full, d["app_id"], it)
    with db() as c:
        cur = c.execute("insert into bots(name,token,app_id,invite,prompt,code,owner) values(?,?,?,?,?,?,?)",
                        (d["name"], enc(d["token"]), d["app_id"], (d.get("invite") or invite_url(d["app_id"])), full, code, CUR.get()))
    save_code(cur.lastrowid, code, "최초 생성")
    return {"id": cur.lastrowid}

@app.get("/api/bots", dependencies=[Depends(auth)])
def lst():
    with db() as c:
        rows = c.execute("select id,name,app_id,invite,prompt from bots where owner=? order by id desc", (CUR.get(),)).fetchall()
    return [dict(r, online=running(r["id"])) for r in rows]

@app.get("/api/bots/{i}", dependencies=[Depends(auth)])
def detail(i: int):
    b = get_bot(i)
    return {"id": i, "name": b["name"], "app_id": b["app_id"], "invite": b["invite"],
            "code": b["code"], "online": running(i)}

@app.get("/api/bots/{i}/check", dependencies=[Depends(auth)])
def check(i: int):
    b = get_bot(i)
    try: return {"ok": True, "intents": intents_of(b["token"])}
    except HTTPException as e: return {"ok": False, "error": str(e.detail)}

@app.post("/api/bots/{i}/regen", dependencies=[Depends(auth)])
def regen(i: int):
    b = get_bot(i)
    if running(i): raise HTTPException(400, "먼저 봇을 꺼주세요")
    code = dodobot_generate(b["prompt"], b["app_id"], intents_of(b["token"]))
    save_code(i, code, "코드 다시 만들기")
    return {"ok": True}

@app.post("/api/bots/{i}/update", dependencies=[Depends(auth)])
def update(i: int, d: dict):
    b = get_bot(i); req = str(d.get("request", "")).strip()
    if not req: raise HTTPException(400, "수정 요청을 적어주세요")
    msg = (f"기존 코드:\n```python\n{b['code']}\n```\n위 코드를 아래 요청에 맞게 수정하세요. 기존 기능은 유지하고 수정된 전체 코드를 출력하세요.\n"
           f"요청: {req}\n허용된 특권 인텐트: {intents_of(b['token'])}")
    save_code(i, ai_loop(SYSTEM, msg, need("BOT_TOKEN")), "도도봇 업데이트: " + req[:60])
    return {"ok": True}

@app.put("/api/bots/{i}/code", dependencies=[Depends(auth)])
def put_code(i: int, d: dict):
    get_bot(i); code = str(d.get("code", ""))
    try: ast.parse(code); need("BOT_TOKEN")(code)
    except Exception as e: raise HTTPException(400, f"코드 오류: {e}")
    save_code(i, code, "직접 수정"); return {"ok": True}

@app.get("/api/bots/{i}/versions", dependencies=[Depends(auth)])
def versions(i: int):
    get_bot(i)
    with db() as c: rows = c.execute("select id,note,ts from versions where bot_id=? order by id desc limit 20", (i,)).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/bots/{i}/rollback/{v}", dependencies=[Depends(auth)])
def rollback(i: int, v: int):
    get_bot(i)
    with db() as c: r = c.execute("select code from versions where id=? and bot_id=?", (v, i)).fetchone()
    if not r: raise HTTPException(404, "버전 없음")
    save_code(i, r[0], f"롤백 (#{v})"); return {"ok": True}

@app.post("/api/bots/{i}/restart", dependencies=[Depends(auth)])
def restart(i: int):
    stop(i); return start(i)

@app.post("/api/bots/{i}/start", dependencies=[Depends(auth)])
def start(i: int):
    b = get_bot(i)
    if running(i): return {"online": True}
    script = DATA / "bots" / f"bot_{i}.py"; script.write_text(b["code"], encoding="utf-8")
    log = open(DATA / f"log_{i}.txt", "w")
    env = dict(os.environ, BOT_TOKEN=b["token"], DID_BLOCK=str(blockfile(i)))
    [env.pop(k, None) for k in ("ANTHROPIC_API_KEY", "DID_SECRET_KEY")]
    procs[i] = subprocess.Popen([sys.executable, str(DATA / "runner.py"), str(script)],
                                stdout=log, stderr=subprocess.STDOUT, env=env)
    return {"online": True}

@app.post("/api/bots/{i}/stop", dependencies=[Depends(auth)])
def stop(i: int):
    get_bot(i)
    p = procs.get(i)
    if p and p.poll() is None:
        p.terminate()
        try: p.wait(8)
        except subprocess.TimeoutExpired: p.kill()
    return {"online": False}

@app.get("/api/bots/{i}/log", dependencies=[Depends(auth)])
def log(i: int):
    get_bot(i)
    f = DATA / f"log_{i}.txt"
    return PlainTextResponse(f.read_text(errors="ignore")[-4000:] if f.exists() else "")

@app.get("/api/bots/{i}/guilds", dependencies=[Depends(auth)])
def guilds(i: int):
    b = get_bot(i); bl = blocked(i)
    return [{"id": g["id"], "name": g["name"], "owner": g.get("owner", False), "blocked": g["id"] in bl}
            for g in discord_req("GET", "/users/@me/guilds", b["token"])]

@app.post("/api/bots/{i}/guilds/{g}/leave", dependencies=[Depends(auth)])
def leave(i: int, g: str):
    discord_req("DELETE", f"/users/@me/guilds/{g}", get_bot(i)["token"]); return {"ok": True}

@app.post("/api/bots/{i}/guilds/{g}/{act}", dependencies=[Depends(auth)])
def block(i: int, g: str, act: str):
    get_bot(i)
    if act not in ("block", "unblock"): raise HTTPException(404)
    s = blocked(i); (s.add if act == "block" else s.discard)(g)
    blockfile(i).write_text(json.dumps(sorted(s))); return {"ok": True}

@app.delete("/api/bots/{i}", dependencies=[Depends(auth)])
def delete(i: int):
    stop(i)
    with db() as c:
        c.execute("delete from bots where id=? and owner=?", (i, CUR.get()))
        c.execute("delete from versions where bot_id=?", (i,))
    return {"ok": True}

@app.get("/robots.txt")
def robots(): return PlainTextResponse(f"User-agent: *\nAllow: /\nDisallow: /app\nSitemap: {SITE}/sitemap.xml")

@app.get("/sitemap.xml")
def sitemap():
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    f'<url><loc>{SITE}/</loc></url></urlset>', media_type="application/xml")

LANDING = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DID - 디스코드 봇 만들기 AI 도도봇 | 코딩 없이 디스코드 봇 제작</title>
<meta name="description" content="DID는 AI 도도봇이 프롬프트만으로 discord.py 디스코드 봇을 만들어 주고, 켜기/끄기와 서버 관리까지 한 곳에서 하는 웹앱입니다.">
<meta name="keywords" content="디스코드 봇 만들기, discord.py, 디스코드 봇 제작, AI 코딩, 도도봇, DID">
<link rel="canonical" href="__SITE__/"><meta property="og:title" content="DID - 디스코드 봇 만들기 AI 도도봇">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebApplication","name":"DID","url":"__SITE__/","applicationCategory":"DeveloperApplication","description":"AI로 디스코드 봇을 만들고 관리하는 웹앱"}</script>
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:10vh auto;padding:0 20px;line-height:1.7}a.b{display:inline-block;background:#5865f2;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none}</style></head>
<body><h1>DID — 디스코드 봇 만들기</h1>
<p>AI 개발 챗봇 <b>도도봇</b>에게 봇 토큰, 애플리케이션 ID, 초대 링크, 원하는 기능을 알려주세요. 도도봇이 discord.py 코드를 작성하고 검증해서 봇을 만들어 줍니다.</p>
<ul><li>만든 봇 목록과 관리 패널</li><li>버튼 하나로 봇 온라인/오프라인 전환</li><li>가입된 서버 목록, 원격 추방, 서버별 사용 차단</li></ul>
<a class="b" href="/app">DID 시작하기</a></body></html>""".replace("__SITE__", SITE)

@app.get("/", response_class=HTMLResponse)
def home(): return LANDING

PANEL = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>DID 패널</title>
<style>body{font-family:system-ui,sans-serif;background:#1e1f22;color:#eee;max-width:900px;margin:0 auto;padding:16px}
input,textarea,button,select{font:inherit;padding:8px;margin:4px 0;border-radius:6px;border:1px solid #444;background:#2b2d31;color:#eee;width:100%;box-sizing:border-box}
button{background:#5865f2;border:0;cursor:pointer;width:auto;margin-right:6px}button.r{background:#da373c}button.g{background:#248046}
.c{background:#2b2d31;padding:12px;border-radius:8px;margin:8px 0;cursor:pointer}pre{background:#111;padding:8px;overflow:auto;max-height:260px}.on{color:#3ba55d}.off{color:#888}</style></head><body>
<h1>DID <button style="font-size:12px" onclick="fetch('/auth/logout',{method:'POST'}).then(()=>location='/login')">로그아웃</button></h1><div id="v"></div>
<script>
const $=s=>document.querySelector(s),esc=t=>String(t??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=async(p,m='GET',b)=>{const r=await fetch('/api'+p,{method:m,headers:{'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});
 if(r.status==401){location='/login';throw 0}if(!r.ok){alert((await r.json().catch(()=>({}))).detail||r.status);throw 0}return r.headers.get('content-type').includes('json')?r.json():r.text()};
async function list(){const bs=await api('/bots'),fs=await api('/features');$('#v').innerHTML=`<h2>봇 목록</h2>${bs.map(b=>`<div class=c onclick="panel(${b.id})"><b>${esc(b.name)}</b> <span class="${b.online?'on':'off'}">● ${b.online?'온라인':'오프라인'}</span></div>`).join('')||'<p>아직 봇이 없어요.</p>'}
<h2>도도봇에게 봇 만들기</h2>
<div class=c style="cursor:default"><b>① 내 디스코드 계정으로 개발자 포털에서 봇을 먼저 만드세요</b><ol style="line-height:1.9">
<li><a href="https://discord.com/developers/applications" target=_blank rel=noopener style=color:#7289da>디스코드 개발자 포털 열기</a> → New Application → 이름 입력</li>
<li>General Information의 <b>Application ID</b> 복사</li>
<li>Bot 메뉴 → Reset Token → <b>토큰</b> 복사 (한 번만 보여요)</li>
<li>같은 Bot 메뉴의 Privileged Gateway Intents에서 <b>Message Content / Server Members</b>를 켜면 더 많은 기능을 만들 수 있어요</li></ol>
<b>② 아래에 붙여넣기</b></div>
<input id=n placeholder="봇 이름"><input id=t type=password placeholder="봇 토큰"><input id=a placeholder="애플리케이션 ID" oninput="iv()"><input id=i placeholder="서버 초대 링크 (비우면 자동 생성)"><div id=ivw></div>
<div>${Object.entries(fs).map(([k,v])=>`<label title="${esc(v)}" style="display:inline-block;margin:4px 10px 4px 0"><input type=checkbox class=ft value="${k}" style="width:auto"> ${esc(k)}</label>`).join('')}</div>
<textarea id=p rows=5 placeholder="명령어와 기능을 전부 여기에 적어주세요 (예: /핑 → 응답속도 표시, /주사위 → 1~6 랜덤, 입장 시 환영 메시지 ...)"></textarea><button id=go onclick=mk()>도도봇, 만들어줘!</button>`}
async function mk(){const g=$('#go');g.disabled=true;g.textContent='도도봇이 코딩 중... (최대 1~2분)';
 try{await api('/bots','POST',{name:$('#n').value,token:$('#t').value,app_id:$('#a').value,invite:$('#i').value,prompt:$('#p').value,features:[...document.querySelectorAll('.ft:checked')].map(x=>x.value)});list()}catch(e){g.disabled=false;g.textContent='도도봇, 만들어줘!'}}
async function panel(id){const b=await api('/bots/'+id);
 $('#v').innerHTML=`<button onclick=list()>← 목록</button><h2>${esc(b.name)} <span class="${b.online?'on':'off'}">● ${b.online?'온라인':'오프라인'}</span></h2>
 <button class=g onclick="act(${id},'start')">켜기</button><button class=r onclick="act(${id},'stop')">끄기</button>
 <button class=r onclick="del(${id})">봇 삭제</button><button onclick="regen(${id})">코드 다시 만들기</button> ${b.invite?`<a style=color:#7289da href="${esc(b.invite)}" target=_blank rel=noopener>초대 링크</a>`:''}
 <div id=ck></div>
<h3>🔄 업데이트</h3><textarea id=ur rows=3 placeholder="도도봇에게 수정 요청 (예: 환영 메시지에 서버 인원수도 표시해줘)"></textarea>
<button onclick="upd(${id})">도도봇으로 업데이트</button><button class=g onclick="rst(${id})">재시작(적용)</button>
<details><summary>코드 직접 수정 · 버전 기록</summary><textarea id=ce rows=14 style="font-family:monospace">${esc(b.code)}</textarea>
<button onclick="savecode(${id})">코드 저장</button><div id=vs></div></details>
<h3>가입된 서버</h3><div id=gs>불러오는 중...</div><h3>로그</h3><pre id=lg></pre><button onclick="panel(${id})">새로고침</button>`;
 try{const gs=await api(`/bots/${id}/guilds`);$('#gs').innerHTML=gs.map(g=>`<div class=c style=cursor:default>${esc(g.name)} ${g.blocked?'🚫차단됨':''}<br>
 <button class=r onclick="if(confirm('추방할까요?'))gact(${id},'${g.id}','leave')">추방</button>
 <button onclick="gact(${id},'${g.id}','${g.blocked?'unblock':'block'}')">${g.blocked?'사용 허용':'사용 차단'}</button></div>`).join('')||'서버 없음'}catch(e){$('#gs').textContent='서버 목록 실패(토큰 확인)'}
 vers(id);api(`/bots/${id}/check`).then(r=>{const y=v=>v?'✅':'❌';$('#ck').innerHTML=r.ok?`인텐트 — Message Content ${y(r.intents.message_content)} · Server Members ${y(r.intents.members)} · Presence ${y(r.intents.presences)}<br><small>포털에서 인텐트를 바꿨다면 '코드 다시 만들기'를 눌러주세요. 도도봇이 새 설정에 맞게 다시 코딩해요.</small>`:'⚠ 토큰 확인 실패: '+esc(r.error)}).catch(()=>{});
 const lg=await api(`/bots/${id}/log`);$('#lg').textContent=lg;
 if(lg.includes('PrivilegedIntents'))$('#lg').textContent+='\n\n[안내] 개발자 포털에서 Privileged Gateway Intents를 켜고 "코드 다시 만들기"를 누르세요.'}
async function act(id,a){await api(`/bots/${id}/${a}`,'POST');setTimeout(()=>panel(id),1500)}
async function gact(id,g,a){await api(`/bots/${id}/guilds/${g}/${a}`,'POST');panel(id)}
function iv(){const a=$('#a').value.trim();$('#ivw').innerHTML=/^[0-9]{15,22}$/.test(a)?`<a style=color:#7289da target=_blank rel=noopener href="https://discord.com/oauth2/authorize?client_id=${a}&scope=bot%20applications.commands&permissions=8">▶ 내 서버에 봇 초대하기 (자동 생성 링크)</a>`:''}
async function regen(id){if(!confirm('도도봇이 코드를 다시 작성합니다 (1~2분)'))return;await api(`/bots/${id}/regen`,'POST');panel(id)}
async function upd(id){const r=$('#ur').value.trim();if(!r)return alert('수정 요청을 입력하세요');const g=event.target;g.disabled=true;g.textContent='도도봇이 코딩 중...';
 try{await api(`/bots/${id}/update`,'POST',{request:r});alert('업데이트 완료! 재시작하면 적용돼요')}catch(e){}panel(id)}
async function rst(id){await api(`/bots/${id}/restart`,'POST');setTimeout(()=>panel(id),2000)}
async function savecode(id){await api(`/bots/${id}/code`,'PUT',{code:$('#ce').value});alert('저장됨 (재시작하면 적용)');vers(id)}
async function vers(id){const v=await api(`/bots/${id}/versions`);$('#vs').innerHTML='<h4>버전 기록</h4>'+v.map(x=>`<div>#${x.id} ${esc(x.note)} <small>${esc(x.ts)}</small> <button onclick="rb(${id},${x.id})">되돌리기</button></div>`).join('')}
async function rb(id,v){await api(`/bots/${id}/rollback/${v}`,'POST');panel(id)}
async function del(id){if(confirm('정말 삭제?')){await api('/bots/'+id,'DELETE');list()}}
list()</script></body></html>"""

@app.get("/app", response_class=HTMLResponse)
def panel_page(did_sid: str = Cookie(None)):
    return PANEL if user_of(did_sid) else RedirectResponse("/login")

def hpw(pw, salt): return hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1).hex()

def session_resp(uid):
    sid = secrets.token_urlsafe(32)
    with db() as c: c.execute("insert into sessions values(?,?)", (sid, uid))
    r = JSONResponse({"ok": True})
    r.set_cookie("did_sid", sid, httponly=True, samesite="lax", secure=SITE.startswith("https"), max_age=1209600)
    return r

@app.post("/auth/register")
def register(d: dict):
    if os.environ.get("DID_ALLOW_SIGNUP", "1") != "1": raise HTTPException(403, "가입이 닫혀 있습니다")
    u, p = str(d.get("username", "")).strip(), str(d.get("password", ""))
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", u) or len(p) < 8:
        raise HTTPException(400, "아이디는 영문/숫자/_ 3~20자, 비밀번호는 8자 이상")
    salt = secrets.token_bytes(16)
    try:
        with db() as c: cur = c.execute("insert into users(username,salt,pw) values(?,?,?)", (u, salt.hex(), hpw(p, salt)))
    except sqlite3.IntegrityError: raise HTTPException(409, "이미 있는 아이디입니다")
    return session_resp(cur.lastrowid)

@app.post("/auth/login")
def login(d: dict):
    with db() as c: r = c.execute("select * from users where username=?", (str(d.get("username", "")),)).fetchone()
    if not r or not secrets.compare_digest(hpw(str(d.get("password", "")), bytes.fromhex(r["salt"])), r["pw"]):
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다")
    return session_resp(r["id"])

@app.post("/auth/logout")
def logout(did_sid: str = Cookie(None)):
    with db() as c: c.execute("delete from sessions where sid=?", (did_sid,))
    r = JSONResponse({"ok": True}); r.delete_cookie("did_sid"); return r

LOGIN = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>DID 로그인</title><style>body{font-family:system-ui;background:#1e1f22;color:#eee;max-width:340px;margin:12vh auto;padding:0 16px}
input,button{font:inherit;padding:10px;margin:5px 0;width:100%;box-sizing:border-box;border-radius:6px;border:1px solid #444;background:#2b2d31;color:#eee}button{background:#5865f2;border:0;cursor:pointer}</style></head>
<body><h1>DID</h1><input id=u placeholder="아이디"><input id=p type=password placeholder="비밀번호 (8자 이상)">
<button onclick="go('login')">로그인</button><button onclick="go('register')" style="background:#4e5058">회원가입</button>
<script>async function go(a){const r=await fetch('/auth/'+a,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.getElementById('u').value,password:document.getElementById('p').value})});
if(r.ok)location='/app';else alert((await r.json()).detail)}</script></body></html>"""

@app.get("/login", response_class=HTMLResponse)
def login_page(): return LOGIN


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
