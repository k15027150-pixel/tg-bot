"""
Telegram Bot for Educational Profile Selection
Версия для Railway с aiogram 3.x - РЕЖИМ POLLING (без вебхука)
"""

import asyncio
import logging
import os
import json
import sqlite3
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types import ReplyKeyboardRemove

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен!")

# Путь к базе данных
if os.environ.get('RAILWAY_VOLUME_MOUNT_PATH'):
    DB_PATH = os.path.join(os.environ['RAILWAY_VOLUME_MOUNT_PATH'], 'bot_database.db')
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bot_database.db')

print(f"📁 БД: {DB_PATH}")
print(f"🤖 Режим: POLLING (вебхук отключен)")
print(f"✅ Бот запускается с токеном: {BOT_TOKEN[:10]}...")

# ==================== КЛАССЫ ДАННЫХ ====================
class ProfileType(Enum):
    PHYS_MATH = "Физико-математический"
    CHEM_BIO = "Химико-биологический"
    HUMANITARIAN = "Гуманитарный"
    SOCIAL_ECON = "Социально-экономический"
    TECH = "Технический"
    ART = "Художественно-эстетический"
    UNDEFINED = "Не определен"

@dataclass
class UserResult:
    user_id: int
    full_name: str = ""
    school_class: str = ""
    matrix_subject: List[str] = field(default_factory=list)
    matrix_activity: List[str] = field(default_factory=list)
    prof_scores: List[int] = field(default_factory=lambda: [0]*6)
    profile_scores: List[int] = field(default_factory=lambda: [0]*10)
    anxiety_level: int = 0
    health_score: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

