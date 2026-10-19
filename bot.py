# -*- coding: utf-8 -*-
"""
بوت تليجرام متكامل - لعبة حرب الشركات
مبني على aiogram 3.x مع قاعدة بيانات SQLite دائمة.
"""

import asyncio
import html
import logging
import random
import string
from datetime import datetime
from typing import Optional, List, Dict, Any, Union

import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
    User,
    Chat,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ==================== الإعدادات الثابتة ====================
BOT_TOKEN = "8687055925:AAEPLHTKWF7oOfaDG3xFWKMJ_DSsrpwJjYU"
DEVELOPER_ID = 8505543329
DEVELOPER_LINK = "https://t.me/EI6ED"
DB_PATH = "war_of_companies.db"

# إعدادات اللعبة
STARTING_BALANCE = 100_000
COMPANY_CREATION_COST = 50_000
COMPANY_ID_PREFIX = "C"

# إعدادات التسجيل
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger(__name__)

# تهيئة البوت والموزع
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ==================== حالات FSM ====================
class CompanyCreation(StatesGroup):
    waiting_for_name = State()
    waiting_for_sector = State()

class InvestmentProcess(StatesGroup):
    waiting_for_amount = State()

# ==================== دوال قاعدة البيانات ====================
async def init_db() -> None:
    """إنشاء جداول قاعدة البيانات في حال عدم وجودها."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                balance INTEGER DEFAULT 100000,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                company_id TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                sector TEXT NOT NULL,
                balance INTEGER DEFAULT 0,
                profit INTEGER DEFAULT 0,
                employees INTEGER DEFAULT 0,
                branches INTEGER DEFAULT 1,
                market_value INTEGER DEFAULT 100000,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (owner_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS investments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                investor_id INTEGER NOT NULL,
                company_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT DEFAULT 'pending', -- pending, accepted, rejected
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (investor_id) REFERENCES users(user_id),
                FOREIGN KEY (company_id) REFERENCES companies(company_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id TEXT NOT NULL,
                name TEXT NOT NULL,
                specialty TEXT NOT NULL,
                salary INTEGER NOT NULL,
                hired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (company_id) REFERENCES companies(company_id)
            )
        """)
        await db.commit()
    logger.info("تم تهيئة قاعدة البيانات بنجاح.")

