import os
import re
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import asyncpg
from asyncpg import create_pool

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
CHANNEL_LINK = "https://t.me/+QIEAfs-6HnI0NmMy"

# ==================== DATABASE POOL ====================
db_pool = None

async def init_db_pool():
    global db_pool
    db_pool = await create_pool(DATABASE_URL, min_size=5, max_size=10)

    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                registered TIMESTAMP DEFAULT NOW(),
                positive INT DEFAULT 0,
                negative INT DEFAULT 0,
                total_deals INT DEFAULT 0,
                deal_sum BIGINT DEFAULT 0,
                bio TEXT DEFAULT ''
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reputation_log (
                id SERIAL PRIMARY KEY,
                from_user BIGINT,
                to_user BIGINT,
                type TEXT CHECK (type IN ('+', '-')),
                message_text TEXT,
                photo_id TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        await conn.execute("UPDATE users SET positive = 0 WHERE positive < 0")
        await conn.execute("UPDATE users SET negative = 0 WHERE negative < 0")
        
        await conn.execute("DELETE FROM users WHERE user_id > 9000000000 OR (user_id < 1000000000 AND user_id > 0)")
        await conn.execute("DELETE FROM reputation_log WHERE from_user > 9000000000 OR (from_user < 1000000000 AND from_user > 0)")
        await conn.execute("DELETE FROM reputation_log WHERE to_user > 9000000000 OR (to_user < 1000000000 AND to_user > 0)")

    print("✅ БД готова, отрицательные счетчики сброшены")

async def get_user(user_id):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

async def get_user_by_username(username):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE username ILIKE $1", username)

async def create_user(user_id, username):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, username, registered)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO UPDATE SET username = $2
        """, user_id, username, datetime.now())

async def update_reputation(to_user, from_user, rep_type, message_text, photo_id):
    async with db_pool.acquire() as conn:
        if rep_type == '+':
            await conn.execute("UPDATE users SET positive = positive + 1 WHERE user_id = $1", to_user)
        else:
            await conn.execute("UPDATE users SET negative = negative + 1 WHERE user_id = $1", to_user)
        await conn.execute("""
            INSERT INTO reputation_log (from_user, to_user, type, message_text, photo_id)
            VALUES ($1, $2, $3, $4, $5)
        """, from_user, to_user, rep_type, message_text, photo_id)

async def delete_review_by_id(review_id):
    async with db_pool.acquire() as conn:
        review = await conn.fetchrow("SELECT * FROM reputation_log WHERE id = $1", review_id)
        if review:
            if review['type'] == '+':
                await conn.execute("UPDATE users SET positive = GREATEST(positive - 1, 0) WHERE user_id = $1", review['to_user'])
            else:
                await conn.execute("UPDATE users SET negative = GREATEST(negative - 1, 0) WHERE user_id = $1", review['to_user'])
            await conn.execute("DELETE FROM reputation_log WHERE id = $1", review_id)
            return True
        return False

# ==================== KEYBOARDS ====================
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("Найти пользователя", callback_data="find_user")],
        [InlineKeyboardButton("Отправить репутацию", callback_data="send_rep")],
        [InlineKeyboardButton("Мой профиль", callback_data="my_profile")],
        [InlineKeyboardButton("Перейти в TESS", url=CHANNEL_LINK)]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="back_to_main")]])

def get_profile_reviews_button(user_id):
    keyboard = [
        [InlineKeyboardButton("Отзывы", callback_data=f"profile_reviews_{user_id}")],
        [InlineKeyboardButton("Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_review_menu_keyboard(user_id):
    keyboard = [
        [InlineKeyboardButton("Положительные", callback_data=f"reviews_pos_{user_id}")],
        [InlineKeyboardButton("Отрицательные", callback_data=f"reviews_neg_{user_id}")],
        [InlineKeyboardButton("Все", callback_data=f"reviews_all_{user_id}")],
        [InlineKeyboardButton("Назад", callback_data=f"back_to_profile_{user_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_review_numbers_keyboard(reviews, user_id, review_type, current_index=0):
    keyboard = []
    row = []
    
    for i, review in enumerate(reviews, 1):
        btn = InlineKeyboardButton(str(i), callback_data=f"review_{review['id']}_{user_id}_{review_type}_{i-1}")
        row.append(btn)
        
        if len(row) == 5:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("Назад", callback_data=f"back_to_review_menu_{user_id}")])
    return InlineKeyboardMarkup(keyboard)

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        return

    user_id = update.effective_user.id
    username = update.effective_user.username or "no_username"

    await create_user(user_id, username)
    
    args = context.args
    if args and args[0].startswith("reviews_"):
        target_user_id = int(args[0].replace("reviews_", ""))
        context.user_data["review_user_id"] = target_user_id
        text = "<b>🔎 Выберите раздел:</b>"
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=get_review_menu_keyboard(target_user_id))
        return

    text = "<b>TESS - твоя гарантия безопасности!</b>\n\nЗдесь ты можешь делиться репутацией и в будущем проводить сделки."
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=get_main_menu())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.message.chat.type != "private":
        return

    user_id = query.from_user.id
    username = query.from_user.username or "no_username"
    await create_user(user_id, username)

    if query.data == "back_to_main":
        text = "<b>TESS - твоя гарантия безопасности!</b>\n\nЗдесь ты можешь делиться репутацией и в будущем проводить сделки."
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=get_main_menu())
        return

    if query.data.startswith("back_to_profile_"):
        target_user_id = int(query.data.split("_")[3])
        user = await get_user(target_user_id)
        
        total = user["positive"] + user["negative"]
        if total > 0:
            positive_percent = (user["positive"] / total * 100)
            negative_percent = (user["negative"] / total * 100)
        else:
            positive_percent = 0.0
            negative_percent = 0.0

        reg_date = user["registered"].strftime("%d %B %Y года.")

        text = (
            f"👤 @{user['username']} (ID: {target_user_id})\n\n"
            f"<blockquote>🏆 {user['positive']} шт. · {positive_percent:.1f}% положительных · {negative_percent:.1f}% отрицательных\n"
            f"🛡 {user['total_deals']} шт. • {user['deal_sum']} RUB сумма сделок</blockquote>\n\n"
            f"ВНИМАТЕЛЬНО СМОТРИТЕ ПОЛЕ «О СЕБЕ» ‼️\n\n"
            f"💳 Депозит: отсутствует\n\n"
            f"📆 Зарегистрирован {reg_date}"
        )
        
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=get_profile_reviews_button(target_user_id))
        return

    if query.data.startswith("back_to_review_menu_"):
        target_user_id = int(query.data.split("_")[4])
        text = "<b>🔎 Выберите раздел:</b>"
        if query.message.photo:
            await query.message.delete()
            await query.message.reply_text(text, parse_mode="HTML", reply_markup=get_review_menu_keyboard(target_user_id))
        else:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=get_review_menu_keyboard(target_user_id))
        return

    if query.data.startswith("profile_reviews_"):
        target_user_id = int(query.data.split("_")[2])
        text = "<b>🔎 Выберите раздел:</b>"
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=get_review_menu_keyboard(target_user_id))
        return

    if query.data.startswith("reviews_"):
        parts = query.data.split("_")
        review_type = parts[1]
        target_user_id = int(parts[2])
        
        async with db_pool.acquire() as conn:
            if review_type == "pos":
                reviews = await conn.fetch("SELECT * FROM reputation_log WHERE to_user = $1 AND type = '+' ORDER BY created_at DESC LIMIT 20", target_user_id)
            elif review_type == "neg":
                reviews = await conn.fetch("SELECT * FROM reputation_log WHERE to_user = $1 AND type = '-' ORDER BY created_at DESC LIMIT 20", target_user_id)
            else:
                reviews = await conn.fetch("SELECT * FROM reputation_log WHERE to_user = $1 ORDER BY created_at DESC LIMIT 20", target_user_id)
        
        if not reviews:
            await query.edit_message_text("<b>Отзывов нет</b>", parse_mode="HTML", reply_markup=get_review_menu_keyboard(target_user_id))
            return
        
        context.user_data[f"reviews_{target_user_id}"] = reviews
        context.user_data[f"review_type_{target_user_id}"] = review_type
        
        first_review = reviews[0]
        from_user = await get_user(first_review['from_user'])
        date = first_review['created_at'].strftime("%d.%m.%Y %H:%M")
        rep_text = "положительный" if first_review['type'] == '+' else "отрицательный"
        
        caption = (
            f"ID: {first_review['id']}\n"
            f"От: @{from_user['username'] if from_user else 'Скрытый профиль'}\n"
            f"Тип: {rep_text}\n"
            f"Дата: {date}\n"
            f"Текст: {first_review['message_text'] if first_review['message_text'] else 'Нет текста'}"
        )
        
        if first_review['photo_id']:
            await query.message.delete()
            msg = await query.message.reply_photo(
                photo=first_review['photo_id'],
                caption=caption,
                parse_mode="HTML",
                reply_markup=get_review_numbers_keyboard(reviews, target_user_id, review_type, 0)
            )
            context.user_data[f"review_msg_{target_user_id}"] = msg.message_id
        else:
            await query.edit_message_text(
                caption,
                parse_mode="HTML",
                reply_markup=get_review_numbers_keyboard(reviews, target_user_id, review_type, 0)
            )
        return
    
    if query.data.startswith("review_"):
        parts = query.data.split("_")
        review_id = int(parts[1])
        target_user_id = int(parts[2])
        review_type = parts[3]
        review_index = int(parts[4])
        
        async with db_pool.acquire() as conn:
            review = await conn.fetchrow("SELECT * FROM reputation_log WHERE id = $1", review_id)
        
        if not review:
            await query.edit_message_text("<b>Отзыв не найден</b>", parse_mode="HTML")
            return
        
        reviews = context.user_data.get(f"reviews_{target_user_id}")
        if not reviews:
            async with db_pool.acquire() as conn:
                if review_type == "pos":
                    reviews = await conn.fetch("SELECT * FROM reputation_log WHERE to_user = $1 AND type = '+' ORDER BY created_at DESC LIMIT 20", target_user_id)
                elif review_type == "neg":
                    reviews = await conn.fetch("SELECT * FROM reputation_log WHERE to_user = $1 AND type = '-' ORDER BY created_at DESC LIMIT 20", target_user_id)
                else:
                    reviews = await conn.fetch("SELECT * FROM reputation_log WHERE to_user = $1 ORDER BY created_at DESC LIMIT 20", target_user_id)
            context.user_data[f"reviews_{target_user_id}"] = reviews
        
        from_user = await get_user(review['from_user'])
        date = review['created_at'].strftime("%d.%m.%Y %H:%M")
        rep_text = "положительный" if review['type'] == '+' else "отрицательный"
        
        caption = (
            f"ID: {review['id']}\n"
            f"От: @{from_user['username'] if from_user else 'Скрытый профиль'}\n"
            f"Тип: {rep_text}\n"
            f"Дата: {date}\n"
            f"Текст: {review['message_text'] if review['message_text'] else 'Нет текста'}"
        )
        
        if review['photo_id']:
            if query.message.photo:
                await query.message.delete()
                msg = await query.message.reply_photo(
                    photo=review['photo_id'],
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=get_review_numbers_keyboard(reviews, target_user_id, review_type, review_index)
                )
                context.user_data[f"review_msg_{target_user_id}"] = msg.message_id
            else:
                await query.message.delete()
                msg = await query.message.reply_photo(
                    photo=review['photo_id'],
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=get_review_numbers_keyboard(reviews, target_user_id, review_type, review_index)
                )
                context.user_data[f"review_msg_{target_user_id}"] = msg.message_id
        else:
            await query.edit_message_text(
                caption,
                parse_mode="HTML",
                reply_markup=get_review_numbers_keyboard(reviews, target_user_id, review_type, review_index)
            )
        return

    if query.data == "find_user":
        context.user_data["state"] = "awaiting_find_username"
        await query.edit_message_text("<b>🔎 Введите username или ID пользователя, которого хотите найти</b>", parse_mode="HTML", reply_markup=get_back_button())

    elif query.data == "send_rep":
        context.user_data["state"] = "awaiting_send_rep_username"
        await query.edit_message_text("<b>🔎 Введите username или ID пользователя для того, чтобы отправить ему репутацию</b>", parse_mode="HTML", reply_markup=get_back_button())

    elif query.data == "my_profile":
        user_id = query.from_user.id
        user = await get_user(user_id)

        if not user:
            await query.edit_message_text("<b>🚫 Ошибка: профиль не найден</b>", parse_mode="HTML", reply_markup=get_back_button())
            return

        total = user["positive"] + user["negative"]
        if total > 0:
            positive_percent = (user["positive"] / total * 100)
            negative_percent = (user["negative"] / total * 100)
        else:
            positive_percent = 0.0
            negative_percent = 0.0

        reg_date = user["registered"].strftime("%d %B %Y года.")

        text = (
            f"👤 @{user['username']} (ID: {user_id})\n\n"
            f"<blockquote>🏆 {user['positive']} шт. · {positive_percent:.1f}% положительных · {negative_percent:.1f}% отрицательных\n"
            f"🛡 {user['total_deals']} шт. • {user['deal_sum']} RUB сумма сделок</blockquote>\n\n"
            f"ВНИМАТЕЛЬНО СМОТРИТЕ ПОЛЕ «О СЕБЕ» ‼️\n\n"
            f"💳 Депозит: отсутствует\n\n"
            f"📆 Зарегистрирован {reg_date}"
        )

        await query.edit_message_text(text, parse_mode="HTML", reply_markup=get_profile_reviews_button(user_id))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "no_username"
    state = context.user_data.get("state")
    chat_type = update.message.chat.type

    await create_user(user_id, username)

    text = ""
    if update.message.text:
        text = update.message.text.strip()
    if update.message.caption:
        text = update.message.caption.strip()

    # ===== ЭМУЛЯЦИЯ /и (ТОЛЬКО ГРУППЫ) =====
    if chat_type != "private" and text and (text.strip() == "/и" or (text.startswith("/и ") and len(text.split()) >= 2)):
        parts = text.split()

        if update.message.reply_to_message:
            target_user = update.message.reply_to_message.from_user
            target_id = target_user.id
            target_username = target_user.username or f"id{target_id}"
            await create_user(target_id, target_username)
            user_data = await get_user(target_id)

        elif len(parts) > 1:
            target = parts[1].lower().replace("@", "")

            async with db_pool.acquire() as conn:
                if target.isdigit():
                    user_data = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", int(target))
                else:
                    user_data = await conn.fetchrow("SELECT * FROM users WHERE username ILIKE $1", target)

            if not user_data:
                await update.message.reply_text("<b>🚫 Пользователь не найден</b>", parse_mode="HTML")
                return

        elif text.strip() == "/и":
            user_data = await get_user(user_id)
        else:
            return

        if user_data:
            total = user_data["positive"] + user_data["negative"]
            if total > 0:
                positive_percent = (user_data["positive"] / total * 100)
                negative_percent = (user_data["negative"] / total * 100)
            else:
                positive_percent = 0.0
                negative_percent = 0.0

            reg_date = user_data["registered"].strftime("%d %B %Y года.")
            
            bot_username = (await context.bot.get_me()).username
            profile_link = f"https://t.me/{bot_username}?start=reviews_{user_data['user_id']}"

            profile_text = (
                f"👤 @{user_data['username']} (ID: {user_data['user_id']})\n\n"
                f"<blockquote><a href='{profile_link}'>🏆 {user_data['positive']} шт. · {positive_percent:.1f}% положительных · {negative_percent:.1f}% отрицательных</a>\n"
                f"🛡 {user_data['total_deals']} шт. • {user_data['deal_sum']} RUB сумма сделок</blockquote>\n\n"
                f"ВНИМАТЕЛЬНО СМОТРИТЕ ПОЛЕ «О СЕБЕ» ‼️\n\n"
                f"💳 Депозит: отсутствует\n\n"
                f"📆 Зарегистрирован {reg_date}"
            )

            keyboard = [[InlineKeyboardButton("Приобрести префикс", url="https://t.me/prade147")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(profile_text, parse_mode="HTML", reply_markup=reply_markup)

        return

    # ===== ПАРСИНГ РЕПУТАЦИИ С ЗАЩИТОЙ ОТ РЕКЛАМЫ =====
    if text:
        mention_pattern = r'@(\w+)|(\b\d{5,}\b)'
        rep_pattern = r'[\+\-]\s*[РрRr][ЕеEe][ПпPp]\b'
        
        mentions = re.findall(mention_pattern, text)
        has_rep = re.search(rep_pattern, text)
        
        # ===== АНТИСПАМ: проверка на рекламу =====
        ad_keywords = ['купить', 'продаю', 'цены', 'оплата', 'баланс', 'карты', 'услуги', 'скам', 'принимаю', 'пушкинские']
        self_promo = ['у меня', 'моя', 'мои', 'моё', 'на мне', 'с меня']
        
        is_ad = False
        lower_text = text.lower()
        
        for keyword in ad_keywords:
            if keyword in lower_text:
                is_ad = True
                break
                
        for promo in self_promo:
            if promo in lower_text:
                is_ad = True
                break
        
        # Проверка на цифры перед +реп (500+реп)
        if re.search(r'\d+\s*[\+\-]\s*[рp][еe][пp]', lower_text):
            is_ad = True

        if mentions and has_rep and state != "awaiting_rep_text" and not is_ad:
            if not update.message.photo:
                await update.message.reply_text(
                    "<b>🚫 Прикрепите фото</b>",
                    parse_mode="HTML"
                )
                return

            rep_type = '+' if '+' in has_rep.group() else '-'
            photo_id = update.message.photo[-1].file_id if update.message.photo else None

            # Определяем настоящего отправителя
            if update.message.forward_from:
                from_user_id = update.message.forward_from.id
                from_username = update.message.forward_from.username or "Скрытый профиль"
                await create_user(from_user_id, from_username)
            else:
                from_user_id = 0
                from_username = "Скрытый профиль"

            for mention in mentions:
                target_username = mention[0] or mention[1]

                async with db_pool.acquire() as conn:
                    if str(target_username).isdigit():
                        target_user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", int(target_username))
                    else:
                        target_user = await conn.fetchrow("SELECT * FROM users WHERE username ILIKE $1", target_username)

                if not target_user:
                    await update.message.reply_text("<b>🚫 Пользователь не найден</b>", parse_mode="HTML")
                    continue

                await create_user(target_user['user_id'], target_user['username'])

                if target_user['user_id'] != from_user_id:
                    await update_reputation(target_user['user_id'], from_user_id, rep_type, text, photo_id)
                    await update.message.reply_text(
                        "<b>✅ Репутация сохранена</b>",
                        parse_mode="HTML"
                    )
                else:
                    await update.message.reply_text(
                        "<b>🚫 Нельзя отправить репутацию самому себе</b>",
                        parse_mode="HTML"
                    )

async def post_init(app):
    await init_db_pool()
    print("✅ Бот запущен, пул соединений готов")

def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()
