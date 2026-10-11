import asyncio
import json
import logging
import os
import random

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup)

import db
from config import (BOT_TOKEN, EXAM_SIZE, FREE_EXAMS_PER_DAY, IMAGES_DIR,
                    MAX_ERRORS, QUESTIONS_PATH, REFERRAL_BONUS_DAYS,
                    REFERRAL_MIN_ANSWERS, SCHOOL_BONUS_DAYS, XP_PER_CORRECT)
from texts_uz import T

router = Router()

with open(QUESTIONS_PATH, encoding="utf-8") as f:
    QUESTIONS = {q["id"]: q for q in json.load(f)}

# Vaqtinchalik sessiyalar (xotirada). Production'da DB yoki Redis'ga ko'chiriladi.
SESSIONS: dict[int, dict] = {}
# Rasm Telegram'ga bir marta yuklanadi, keyin file_id orqali tez yuboriladi.
FILE_IDS: dict[int, str] = {}
LETTERS = "ABCDEFGH"


def menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=T["btn_practice"]), KeyboardButton(text=T["btn_exam"])],
            [KeyboardButton(text=T["btn_stats"]), KeyboardButton(text=T["btn_invite"])],
        ],
        resize_keyboard=True,
    )


def question_kb(q: dict) -> InlineKeyboardMarkup:
    """Variantlar har safar aralashtiriladi; callback'da asl indeks yuboriladi."""
    order = list(range(len(q["options"])))
    random.shuffle(order)
    rows = [
        [InlineKeyboardButton(text=f"{LETTERS[pos]}. {q['options'][orig]}",
                              callback_data=f"a:{q['id']}:{orig}")]
        for pos, orig in enumerate(order)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def question_text(q: dict, header: str) -> str:
    return f"{header}\n[{q['topic']}]\n\n{q['text']}"


def image_path(q: dict) -> str | None:
    name = q.get("image")
    if not name:
        return None
    path = os.path.join(IMAGES_DIR, name)
    return path if os.path.exists(path) else None


async def send_question(msg: Message, q: dict, header: str):
    text, kb = question_text(q, header), question_kb(q)
    path = image_path(q)
    if path:
        photo = FILE_IDS.get(q["id"]) or FSInputFile(path)
        sent = await msg.answer_photo(photo, caption=text, reply_markup=kb)
        FILE_IDS[q["id"]] = sent.photo[-1].file_id
    else:
        await msg.answer(text, reply_markup=kb)


async def edit_message(msg: Message, text: str, kb: InlineKeyboardMarkup | None = None):
    """Rasmli xabarda caption, oddiy xabarda text tahrirlanadi."""
    if msg.photo:
        await msg.edit_caption(caption=text, reply_markup=kb)
    else:
        await msg.edit_text(text, reply_markup=kb)


def result_text(q: dict, choice: int) -> str:
    if choice == q["answer"]:
        text = T["correct"].format(xp=XP_PER_CORRECT)
    else:
        text = T["wrong"].format(chosen=q["options"][choice], answer=q["options"][q["answer"]])
    if q.get("explain"):
        text += T["explain"].format(text=q["explain"])
    return text


@router.message(CommandStart())
async def start(m: Message, command: CommandObject):
    arg = command.args or ""
    ref_by = int(arg[4:]) if arg.startswith("ref_") and arg[4:].isdigit() else None
    school_code = arg[7:] if arg.startswith("school_") else None

    res = await db.register(m.from_user.id, m.from_user.full_name, ref_by, school_code)
    await m.answer(T["welcome"].format(name=m.from_user.first_name), reply_markup=menu_kb())
    if res["school"]:
        await m.answer(T["school_welcome"].format(school=res["school"], days=SCHOOL_BONUS_DAYS))


@router.message(F.text == T["btn_practice"])
async def practice(m: Message):
    if not QUESTIONS:
        return await m.answer(T["no_questions"])
    SESSIONS[m.from_user.id] = {"mode": "practice"}
    q = random.choice(list(QUESTIONS.values()))
    await send_question(m, q, T["practice_header"].format(num=q["id"]))


@router.message(F.text == T["btn_exam"])
async def exam(m: Message):
    uid = m.from_user.id
    if not QUESTIONS:
        return await m.answer(T["no_questions"])
    if not await db.is_premium(uid) and await db.exams_today(uid) >= FREE_EXAMS_PER_DAY:
        return await m.answer(T["exam_locked"])

    ids = random.sample(list(QUESTIONS), min(EXAM_SIZE, len(QUESTIONS)))
    exam_id = await db.start_exam(uid)
    SESSIONS[uid] = {"mode": "exam", "ids": ids, "i": 0, "errors": 0, "exam_id": exam_id}
    await send_exam_question(m, uid)


def exam_header(s: dict, q: dict) -> str:
    return T["exam_header"].format(i=s["i"] + 1, n=len(s["ids"]), num=q["id"])


async def send_exam_question(msg: Message, uid: int):
    s = SESSIONS[uid]
    q = QUESTIONS[s["ids"][s["i"]]]
    await send_question(msg, q, exam_header(s, q))


async def finish_exam(msg: Message, uid: int):
    s = SESSIONS.pop(uid)
    passed = s["errors"] <= MAX_ERRORS
    await db.finish_exam(s["exam_id"], s["errors"], passed)
    key = "exam_pass" if passed else "exam_fail"
    await msg.answer(T[key].format(errors=s["errors"], max_errors=MAX_ERRORS), reply_markup=menu_kb())


async def reward_referrer(bot: Bot, uid: int, referrer: int | None):
    if not referrer:
        return
    try:
        await bot.send_message(uid, T["ref_reward_self"].format(days=REFERRAL_BONUS_DAYS))
        await bot.send_message(referrer, T["ref_reward"].format(days=REFERRAL_BONUS_DAYS))
    except Exception:  # foydalanuvchi botni bloklagan bo'lishi mumkin
        logging.exception("Referral xabarini yuborib bo'lmadi")


@router.callback_query(F.data.startswith("a:"))
async def on_answer(cb: CallbackQuery):
    uid = cb.from_user.id
    _, qid, choice = cb.data.split(":")
    q = QUESTIONS.get(int(qid))
    if not q:
        return await cb.answer()
    choice = int(choice)
    s = SESSIONS.get(uid, {"mode": "practice"})

    if s["mode"] == "exam":
        # Eski/qayta bosilgan tugmalardan himoya: faqat joriy savolga javob olinadi
        if s["ids"][s["i"]] != q["id"]:
            return await cb.answer()
        header = exam_header(s, q)
    else:
        header = T["practice_header"].format(num=q["id"])

    correct = choice == q["answer"]
    res = await db.record_answer(uid, q["id"], q["topic"], correct)
    await reward_referrer(cb.bot, uid, res["referrer"])

    body = f"{question_text(q, header)}\n\n{result_text(q, choice)}"

    if s["mode"] == "exam":
        await edit_message(cb.message, body)  # tugmalar olib tashlanadi
        if not correct:
            s["errors"] += 1
        s["i"] += 1
        if s["errors"] > MAX_ERRORS or s["i"] >= len(s["ids"]):
            await finish_exam(cb.message, uid)
        else:
            await send_exam_question(cb.message, uid)
        return await cb.answer()

    next_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=T["btn_next"], callback_data="next")]])
    await edit_message(cb.message, body, next_kb)
    await cb.answer()


@router.callback_query(F.data == "next")
async def on_next(cb: CallbackQuery):
    q = random.choice(list(QUESTIONS.values()))
    await send_question(cb.message, q, T["practice_header"].format(num=q["id"]))
    await cb.answer()


@router.message(F.text == T["btn_stats"])
async def stats(m: Message):
    st = await db.get_stats(m.from_user.id)
    weak = T["weak_topic"].format(topic=st["weak_topic"]) if st["weak_topic"] else ""
    await m.answer(T["stats"].format(
        streak=st["streak"], xp=st["xp"], correct=st["correct"], total=st["total"],
        weak=weak, premium=st["premium"] or T["no_premium"]))


@router.message(F.text == T["btn_invite"])
async def invite(m: Message):
    me = await m.bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{m.from_user.id}"
    await m.answer(T["invite"].format(
        min_answers=REFERRAL_MIN_ANSWERS, days=REFERRAL_BONUS_DAYS, link=link))


async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN topilmadi. .env yoki muhit o'zgaruvchisiga qo'ying.")
    await db.init()
    await db.add_school("demo", "Demo avtomaktab")  # test uchun: t.me/<bot>?start=school_demo
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