# ==================== БАЗА ДАННЫХ ====================
class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT NOT NULL,
                school_class TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS test_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                matrix_subject TEXT,
                matrix_activity TEXT,
                prof_scores TEXT,
                profile_scores TEXT,
                anxiety_level INTEGER DEFAULT 0,
                health_score INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        print("✅ БД инициализирована")
    
    def save_user(self, user_id: int, full_name: str, school_class: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO users (user_id, full_name, school_class) VALUES (?, ?, ?)', 
                       (user_id, full_name, school_class))
        conn.commit()
        conn.close()
    
    def save_test_results(self, result: UserResult):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO test_results 
            (user_id, matrix_subject, matrix_activity, prof_scores, profile_scores, anxiety_level, health_score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            result.user_id,
            json.dumps(result.matrix_subject, ensure_ascii=False),
            json.dumps(result.matrix_activity, ensure_ascii=False),
            json.dumps(result.prof_scores),
            json.dumps(result.profile_scores),
            result.anxiety_level,
            result.health_score
        ))
        conn.commit()
        conn.close()
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def get_latest_results(self, user_id: int) -> Optional[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM test_results WHERE user_id = ? ORDER BY created_at DESC LIMIT 1', (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            result = dict(row)
            for f in ['matrix_subject', 'matrix_activity', 'prof_scores', 'profile_scores']:
                if result.get(f):
                    result[f] = json.loads(result[f])
            return result
        return None

# ==================== СОСТОЯНИЯ FSM ====================
class TestStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_class = State()
    matrix_subject = State()
    matrix_activity = State()
    prof_aptitude = State()
    profile_questions = State()
    anxiety_test = State()
    health_test = State()

# ==================== ВОПРОСЫ ====================
PROF_APTITUDE_QUESTIONS = [
    {"text": "1. Мне хотелось бы в своей профессиональной деятельности", "options": [
        ("общаться с самыми разными людьми", "I"), 
        ("снимать фильмы, писать книги, рисовать, выступать на сцене", "IV"), 
        ("заниматься расчетами; вести документацию", "VI")]},
    {"text": "2. В книге или кинофильме меня больше всего привлекает", "options": [
        ("возможность следить за ходом мыслей автора", "II"), 
        ("художественная форма, мастерство писателя или режиссера", "IV"), 
        ("сюжет, действия героев", "V")]},
    {"text": "3. Меня больше обрадует Нобелевская премия", "options": [
        ("за общественную деятельность", "I"), 
        ("в области науки", "II"), 
        ("в области искусства", "IV")]},
    {"text": "4. Я скорее соглашусь стать", "options": [
        ("механиком", "III"), 
        ("спасателем", "V"), 
        ("бухгалтером", "VI")]},
    {"text": "5. Будущее людей определяют", "options": [
        ("взаимопонимание между людьми", "I"), 
        ("научные открытия", "II"), 
        ("развитие производства", "III")]},
    {"text": "6. Если я стану руководителем, то в первую очередь займусь", "options": [
        ("созданием дружного, сплоченного коллектива", "I"), 
        ("разработкой новых технологий обучения", "II"), 
        ("работой с документами", "VI")]}
]

PROFILE_QUESTIONS = [
    "1. Интересно ли вам узнавать об открытиях в области физики и математики?",
    "2. Интересно ли вам смотреть передачи о жизни растений и животных?",
    "3. Интересно ли вам выяснять устройство электроприборов?",
    "4. Интересно ли вам читать научно-популярные технические журналы?",
    "5. Интересно ли вам смотреть передачи о жизни людей в разных странах?",
    "6. Интересно ли вам бывать на выставках, концертах, спектаклях?",
    "7. Интересно ли вам обсуждать и анализировать события в стране и за рубежом?",
    "8. Интересно ли вам наблюдать за работой медсестры, врача?",
    "9. Интересно ли вам создавать уют и порядок в доме, классе, школе?",
    "10.Интересно ли вам читать книги и смотреть фильмы о войнах и сражениях?"
]

PROFILE_MAPPING = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

ANXIETY_QUESTIONS = [
    "1. У меня бывают головные боли после напряженной работы.",
    "2. Перед важными уроками мне снятся тревожные сны.",
    "3. В школе я чувствую себя неуютно.",
    "4. Мне трудно сосредоточить внимание на объяснении учителя.",
    "5. Если преподаватель отступает от темы урока, меня это сбивает.",
    "6. Меня тревожат мысли о предстоящем зачете или экзамене.",
    "7. Иногда мне кажется, что я почти ничего не знаю о предмете.",
    "8. Если у меня что-то не получается, я опускаю руки.",
    "9. Я часто не успеваю усвоить учебный материал на уроке.",
    "10. Я болезненно реагирую на критические замечания."
]

HEALTH_QUESTIONS = [
    "1. Утром мне трудно вставать вовремя, я не чувствую себя бодрым.",
    "2. Мне трудно сосредоточиться, когда я принимаюсь за работу.",
    "3. Когда меня что-то расстроило, или когда я чего-то боюсь, то в животе возникает неприятное чувство.",
    "4. Утром я ограничиваюсь лишь чашкой чая или кофе.",
    "5. Я часто мерзну.",
    "6. Когда приходится долго стоять, мне хочется облокотиться на что-нибудь.",
    "7. При резком наклоне у меня кружится голова или темнеет в глазах.",
    "8. Мне становится не по себе, если я нахожусь на большой высоте или в закрытом помещении.",
    "9. У меня часто бывают головные боли.",
    "10. Когда мне надо сосредоточиться, то я могу покачивать ногой, грызть ногти, что-то рисовать."
]

# ==================== ФУНКЦИИ ====================
def get_profile_recommendation(scores):
    mapping = {
        0: ProfileType.PHYS_MATH, 
        1: ProfileType.CHEM_BIO, 
        2: ProfileType.TECH, 
        3: ProfileType.TECH,
        4: ProfileType.CHEM_BIO, 
        5: ProfileType.ART, 
        6: ProfileType.HUMANITARIAN, 
        7: ProfileType.HUMANITARIAN,
        8: ProfileType.SOCIAL_ECON, 
        9: ProfileType.SOCIAL_ECON
    }
    if not scores or max(scores) == 0:
        return ProfileType.UNDEFINED
    return mapping.get(scores.index(max(scores)), ProfileType.UNDEFINED)

def get_professions_for_profile(profile):
    prof = {
        ProfileType.PHYS_MATH: ["Физик", "Математик", "Инженер-конструктор", "Программист", "Аналитик данных"],
        ProfileType.CHEM_BIO: ["Химик", "Биолог", "Врач", "Фармацевт", "Эколог"],
        ProfileType.HUMANITARIAN: ["Журналист", "Писатель", "Переводчик", "Учитель", "Психолог"],
        ProfileType.SOCIAL_ECON: ["Экономист", "Менеджер", "Предприниматель", "Финансист", "Маркетолог"],
        ProfileType.TECH: ["Инженер", "Технолог", "Механик", "Электрик", "Строитель"],
        ProfileType.ART: ["Художник", "Дизайнер", "Музыкант", "Актер", "Режиссер"],
        ProfileType.UNDEFINED: ["Пройдите тестирование полностью"]
    }
    return prof.get(profile, prof[ProfileType.UNDEFINED])

# ==================== КЛАВИАТУРЫ ====================
def get_matrix_subject_keyboard():
    subjects = ["Человек", "Информация", "Финансы", "Техника", "Искусство", "Животные и растения", "Изделия и продукты", "Природные ресурсы"]
    keyboard = []
    for i in range(0, len(subjects), 2):
        row = [KeyboardButton(text=subjects[i])]
        if i + 1 < len(subjects):
            row.append(KeyboardButton(text=subjects[i + 1]))
        keyboard.append(row)
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_matrix_activity_keyboard():
    activities = ["Управление", "Обслуживание", "Образование", "Производство", "Конструирование", "Исследование", "Защита", "Контроль"]
    keyboard = []
    for i in range(0, len(activities), 2):
        row = [KeyboardButton(text=activities[i])]
        if i + 1 < len(activities):
            row.append(KeyboardButton(text=activities[i + 1]))
        keyboard.append(row)
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_yes_no_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Да"), KeyboardButton(text="Нет")]], resize_keyboard=True)

def get_answer_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="А"), KeyboardButton(text="Б"), KeyboardButton(text="В")]], resize_keyboard=True)

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db = DatabaseManager(DB_PATH)
user_results: Dict[int, UserResult] = {}