async def get_or_create_user(user: User) -> Dict[str, Any]:
    """جلب بيانات المستخدم أو إنشائه إذا لم يكن موجوداً."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
        await db.execute(
            "INSERT INTO users (user_id, username, full_name, balance) VALUES (?, ?, ?, ?)",
            (user.id, user.username or "", user.full_name, STARTING_BALANCE)
        )
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row)

async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def update_user_balance(user_id: int, amount: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()

async def generate_company_id() -> str:
    """توليد معرف شركة فريد (مثال: C101)."""
    async with aiosqlite.connect(DB_PATH) as db:
        while True:
            num = random.randint(100, 9999)
            cid = f"{COMPANY_ID_PREFIX}{num}"
            async with db.execute("SELECT 1 FROM companies WHERE company_id = ?", (cid,)) as cursor:
                if not await cursor.fetchone():
                    return cid

async def create_company(owner_id: int, name: str, sector: str) -> str:
    """إنشاء شركة جديدة وخصم التكلفة من رصيد المالك."""
    company_id = await generate_company_id()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN")
        await db.execute(
            "INSERT INTO companies (company_id, owner_id, name, sector, balance, market_value) VALUES (?, ?, ?, ?, ?, ?)",
            (company_id, owner_id, name, sector, 0, 100_000)
        )
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (COMPANY_CREATION_COST, owner_id))
        await db.commit()
    return company_id

async def get_company_by_owner(owner_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM companies WHERE owner_id = ?", (owner_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def get_company(company_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM companies WHERE company_id = ?", (company_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def update_company_balance(company_id: str, amount: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE companies SET balance = balance + ? WHERE company_id = ?", (amount, company_id))
        await db.commit()

async def update_company_profit(company_id: str, amount: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE companies SET profit = profit + ? WHERE company_id = ?", (amount, company_id))
        await db.commit()

async def update_company_market_value(company_id: str, new_value: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE companies SET market_value = ? WHERE company_id = ?", (new_value, company_id))
        await db.commit()

async def get_top_companies(limit: int = 20) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT c.*, u.full_name as owner_name FROM companies c JOIN users u ON c.owner_id = u.user_id ORDER BY c.market_value DESC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def get_user_company_rank(owner_id: int) -> Optional[int]:
    """جلب مركز شركة المستخدم في التصنيف العالمي."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT COUNT(*) + 1 FROM companies
            WHERE market_value > (SELECT market_value FROM companies WHERE owner_id = ?)
        """, (owner_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def create_investment_request(investor_id: int, company_id: str, amount: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO investments (investor_id, company_id, amount, status) VALUES (?, ?, ?, 'pending')",
            (investor_id, company_id, amount)
        )
        await db.commit()
        return cursor.lastrowid

async def get_investment(investment_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM investments WHERE id = ?", (investment_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def update_investment_status(investment_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE investments SET status = ? WHERE id = ?", (status, investment_id))
        await db.commit()

async def get_company_employees(company_id: str) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM employees WHERE company_id = ?", (company_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def add_employee(company_id: str, name: str, specialty: str, salary: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO employees (company_id, name, specialty, salary) VALUES (?, ?, ?, ?)",
            (company_id, name, specialty, salary)
        )
        await db.commit()
        await db.execute("UPDATE companies SET employees = employees + 1 WHERE company_id = ?", (company_id,))
        await db.commit()
        return cursor.lastrowid

# ==================== لوحات المفاتيح (Keyboards) ====================
def get_start_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📜 قوانين اللعبة", callback_data="rules")
    builder.button(text="➕ إضافة البوت لمجموعة", url=f"https://t.me/{(await bot.get_me()).username}?startgroup=true")
    builder.adjust(1)
    return builder.as_markup()

def get_developer_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👨‍💻 المطور", url=DEVELOPER_LINK)
    return builder.as_markup()

def get_sector_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    sectors = ["تكنولوجيا", "طاقة", "تجارة", "عقارات", "أدوية", "أغذية"]
    for sector in sectors:
        builder.button(text=sector, callback_data=f"sector_{sector}")
    builder.adjust(2)
    return builder.as_markup()

def get_company_info_keyboard(company_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 جمع الأرباح", callback_data=f"collect_profit_{company_id}")
    builder.button(text="👥 توظيف", callback_data=f"hire_menu_{company_id}")
    builder.button(text="🏢 فتح فرع", callback_data=f"open_branch_{company_id}")
    builder.button(text="⬆️ ترقية", callback_data=f"upgrade_{company_id}")
    builder.adjust(2)
    return builder.as_markup()

def get_hiring_keyboard(company_id: str, candidate_index: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ السابق", callback_data=f"hire_prev_{company_id}_{candidate_index}")
    builder.button(text="التالي ▶️", callback_data=f"hire_next_{company_id}_{candidate_index}")
    builder.button(text="✅ توظيف هذا المرشح", callback_data=f"hire_accept_{company_id}_{candidate_index}")
    builder.adjust(2, 1)
    return builder.as_markup()

def get_investment_keyboard(investment_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ قبول الاستثمار", callback_data=f"inv_accept_{investment_id}")
    builder.button(text="❌ رفض الاستثمار", callback_data=f"inv_reject_{investment_id}")
    builder.adjust(2)
    return builder.as_markup()

def get_rules_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 رجوع", callback_data="back_to_start")
    return builder.as_markup()

# ==================== بيانات المرشحين للتوظيف ====================
CANDIDATE_NAMES = ["أحمد", "محمد", "سارة", "ليلى", "عمر", "خالد", "نورة", "فاطمة", "يوسف", "مريم"]
CANDIDATE_SPECIALTIES = ["مهندس برمجيات", "محلل مالي", "مدير تسويق", "خبير موارد بشرية", "مهندس مدني", "طبيب", "محاسب", "مستشار قانوني"]

def generate_candidates(count: int = 5) -> List[Dict[str, Any]]:
    """توليد قائمة مرشحين عشوائيين."""
    candidates = []
    used_names = set()
    for _ in range(count):
        name = random.choice(CANDIDATE_NAMES)
        while name in used_names:
            name = random.choice(CANDIDATE_NAMES)
        used_names.add(name)
        specialty = random.choice(CANDIDATE_SPECIALTIES)
        salary = random.randint(5000, 25000)
        hiring_cost = salary * 2
        candidates.append({
            "name": name,
            "specialty": specialty,
            "salary": salary,
            "hiring_cost": hiring_cost
        })
    return candidates

# ==================== معالجات الرسائل ====================
@dp.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def cmd_start_private(message: Message) -> None:
    """معالج /start في المحادثة الخاصة."""
    await get_or_create_user(message.from_user)
    text = (
        f"👋 أهلاً بك <b>{html.quote(message.from_user.full_name)}</b> في بوت <b>حرب الشركات</b>!\n\n"
        "🌍 هذه اللعبة مصممة للعب داخل المجموعات (الكروبات) فقط.\n"
        "يمكنك إضافة البوت إلى مجموعتك والبدء بتأسيس إمبراطوريتك الاقتصادية.\n\n"
        "📜 اطلع على القوانين أو أضف البوت لمجموعتك من الأزرار أدناه."
    )
    await message.answer(text, reply_markup=get_start_keyboard())

@dp.message(F.text == "المطور", F.chat.type.in_({ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP}))
async def show_developer(message: Message) -> None:
    """جلب معلومات المطور من تليجرام وإرسالها."""
    try:
        dev_chat: Chat = await bot.get_chat(DEVELOPER_ID)
        dev_user: Optional[User] = None
        if dev_chat.type == ChatType.PRIVATE:
            dev_user = await bot.get_users(DEVELOPER_ID) if hasattr(bot, 'get_users') else None
        
        full_name = dev_chat.full_name or "غير متاح"
        bio = getattr(dev_chat, 'bio', None) or "لا يوجد بايو."
        username = f"@{dev_chat.username}" if dev_chat.username else "غير متاح"

        text = (
            f"👨‍💻 <b>معلومات المطور</b>\n\n"
            f"👤 الاسم: {html.quote(full_name)}\n"
            f"🆔 الأيدي: <code>{DEVELOPER_ID}</code>\n"
            f"📝 البايو: {html.quote(bio)}\n"
            f"🔗 المعرف: {username}\n\n"
            f"للتوااصل مع المطور اضغط على الزر أدناه."
        )

        # محاولة إرسال الصورة الشخصية إذا وجدت
        photos = await bot.get_user_profile_photos(DEVELOPER_ID, limit=1)
        if photos.total_count > 0:
            file_id = photos.photos[0][-1].file_id
            await message.answer_photo(photo=file_id, caption=text, reply_markup=get_developer_keyboard())
        else:
            await message.answer(text, reply_markup=get_developer_keyboard())

    except TelegramAPIError as e:
        logger.error(f"خطأ في جلب بيانات المطور: {e}")
        await message.answer("⚠️ تعذر جلب بيانات المطور حالياً. يرجى المحاولة لاحقاً.")

@dp.message(F.text.lower() == "انشاء شركة")
async def create_company_start(message: Message, state: FSMContext) -> None:
    """بدء عملية إنشاء شركة."""
    user = await get_or_create_user(message.from_user)
    if user['balance'] < COMPANY_CREATION_COST:
        await message.answer(
            f"❌ عذراً، رصيدك غير كافٍ لتأسيس شركة.\n"
            f"💰 رصيدك الحالي: <b>{user['balance']:,}$</b>\n"
            f"💵 تكلفة التأسيس: <b>{COMPANY_CREATION_COST:,}$</b>"
        )
        return

    existing_company = await get_company_by_owner(message.from_user.id)
    if existing_company:
        await message.answer("⚠️ لديك شركة بالفعل! استخدم كلمة <b>شركتي</b> لعرض معلوماتها.")
        return

    await state.set_state(CompanyCreation.waiting_for_name)
    await message.answer(
        f"🏢 <b>تأسيس شركة جديدة</b>\n\n"
        f"💰 سيتم خصم <b>{COMPANY_CREATION_COST:,}$</b> من رصيدك فوراً.\n"
        f"✍️ الرجاء إرسال <b>اسم الشركة</b> الآن:"
    )

@dp.message(CompanyCreation.waiting_for_name)
async def create_company_name(message: Message, state: FSMContext) -> None:
    """استقبال اسم الشركة والانتقال لاختيار المجال."""
    company_name = message.text.strip()
    if len(company_name) < 3:
        await message.answer("⚠️ اسم الشركة قصير جداً. يرجى إرسال اسم مكون من 3 أحرف على الأقل.")
        return
    if len(company_name) > 50:
        await message.answer("⚠️ اسم الشركة طويل جداً. الحد الأقصى 50 حرفاً.")
        return

    await state.update_data(company_name=company_name)
    await state.set_state(CompanyCreation.waiting_for_sector)
    await message.answer(
        f"✅ تم حفظ الاسم: <b>{html.quote(company_name)}</b>\n\n"
        f"📊 الآن اختر <b>مجال الشركة</b> من الأزرار أدناه:",
        reply_markup=get_sector_keyboard()
    )

@dp.callback_query(F.data.startswith("sector_"), CompanyCreation.waiting_for_sector)
async def create_company_sector(callback: CallbackQuery, state: FSMContext) -> None:
    """استكمال إنشاء الشركة بعد اختيار المجال."""
    sector = callback.data.split("_", 1)[1]
    data = await state.get_data()
    company_name = data.get("company_name")
    if not company_name:
        await callback.answer("⚠️ حدث خطأ، يرجى إعادة المحاولة.", show_alert=True)
        await state.clear()
        return

    user = await get_or_create_user(callback.from_user)
    if user['balance'] < COMPANY_CREATION_COST:
        await callback.message.answer("❌ رصيدك لم يعد كافياً لتأسيس الشركة.")
        await state.clear()
        await callback.answer()
        return

    company_id = await create_company(callback.from_user.id, company_name, sector)
    await state.clear()

    await callback.message.edit_text(
        f"🎉 <b>تم تأسيس شركتك بنجاح!</b>\n\n"
        f"🏢 الاسم: <b>{html.quote(company_name)}</b>\n"
        f"🆔 المعرف: <code>{company_id}</code>\n"
        f"📊 المجال: <b>{sector}</b>\n"
        f"💰 تم خصم <b>{COMPANY_CREATION_COST:,}$</b> من رصيدك.\n\n"
        f"استخدم كلمة <b>شركتي</b> لإدارة شركتك.",
        reply_markup=None
    )
    await callback.answer("تم إنشاء الشركة بنجاح!")

@dp.message(F.text.lower().in_({"شركتي", "معلومات الشركة"}))
async def show_company_info(message: Message) -> None:
    """عرض بطاقة معلومات الشركة."""
    company = await get_company_by_owner(message.from_user.id)
    if not company:
        await message.answer("❌ ليس لديك شركة بعد. اكتب <b>انشاء شركة</b> لتأسيس واحدة.")
        return

    user = await get_user(company['owner_id'])
    rank = await get_user_company_rank(company['owner_id'])

    text = (
        f"🏢 <b>بطاقة الشركة</b>\n\n"
        f"📛 الاسم: <b>{html.quote(company['name'])}</b>\n"
        f"🆔 المعرف: <code>{company['company_id']}</code>\n"
        f"👤 المالك: {html.quote(user['full_name'] if user else 'غير معروف')}\n"
        f"📊 المجال: <b>{company['sector']}</b>\n"
        f"💰 الأرباح: <b>{company['profit']:,}$</b>\n"
        f"💵 السيولة: <b>{company['balance']:,}$</b>\n"
        f"👥 الموظفون: <b>{company['employees']}</b>\n"
        f"🏢 الفروع: <b>{company['branches']}</b>\n"
        f"📈 القيمة السوقية: <b>{company['market_value']:,}$</b>\n"
        f"🏆 المركز: <b>#{rank}</b>"
    )
    await message.answer(text, reply_markup=get_company_info_keyboard(company['company_id']))

@dp.message(F.text.lower() == "توظيف")
async def hiring_start(message: Message) -> None:
    """بدء عملية التوظيف (عرض أول مرشح)."""
    company = await get_company_by_owner(message.from_user.id)
    if not company:
        await message.answer("❌ يجب أن تمتلك شركة أولاً لتتمكن من التوظيف.")
        return

    candidates = generate_candidates(5)
    # تخزين المرشحين مؤقتاً في الذاكرة لكل شركة (يمكن تحسينه بتخزينهم في DB)
    # لتجنب التعقيد، سنعيد توليدهم عند كل ضغطة زر، لكن الأفضل تخزينهم.
    # سنستخدم قاموساً عالمياً مؤقتاً.
    hiring_cache[company['company_id']] = candidates

    await show_candidate(message, company['company_id'], 0)

async def show_candidate(message_or_callback: Union[Message, CallbackQuery], company_id: str, index: int) -> None:
    """عرض بطاقة مرشح معين."""
    candidates = hiring_cache.get(company_id, [])
    if not candidates:
        await message_or_callback.answer("⚠️ لا يوجد مرشحون متاحون حالياً.")
        return
    if index < 0:
        index = len(candidates) - 1
    if index >= len(candidates):
        index = 0

    cand = candidates[index]
    text = (
        f"👤 <b>مرشح للتوظيف</b>\n\n"
        f"📛 الاسم: <b>{html.quote(cand['name'])}</b>\n"
        f"💼 الاختصاص: <b>{cand['specialty']}</b>\n"
        f"💵 الراتب السنوي: <b>{cand['salary']:,}$</b>\n"
        f"💰 تكلفة التعيين: <b>{cand['hiring_cost']:,}$</b>\n\n"
        f"المرشح {index + 1} من {len(candidates)}"
    )
    keyboard = get_hiring_keyboard(company_id, index)

    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.edit_text(text, reply_markup=keyboard)
        await message_or_callback.answer()
    else:
        await message_or_callback.answer(text, reply_markup=keyboard)

# قاموس مؤقت لتخزين المرشحين
hiring_cache: Dict[str, List[Dict[str, Any]]] = {}

@dp.callback_query(F.data.startswith("hire_prev_"))
async def hire_prev(callback: CallbackQuery) -> None:
    _, _, cid, idx = callback.data.split("_")
    await show_candidate(callback, cid, int(idx) - 1)

@dp.callback_query(F.data.startswith("hire_next_"))
async def hire_next(callback: CallbackQuery) -> None:
    _, _, cid, idx = callback.data.split("_")
    await show_candidate(callback, cid, int(idx) + 1)

@dp.callback_query(F.data.startswith("hire_accept_"))
async def hire_accept(callback: CallbackQuery) -> None:
    _, _, cid, idx = callback.data.split("_")
    index = int(idx)
    candidates = hiring_cache.get(cid, [])
    if index >= len(candidates):
        await callback.answer("⚠️ المرشح غير متوفر.", show_alert=True)
        return
    cand = candidates[index]
    company = await get_company(cid)
    if not company:
        await callback.answer("⚠️ الشركة غير موجودة.", show_alert=True)
        return
    if company['balance'] < cand['hiring_cost']:
        await callback.answer(f"❌ سيولة الشركة غير كافية. تحتاج {cand['hiring_cost']:,}$", show_alert=True)
        return

    # خصم التكلفة وإضافة الموظف
    await update_company_balance(cid, -cand['hiring_cost'])
    await add_employee(cid, cand['name'], cand['specialty'], cand['salary'])
    candidates.pop(index)
    await callback.message.edit_text(
        f"✅ تم توظيف <b>{html.quote(cand['name'])}</b> بنجاح!\n"
        f"💼 الاختصاص: {cand['specialty']}\n"
        f"💵 الراتب: {cand['salary']:,}$\n"
        f"💰 تم خصم {cand['hiring_cost']:,}$ من سيولة الشركة."
    )
    await callback.answer("تم التوظيف!")

@dp.callback_query(F.data.startswith("collect_profit_"))
async def collect_profit(callback: CallbackQuery) -> None:
    cid = callback.data.split("_", 2)[2]
    company = await get_company(cid)
    if not company or company['owner_id'] != callback.from_user.id:
        await callback.answer("⚠️ ليس لديك صلاحية.", show_alert=True)
        return
    if company['profit'] <= 0:
        await callback.answer("⚠️ لا توجد أرباح لجمعها حالياً.", show_alert=True)
        return
    amount = company['profit']
    await update_company_balance(cid, amount)
    await db_update_profit_to_zero(cid)  # دالة مساعدة
    await callback.message.edit_text(f"💰 تم جمع أرباح بقيمة <b>{amount:,}$</b> وإضافتها لسيولة الشركة.")
    await callback.answer("تم جمع الأرباح!")

async def db_update_profit_to_zero(company_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE companies SET profit = 0 WHERE company_id = ?", (company_id,))
        await db.commit()

@dp.callback_query(F.data.startswith("open_branch_"))
async def open_branch(callback: CallbackQuery) -> None:
    cid = callback.data.split("_", 2)[2]
    company = await get_company(cid)
    if not company or company['owner_id'] != callback.from_user.id:
        await callback.answer("⚠️ ليس لديك صلاحية.", show_alert=True)
        return
    cost = 50_000
    if company['balance'] < cost:
        await callback.answer(f"❌ سيولة غير كافية. تحتاج {cost:,}$", show_alert=True)
        return
    await update_company_balance(cid, -cost)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE companies SET branches = branches + 1 WHERE company_id = ?", (cid,))
        await db.commit()
    await callback.message.edit_text(f"🏢 تم فتح فرع جديد بتكلفة <b>{cost:,}$</b>.")
    await callback.answer("تم فتح الفرع!")

@dp.callback_query(F.data.startswith("upgrade_"))
async def upgrade_company(callback: CallbackQuery) -> None:
    cid = callback.data.split("_", 1)[1]
    company = await get_company(cid)
    if not company or company['owner_id'] != callback.from_user.id:
        await callback.answer("⚠️ ليس لديك صلاحية.", show_alert=True)
        return
    cost = 100_000
    if company['balance'] < cost:
        await callback.answer(f"❌ سيولة غير كافية. تحتاج {cost:,}$", show_alert=True)
        return
    await update_company_balance(cid, -cost)
    new_mv = company['market_value'] + 50_000
    await update_company_market_value(cid, new_mv)
    await callback.message.edit_text(f"⬆️ تم ترقية الشركة! القيمة السوقية الجديدة: <b>{new_mv:,}$</b>.")
    await callback.answer("تمت الترقية!")

# ==================== نظام الاستثمار عبر الخاص ====================
@dp.message(F.text.regexp(r"^استثمار\s+C\d+\s+\d+$"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def investment_command(message: Message) -> None:
    """معالجة صيغة: استثمار C105 8000"""
    parts = message.text.split()
    if len(parts) != 3:
        await message.reply("⚠️ الصيغة غير صحيحة. استخدم: <code>استثمار [ID الشركة] [المبلغ]</code>")
        return
    company_id = parts[1].upper()
    try:
        amount = int(parts[2])
    except ValueError:
        await message.reply("⚠️ المبلغ يجب أن يكون رقماً صحيحاً.")
        return
    if amount <= 0:
        await message.reply("⚠️ المبلغ يجب أن يكون أكبر من صفر.")
        return

    company = await get_company(company_id)
    if not company:
        await message.reply("❌ الشركة المستهدفة غير موجودة.")
        return
    investor = await get_or_create_user(message.from_user)
    if investor['balance'] < amount:
        await message.reply("❌ رصيدك غير كافٍ لهذا الاستثمار.")
        return

    # خصم المبلغ مؤقتاً (سيتم إعادته إذا رُفض)
    await update_user_balance(message.from_user.id, -amount)
    inv_id = await create_investment_request(message.from_user.id, company_id, amount)

    # إرسال إشعار لصاحب الشركة في الخاص
    try:
        owner = await get_user(company['owner_id'])
        owner_user = await bot.get_users(company['owner_id']) if owner else None
        text = (
            f"📩 <b>طلب استثمار جديد</b>\n\n"
            f"👤 المستثمر: {html.quote(message.from_user.full_name)}\n"
            f"🆔 معرف الشركة: <code>{company_id}</code>\n"
            f"💰 المبلغ: <b>{amount:,}$</b>\n\n"
            f"هل توافق على هذا الاستثمار؟"
        )
        await bot.send_message(
            company['owner_id'],
            text,
            reply_markup=get_investment_keyboard(inv_id)
        )
        await message.reply("✅ تم إرسال طلب الاستثمار إلى صاحب الشركة. سيتم إعلامك بالنتيجة.")
    except TelegramAPIError:
        await message.reply("⚠️ تعذر إرسال الإشعار لصاحب الشركة (قد يكون الخاص مغلقاً). تم حفظ الطلب.")
    except Exception as e:
        logger.error(f"خطأ في إرسال طلب الاستثمار: {e}")
        await message.reply("⚠️ حدث خطأ غير متوقع.")

@dp.callback_query(F.data.startswith("inv_accept_"))
async def accept_investment(callback: CallbackQuery) -> None:
    inv_id = int(callback.data.split("_")[2])
    inv = await get_investment(inv_id)
    if not inv or inv['status'] != 'pending':
        await callback.answer("⚠️ هذا الطلب لم يعد صالحاً.", show_alert=True)
        return
    company = await get_company(inv['company_id'])
    if not company or company['owner_id'] != callback.from_user.id:
        await callback.answer("⚠️ ليس لديك صلاحية.", show_alert=True)
        return

    await update_investment_status(inv_id, 'accepted')
    await update_company_balance(inv['company_id'], inv['amount'])
    await update_company_market_value(inv['company_id'], company['market_value'] + inv['amount'] // 2)

    await callback.message.edit_text(f"✅ تم قبول الاستثمار بقيمة <b>{inv['amount']:,}$</b> وإضافته لسيولة الشركة.")
    try:
        await bot.send_message(inv['investor_id'], f"🎉 تم قبول استثمارك في شركة <code>{inv['company_id']}</code> بقيمة {inv['amount']:,}$.")
    except TelegramAPIError:
        pass
    await callback.answer("تم قبول الاستثمار!")

@dp.callback_query(F.data.startswith("inv_reject_"))
async def reject_investment(callback: CallbackQuery) -> None:
    inv_id = int(callback.data.split("_")[2])
    inv = await get_investment(inv_id)
    if not inv or inv['status'] != 'pending':
        await callback.answer("⚠️ هذا الطلب لم يعد صالحاً.", show_alert=True)
        return
    company = await get_company(inv['company_id'])
    if not company or company['owner_id'] != callback.from_user.id:
        await callback.answer("⚠️ ليس لديك صلاحية.", show_alert=True)
        return

    await update_investment_status(inv_id, 'rejected')
    await update_user_balance(inv['investor_id'], inv['amount'])  # إعادة المبلغ

    await callback.message.edit_text(f"❌ تم رفض الاستثمار. تم إعادة المبلغ للمستثمر.")
    try:
        await bot.send_message(inv['investor_id'], f"❌ تم رفض استثمارك في شركة <code>{inv['company_id']}</code>. تم إعادة {inv['amount']:,}$ إلى رصيدك.")
    except TelegramAPIError:
        pass
    await callback.answer("تم رفض الاستثمار.")

# ==================== نظام التصنيف (التوب) ====================
@dp.message(F.text.lower().in_({"التوب", "ترتيب الشركات"}))
async def show_top(message: Message) -> None:
    """عرض أفضل 20 شركة حسب القيمة السوقية."""
    top = await get_top_companies(20)
    if not top:
        await message.answer("📊 لا توجد شركات مسجلة حتى الآن.")
        return

    text_lines = ["🏆 <b>أفضل 20 شركة (حسب القيمة السوقية)</b>\n"]
    for i, comp in enumerate(top, 1):
        text_lines.append(
            f"{i}. <b>{html.quote(comp['name'])}</b> (<code>{comp['company_id']}</code>) - "
            f"{comp['market_value']:,}$ | 👤 {html.quote(comp['owner_name'])}"
        )

    user_company = await get_company_by_owner(message.from_user.id)
    if user_company:
        rank = await get_user_company_rank(message.from_user.id)
        if rank and rank > 20:
            text_lines.append(
                f"\n\n📌 <b>مركز شركتك:</b> #{rank} - <b>{html.quote(user_company['name'])}</b> "
                f"بقيمة <b>{user_company['market_value']:,}$</b>"
            )
        elif rank:
            text_lines.append(f"\n\n✅ شركتك ضمن أفضل 20 (المركز #{rank}).")
    else:
        text_lines.append("\n\n💡 ليس لديك شركة بعد. اكتب <b>انشاء شركة</b> للانضمام.")

    await message.answer("\n".join(text_lines))

# ==================== معالجات الأزرار العامة ====================
@dp.callback_query(F.data == "rules")
async def show_rules(callback: CallbackQuery) -> None:
    rules_text = (
        "📜 <b>قوانين لعبة حرب الشركات</b>\n\n"
        "1️⃣ يبدأ كل لاعب برصيد <b>100,000$</b>.\n"
        "2️⃣ تأسيس شركة يكلف <b>50,000$</b> ويتم خصمها فوراً.\n"
        "3️⃣ يمكنك توظيف موظفين لزيادة أرباح شركتك.\n"
        "4️⃣ يمكنك استثمار أموالك في شركات أخرى عبر صيغة: <code>استثمار [ID] [المبلغ]</code>.\n"
        "5️⃣ التصنيف العالمي يعتمد على القيمة السوقية للشركة.\n"
        "6️⃣ اللعب متاح داخل المجموعات فقط.\n\n"
        "🎮 حظاً موفقاً في بناء إمبراطوريتك!"
    )
    await callback.message.edit_text(rules_text, reply_markup=get_rules_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "back_to_start")
async def back_to_start(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "👋 أهلاً بك مجدداً! اختر من الأزرار أدناه:",
        reply_markup=get_start_keyboard()
    )
    await callback.answer()

# ==================== نقطة التشغيل الرئيسية ====================
async def main() -> None:
    await init_db()
    logger.info("بدء تشغيل البوت...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
