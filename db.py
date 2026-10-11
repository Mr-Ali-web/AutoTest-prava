from datetime import datetime, timedelta, timezone

import aiosqlite

from config import (DB_PATH, REFERRAL_BONUS_DAYS, REFERRAL_MIN_ANSWERS,
                    SCHOOL_BONUS_DAYS, XP_PER_CORRECT)

SCHEMA = """
CREATE TABLE IF NOT EXISTS schools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    name TEXT,
    created_at TEXT NOT NULL,
    streak INTEGER NOT NULL DEFAULT 0,
    last_active TEXT,
    xp INTEGER NOT NULL DEFAULT 0,
    premium_until TEXT,
    referred_by INTEGER,
    ref_rewarded INTEGER NOT NULL DEFAULT 0,
    school_id INTEGER REFERENCES schools(id)
);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    topic TEXT,
    correct INTEGER NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    errors INTEGER,
    passed INTEGER
);
CREATE INDEX IF NOT EXISTS idx_answers_user ON answers(tg_id);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def init() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def add_school(code: str, name: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO schools(code, name) VALUES (?, ?)", (code, name))
        await db.commit()


async def _grant_days(db: aiosqlite.Connection, tg_id: int, days: int) -> None:
    cur = await db.execute("SELECT premium_until FROM users WHERE tg_id=?", (tg_id,))
    row = await cur.fetchone()
    base = _now()
    if row and row[0]:
        current = datetime.fromisoformat(row[0])
        if current > base:
            base = current
    await db.execute("UPDATE users SET premium_until=? WHERE tg_id=?",
                     (_iso(base + timedelta(days=days)), tg_id))


async def register(tg_id: int, name: str, ref_by: int | None = None,
                   school_code: str | None = None) -> dict:
    """Yangi foydalanuvchini yaratadi. Qaytaradi: {"created": bool, "school": str|None}."""
    result = {"created": False, "school": None}
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM users WHERE tg_id=?", (tg_id,))
        if await cur.fetchone():
            return result
        if ref_by == tg_id:
            ref_by = None
        school_id = None
        if school_code:
            cur = await db.execute("SELECT id, name FROM schools WHERE code=?", (school_code,))
            row = await cur.fetchone()
            if row:
                school_id, result["school"] = row[0], row[1]
        await db.execute(
            "INSERT INTO users(tg_id, name, created_at, referred_by, school_id) VALUES (?,?,?,?,?)",
            (tg_id, name, _iso(_now()), ref_by, school_id))
        if school_id:
            await _grant_days(db, tg_id, SCHOOL_BONUS_DAYS)
        await db.commit()
        result["created"] = True
    return result


async def is_premium(tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT premium_until FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
    return bool(row and row[0] and datetime.fromisoformat(row[0]) > _now())


async def record_answer(tg_id: int, question_id: int, topic: str, correct: bool,
                        xp_per_correct: int = XP_PER_CORRECT) -> dict:
    """Javobni yozadi, streak/XP ni yangilaydi, kerak bo'lsa referral bonusini beradi.
    Qaytaradi: {"referrer": int|None} - bonus berilgan bo'lsa taklif qilgan odam id si."""
    out = {"referrer": None}
    today = _now().date()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO answers(tg_id, question_id, topic, correct, ts) VALUES (?,?,?,?,?)",
            (tg_id, question_id, topic, int(correct), _iso(_now())))

        cur = await db.execute(
            "SELECT streak, last_active, referred_by, ref_rewarded FROM users WHERE tg_id=?", (tg_id,))
        streak, last_active, referred_by, ref_rewarded = await cur.fetchone()
        last = datetime.fromisoformat(last_active).date() if last_active else None
        if last != today:
            streak = streak + 1 if last == today - timedelta(days=1) else 1
        await db.execute(
            "UPDATE users SET streak=?, last_active=?, xp=xp+? WHERE tg_id=?",
            (streak, _iso(_now()), xp_per_correct if correct else 0, tg_id))

        if referred_by and not ref_rewarded:
            cur = await db.execute("SELECT COUNT(*) FROM answers WHERE tg_id=?", (tg_id,))
            (count,) = await cur.fetchone()
            if count >= REFERRAL_MIN_ANSWERS:
                await _grant_days(db, tg_id, REFERRAL_BONUS_DAYS)
                await _grant_days(db, referred_by, REFERRAL_BONUS_DAYS)
                await db.execute("UPDATE users SET ref_rewarded=1 WHERE tg_id=?", (tg_id,))
                out["referrer"] = referred_by
        await db.commit()
    return out


async def exams_today(tg_id: int) -> int:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM exams WHERE tg_id=? AND started_at>=?", (tg_id, _iso(start)))
        (n,) = await cur.fetchone()
    return n


async def start_exam(tg_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO exams(tg_id, started_at) VALUES (?,?)",
                               (tg_id, _iso(_now())))
        await db.commit()
        return cur.lastrowid


async def finish_exam(exam_id: int, errors: int, passed: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE exams SET errors=?, passed=? WHERE id=?",
                         (errors, int(passed), exam_id))
        await db.commit()


async def get_stats(tg_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT streak, xp, premium_until FROM users WHERE tg_id=?", (tg_id,))
        streak, xp, premium_until = await cur.fetchone()
        cur = await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(correct),0) FROM answers WHERE tg_id=?", (tg_id,))
        total, correct = await cur.fetchone()
        cur = await db.execute(
            """SELECT topic FROM answers WHERE tg_id=? GROUP BY topic
               HAVING COUNT(*) >= 3 ORDER BY AVG(correct) ASC LIMIT 1""", (tg_id,))
        weak = await cur.fetchone()
    premium = None
    if premium_until and datetime.fromisoformat(premium_until) > _now():
        premium = datetime.fromisoformat(premium_until).strftime("%d.%m.%Y")
    return {"streak": streak, "xp": xp, "total": total, "correct": correct,
            "weak_topic": weak[0] if weak else None, "premium": premium}