# ==================== ОБРАБОТЧИКИ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    saved_user = db.get_user(user_id)
    saved_results = db.get_latest_results(user_id)
    
    if saved_user and saved_results:
        await message.answer(
            f"👋 С возвращением, {saved_user['full_name']}!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📊 Мои результаты", callback_data="show_results")],
                [InlineKeyboardButton(text="🔄 Пройти заново", callback_data="restart_test")]
            ])
        )
        return
    
    user_results[user_id] = UserResult(user_id=user_id)
    await state.clear()
    await message.answer("🌟 **Добро пожаловать в Тетрадь самодиагностики!**\n\n**Как вас зовут?**", parse_mode="Markdown")
    await state.set_state(TestStates.waiting_for_name)

@dp.callback_query(lambda c: c.data == "show_results")
async def show_saved_results(callback: types.CallbackQuery):
    user = db.get_user(callback.from_user.id)
    results = db.get_latest_results(callback.from_user.id)
    if results and user:
        profile = get_profile_recommendation(results.get('profile_scores', [0]*10))
        text = f"📊 **Результаты**\n👤 {user['full_name']}\n\n**Профиль:** {profile.value}\n\n**Профессии:**\n" + "\n".join(f"• {p}" for p in get_professions_for_profile(profile)[:5])
        await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "restart_test")
async def restart_test(callback: types.CallbackQuery, state: FSMContext):
    user_results[callback.from_user.id] = UserResult(user_id=callback.from_user.id)
    await state.clear()
    await callback.message.answer("🔄 Начинаем заново!\n**В каком вы классе?**", parse_mode="Markdown")
    await state.set_state(TestStates.waiting_for_class)
    await callback.answer()

