import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "pravaga.db"))
QUESTIONS_PATH = os.getenv("QUESTIONS_PATH", os.path.join(BASE_DIR, "data", "questions_yhq_v1.json"))

IMAGES_DIR = os.getenv("IMAGES_DIR", os.path.join(BASE_DIR, "data", "images"))

# Imtihon qoidalari (rasmiy format: 20 savol, ko'pi bilan 2 xato)
EXAM_SIZE = 20
MAX_ERRORS = 2

# Biznes qoidalari - keyin real raqamlar bilan sozlanadi
FREE_EXAMS_PER_DAY = 1
REFERRAL_BONUS_DAYS = 3      # taklif qilgan ham, taklif qilingan ham oladi
REFERRAL_MIN_ANSWERS = 5     # do'sti kamida shuncha savol yechsa bonus beriladi
SCHOOL_BONUS_DAYS = 7        # avtomaktab havolasi orqali kirganlarga
XP_PER_CORRECT = 10
