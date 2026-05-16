"""ОрфоДракон — серверное ядро. Чистая stdlib Python, без внешних зависимостей."""
import http.server, json, os, re, sys, time, random, threading, contextlib
import urllib.parse, webbrowser, sqlite3, socket
from pathlib import Path
from datetime import date, timedelta

APP_DIR = Path(__file__).parent.resolve()
DATA_DIR = APP_DIR / "data"
STATIC_DIR = APP_DIR / "static"
ICONS_DIR = APP_DIR / "icons"
PORT = 17832
APP_VERSION = "2026.05.14-manual-rule-tail-ege-fix"
_STATIC_CACHE = {}

if os.name == "nt":
    USER_DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "OrfoDragon"
elif sys.platform == "darwin":
    USER_DATA_DIR = Path.home() / "Library" / "Application Support" / "OrfoDragon"
else:
    USER_DATA_DIR = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    ) / "OrfoDragon"
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
PORT_FILE = USER_DATA_DIR / "port.txt"
DB_PATH = USER_DATA_DIR / "orfodragon.db"
VOWELS = "аеёиоуыэюя"

# ── tiny helpers ────────────────────────────────────────────────────
def now_ms(): return int(time.time() * 1000)
def day_key(ts=None):
    return date.fromtimestamp((ts / 1000) if ts else time.time()).isoformat()
def prev_day_key(): return (date.today() - timedelta(days=1)).isoformat()
def normalize_word(t): return re.sub(r"[^а-яё\-]","",str(t or "").lower()).strip("-")
def fipi_key(t): return normalize_word(t).replace("ё", "е")
def add_accent(word, idx):
    if idx < 0 or idx >= len(word): return word
    if len(word) > idx+1 and word[idx+1] == "\u0301": return word
    return word[:idx+1] + "\u0301" + word[idx+1:]
def shuffle(lst): lst = list(lst); random.shuffle(lst); return lst

# ── DB ───────────────────────────────────────────────────────────────
_db_lock = threading.Lock()

@contextlib.contextmanager
def get_db():
    c = sqlite3.connect(str(DB_PATH), timeout=15, check_same_thread=False)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=DELETE")
        yield c
        c.commit()
    finally:
        c.close()