@dp.message(TestStates.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    user_results[message.from_user.id].full_name = message.text.strip()
    await message.answer(f"Приятно познакомиться!\n**В каком вы классе?**", parse_mode="Markdown")
    await state.set_state(TestStates.waiting_for_class)

@dp.message(TestStates.waiting_for_class)
async def process_class(message: types.Message, state: FSMContext):
    user_results[message.from_user.id].school_class = message.text.strip()
    await message.answer("📋 **Матрица выбора профессии**\n\n**Шаг 1:** С кем или с чем вы бы хотели работать?", parse_mode="Markdown", reply_markup=get_matrix_subject_keyboard())
    await state.set_state(TestStates.matrix_subject)

@dp.message(TestStates.matrix_subject)
async def process_matrix_subject(message: types.Message, state: FSMContext):
    valid = ["Человек", "Информация", "Финансы", "Техника", "Искусство", "Животные и растения", "Изделия и продукты", "Природные ресурсы"]
    if message.text not in valid:
        await message.answer("Выберите из кнопок", reply_markup=get_matrix_subject_keyboard())
        return
    await state.update_data(matrix_subject=message.text)
    await message.answer("**Шаг 2:** Чем бы вы хотели заниматься?", parse_mode="Markdown", reply_markup=get_matrix_activity_keyboard())
    await state.set_state(TestStates.matrix_activity)

@dp.message(TestStates.matrix_activity)
async def process_matrix_activity(message: types.Message, state: FSMContext):
    valid = ["Управление", "Обслуживание", "Образование", "Производство", "Конструирование", "Исследование", "Защита", "Контроль"]
    if message.text not in valid:
        await message.answer("Выберите из кнопок", reply_markup=get_matrix_activity_keyboard())
        return
    data = await state.get_data()
    user_results[message.from_user.id].matrix_subject = [data.get("matrix_subject")]
    user_results[message.from_user.id].matrix_activity = [message.text]
    await message.answer("✅ **Переходим к тесту профессиональных склонностей**", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    await asyncio.sleep(1)
    await state.update_data(prof_index=0, prof_scores=[0]*6)
    await send_prof_question(message, state)

async def send_prof_question(message: types.Message, state: FSMContext):
    data = await state.get_data()
    idx = data.get("prof_index", 0)
    if idx < len(PROF_APTITUDE_QUESTIONS):
        q = PROF_APTITUDE_QUESTIONS[idx]
        text = f"📝 **Вопрос {idx+1}/{len(PROF_APTITUDE_QUESTIONS)}:**\n\n{q['text']}\n\nА) {q['options'][0][0]}\nБ) {q['options'][1][0]}\nВ) {q['options'][2][0]}"
        await message.answer(text, parse_mode="Markdown", reply_markup=get_answer_keyboard())
        await state.set_state(TestStates.prof_aptitude)

@dp.message(TestStates.prof_aptitude)
async def process_prof_answer(message: types.Message, state: FSMContext):
    if message.text not in ['А', 'Б', 'В']:
        await message.answer("Выберите А, Б или В", reply_markup=get_answer_keyboard())
        return
    data = await state.get_data()
    idx = data.get("prof_index", 0)
    scores = data.get("prof_scores", [0]*6)
    if message.text == 'А':
        scores[0] += 1
    elif message.text == 'Б':
        scores[3] += 1
    elif message.text == 'В':
        scores[5] += 1
    idx += 1
    await state.update_data(prof_index=idx, prof_scores=scores)
    if idx < len(PROF_APTITUDE_QUESTIONS):
        await send_prof_question(message, state)
    else:
        user_results[message.from_user.id].prof_scores = scores
        await message.answer("✅ **Тест профсклонностей завершен!**\n➡️ **Тест 'Профиль'**", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        await asyncio.sleep(1)
        await state.update_data(profile_index=0, profile_scores=[0]*10)
        await send_profile_question(message, state)

async def send_profile_question(message: types.Message, state: FSMContext):
    data = await state.get_data()
    idx = data.get("profile_index", 0)
    if idx < len(PROFILE_QUESTIONS):
        await message.answer(f"📋 **Вопрос {idx+1}/{len(PROFILE_QUESTIONS)}:**\n\n{PROFILE_QUESTIONS[idx]}", parse_mode="Markdown", reply_markup=get_yes_no_keyboard())
        await state.set_state(TestStates.profile_questions)

@dp.message(TestStates.profile_questions)
async def process_profile_answer(message: types.Message, state: FSMContext):
    answer = message.text.strip().lower()
    
    if answer not in ['да', 'нет']:
        await message.answer("Ответьте Да или Нет", reply_markup=get_yes_no_keyboard())
        return
    
    is_yes = answer == 'да'
    
    data = await state.get_data()
    idx = data.get("profile_index", 0)
    scores = data.get("profile_scores", [0]*10)
    
    if is_yes and idx < len(PROFILE_MAPPING):
        scores[PROFILE_MAPPING[idx]-1] += 1
    
    idx += 1
    await state.update_data(profile_index=idx, profile_scores=scores)
    
    if idx < len(PROFILE_QUESTIONS):
        await send_profile_question(message, state)
    else:
        user_results[message.from_user.id].profile_scores = scores
        await message.answer("✅ **Тест 'Профиль' завершен!**\n➡️ **Тест на тревожность**", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        await asyncio.sleep(1)
        await state.update_data(anxiety_index=0, anxiety_score=0)
        await send_anxiety_question(message, state)

async def send_anxiety_question(message: types.Message, state: FSMContext):
    data = await state.get_data()
    idx = data.get("anxiety_index", 0)
    if idx < len(ANXIETY_QUESTIONS):
        await message.answer(f"😟 **Вопрос {idx+1}/{len(ANXIETY_QUESTIONS)}:**\n\n{ANXIETY_QUESTIONS[idx]}", parse_mode="Markdown", reply_markup=get_yes_no_keyboard())
        await state.set_state(TestStates.anxiety_test)

@dp.message(TestStates.anxiety_test)
async def process_anxiety_answer(message: types.Message, state: FSMContext):
    answer = message.text.strip().lower()
    
    if answer not in ['да', 'нет']:
        await message.answer("Ответьте Да или Нет", reply_markup=get_yes_no_keyboard())
        return
    
    is_yes = answer == 'да'
    
    data = await state.get_data()
    idx = data.get("anxiety_index", 0)
    score = data.get("anxiety_score", 0)
    
    if is_yes:
        score += 1
    
    idx += 1
    await state.update_data(anxiety_index=idx, anxiety_score=score)
    
    if idx < len(ANXIETY_QUESTIONS):
        await send_anxiety_question(message, state)
    else:
        user_results[message.from_user.id].anxiety_level = score
        await message.answer("✅ **Тест на тревожность завершен!**\n➡️ **Заключительный тест**", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        await asyncio.sleep(1)
        await state.update_data(health_index=0, health_score=0)
        await send_health_question(message, state)

async def send_health_question(message: types.Message, state: FSMContext):
    data = await state.get_data()
    idx = data.get("health_index", 0)
    if idx < len(HEALTH_QUESTIONS):
        await message.answer(f"💪 **Вопрос {idx+1}/{len(HEALTH_QUESTIONS)}:**\n\n{HEALTH_QUESTIONS[idx]}", parse_mode="Markdown", reply_markup=get_yes_no_keyboard())
        await state.set_state(TestStates.health_test)

@dp.message(TestStates.health_test)
async def process_health_answer(message: types.Message, state: FSMContext):
    answer = message.text.strip().lower()
    
    if answer not in ['да', 'нет']:
        await message.answer("Ответьте Да или Нет", reply_markup=get_yes_no_keyboard())
        return
    
    is_yes = answer == 'да'
    
    data = await state.get_data()
    idx = data.get("health_index", 0)
    score = data.get("health_score", 0)
    
    if is_yes:
        score += 1
    
    idx += 1
    await state.update_data(health_index=idx, health_score=score)
    
    if idx < len(HEALTH_QUESTIONS):
        await send_health_question(message, state)
    else:
        result = user_results.get(message.from_user.id)
        if result:
            result.health_score = score
            db.save_user(result.user_id, result.full_name, result.school_class)
            db.save_test_results(result)
        
        profile = get_profile_recommendation(result.profile_scores if result else [0]*10)
        await message.answer(
            f"🎉 **Тестирование завершено!**\n\n**Профиль:** {profile.value}\n\n**Профессии:**\n" + "\n".join(f"• {p}" for p in get_professions_for_profile(profile)[:5]) + "\n\n/results - полные результаты",
            parse_mode="Markdown", reply_markup=ReplyKeyboardRemove()
        )
        await state.clear()

@dp.message(Command("results"))
async def show_results(message: types.Message):
    user = db.get_user(message.from_user.id)
    results = db.get_latest_results(message.from_user.id)
    if not results or not user:
        await message.answer("Сначала пройдите /start")
        return
    profile = get_profile_recommendation(results.get('profile_scores', [0]*10))
    text = f"📊 **Результаты**\n👤 {user['full_name']}"
    if user.get('school_class'):
        text += f", {user['school_class']} класс"
    text += f"\n\n**Профиль:** {profile.value}\n\n**Профессии:**\n" + "\n".join(f"• {p}" for p in get_professions_for_profile(profile)[:5])
    
    anxiety = results.get('anxiety_level', 0)
    if anxiety > 0:
        level = "низкий" if anxiety <= 6 else "средний" if anxiety <= 13 else "высокий"
        text += f"\n\n**Тревожность:** {level} ({anxiety} баллов)"
    
    health = results.get('health_score', 0)
    if health > 0:
        htext = "хорошее" if health <= 5 else "среднее" if health <= 10 else "требует внимания"
        text += f"\n**Самочувствие:** {htext}"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer("📚 **Команды:**\n/start - начать\n/results - результаты\n/help - помощь\n/cancel - отмена", parse_mode="Markdown")

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено. /start для начала", reply_markup=ReplyKeyboardRemove())

# ==================== ЗАПУСК ====================
async def main():
    logging.basicConfig(level=logging.INFO)
    print("🚀 Запуск бота в режиме POLLING...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
