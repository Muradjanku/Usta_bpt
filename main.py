import os
import re
import logging
import sqlite3
import requests
from contextlib import asynccontextmanager
from http import HTTPStatus
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from bs4 import BeautifulSoup
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Logging sozlamalari
logging.basicConfig(
    filename='bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# .env faylidan token va boshqa sozlamalarni olish
load_dotenv()
TELEGRAM_BOT_TOKEN: str = os.getenv('BOT_TOKEN')
WEBHOOK_DOMAIN: str = os.getenv('RAILWAY_PUBLIC_DOMAIN')
PORT: int = int(os.getenv('PORT', 8443))

# Kirish ma'lumotlarini tozalash
def sanitize_input(text: str) -> str:
    return re.sub(r'[^\w\s]', '', text) if text else ''

# Aros.uz saytidan mahsulot katalogini scrap qilish
def scrape_aros_catalog():
    try:
        url = "https://aros.uz/uz"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        products = []
        # Aros.uz saytidagi haqiqiy CSS selektorlarni ishlatish kerak
        # Masalan: product_elements = soup.select('.product-card') yoki '.item')
        product_elements = soup.select('.product-item')  # Placeholder, sayt strukturasiga moslashtiring
        for item in product_elements[:5]:  # Faqat birinchi 5 ta mahsulot
            name = item.select_one('.product-name').text.strip() if item.select_one('.product-name') else 'Noma\'lum'
            price = item.select_one('.product-price').text.strip() if item.select_one('.product-price') else 'Noma\'lum'
            link = item.select_one('a')['href'] if item.select_one('a') else 'https://aros.uz/uz'
            # Kategoriyani umumiy sifatida belgilash (haqiqiy kategoriyalar saytdan olinishi kerak)
            category = 'Aksessuarlar' if 'accessory' in name.lower() else 'Ehtiyot qismlar'
            products.append({'name': name, 'price': price, 'link': link, 'category': category})

        # Ma'lumotlar bazasiga saqlash
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS aros_products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    price TEXT,
                    link TEXT,
                    category TEXT
                )
            ''')
            cursor.executemany('INSERT OR IGNORE INTO aros_products (name, price, link, category) VALUES (?, ?, ?, ?)',
                              [(p['name'], p['price'], p['link'], p['category']) for p in products])
            conn.commit()
        return products
    except Exception as e:
        logging.error(f"Aros.uz scraping xatosi: {e}")
        return []

# Inline tugmalar uchun aksessuarlar kategoriyalari
def get_accessories_inline():
    categories = ["Aksessuarlar", "Ehtiyot qismlar"]
    keyboard = [[InlineKeyboardButton(cat, callback_data=f"aros_{cat.lower()}")] for cat in categories]
    return InlineKeyboardMarkup(keyboard)

# Tugmalar ro'yxati
reply_keyboard = [
    ["📋 Aros.uz katalogi", "🛒 Sotib olish"],
    ["📞 Aloqa", "❓ Yordam"]
]

# ReplyKeyboardMarkup
markup = ReplyKeyboardMarkup(
    reply_keyboard,
    resize_keyboard=True,
    one_time_keyboard=False,
    input_field_placeholder="Quyidagi tugmalardan birini tanlang"
)

# Build the Telegram Bot application
bot_builder = (
    Application.builder()
    .token(TELEGRAM_BOT_TOKEN)
    .updater(None)
    .build()
)

@asynccontextmanager
async def lifespan(_: FastAPI):
    """ Sets the webhook for the Telegram Bot and manages its lifecycle (start/stop). """
    if not TELEGRAM_BOT_TOKEN or not WEBHOOK_DOMAIN:
        logging.error("BOT_TOKEN yoki RAILWAY_PUBLIC_DOMAIN topilmadi!")
        raise ValueError("BOT_TOKEN yoki RAILWAY_PUBLIC_DOMAIN muhit o'zgaruvchilari o'rnatilmagan!")
    
    webhook_url = f"https://{WEBHOOK_DOMAIN}/{TELEGRAM_BOT_TOKEN}"
    await bot_builder.bot.setWebhook(url=webhook_url)
    async with bot_builder:
        await bot_builder.start()
        yield
        await bot_builder.stop()

app = FastAPI(lifespan=lifespan)

@app.post("/")
async def process_update(request: Request):
    """ Handles incoming Telegram updates and processes them with the bot. """
    try:
        message = await request.json()
        update = Update.de_json(data=message, bot=bot_builder.bot)
        if update is None:
            logging.error("Update deserializatsiyasi muvaffaqiyatsiz bo'ldi")
            return Response(status_code=HTTPStatus.BAD_REQUEST)
        await bot_builder.process_update(update)
        return Response(status_code=HTTPStatus.OK)
    except Exception as e:
        logging.error(f"Updateni qayta ishlashda xato: {e}")
        return Response(status_code=HTTPStatus.INTERNAL_SERVER_ERROR)

async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """ Handles the /start command by sending a welcome message. """
    await update.message.reply_text(
        "Aros.uz aksessuarlar va ehtiyot qismlar botiga xush kelibsiz! Quyidagi tugmalardan foydalaning:",
        reply_markup=markup
    )

async def catalog(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """ Handles the /catalog command by showing Aros.uz products. """
    products = scrape_aros_catalog()
    if products:
        response = "Aros.uz katalogi (so'nggi yangilanish):\n"
        for product in products:
            response += f"- {product['name']}: {product['price']}\nHavola: {product['link']}\n"
    else:
        response = "Aros.uz katalogini yuklashda xato yuz berdi. Keyinroq qayta urinib ko'ring."
    await update.message.reply_text(response)

async def handle_message(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """ Handles text messages and responds based on button selections. """
    try:
        text = sanitize_input(update.message.text)
        valid_commands = [item for sublist in reply_keyboard for item in sublist]
        if text not in valid_commands:
            await update.message.reply_text("Noto‘g‘ri buyruq. Iltimos, menyudan tanlang.")
            return

        if text == "📋 Aros.uz katalogi":
            await update.message.reply_text(
                "Aros.uz katalogini ko'rish uchun kategoriyani tanlang:",
                reply_markup=get_accessories_inline()
            )
        elif text == "🛒 Sotib olish":
            await update.message.reply_text(
                "Sotib olish uchun:\n"
                "- Aros.uz (aksessuarlar): https://aros.uz/uz\n"
                "Katalogni ko'rish: /catalog"
            )
        elif text == "📞 Aloqa":
            await update.message.reply_text(
                "Aloqa uchun:\n"
                "- Telegram: @AdminSupport\n"
                "- Telefon: +998901234567\n"
                "- Email: support@arosbot.uz"
            )
        elif text == "❓ Yordam":
            await update.message.reply_text(
                "Bu bot Aros.uz aksessuarlar va ehtiyot qismlari haqida ma'lumot beradi:\n"
                "- Katalogni ko'rish: /catalog\n"
                "- Sotib olish: /buy\n"
                "Savollar uchun /contact"
            )
    except Exception as e:
        logging.error(f"Xabar qayta ishlashda xato: {e}")
        await update.message.reply_text("Xato yuz berdi. Iltimos, qayta urinib ko‘ring.")

async def buy(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """ Handles the /buy command. """
    await update.message.reply_text(
        "Aros.uz'dan sotib olish uchun:\n"
        "- Sayt: https://aros.uz/uz\n"
        "Mahsulot tanlash uchun: /catalog"
    )

async def contact(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """ Handles the /contact command. """
    await update.message.reply_text(
        "Aloqa uchun:\n"
        "- Telegram: @AdminSupport\n"
        "- Telefon: +998901234567\n"
        "- Email: support@arosbot.uz"
    )

async def button_callback(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """ Handles inline button callbacks. """
    query = update.callback_query
    await query.answer()
    data = sanitize_input(query.data)

    if data.startswith("aros_"):
        category = data.replace("aros_", "").capitalize()
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT name, price, link FROM aros_products WHERE category = ?', (category,))
            products = cursor.fetchall()
        if products:
            response = f"Aros.uz {category} katalogi:\n"
            for name, price, link in products:
                response += f"- {name}: {price}\nHavola: {link}\n"
        else:
            response = f"{category} uchun mahsulotlar topilmadi. /catalog bilan qayta urinib ko'ring."
        await query.message.reply_text(response)

# Bot handlerlarni qo'shish
bot_builder.add_handler(CommandHandler("start", start))
bot_builder.add_handler(CommandHandler("catalog", catalog))
bot_builder.add_handler(CommandHandler("buy", buy))
bot_builder.add_handler(CommandHandler("contact", contact))
bot_builder.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
bot_builder.add_handler(CallbackQueryHandler(button_callback))