def init_db():
    with get_db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS cards(
            id TEXT PRIMARY KEY, kind TEXT DEFAULT 'stress',
            word TEXT, stressed TEXT, prompt TEXT, answer TEXT,
            rule TEXT DEFAULT '', source TEXT DEFAULT '', note TEXT DEFAULT '',
            created_at INTEGER, updated_at INTEGER,
            interval_minutes INTEGER DEFAULT 15, ease REAL DEFAULT 2.2,
            reps INTEGER DEFAULT 0, due_at INTEGER, last_result TEXT,
            favorite INTEGER DEFAULT 0, times_looked_up INTEGER DEFAULT 0,
            score INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS review_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT, result TEXT, at INTEGER,
            FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS profile(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS app_state(key TEXT PRIMARY KEY, value TEXT);
        """)
        c.commit()

# ── settings / profile ───────────────────────────────────────────────
_DS = {
    "apiBaseUrl":"", "reviewCheckMinutes":60,
    "firstIntervalMinutes":15, "hardResetMinutes":20,
    "secondIntervalMinutes":1440, "thirdIntervalMinutes":4320,
    "defaultEase":2.2, "maxEase":3.0, "minEase":1.3,
    "autoSaveProcessedWords":1, "dailyGoal":10,
    "examMode":1, "reviewBatchSize":10, "textScale":1
}
_DP = {
    "xp":0,"level":1,"currentStreak":0,"bestStreak":0,"lastActiveDay":"",
    "totalReviews":0,"totalCorrect":0,"rememberedToday":0,
    "dailyProgressDay":"","dailyMissionDay":"",
    "dailyGoalHits":0,"dailyGoalRewardedDay":"","chestClaimedDay":"",
    "missionsCompleted":0,
    "unlockedMascotSkins":["dragon_king","dragon_boss","dragon_sage"],
    "selectedMascotSkin":"dragon_king",
    "bossStage":1,"bossHp":1200,"bossMaxHp":1200,
    "bossSkin":"dragon_boss","bossVariant":"mixed","bossLastDamage":0
}

def _kv_load(tbl, defaults):
    result = dict(defaults)
    try:
        with get_db() as c:
            for row in c.execute(f"SELECT key,value FROM {tbl}").fetchall():
                try: result[row[0]] = json.loads(row[1])
                except: result[row[0]] = row[1]
    except: pass
    return result

def _kv_save(tbl, data):
    with _db_lock:
        with get_db() as c:
            for k, v in data.items():
                c.execute(
                    f"INSERT OR REPLACE INTO {tbl}(key,value) VALUES(?,?)",
                    (k, json.dumps(v, ensure_ascii=False))
                )
            c.commit()

def get_settings(): return _kv_load("settings", _DS)
def save_settings(d):
    base = get_settings()
    base.update({k:v for k,v in (d or {}).items() if k in _DS})
    _kv_save("settings", base)
def get_profile(): return _kv_load("profile", _DP)
def save_profile(d): _kv_save("profile", d)

def get_app_state(k, d=None):
    try:
        with get_db() as c:
            r = c.execute("SELECT value FROM app_state WHERE key=?", (k,)).fetchone()
            return json.loads(r[0]) if r else d
    except: return d
def set_app_state(k, v): _kv_save("app_state", {k: v})

# ── dictionary ───────────────────────────────────────────────────────
_dict_cache = None
_ege_cache = None

def get_dictionary():
    global _dict_cache
    if _dict_cache is None:
        try:
            with open(DATA_DIR / "local_dictionary.json", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = {}
        ege = get_ege_words()
        out = {}
        for w, st in raw.items():
            if fipi_key(w) in ege:
                out[normalize_word(w)] = st
                out[fipi_key(w)] = st
        # ручные исправления/алиасы для слов, которые в списке ФИПИ часто пишутся без «ё»
        if "договоренность" in ege:
            out["договоренность"] = "догово́ренность"
        if "шофер" in ege:
            out["шофер"] = "шофёр"
        if "щелкать" in ege:
            out["щелкать"] = "щёлкать"
        _dict_cache = out
    return _dict_cache

def get_ege_words():
    global _ege_cache
    if _ege_cache is None:
        try:
            with open(DATA_DIR / "ege_words.json", encoding="utf-8") as f:
                items = json.load(f)
            _ege_cache = set(fipi_key(x) for x in items if x)
        except: _ege_cache = set()
    return _ege_cache

# ── cards CRUD ───────────────────────────────────────────────────────
def _row(r): return {**dict(r), "favorite": bool(dict(r).get("favorite",0))} if r else None

def get_all_cards(kind=None, fav=None, search=None, sort="created"):
    with get_db() as c: rows = c.execute("SELECT * FROM cards").fetchall()
    cards = [_row(r) for r in rows]
    if kind:   cards = [x for x in cards if x.get("kind") == kind]
    if fav == "1": cards = [x for x in cards if x.get("favorite")]
    if search:
        s = search.lower()
        cards = [x for x in cards if
                 s in (x.get("word") or "").lower() or
                 s in (x.get("stressed") or "").lower() or
                 s in (x.get("prompt") or "").lower()]
    key = {"alpha": lambda x: x.get("word",""),
           "score": lambda x: -x.get("score",0),
           "updated": lambda x: -x.get("updated_at",0)}.get(sort, lambda x: -x.get("created_at",0))
    cards.sort(key=key)
    return cards

def get_card(cid):
    with get_db() as c:
        r = c.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone()
    return _row(r)

def upsert_stress_card(word, stressed, source, note="", favorite=False):
    word = normalize_word(word)
    if not word or not stressed: return
    s = get_settings(); now = now_ms(); ex = get_card(word) or {}
    with _db_lock:
        with get_db() as c:
            c.execute(
                "INSERT OR REPLACE INTO cards"
                "(id,kind,word,stressed,prompt,answer,rule,source,note,"
                "created_at,updated_at,interval_minutes,ease,reps,due_at,"
                "last_result,favorite,times_looked_up,score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (word,"stress",word,stressed,word,stressed,
                 ex.get("rule",""), source, note or ex.get("note",""),
                 ex.get("created_at",now), now,
                 ex.get("interval_minutes",s["firstIntervalMinutes"]),
                 ex.get("ease",s["defaultEase"]),
                 ex.get("reps",0),
                 ex.get("due_at", now + s["firstIntervalMinutes"]*60000),
                 ex.get("last_result"),
                 1 if (favorite or ex.get("favorite")) else 0,
                 ex.get("times_looked_up",0)+1,
                 ex.get("score",0)))
            c.commit()
    touch_daily_activity()

def save_stress_override(word, stressed):
    word = normalize_word(word)
    if not word or not stressed: return
    s = get_settings(); now = now_ms(); ex = get_card(word) or {}
    with _db_lock:
        with get_db() as c:
            c.execute(
                "INSERT OR REPLACE INTO cards"
                "(id,kind,word,stressed,prompt,answer,rule,source,note,"
                "created_at,updated_at,interval_minutes,ease,reps,due_at,"
                "last_result,favorite,times_looked_up,score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (word,"stress",word,stressed,word,stressed,
                 ex.get("rule",""), "manual_override",
                 "Исправлено вручную.",
                 ex.get("created_at",now), now,
                 ex.get("interval_minutes",s["firstIntervalMinutes"]),
                 ex.get("ease",s["defaultEase"]),
                 ex.get("reps",0),
                 ex.get("due_at", now + s["firstIntervalMinutes"]*60000),
                 ex.get("last_result"), 1,
                 ex.get("times_looked_up",0)+1, ex.get("score",0)))
            c.commit()

def save_punctuation_card(original, corrected, explanations, note="", favorite=True, manual_rule=""):
    now = now_ms()
    cid = f"punct:{original[:100]}"
    manual_rule = (manual_rule or "").strip()
    rule = manual_rule or (" · ".join(explanations) if explanations else (note or "Исправлено вручную"))
    ex = get_card(cid) or {}; s = get_settings()
    with _db_lock:
        with get_db() as c:
            c.execute(
                "INSERT OR REPLACE INTO cards"
                "(id,kind,word,prompt,answer,rule,source,note,"
                "created_at,updated_at,interval_minutes,ease,reps,due_at,"
                "last_result,favorite,times_looked_up,score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid,"punctuation",original[:80],original,corrected,rule,
                 "manual_punctuation", note or "",
                 ex.get("created_at",now), now,
                 ex.get("interval_minutes",s["firstIntervalMinutes"]),
                 ex.get("ease",s["defaultEase"]),
                 ex.get("reps",0),
                 ex.get("due_at", now + s["firstIntervalMinutes"]*60000),
                 ex.get("last_result"),
                 1 if favorite else (1 if ex.get("favorite") else 0),
                 ex.get("times_looked_up",0)+1, ex.get("score",0)))
            c.commit()
    touch_daily_activity()
    return cid

def search_punctuation_card(text):
    """Return a saved punctuation card if this sentence (ignoring punctuation) matches."""
    if not text: return None
    def norm(s): return re.sub(r"\s+", " ", re.sub(r"[.,!?;:]", "", str(s or "")).strip().lower())
    tn = norm(text)
    for card in get_all_cards(kind="punctuation"):
        pn = norm(card.get("prompt",""))
        if pn == tn or (len(tn) > 5 and (tn in pn or pn in tn)):
            return card
    return None

def delete_card(cid):
    with _db_lock:
        with get_db() as c:
            n = c.execute("DELETE FROM cards WHERE id=?", (cid,)).rowcount
            c.execute("DELETE FROM review_history WHERE card_id=?", (cid,))
            c.commit()
    return n > 0

def delete_cards_many(ids): return sum(1 for cid in ids if delete_card(cid))

def toggle_favorite(cid, val):
    with _db_lock:
        with get_db() as c:
            c.execute("UPDATE cards SET favorite=?,updated_at=? WHERE id=?",
                      (1 if val else 0, now_ms(), cid))
            c.commit()

def get_due_cards():
    s = get_settings(); n = now_ms(); ege = get_ege_words()
    with get_db() as c:
        rows = c.execute("SELECT * FROM cards WHERE due_at<=? ORDER BY due_at", (n,)).fetchall()
    cards = [_row(r) for r in rows]
    if s.get("examMode"):
        pri = [x for x in cards if x["kind"]=="punctuation" or
               normalize_word(x.get("word","")) in ege]
        if pri: return pri
    return cards

def get_favorites():
    with get_db() as c:
        rows = c.execute(
            "SELECT * FROM cards WHERE favorite=1 ORDER BY score DESC, updated_at DESC LIMIT 50"
        ).fetchall()
    return [_row(r) for r in rows]

# ── stress logic ─────────────────────────────────────────────────────
def resolve_stress(word):
    n = normalize_word(word)
    if not n: return {"word":word,"stressed":word,"source":"passthrough","fallback":True,"note":""}
    d = get_dictionary()
    if n in d:
        return {"word":n,"stressed":d[n],"source":"local_dictionary","fallback":False,"note":"Из локального словаря ФИПИ."}
    if fipi_key(n) in d:
        return {"word":n,"stressed":d[fipi_key(n)],"source":"local_dictionary","fallback":False,"note":"Из локального словаря ФИПИ."}
    v = [i for i, c in enumerate(n) if c in VOWELS]
    if not v:
        return {"word":n,"stressed":n,"source":"fallback_passthrough","fallback":True,"note":"Ударение не определено."}
    pos = v[0] if len(v)==1 else v[-1]
    return {"word":n,"stressed":add_accent(n,pos),"source":"extension_fallback","fallback":True,
            "note":"Ударение может стоять неправильно, обязательно перепроверяйте."}

def search_stress(query, save=False):
    n = normalize_word(query)
    if not n: return {"normalizedQuery":"","exact":None,"results":[]}
    card = get_card(n); d = get_dictionary(); results = []; exact = None
    if card:
        exact = {**card,"location":"cards"}; results.append(exact)
    dk = n if n in d else fipi_key(n)
    if dk in d:
        item = {"id":n,"word":n,"stressed":d[dk],"source":"local_dictionary",
                "location":"dictionary","fallback":False,"note":"Из локального словаря ФИПИ."}
        if not exact: exact = item
        results.append(item)
    if not exact:
        r = resolve_stress(n); exact = {"id":n,**r,"location":"resolved"}; results.append(exact)
        if save and r.get("stressed"):
            upsert_stress_card(n, r["stressed"], r["source"], r.get("note",""), False)
    with get_db() as c:
        rows = c.execute(
            "SELECT * FROM cards WHERE kind='stress' AND id!=? AND id LIKE ? LIMIT 10",
            (n, f"%{n}%")
        ).fetchall()
    for row in rows: results.append({**_row(row),"location":"cards"})
    return {"normalizedQuery":n,"exact":exact,"results":results[:20]}

# ── punctuation ───────────────────────────────────────────────────────
def local_punctuate(text):
    """Same rules as original extension's localPunctuateText."""
    result = str(text or "").strip(); exp = []
    def ap(pat, rep, note):
        nonlocal result
        nxt = re.sub(pat, rep, result, flags=re.IGNORECASE)
        if nxt != result: result = nxt; (exp.append(note) if note not in exp else None)
    ap(r"\s+(потому что)\b",     r", \1", "Запятая перед «потому что».")
    ap(r"\s+(так как)\b",        r", \1", "Запятая перед «так как».")
    ap(r"\s+(так что)\b",        r", \1", "Запятая перед «так что».")
    ap(r"\s+(но)\s+",            r", \1 ", "Запятая перед «но».")
    ap(r"\s+(а)\s+",             r", \1 ", "Запятая перед «а».")
    ap(r"\s+(чтобы)\b",          r", \1", "Запятая перед «чтобы».")
    ap(r"\s+(что)\b",            r", \1", "Запятая перед «что».")
    ap(r"\s+(если)\b",           r", \1", "Запятая перед «если».")
    ap(r"\s+(когда)\b",          r", \1", "Запятая перед «когда».")
    ap(r"\s+(хотя)\b",           r", \1", "Запятая перед «хотя».")
    ap(r"\s+(словно|будто|как будто)\b", r", \1", "Запятая перед сравнительным союзом.")
    ap(r"\s+(однако)\s+",        r", \1 ", "Запятая перед «однако».")
    ap(r"^(Когда\s+[^,]{3,60}?)(\s+)(?=[А-Яа-яЁё])", r"\1, ",
       "Запятая после придаточного с «когда».")
    ap(r"^(Если\s+[^,]{3,60}?)(\s+)(?=[А-Яа-яЁё])", r"\1, ",
       "Запятая после придаточного с «если».")
    result = re.sub(r"\s+,", ",", result)
    result = re.sub(r",\s*,", ",", result)
    result = re.sub(r"\s{2,}", " ", result).strip()
    if result and not re.search(r"[.!?]$", result): result += "."
    if not exp: exp.append("Точного правила не найдено. Проверь вручную.")
    return {"original":text,"result":result,"explanations":exp,
            "note":"Запятые расставлены по базовым правилам. Обязательно проверьте вручную."}

# ── XP / profile ─────────────────────────────────────────────────────
def add_xp(pts):
    p = get_profile()
    p["xp"] = p.get("xp",0) + pts; p["level"] = p["xp"]//100 + 1
    _kv_save("profile", {"xp":p["xp"],"level":p["level"]})

def daily_goal_target(s=None, p=None):
    if s is None: s = get_settings()
    if p is None: p = get_profile()
    base = int(s.get("dailyGoal",10))
    bonus = min(12, int(p.get("missionsCompleted",0))//2)
    return base + bonus

def roll_daily_progress(p):
    today = day_key()
    if not p.get("dailyProgressDay"):
        p["dailyProgressDay"] = today; p["dailyMissionDay"] = today
    if p["dailyProgressDay"] != today:
        p["dailyProgressDay"] = today; p["dailyMissionDay"] = today
        p["rememberedToday"] = 0; p["dailyGoalRewardedDay"] = ""
    return p

def touch_daily_activity():
    p = get_profile(); roll_daily_progress(p); today = day_key()
    last = p.get("lastActiveDay","")
    if not last: p["lastActiveDay"] = today; p["currentStreak"] = 1
    elif last != today:
        p["currentStreak"] = (p.get("currentStreak",0)+1) if last==prev_day_key() else 1
        p["lastActiveDay"] = today
    p["bestStreak"] = max(p.get("bestStreak",0), p.get("currentStreak",1))
    save_profile(p)

# ── boss ──────────────────────────────────────────────────────────────
DRAGON_SKINS = ["dragon_academy","dragon_ege","dragon_gold","dragon_forest",
                "dragon_storm","dragon_crystal","dragon_shadow"]

def _boss_max_hp(stage): return 1200 if stage <= 1 else 3500 + (stage-2)*1800

def _normalize_boss(p, variant="mixed"):
    stage = max(1, int(p.get("bossStage",1)))
    maxhp = _boss_max_hp(stage)
    p["bossStage"] = stage; p["bossMaxHp"] = maxhp
    p["bossHp"] = max(0, min(maxhp, int(p.get("bossHp", maxhp))))
    p["bossSkin"] = p.get("bossSkin","dragon_boss"); p["bossVariant"] = variant
    p["bossLastDamage"] = int(p.get("bossLastDamage",0))

def _boss_variant(weak):
    kinds = [w.get("kind","stress") for w in (weak or [])]
    if not kinds: return "mixed"
    sc = sum(1 for k in kinds if k=="stress"); pc = sum(1 for k in kinds if k=="punctuation")
    if sc==0 and pc>0: return "punctuation"
    if pc==0 and sc>0: return "stress"
    return "mixed"

def damage_boss(amount, variant="mixed"):
    p = get_profile(); _normalize_boss(p, variant)
    p["bossHp"] = max(0, p["bossHp"] - amount); p["bossLastDamage"] = amount
    if p["bossHp"] == 0:
        p["bossStage"] = p.get("bossStage",1) + 1
        new_max = _boss_max_hp(p["bossStage"]); p["bossMaxHp"] = new_max; p["bossHp"] = new_max
        locked = [sk for sk in DRAGON_SKINS if sk not in p.get("unlockedMascotSkins",[])]
        if locked: p["bossSkin"] = random.choice(locked)
    save_profile(p)
    return {"bossHp":p["bossHp"],"bossMaxHp":p["bossMaxHp"],
            "bossVariant":variant,"bossSkin":p["bossSkin"],"bossStage":p["bossStage"]}

# ── grade card ────────────────────────────────────────────────────────
def grade_card(cid, result):
    s = get_settings(); card = get_card(cid)
    if not card: return {"ok":False,"reason":"card_not_found"}
    now = now_ms()
    dmg = 55 if (result=="remember" and card["kind"]=="punctuation") else \
          45 if result=="remember" else 0
    bossVariant = "punctuation" if card["kind"]=="punctuation" else "stress"
    if result == "remember":
        reps = card["reps"] + 1
        if reps == 1:   interval = s["secondIntervalMinutes"]
        elif reps == 2: interval = s["thirdIntervalMinutes"]
        else:           interval = round(card["interval_minutes"] * max(1.8, card["ease"]))
        ease = min(card["ease"]+0.15, s["maxEase"]); last="remember"; score=card["score"]+3; fav=0
        add_xp(8 if card["kind"]=="punctuation" else 6)
    else:
        reps=0; interval=max(10, round(s["hardResetMinutes"]/2))
        ease=max(card["ease"]-0.25, s["minEase"]); last="forget"
        score=max(0, card["score"]-2); fav=1; add_xp(1)
    with _db_lock:
        with get_db() as c:
            c.execute(
                "UPDATE cards SET reps=?,interval_minutes=?,ease=?,last_result=?,"
                "score=?,favorite=?,updated_at=?,due_at=? WHERE id=?",
                (reps,interval,ease,last,score,fav,now,now+interval*60000,cid))
            c.execute("INSERT INTO review_history(card_id,result,at) VALUES(?,?,?)",
                      (cid,result,now))
            c.commit()
    touch_daily_activity()
    p = get_profile(); roll_daily_progress(p)
    p["totalReviews"] = p.get("totalReviews",0) + 1
    if result == "remember":
        p["totalCorrect"] = p.get("totalCorrect",0) + 1
        p["rememberedToday"] = p.get("rememberedToday",0) + 1
        tgt = daily_goal_target(s, p)
        if p["rememberedToday"] >= tgt and p.get("dailyGoalRewardedDay") != day_key():
            p["dailyGoalHits"] = p.get("dailyGoalHits",0) + 1
            p["dailyGoalRewardedDay"] = day_key()
            p["xp"] = p.get("xp",0) + 20; p["level"] = p["xp"]//100 + 1
    save_profile(p)
    boss = damage_boss(dmg, bossVariant) if dmg else None
    return {"ok":True,"boss":boss,"damage":dmg}

# ── chest / skin ──────────────────────────────────────────────────────
def claim_daily_chest():
    p = get_profile(); roll_daily_progress(p); s = get_settings(); today = day_key()
    tgt = daily_goal_target(s, p); rem = p.get("rememberedToday",0)
    if rem < tgt: return {"ok":False,"reason":"goal_not_reached","progress":rem,"target":tgt}
    if p.get("chestClaimedDay") == today: return {"ok":False,"reason":"already_claimed"}
    p["chestClaimedDay"] = today; p["missionsCompleted"] = p.get("missionsCompleted",0)+1
    unlocked = list(p.get("unlockedMascotSkins",["dragon_king","dragon_boss","dragon_sage"]))
    locked = [sk for sk in DRAGON_SKINS if sk not in unlocked]
    skin = None
    if locked and random.random() < 0.35: skin = random.choice(locked); unlocked.append(skin)
    p["unlockedMascotSkins"] = unlocked; save_profile(p); add_xp(30)
    fresh = get_profile()
    return {"ok":True,"xp":fresh.get("xp",0),"level":fresh.get("level",1),
            "skinUnlocked":skin,"unlockedMascotSkins":unlocked}

def set_mascot_skin(skin):
    p = get_profile()
    unlocked = set(p.get("unlockedMascotSkins",[]) + ["dragon_king","dragon_boss","dragon_sage"])
    if skin not in unlocked: return {"ok":False,"reason":"skin_locked"}
    p["selectedMascotSkin"] = skin; save_profile(p); return {"ok":True}

# ── weak spots / missions ─────────────────────────────────────────────
def _weak_spots_for_week():
    week_ago = now_ms() - 7*24*3600*1000
    with get_db() as c:
        rows = c.execute(
            "SELECT ca.*, COUNT(rh.id) as forgets "
            "FROM cards ca JOIN review_history rh ON ca.id=rh.card_id "
            "WHERE rh.result='forget' AND rh.at>=? "
            "GROUP BY ca.id ORDER BY forgets DESC LIMIT 10",
            (week_ago,)
        ).fetchall()
    result = []
    for r in rows:
        d = _row(r); d["forgets"] = dict(r)["forgets"]
        d["label"] = d.get("prompt") or d.get("word") or "?"
        result.append(d)
    return result

def _today_hist_count(kind=None, result=None):
    today = day_key()
    q = ("SELECT COUNT(*) FROM review_history rh "
         "JOIN cards ca ON rh.card_id=ca.id "
         "WHERE strftime('%Y-%m-%d', rh.at/1000, 'unixepoch')=?")
    params = [today]
    if kind:   q += " AND ca.kind=?";     params.append(kind)
    if result: q += " AND rh.result=?";   params.append(result)
    with get_db() as c: return c.execute(q, params).fetchone()[0]

def _mission_target(base, p):
    bonus = min(12, int(p.get("missionsCompleted",0))//2)
    return base + bonus

def _missions_from_state(p, s):
    roll_daily_progress(p)
    rem = p.get("rememberedToday",0); base_goal = int(s.get("dailyGoal",10))
    goal = daily_goal_target(s, p); today_key = day_key()
    seed_str = p.get("dailyMissionDay") or today_key
    seed = sum(ord(c)*(i+1) for i, c in enumerate(seed_str))
    stress_rem   = _today_hist_count(kind="stress",   result="remember")
    punct_rem    = _today_hist_count(kind="punctuation", result="remember")
    all_rem      = _today_hist_count(result="remember")
    all_forget   = _today_hist_count(result="forget")
    all_reviewed = _today_hist_count()
    variants = [
        {"id":"stress_today","title":"Тренировка дня: ударения",
         "progress":stress_rem, "target":_mission_target(min(5,max(3,base_goal)),p)},
        {"id":"punct_today","title":"Тренировка дня: запятые",
         "progress":punct_rem,  "target":_mission_target(min(5,max(3,base_goal)),p)},
        {"id":"acc_today","title":"Тренировка дня: без ошибок",
         "progress":max(0,all_rem-all_forget), "target":_mission_target(min(7,max(4,base_goal)),p)},
    ]
    focus = variants[seed % len(variants)]
    missions = [
        {"id":"daily_goal","title":"Дневная цель","progress":min(rem,goal),"target":goal,"type":"daily"},
        {**focus,"type":"focus"},
        {"id":"review_today","title":"Разогрев: повтори карточки",
         "progress":all_reviewed, "target":_mission_target(min(8,max(4,base_goal)),p),"type":"review"},
        {"id":"daily_chest",
         "title":"Сундук дня открыт" if p.get("chestClaimedDay")==today_key else "Открой сундук дня",
         "progress":goal if p.get("chestClaimedDay")==today_key else min(rem,goal),
         "target":goal, "type":"chest"},
    ]
    for m in missions:
        m["progress"] = min(int(m.get("progress",0)), int(m.get("target",1)))
    return missions

# ── leagues ───────────────────────────────────────────────────────────
LEAGUES = ["Бронзовый Орфомастер","Серебряный Орфомастер",
           "Золотой Орфомастер","Платиновый Орфомастер","Изумрудный Орфомастер"]
def _league_for_level(lvl):
    lvl = max(1,int(lvl or 1)); idx = (lvl-1)//7
    if idx < len(LEAGUES)-1: return LEAGUES[idx]
    return f"Алмазный Орфомастер {idx-len(LEAGUES)+2}"
def _next_league_level(lvl):
    return ((max(1,int(lvl or 1))-1)//7 + 1)*7 + 1

# ── state snapshot ────────────────────────────────────────────────────
def get_state_snapshot():
    s = get_settings(); p = get_profile(); roll_daily_progress(p)
    items = get_all_cards(sort="updated"); n = now_ms()
    due  = [x for x in items if x.get("due_at",0) <= n]
    favs = [x for x in items if x.get("favorite")]
    weak = _weak_spots_for_week()
    pri_rev = (weak if weak else due)[:50]
    goal = daily_goal_target(s, p); rem = p.get("rememberedToday",0)
    rating = round(p.get("xp",0) + p.get("totalCorrect",0)*5)
    league = _league_for_level(p.get("level",1))
    acc = round(p.get("totalCorrect",0)/p.get("totalReviews",1)*100) if p.get("totalReviews") else 0
    stats = {
        "total":len(items),"due":len(due),"favorites":len(favs),"weakCount":len(weak),
        "remembered":sum(1 for x in items if x.get("last_result")=="remember"),
        "forgotten":sum(1 for x in items if x.get("last_result")=="forget"),
        "accuracy":acc,
        "currentStreak":p.get("currentStreak",0),"bestStreak":p.get("bestStreak",0),
        "xp":p.get("xp",0),"level":p.get("level",1),"rating":rating,
        "goalProgress":f"{min(rem,goal)}/{goal}",
        "league":league,"nextLeague":_next_league_level(p.get("level",1))
    }
    variant = _boss_variant(weak); _normalize_boss(p, variant); save_profile(p)
    missions = _missions_from_state(p, s)
    chest_prog = goal if p.get("chestClaimedDay")==day_key() else min(rem,goal)
    chest_avail = chest_prog >= goal and p.get("chestClaimedDay") != day_key()
    return {
        "ok": True, "appVersion": APP_VERSION,
        "settings":s, "cards":items[:100],
        "favorites":sorted(favs, key=lambda x:-x.get("score",0))[:50],
        "dueCards":due[:50], "priorityReviewCards":pri_rev,
        "profile":p, "stats":stats,
        "gamification":{
            "league":league,"nextLeague":stats["nextLeague"],
            "chestAvailable":chest_avail,"weakSpots":weak,
            "bossVariant":variant,"bossHp":p["bossHp"],"bossMaxHp":p["bossMaxHp"],
            "bossSkin":p["bossSkin"],"bossStage":p["bossStage"],"bossLastDamage":p["bossLastDamage"],
            "missions":missions,"dailyGoal":goal,"rememberedToday":min(rem,goal*2),
            "dailyProgressDay":p.get("dailyProgressDay",day_key()),
            "chestProgress":{"progress":chest_prog,"target":goal,
                             "claimed":p.get("chestClaimedDay")==day_key()},
            "chestClaimedDay":p.get("chestClaimedDay",""),
            "chestAvailable":chest_avail,
        }
    }

# ── EGE exam ──────────────────────────────────────────────────────────
_ege_session = None; _ege_result = None

def _build_stress_options(stressed):
    clean = re.sub(r"\u0301","",stressed)
    positions = [i for i,c in enumerate(clean) if c in VOWELS]
    variants = set([stressed] + [add_accent(clean,p) for p in positions])
    return shuffle(list(filter(None,variants)))[:min(4,max(2,len(variants)))]

def _build_punct_options(correct, prompt):
    """Build varied punctuation options with proper spacing."""
    def normalize(s):
        s = re.sub(r"\s+,", ",", str(s or "").strip())
        s = re.sub(r",\s*", ", ", s)
        s = re.sub(r"\s{2,}", " ", s).strip()
        return s
    def no_comma(text): return normalize(re.sub(r",", "", str(text or "")))
    def inject_at(text, pos):
        words = no_comma(text).split()
        if len(words) < 3 or pos <= 0 or pos >= len(words): return ""
        words[pos-1] = words[pos-1] + ","
        return normalize(" ".join(words))
    def comma_before_word(text, word):
        val = no_comma(text)
        pat = re.compile(r"\s+(" + re.escape(word) + r")\b", re.IGNORECASE)
        if pat.search(val): return normalize(pat.sub(r", \1", val))
        return ""
    right = str(correct or "").strip(); base = str(prompt or "").strip(); rn = normalize(right)
    # Gather diverse wrong options
    candidates = set()
    candidates.add(no_comma(right))   # no commas at all
    # comma before common conjunctions
    for w in ["что","чтобы","если","когда","хотя","но","а","потому что","так как"]:
        v = comma_before_word(base, w)
        if v and v != rn: candidates.add(v)
    # positional variants
    words = no_comma(base).split()
    for i in range(1, min(len(words), 6)):
        v = inject_at(base, i)
        if v and v != rn: candidates.add(v)
    # remove empty and correct
    candidates.discard(""); candidates.discard(rn)
    wrong = shuffle(list(candidates))[:3]
    opts = [rn] + wrong
    return shuffle(opts)[:4] if len(opts) >= 2 else (opts if opts else [rn])

def _infer_topic(q):
    if q.get("kind") == "stress": return "Ударения"
    p = (q.get("prompt","")).lower()
    if re.search(r"потому что|так как|чтобы|если|когда|хотя|который", p):
        return "Сложноподчинённые предложения"
    if re.search(r"\bно\b|\bа\b|однако|зато", p): return "Противительные союзы"
    return "Пунктуация"

def create_ege_exam():
    global _ege_session
    cards = get_all_cards()
    stress = [c for c in cards if c.get("kind")=="stress" and c.get("stressed")]
    punct  = [c for c in cards if c.get("kind")=="punctuation" and c.get("answer")]
    questions = []
    for c in shuffle(stress)[:5]:
        opts = _build_stress_options(c["stressed"])
        if len(opts) < 2: continue
        questions.append({"id":f"s:{c['id']}","kind":"stress","prompt":c["word"],
            "question":"Укажите правильный вариант ударения",
            "options":opts,"correct":c["stressed"],
            "explanation":c.get("note","Словарное ударение."),"topic":"Ударения"})
    for c in shuffle(punct)[:5]:
        opts = _build_punct_options(c["answer"], c.get("prompt",""))
        if len(opts) < 2: continue
        questions.append({"id":f"p:{c['id']}","kind":"punctuation",
            "prompt":c.get("prompt",c.get("word","")),
            "question":"Выберите вариант с правильной расстановкой запятых",
            "options":opts,"correct":c["answer"],
            "explanation":c.get("rule","Запятые расставлены по правилам пунктуации."),
            "topic":_infer_topic(c)})
    questions = shuffle(questions)[:10]
    _ege_session = {"questions":questions,"createdAt":now_ms(),
                    "startedAt":now_ms(),"durationSeconds":600,"submittedAt":None}
    return _ege_session

def get_ege_session():
    global _ege_session
    if not _ege_session: _ege_session = create_ege_exam()
    return _ege_session

def _norm_ege(s):
    s = re.sub(r",\s*", ", ", str(s or "").strip().rstrip("."))
    return re.sub(r"\s+", " ", s).replace("ё","е").lower().strip()

def submit_ege_exam(answers, expired=False):
    global _ege_result, _ege_session
    session = get_ege_session(); questions = session.get("questions",[])
    correct = 0; results = []
    for i, q in enumerate(questions):
        ans = answers[i] if i < len(answers) else ""
        is_cor = _norm_ege(ans) == _norm_ege(q.get("correct",""))
        if is_cor: correct += 1
        results.append({**q,"userAnswer":ans,"isCorrect":is_cor})
    total = len(questions); pct = round(correct/total*100) if total else 0
    marks = [(90,"5 — отлично"),(70,"4 — хорошо"),(50,"3 — удовлетворительно"),(0,"2 — нужно ещё тренироваться")]
    mark = next(m for t, m in marks if pct >= t)
    xp_gained = correct*12 + (30 if pct>=80 else 15 if pct>=60 else 0)
    add_xp(xp_gained)
    boss_dmg = max(20, xp_gained*2)
    boss = damage_boss(boss_dmg, "mixed")
    _ege_result = {"questions":results,"total":total,"correct":correct,"percent":pct,
                   "mark":mark,"xpGained":xp_gained,"bossDamage":boss_dmg,"boss":boss,"expired":expired}
    _ege_session = None
    return _ege_result

def get_ege_result(): return _ege_result

# ── training ──────────────────────────────────────────────────────────
_train_session = None

def build_training_session(mode="all", count=10):
    global _train_session
    cards = get_all_cards()
    if mode == "stress":     cards = [c for c in cards if c.get("kind")=="stress"]
    elif mode == "punctuation": cards = [c for c in cards if c.get("kind")=="punctuation"]
    elif mode == "weak":     cards = _weak_spots_for_week()
    due = [c for c in cards if c.get("due_at",0) <= now_ms()]
    source = due if due else cards
    picked = shuffle(source)[:max(1,count)]
    _train_session = {"cards":picked,"mode":mode,"count":len(picked),"createdAt":now_ms()}
    return _train_session

# ── export / import ───────────────────────────────────────────────────
def export_cards_txt():
    """Export all cards as a human-readable text file."""
    lines = ["# ОрфоДракон — все карточки", ""]
    stress = get_all_cards(kind="stress")
    punct  = get_all_cards(kind="punctuation")
    if stress:
        lines.append("== УДАРЕНИЯ ==")
        for c in stress:
            lines.append(f"{c.get('word','')} — {c.get('stressed','')}")
        lines.append("")
    if punct:
        lines.append("== ЗАПЯТЫЕ ==")
        for c in punct:
            lines.append(f"Исходное:    {c.get('prompt','')}")
            lines.append(f"С запятыми:  {c.get('answer','')}")
            if c.get("rule"): lines.append(f"Правило:     {c.get('rule','')}")
            lines.append("")
    return "\n".join(lines)

def import_cards_txt(text):
    """Импорт TXT. Поддерживаются форматы:
    1) слово|сло́во
    2) слово — сло́во
    3) исходный текст|текст с запятыми|правило
    4) блоки экспортного формата == УДАРЕНИЯ == / == ЗАПЯТЫЕ ==
    """
    count = 0
    mode = None
    pending_original = pending_answer = pending_rule = None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        upper = line.upper()
        if upper.startswith("== УДАРЕНИЯ"):
            mode = "stress"; continue
        if upper.startswith("== ЗАПЯТЫЕ"):
            mode = "punct"; continue
        # экспортный многострочный формат запятых
        if line.lower().startswith("исходное:"):
            pending_original = line.split(":",1)[1].strip(); continue
        if line.lower().startswith("с запятыми:"):
            pending_answer = line.split(":",1)[1].strip(); continue
        if line.lower().startswith("правило:"):
            pending_rule = line.split(":",1)[1].strip()
            if pending_original and pending_answer:
                save_punctuation_card(pending_original, pending_answer, [pending_rule or "Импорт TXT"], "Импорт TXT", True)
                count += 1
            pending_original = pending_answer = pending_rule = None
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3:
            if save_punctuation_card(parts[0], parts[1], [parts[2]], "Импорт TXT", True):
                count += 1
            continue
        if len(parts) == 2:
            # Предложение с пробелами считаем пунктуацией; одиночное слово — ударением.
            if " " in parts[0] or " " in parts[1] or "," in parts[1]:
                if save_punctuation_card(parts[0], parts[1], ["Импорт TXT"], "Импорт TXT", True):
                    count += 1
            else:
                if upsert_stress_card(parts[0], parts[1], "import", "Импорт TXT", False):
                    count += 1
            continue
        if " — " in line:
            word, stressed = line.split(" — ", 1)
            if upsert_stress_card(word, stressed, "import", "Импорт TXT", False):
                count += 1
    return count

def initialize_default_cards():
    """Создаёт стартовые карточки только из списка ФИПИ.
    В local_dictionary могут быть служебные формы, но в готовый набор
    автоматически попадают только слова из data/ege_words.json.
    """
    if get_app_state("defaultCardsInitializedV2"): return 0
    d = get_dictionary(); ege = sorted(get_ege_words()); s = get_settings(); now = now_ms(); added = 0
    with _db_lock:
        with get_db() as c:
            for key in ege:
                stressed = d.get(key)
                if not stressed:
                    continue
                word = key
                if c.execute("SELECT id FROM cards WHERE id=?", (word,)).fetchone():
                    continue
                c.execute(
                    "INSERT INTO cards(id,kind,word,stressed,prompt,answer,source,note,"
                    "created_at,updated_at,interval_minutes,ease,reps,due_at,"
                    "last_result,favorite,times_looked_up,score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (word,"stress",word,stressed,word,stressed,"fipi_dictionary",
                     "Слово из списка ФИПИ.",
                     now,now,s["firstIntervalMinutes"],s["defaultEase"],0,
                     now+s["firstIntervalMinutes"]*60000,None,1,0,0))
                added += 1
    set_app_state("defaultCardsInitializedV2", True)
    return added

# ── HTTP server ───────────────────────────────────────────────────────
def _is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5); return s.connect_ex(("127.0.0.1", port)) == 0

def _find_free_port(start=PORT, attempts=20):
    for p in range(start, start+attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try: s.bind(("127.0.0.1",p)); return p
            except OSError: continue
    return None

def _json(h, d, st=200):
    b = json.dumps(d, ensure_ascii=False, default=str).encode("utf-8")
    h.send_response(st)
    h.send_header("Content-Type","application/json; charset=utf-8")
    h.send_header("Content-Length",str(len(b)))
    h.send_header("Access-Control-Allow-Origin","*")
    h.send_header("Connection","close")
    h.end_headers(); h.wfile.write(b)

def _file(h, path):
    path = Path(path).resolve()
    if not path.exists(): h.send_error(404); return
    ct = {".html":"text/html; charset=utf-8",".css":"text/css",
          ".js":"application/javascript",".svg":"image/svg+xml",
          ".png":"image/png",".ico":"image/x-icon",".json":"application/json"
         }.get(path.suffix.lower(),"application/octet-stream")
    key = str(path)
    if key in _STATIC_CACHE:
        data = _STATIC_CACHE[key]
    else:
        with open(path, "rb") as f:
            data = f.read()
        _STATIC_CACHE[key] = data
    h.send_response(200); h.send_header("Content-Type",ct)
    h.send_header("Content-Length",str(len(data)))
    h.send_header("Cache-Control","max-age=3600")
    h.send_header("Connection","close")
    h.end_headers(); h.wfile.write(data); h.close_connection = True

def _body(h):
    n = int(h.headers.get("Content-Length",0))
    if n == 0: return {}
    raw = h.rfile.read(n)
    try: return json.loads(raw.decode("utf-8"))
    except: return {}

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
        self.end_headers()

    def do_GET(self):
        try:
            pr = urllib.parse.urlparse(self.path)
            p = pr.path
            qs = urllib.parse.parse_qs(pr.query)
            def q1(k,d=""): return (qs.get(k,[""])[0] or d)

            if p in ("/","/index.html"): return _file(self, STATIC_DIR/"index.html")
            if p.startswith("/icons/"): return _file(self, ICONS_DIR/p[7:])
            if p.startswith("/static/"): return _file(self, STATIC_DIR/p[8:])

            if p == "/api/health":   return _json(self, {"ok": True, "appVersion": APP_VERSION, "dbPath": str(DB_PATH)})
            if p == "/api/state":    return _json(self, get_state_snapshot())
            if p == "/api/stats":    return _json(self, get_state_snapshot()["stats"])
            if p == "/api/settings": return _json(self, get_settings())
            if p == "/api/profile":  return _json(self, get_profile())
            if p == "/api/search":   return _json(self, search_stress(q1("q"), q1("save")=="1"))
            if p == "/api/cards":
                return _json(self, get_all_cards(
                    q1("kind") or None, q1("favorite") or None,
                    q1("search") or None, q1("sort","created")))
            if p == "/api/due":       return _json(self, get_due_cards())
            if p == "/api/cards/due": return _json(self, {"count": len(get_due_cards()), "cards": get_due_cards()[:20]})
            if p == "/api/favorites": return _json(self, get_favorites())
            if p == "/api/ege/session": return _json(self, get_ege_session())
            if p == "/api/ege/result":  return _json(self, get_ege_result() or {})
            if p == "/api/punctuate/lookup":
                card = search_punctuation_card(q1("text"))
                return _json(self, {"found": card is not None, "card": card})
            if p == "/api/export/txt":
                data = export_cards_txt().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type","text/plain; charset=utf-8")
                self.send_header("Content-Disposition",'attachment; filename="orfodragon_cards.txt"')
                self.send_header("Content-Length",str(len(data)))
                self.end_headers(); self.wfile.write(data); return

            idx = STATIC_DIR/"index.html"
            if idx.exists(): return _file(self, idx)
            self.send_error(404)
        except Exception as e:
            print(f"GET {self.path}: {e}")
            try: self.send_error(500, str(e))
            except: pass

    def do_POST(self):
        try:
            p = urllib.parse.urlparse(self.path).path
            b = _body(self)

            if p == "/api/search/save":
                upsert_stress_card(normalize_word(b.get("word","")),
                    b.get("stressed",""), b.get("source","search"),
                    b.get("note",""), True)
                return _json(self, {"ok":True})
            if p == "/api/punctuate":
                return _json(self, local_punctuate(b.get("text","")))
            if p == "/api/punctuate/save":
                cid = save_punctuation_card(b.get("original",""), b.get("corrected",""),
                    b.get("explanations",[]), b.get("note",""), b.get("favorite",True), b.get("manualRule", ""))
                return _json(self, {"ok":True,"id":cid})
            if p == "/api/cards/grade":
                return _json(self, grade_card(b.get("id",b.get("word","")), b.get("result","")))
            if p == "/api/cards/favorite":
                toggle_favorite(b.get("id",""), bool(b.get("value",False)))
                return _json(self, {"ok":True})
            if p == "/api/cards/delete":
                return _json(self, {"ok":delete_card(b.get("id",""))})
            if p == "/api/cards/delete_many":
                return _json(self, {"ok":True,"deleted":delete_cards_many(b.get("ids",[]))})
            if p == "/api/cards/stress":
                upsert_stress_card(normalize_word(b.get("word","")),
                    b.get("stressed",""), b.get("source","manual"),
                    b.get("note",""), b.get("favorite",True))
                return _json(self, {"ok":True})
            if p == "/api/cards/stress_override":
                save_stress_override(b.get("word",""), b.get("stressed",""))
                return _json(self, {"ok":True})
            if p == "/api/settings/save":
                save_settings(b); return _json(self, {"ok":True})
            if p == "/api/chest/claim":
                return _json(self, claim_daily_chest())
            if p == "/api/mascot/skin":
                return _json(self, set_mascot_skin(b.get("skin","dragon_king")))
            if p == "/api/training/start":
                return _json(self, build_training_session(b.get("mode","all"),int(b.get("count",10))))
            if p == "/api/ege/create":
                return _json(self, create_ege_exam())
            if p == "/api/ege/submit":
                return _json(self, submit_ege_exam(b.get("answers",[]),b.get("expired",False)))
            if p == "/api/import/txt":
                count = import_cards_txt(b.get("data", b.get("text", "")))
                return _json(self, {"ok":True,"imported":count})
            if p == "/api/import_commas":
                count = import_cards_txt(b.get("text", b.get("data", "")))
                return _json(self, {"ok":True,"imported":count})
            if p == "/api/import/json_legacy":
                # Legacy JSON import for backwards compatibility
                data_str = b.get("data","")
                try:
                    data = json.loads(data_str)
                    cards = data.get("cards", data) if isinstance(data, dict) else data
                    if isinstance(cards, dict): cards = list(cards.values())
                    if not isinstance(cards, list): return _json(self, {"ok":True,"imported":0})
                    s = get_settings(); now = now_ms(); count = 0
                    with _db_lock:
                        with get_db() as c:
                            for item in cards:
                                if not isinstance(item, dict): continue
                                cid = str(item.get("id", item.get("word", f"imp-{now}-{count}")))
                                c.execute("INSERT OR REPLACE INTO cards(id,kind,word,stressed,prompt,answer,rule,source,note,created_at,updated_at,interval_minutes,ease,reps,due_at,last_result,favorite,times_looked_up,score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (cid,item.get("kind","stress"),item.get("word",cid),item.get("stressed",item.get("answer","")),item.get("prompt",item.get("word","")),item.get("answer",item.get("stressed","")),item.get("rule",""),item.get("source","import"),item.get("note",""),item.get("createdAt",now),now,int(item.get("intervalMinutes",s["firstIntervalMinutes"])),float(item.get("ease",s["defaultEase"])),int(item.get("reps",0)),now,item.get("lastResult"),1 if item.get("favorite") else 0,int(item.get("timesLookedUp",0)),int(item.get("score",0))))
                                count += 1
                            c.commit()
                    return _json(self, {"ok":True,"imported":count})
                except Exception as e:
                    return _json(self, {"ok":False,"imported":0,"error":str(e)})

            self.send_error(404, f"Unknown: {p}")
        except Exception as e:
            print(f"POST {self.path}: {e}")
            try: self.send_error(500, str(e))
            except: pass

def run_server(port=PORT, open_browser=True):
    init_db(); initialize_default_cards()
    if _is_port_in_use(port):
        print(f"Уже запущен: http://127.0.0.1:{port}")
        if open_browser: webbrowser.open(f"http://127.0.0.1:{port}")
        return
    actual_port = _find_free_port(port)
    if actual_port is None: print("Ошибка: нет свободного порта"); return
    if actual_port != port: print(f"Порт {port} занят, использую {actual_port}")
    try: PORT_FILE.write_text(str(actual_port))
    except: pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", actual_port), Handler)
    print(f"ОрфоДракон: http://127.0.0.1:{actual_port}")
    if open_browser:
        def _open(): time.sleep(0.8); webbrowser.open(f"http://127.0.0.1:{actual_port}")
        threading.Thread(target=_open, daemon=True).start()
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nОстановлен."); server.shutdown()
    finally:
        try: PORT_FILE.unlink()
        except: pass

if __name__ == "__main__": run_server()
