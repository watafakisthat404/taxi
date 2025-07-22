import logging
import json
import os
import uuid
from datetime import datetime, timedelta
import asyncio
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.helpers import escape_markdown # MarkdownV2 uchun

# Logging sozlamalari
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Konfiguratsiya ---
TELEGRAM_BOT_TOKEN = "7802328492:AAFhRX5BvY_YDWep11aKLl2FbdmKzjus3cQ" # <- O'zingizning bot tokeningizni kiriting
ADMIN_TELEGRAM_IDS = ["1382414440", "6400925437"] # <- O'zingizning admin Telegram ID'laringizni kiriting (string formatida)

DB_FILE = 'databasee.json' # Ma'lumotlar saqlanadigan fayl nomi
DB_LOCK = asyncio.Lock() # Faylga yozish/o'qish uchun blokirovka

# --- Ma'lumotlar bazasi (JSON fayl) funksiyalari ---
async def _load_db_data():
    """Ma'lumotlarni JSON fayldan o'qiydi."""
    async with DB_LOCK:
        if not os.path.exists(DB_FILE):
            # Agar fayl mavjud bo'lmasa, bo'sh struktura bilan yaratish
            initial_data = {
                "regions": [],
                "districts": [],
                "routes": [],
                "orders": [],
                "driver_profiles": {}, # {driver_id: {balance: X, subscriptionEndDate: Y}}
                "drivers": [] # Faqat shu ro'yxatdagi ID'lar haydovchi bo'la oladi
            }
            with open(DB_FILE, 'w', encoding='utf-8') as f:
                json.dump(initial_data, f, ensure_ascii=False, indent=4)
            return initial_data

        with open(DB_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Eski fayllar uchun yangi kalitlarni qo'shish (migratsiya)
            if "drivers" not in data:
                data["drivers"] = list(data["driver_profiles"].keys()) # Mavjud profillar haydovchi deb hisoblansin
            # Eski marshrutlardagi nomlarni ID'larga o'zgartirish (agar kerak bo'lsa)
            for route in data.get("routes", []):
                if "fromRegion" in route and "fromRegionId" not in route:
                    # Bu yerda nomdan ID topish logikasi kerak bo'ladi
                    # Hozircha oddiyroq qilib, agar ID bo'lmasa, uni None qilib qo'yamiz
                    # Yoki barcha regions/districts ga ID qo'shib, keyin shu yerda bog'lash mumkin
                    pass # Hozircha migratsiya qilinmaydi, yangi marshrutlar ID bilan yaratiladi
            return data
def escape_markdown(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!\\"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)
async def _save_db_data(data):
    """Ma'lumotlarni JSON faylga yozadi."""
    async with DB_LOCK:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

# --- Foydalanuvchi holatlari va ma'lumotlari ---
user_states = {} # {user_id: 'state_name', ...}
user_data = {} # {user_id: {'key': 'value'}, ...}

# Foydalanuvchi holatlari constants
STATE_NONE = 'none'
STATE_AWAITING_FROM_REGION = 'awaiting_from_region'
STATE_AWAITING_FROM_DISTRICT = 'awaiting_from_district'
STATE_AWAITING_TO_REGION = 'awaiting_to_region'
STATE_AWAITING_TO_DISTRICT = 'awaiting_to_district'
STATE_AWAITING_PHONE_NUMBER = 'awaiting_phone_number'
STATE_AWAITING_COMMENT = 'awaiting_comment'

STATE_ADMIN_ADD_REGION = 'admin_add_region'
STATE_ADMIN_DELETE_REGION_SELECT = 'admin_delete_region_select'
STATE_ADMIN_SELECT_REGION_FOR_DISTRICT = 'admin_select_region_for_district'
STATE_ADMIN_ADD_DISTRICT = 'admin_add_district'
STATE_ADMIN_DELETE_DISTRICT_SELECT_REGION = 'admin_delete_district_select_region'
STATE_ADMIN_DELETE_DISTRICT_SELECT_DISTRICT = 'admin_delete_district_select_district'
STATE_ADMIN_ADD_ROUTE_FROM_REGION = 'admin_add_route_from_region'
STATE_ADMIN_ADD_ROUTE_FROM_DISTRICT = 'admin_add_route_from_district'
STATE_ADMIN_ADD_ROUTE_TO_REGION = 'admin_add_route_to_region'
STATE_ADMIN_ADD_ROUTE_TO_DISTRICT = 'admin_add_route_to_district'
STATE_ADMIN_DELETE_ROUTE_SELECT = 'admin_delete_route_select'
STATE_ADMIN_SELECT_ROUTE_FOR_GROUP = 'admin_select_route_for_group'
STATE_ADMIN_ADD_GROUP_ID = 'admin_add_group_id'
STATE_ADMIN_ADD_GROUP_NAME = 'admin_add_group_name'
STATE_ADMIN_ADD_DRIVER_BALANCE_ID = 'admin_add_driver_balance_id'
STATE_ADMIN_ADD_DRIVER_BALANCE_AMOUNT = 'admin_add_driver_balance_amount'
STATE_ADMIN_ADD_DRIVER_SUBSCRIPTION_ID = 'admin_add_driver_subscription_id'
STATE_ADMIN_ADD_DRIVER_SUBSCRIPTION_DAYS = 'admin_add_driver_subscription_days'
STATE_ADMIN_ADD_DRIVER_ID = 'admin_add_driver_id' # Yangi holat
STATE_ADMIN_REMOVE_DRIVER_ID = 'admin_remove_driver_id' # Yangi holat
STATE_ADMIN_SEND_AD = 'admin_send_ad'

# --- Yordamchi funksiyalar ---
async def is_admin(user_id: int) -> bool:
    """Foydalanuvchi admin ekanligini tekshiradi."""
    return str(user_id) in ADMIN_TELEGRAM_IDS

async def is_driver(user_id: int) -> bool:
    """Foydalanuvchi haydovchi ekanligini tekshiradi."""
    data = await _load_db_data()
    return str(user_id) in data.get('drivers', [])

async def get_regions():
    """Ma'lumotlar bazasidan viloyatlar ro'yxatini oladi."""
    data = await _load_db_data()
    return sorted(data['regions'], key=lambda x: x['name'])

async def get_districts_by_region(region_id: str):
    """Ma'lumotlar bazasidan berilgan viloyatga tegishli tumanlar ro'yxatini oladi."""
    data = await _load_db_data()
    return sorted([d for d in data['districts'] if d['regionId'] == region_id], key=lambda x: x['name'])

async def get_routes():
    """Ma'lumotlar bazasidan marshrutlar ro'yxatini oladi."""
    data = await _load_db_data()
    return data['routes']

async def get_driver_profile(user_id: int):
    """Haydovchining profilini oladi yoki yaratadi."""
    data = await _load_db_data()
    driver_id_str = str(user_id)
    if driver_id_str not in data['driver_profiles']:
        data['driver_profiles'][driver_id_str] = {"balance": 0, "subscriptionEndDate": None}
        await _save_db_data(data)
    return data['driver_profiles'][driver_id_str]

async def update_driver_balance(user_id: int, amount: int):
    """Haydovchi balansini yangilaydi."""
    data = await _load_db_data()
    driver_id_str = str(user_id)
    if driver_id_str not in data['driver_profiles']:
        data['driver_profiles'][driver_id_str] = {"balance": 0, "subscriptionEndDate": None}

    data['driver_profiles'][driver_id_str]['balance'] += amount
    await _save_db_data(data)
    return data['driver_profiles'][driver_id_str]['balance']

async def update_driver_subscription(user_id: int, days_to_add: int):
    """Haydovchi obuna muddatini yangilaydi."""
    data = await _load_db_data()
    driver_id_str = str(user_id)
    if driver_id_str not in data['driver_profiles']:
        data['driver_profiles'][driver_id_str] = {"balance": 0, "subscriptionEndDate": None}

    profile = data['driver_profiles'][driver_id_str]
    current_end_date_str = profile.get('subscriptionEndDate')
    current_end_date = datetime.now()
    if current_end_date_str:
        try:
            current_end_date = datetime.fromisoformat(current_end_date_str)
        except ValueError:
            current_end_date = datetime.now() # Format xato bo'lsa, bugundan boshla

    # Agar obuna muddati o'tgan bo'lsa, bugundan boshlab hisobla
    if current_end_date < datetime.now():
        current_end_date = datetime.now()

    new_end_date = current_end_date + timedelta(days=days_to_add)
    profile['subscriptionEndDate'] = new_end_date.isoformat()
    await _save_db_data(data)
    return new_end_date

async def get_suitable_groups_for_order(from_region_id, from_district_id, to_region_id, to_district_id):
    """Buyurtmaga mos keluvchi guruh ID'larini topadi."""
    data = await _load_db_data()
    routes = data['routes']
    matched_groups = set()

    for route in routes:
        # Kengroq qidiruv strategiyasi:
        # 1. Viloyat-tuman -> Viloyat-tuman (aniq moslik)
        if (route.get('fromRegionId') == from_region_id and
            route.get('fromDistrictId') == from_district_id and
            route.get('toRegionId') == to_region_id and
            route.get('toDistrictId') == to_district_id):
            for group in route.get('groupIds', []):
                matched_groups.add((group['id'], group['name']))

        # 2. Viloyat -> Viloyat (tumanlar ahamiyatsiz)
        if (route.get('fromRegionId') == from_region_id and
            route.get('fromDistrictId') is None and
            route.get('toRegionId') == to_region_id and
            route.get('toDistrictId') is None):
            for group in route.get('groupIds', []):
                matched_groups.add((group['id'], group['name']))

        # 3. Viloyat-tuman -> Viloyat (borish tumani ahamiyatsiz)
        if (route.get('fromRegionId') == from_region_id and
            route.get('fromDistrictId') == from_district_id and
            route.get('toRegionId') == to_region_id and
            route.get('toDistrictId') is None):
            for group in route.get('groupIds', []):
                matched_groups.add((group['id'], group['name']))

        # 4. Viloyat -> Viloyat-tuman (jo'nab ketish tumani ahamiyatsiz)
        if (route.get('fromRegionId') == from_region_id and
            route.get('fromDistrictId') is None and
            route.get('toRegionId') == to_region_id and
            route.get('toDistrictId') == to_district_id):
            for group in route.get('groupIds', []):
                matched_groups.add((group['id'], group['name']))

    return list(matched_groups) # (group_id, group_name) tuplelar ro'yxati

# --- Buyruq ishlovchilari ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bot ishga tushirilganda ishlaydigan funksiya."""
    user_id = update.effective_user.id
    user_states[user_id] = STATE_NONE
    user_data[user_id] = {}

    keyboard = [
        [InlineKeyboardButton("Mijoz", callback_data="customer_menu")],
        [InlineKeyboardButton("Haydovchi", callback_data="driver_menu")]
    ]
    if await is_admin(user_id):
        keyboard.append([InlineKeyboardButton("Admin Paneli", callback_data="admin_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    # update.message yoki update.callback_query.message dan foydalanish
    if update.message:
        await update.message.reply_text(
            escape_markdown("Assalomu alaykum! Taksi buyurtma botimizga xush kelibsiz. Iltimos, rolingizni tanlang:"),
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            escape_markdown("Assalomu alaykum! Taksi buyurtma botimizga xush kelibsiz. Iltimos, rolingizni tanlang:"),
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Yordam buyrug'ini boshqaradi."""
    await update.message.reply_text(escape_markdown("Bu bot sizga taksi buyurtma qilish va boshqarishda yordam beradi."), parse_mode='MarkdownV2')

# --- Mijoz funksiyalari ---

async def customer_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mijoz menyusini ko'rsatadi."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_states[user_id] = STATE_NONE # Holatni tiklash
    user_data[user_id] = {} # Oldingi ma'lumotlarni tozalash

    keyboard = [
        [InlineKeyboardButton("Taksi buyurtma berish", callback_data="new_order")],
        [InlineKeyboardButton("Bosh menyuga", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(escape_markdown("Siz mijoz menyusidasiz. Xizmatni tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')

async def new_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Yangi buyurtma jarayonini boshlaydi."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_data[user_id] = {} # Oldingi buyurtma ma'lumotlarini tozalash

    regions = await get_regions()
    if not regions:
        await query.edit_message_text(escape_markdown("Hozircha viloyatlar ro'yxati mavjud emas. Admin bilan bog'laning."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    keyboard = [[InlineKeyboardButton(escape_markdown(region['name']), callback_data=f"from_region_{region['id']}")] for region in regions]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(escape_markdown("Jo'nab ketadigan viloyatni tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    user_states[user_id] = STATE_AWAITING_FROM_REGION

async def select_from_region(update: Update, context: ContextTypes.DEFAULT_TYPE, region_id: str) -> None:
    """Jo'nab ketish viloyatini tanlash."""
    user_id = update.callback_query.from_user.id
    data_db = await _load_db_data()
    region_name = next((r['name'] for r in data_db['regions'] if r['id'] == region_id), "Noma'lum")

    user_data[user_id]['from_region_id'] = region_id
    user_data[user_id]['from_region_name'] = region_name

    districts = await get_districts_by_region(region_id)
    keyboard = []
    if districts:
        keyboard = [[InlineKeyboardButton(escape_markdown(d['name']), callback_data=f"from_district_{d['id']}")] for d in districts]
    keyboard.append([InlineKeyboardButton(escape_markdown("Tuman muhim emas / O'tkazib yuborish"), callback_data="from_district_none")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(escape_markdown(f"Jo'nab ketadigan tumanni tanlang (ixtiyoriy):"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    user_states[user_id] = STATE_AWAITING_FROM_DISTRICT

async def select_from_district(update: Update, context: ContextTypes.DEFAULT_TYPE, district_id: str) -> None:
    """Jo'nab ketish tumanini tanlash."""
    user_id = update.callback_query.from_user.id
    data_db = await _load_db_data()
    district_name = next((d['name'] for d in data_db['districts'] if d['id'] == district_id), None)

    user_data[user_id]['from_district_id'] = district_id if district_id != "none" else None
    user_data[user_id]['from_district_name'] = district_name if district_id != "none" else None

    regions = await get_regions()
    keyboard = [[InlineKeyboardButton(escape_markdown(region['name']), callback_data=f"to_region_{region['id']}")] for region in regions]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(escape_markdown("Boradigan viloyatni tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    user_states[user_id] = STATE_AWAITING_TO_REGION

async def select_to_region(update: Update, context: ContextTypes.DEFAULT_TYPE, region_id: str) -> None:
    """Borish viloyatini tanlash."""
    user_id = update.callback_query.from_user.id
    data_db = await _load_db_data()
    region_name = next((r['name'] for r in data_db['regions'] if r['id'] == region_id), "Noma'lum")

    user_data[user_id]['to_region_id'] = region_id
    user_data[user_id]['to_region_name'] = region_name

    districts = await get_districts_by_region(region_id)
    keyboard = []
    if districts:
        keyboard = [[InlineKeyboardButton(escape_markdown(d['name']), callback_data=f"to_district_{d['id']}")] for d in districts]
    keyboard.append([InlineKeyboardButton(escape_markdown("Tuman muhim emas / O'tkazib yuborish"), callback_data="to_district_none")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(escape_markdown(f"Boradigan tumanni tanlang (ixtiyoriy):"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    user_states[user_id] = STATE_AWAITING_TO_DISTRICT

async def select_to_district(update: Update, context: ContextTypes.DEFAULT_TYPE, district_id: str) -> None:
    """Borish tumanini tanlash."""
    user_id = update.callback_query.from_user.id
    data_db = await _load_db_data()
    district_name = next((d['name'] for d in data_db['districts'] if d['id'] == district_id), None)

    user_data[user_id]['to_district_id'] = district_id if district_id != "none" else None
    user_data[user_id]['to_district_name'] = district_name if district_id != "none" else None

    # Telefon raqamini yuborishni so'rash
    await update.callback_query.edit_message_text(
        escape_markdown("Iltimos, telefon raqamingizni kiriting (Masalan: +9989Xxxxxxxx):"), parse_mode='MarkdownV2'
    )
    user_states[user_id] = STATE_AWAITING_PHONE_NUMBER

async def handle_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Foydalanuvchidan telefon raqamini qabul qiladi."""
    user_id = update.effective_user.id
    # Bu funksiya endi handle_message orqali chaqiriladi, shuning uchun holatni bu yerda tekshirish shart emas
    # if user_states.get(user_id) != STATE_AWAITING_PHONE_NUMBER:
    #     return

    phone_number = update.message.text.strip()
    # Telefon raqamini validatsiya qilish (soddalashtirilgan)
    if not (phone_number.startswith('+998') and len(phone_number) == 13 and phone_number[1:].isdigit()):
        await update.message.reply_text(escape_markdown("Iltimos, to'g'ri telefon raqamini kiriting. Misol: +9989Xxxxxxxx"), parse_mode='MarkdownV2')
        return

    user_data[user_id]['phone_number'] = phone_number

    keyboard = [
        [InlineKeyboardButton("Izohsiz", callback_data="comment_none")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        escape_markdown("Qo'shimcha izohlaringiz bormi? (ixtiyoriy):"),
        reply_markup=reply_markup,
        parse_mode='MarkdownV2'
    )
    user_states[user_id] = STATE_AWAITING_COMMENT

async def handle_comment(update: Update, context: ContextTypes.DEFAULT_TYPE, comment_text: str = None) -> None:
    """Foydalanuvchidan izohni qabul qiladi va buyurtmani yakunlaydi."""
    user_id = update.effective_user.id
    # Bu funksiya endi handle_message orqali chaqiriladi, shuning uchun holatni bu yerda tekshirish shart emas
    # if user_states.get(user_id) not in [STATE_AWAITING_COMMENT, STATE_NONE]:
    #     return

    if update.callback_query:
        await update.callback_query.answer()
        user_data[user_id]['comment'] = None
    elif update.message and comment_text is None: # Bu izoh uchun matnli xabar
        user_data[user_id]['comment'] = update.message.text.strip()
    else: # comment_text to'g'ridan-to'g'ri berilgan holat
        user_data[user_id]['comment'] = comment_text

    order_details = user_data[user_id]

    # Buyurtmani ma'lumotlar bazasiga saqlash
    data = await _load_db_data()
    order_id = str(uuid.uuid4()) # Unikal ID yaratish
    new_order = {
        'id': order_id,
        'customerId': str(user_id),
        'customerUsername': update.effective_user.username or update.effective_user.first_name,
        'fromRegionId': order_details['from_region_id'],
        'fromRegion': order_details['from_region_name'],
        'fromDistrictId': order_details['from_district_id'],
        'fromDistrict': order_details['from_district_name'],
        'toRegionId': order_details['to_region_id'],
        'toRegion': order_details['to_region_name'],
        'toDistrictId': order_details['to_district_id'],
        'toDistrict': order_details['to_district_name'],
        'phoneNumber': order_details['phone_number'],
        'comment': order_details['comment'],
        'status': 'pending', # pending, accepted, cancelled, completed
        'createdAt': datetime.now().isoformat(),
        'acceptedBy': None,
        'acceptedUsername': None,
        'acceptedAt': None,
        'groupMessageId': None, # Qaysi guruhdagi xabar ekanligini saqlash
        'groupChatId': None # Qaysi guruhning chat ID'si ekanligini saqlash
    }
    data['orders'].append(new_order)
    await _save_db_data(data)

    try:
        # Buyurtmaga mos keluvchi guruhlarga yuborish
        suitable_groups = await get_suitable_groups_for_order(
            new_order['fromRegionId'],
            new_order['fromDistrictId'],
            new_order['toRegionId'],
            new_order['toDistrictId']
        )

        order_text = (
            f"🚕 \\*\\*Yangi Buyurtma\\!\\*\\*\n\n" # Escaped asterisks and exclamation mark
            f"🔹 \\*\\*Kimdan:\\*\\* {escape_markdown(new_order['fromRegion'])} "
            f"{f'\\({escape_markdown(new_order["fromDistrict"])}\\)' if new_order['fromDistrict'] else ''}\n" # Escaped parentheses
            f"🔸 \\*\\*Kimga:\\*\\* {escape_markdown(new_order['toRegion'])} "
            f"{f'\\({escape_markdown(new_order["toDistrict"])}\\)' if new_order['toDistrict'] else ''}\n" # Escaped parentheses
            f"📞 \\*\\*Telefon:\\*\\* `{escape_markdown(new_order['phoneNumber'])}`\n"
            f"📝 \\*\\*Izoh:\\*\\* {escape_markdown(new_order['comment'] or 'Yo\'q')}\n\n"
            f"🆔 Buyurtma ID: `{escape_markdown(order_id)}`\n"
            f"👤 Mijoz ID: `{escape_markdown(new_order['customerId'])}`"
        )

        keyboard = [
            [InlineKeyboardButton("✅ Qabul qilish", callback_data=f"accept_order_{order_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        sent_to_groups = []
        if suitable_groups:
            for group_id, group_name in suitable_groups:
                try:
                    message_sent = await context.bot.send_message(
                        chat_id=group_id,
                        text=order_text,
                        reply_markup=reply_markup,
                        parse_mode='MarkdownV2'
                    )
                    # Yuborilgan xabarning ma'lumotlarini buyurtmaga saqlash
                    # Bu yerda bir buyurtma bir nechta guruhga yuborilishi mumkin, lekin faqat birinchisining message_id'sini saqlaymiz.
                    # Agar bir nechta guruhdagi xabarni o'zgartirish kerak bo'lsa, bu logikani murakkablashtirish kerak.
                    # Misol uchun: orderda `sentToGroups: [{"groupId": ..., "messageId": ...}]` kabi.
                    # Hozircha faqat bitta (oxirgi) guruhni saqlaymiz, bu yetarli bo'lishi mumkin.
                    data = await _load_db_data() # Yangilangan ma'lumotlarni qayta yuklash
                    for i, order_item in enumerate(data['orders']):
                        if order_item['id'] == order_id:
                            data['orders'][i]['groupMessageId'] = message_sent.message_id
                            data['orders'][i]['groupChatId'] = group_id
                            break
                    await _save_db_data(data)

                    sent_to_groups.append(group_name)
                    logger.info(f"Buyurtma {order_id} guruhga {group_name} ({group_id}) yuborildi. Message ID: {message_sent.message_id}")
                except Exception as e:
                    logger.error(f"Buyurtma {order_id} ni guruhga {group_name} ({group_id}) yuborishda xato: {e}")

            group_msg = f"Buyurtma {', '.join(sent_to_groups)} guruh\\(lar\\)iga yuborildi\\." # Escaped parentheses and period
        else:
            group_msg = escape_markdown("Buyurtmaga mos keluvchi guruhlar topilmadi. Admin bilan bog'laning.")

        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ Buyurtmangiz muvaffaqiyatli qabul qilindi\\!\n\n{group_msg}", # Escaped exclamation mark
            parse_mode='MarkdownV2'
        )
        user_states[user_id] = STATE_NONE
        user_data[user_id] = {} # Muvaffaqiyatli buyurtmadan keyin ma'lumotlarni tozalash
    except Exception as e:
        logger.error(f"Buyurtmani saqlashda yoki yuborishda xato: {e}")
        await context.bot.send_message(chat_id=user_id, text=escape_markdown("Buyurtmani qabul qilishda xato yuz berdi. Iltimos, qayta urinib ko'ring."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        user_data[user_id] = {}


# --- Haydovchi funksiyalari ---

async def driver_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Haydovchi menyusini ko'rsatadi."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_states[user_id] = STATE_NONE
    user_data[user_id] = {}

    if not await is_driver(user_id):
        await query.edit_message_text(escape_markdown("Siz haydovchilar ro'yxatida emassiz. Haydovchi bo'lish uchun admin bilan bog'laning."), parse_mode='MarkdownV2')
        return

    driver_profile = await get_driver_profile(user_id)
    balance = driver_profile.get('balance', 0)
    subscription_end_date_str = driver_profile.get('subscriptionEndDate')

    sub_status = "Yo'q"
    if subscription_end_date_str:
        sub_date = datetime.fromisoformat(subscription_end_date_str)
        if sub_date > datetime.now():
            sub_status = sub_date.strftime("%Y-%m-%d %H:%M")
        else:
            sub_status = "Muddati o'tgan"

    menu_text = (
        f"Siz haydovchi menyusidasiz\\.\n\n" # Escaped period
        f"💰 Balansingiz: \\*\\*{escape_markdown(str(balance))} so'm\\*\\*\n" # Escaped asterisks
        f"📅 Obuna muddati: \\*\\*{escape_markdown(sub_status)}\\*\\*\n\n" # Escaped asterisks
        f"Xizmatni tanlang:"
    )

    keyboard = [
        [InlineKeyboardButton("Mening buyurtmalarim", callback_data="my_accepted_orders")],
        [InlineKeyboardButton("Bosh menyuga", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(menu_text, reply_markup=reply_markup, parse_mode='MarkdownV2')

async def accept_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str) -> None:
    """Haydovchi buyurtmani qabul qiladi."""
    query = update.callback_query
    await query.answer()
    driver_id = query.from_user.id
    driver_username = query.from_user.username or query.from_user.first_name

    if not await is_driver(driver_id):
        await query.answer("Siz haydovchilar ro'yxatida emassiz. Buyurtmalarni qabul qila olmaysiz.", show_alert=True)
        # Xabarni yangilash (Qabul qilish tugmasini olib tashlash)
        try:
            # Agar xabar caption emas, text bo'lsa
            if query.message.caption:
                await query.edit_message_caption(f"{query.message.caption}\n\n⚠️ {escape_markdown('Siz haydovchi emassiz. Buyurtma faol emas.')}", parse_mode='MarkdownV2', reply_markup=None)
            else:
                await query.edit_message_text(f"{query.message.text}\n\n⚠️ {escape_markdown('Siz haydovchi emassiz. Buyurtma faol emas.')}", parse_mode='MarkdownV2', reply_markup=None)
        except Exception as e:
            logger.error(f"Xabarni yangilashda xato: {e}")
        return

    driver_profile = await get_driver_profile(driver_id)
    balance = driver_profile.get('balance', 0)
    subscription_end_date_str = driver_profile.get('subscriptionEndDate')

    is_subscription_active = False
    if subscription_end_date_str:
        sub_date = datetime.fromisoformat(subscription_end_date_str)
        if sub_date > datetime.now():
            is_subscription_active = True

    ORDER_COST = 100 # Har bir buyurtma uchun to'lov miqdori

    if not is_subscription_active:
        await query.answer("Obuna muddatingiz tugagan. Yangilash uchun admin bilan bog'laning.", show_alert=True)
        return

    if balance < ORDER_COST:
        await query.answer("Balansingizda mablag' yetarli emas. Iltimos, balansni to'ldiring.", show_alert=True)
        return

    data = await _load_db_data()
    order_found = False
    order_details = None
    order_index = -1

    for i, order in enumerate(data['orders']):
        if order['id'] == order_id:
            order_found = True
            order_details = order
            order_index = i
            break

    if not order_found or order_details.get('status') != 'pending':
        await query.answer("Bu buyurtma allaqachon qabul qilingan yoki bekor qilingan.", show_alert=True)
        # Xabarni yangilash (Qabul qilish tugmasini olib tashlash)
        try:
            if query.message.caption:
                await query.edit_message_caption(f"{query.message.caption}\n\n⚠️ {escape_markdown('Bu buyurtma allaqachon qabul qilingan yoki bekor qilingan.')}", parse_mode='MarkdownV2', reply_markup=None)
            else:
                await query.edit_message_text(f"{query.message.text}\n\n⚠️ {escape_markdown('Bu buyurtma allaqachon qabul qilingan yoki bekor qilingan.')}", parse_mode='MarkdownV2', reply_markup=None)
        except Exception as e:
            logger.error(f"Xabarni yangilashda xato: {e}")
        return

    try:
        # Buyurtma holatini yangilash
        data['orders'][order_index]['status'] = 'accepted'
        data['orders'][order_index]['acceptedBy'] = str(driver_id)
        data['orders'][order_index]['acceptedUsername'] = driver_username
        data['orders'][order_index]['acceptedAt'] = datetime.now().isoformat()
        await _save_db_data(data)

        # Haydovchi balansidan yechish
        new_balance = await update_driver_balance(driver_id, -ORDER_COST)

        # Original xabarni yangilash (Qabul qilish tugmasini olib tashlash)
        accepted_message_text = (
            f"🚕 \\*\\*Yangi Buyurtma\\!\\*\\*\n\n" # Escaped asterisks and exclamation mark
            f"🔹 \\*\\*Kimdan:\\*\\* {escape_markdown(order_details['fromRegion'])} "
            f"{f'\\({escape_markdown(order_details["fromDistrict"])}\\)' if order_details['fromDistrict'] else ''}\n" # Escaped parentheses
            f"🔸 \\*\\*Kimga:\\*\\* {escape_markdown(order_details['toRegion'])} "
            f"{f'\\({escape_markdown(order_details["toDistrict"])}\\)' if order_details['toDistrict'] else ''}\n" # Escaped parentheses
            f"📞 \\*\\*Telefon:\\*\\* `{escape_markdown(order_details['phoneNumber'])}`\n"
            f"📝 \\*\\*Izoh:\\*\\* {escape_markdown(order_details['comment'] or 'Yo\'q')}\n\n"
            f"🆔 Buyurtma ID: `{escape_markdown(order_id)}`\n"
            f"👤 Mijoz ID: `{escape_markdown(order_details['customerId'])}`\n\n"
            f"✅ \\*\\*Qabul qildi:\\*\\* {escape_markdown(driver_username)} \\({escape_markdown(str(driver_id))}\\)" # Escaped asterisks and parentheses
        )
        keyboard = [
            [InlineKeyboardButton("↩️ Buyurtmani qaytarish", callback_data=f"return_order_{order_id}")],
            [InlineKeyboardButton("Buyurtma bajarildi", callback_data=f"complete_order_{order_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            if query.message.caption:
                await query.edit_message_caption(
                    caption=accepted_message_text,
                    reply_markup=reply_markup,
                    parse_mode='MarkdownV2'
                )
            else:
                await query.edit_message_text(
                    text=accepted_message_text,
                    reply_markup=reply_markup,
                    parse_mode='MarkdownV2'
                )
        except Exception as e:
            logger.error(f"Original buyurtma xabarini yangilashda xato: {e}")
            await query.message.reply_text(f"Buyurtma {escape_markdown(order_id)} siz tomondan qabul qilindi\\. Original xabarni yangilashda xato yuz berdi\\.", parse_mode='MarkdownV2')
        await query.answer("Buyurtma muvaffaqiyatli qabul qilindi!", show_alert=True)
        await context.bot.send_message(
            chat_id=order_details['customerId'],
            text=f"✅ Sizning buyurtmangiz \\(ID: `{escape_markdown(order_id)}`\\) haydovchi " # Escaped parentheses
            f"\\*\\*{escape_markdown(driver_username)}\\*\\* \\({escape_markdown(str(driver_id))}\\) tomonidan qabul qilindi\\.\n" # Escaped asterisks and parentheses
            f"Haydovchi bilan bog'lanish uchun: `{escape_markdown(order_details['phoneNumber'])}` \\(Buyurtma bergan telefon raqami\\)\\.", # Escaped parentheses
            parse_mode='MarkdownV2'
        )
    except Exception as e:
        logger.error(f"Buyurtmani qabul qilishda xato: {e}")
        await query.answer("Buyurtmani qabul qilishda xato yuz berdi. Iltimas, qayta urinib ko'ring.", show_alert=True)

async def return_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str) -> None:
    """Haydovchi qabul qilgan buyurtmani qaytaradi."""
    query = update.callback_query
    await query.answer()
    driver_id = str(query.from_user.id)

    data = await _load_db_data()
    order_found = False
    order_details = None
    order_index = -1

    for i, order in enumerate(data['orders']):
        if order['id'] == order_id:
            order_found = True
            order_details = order
            order_index = i
            break

    if not order_found:
        await query.answer("Bu buyurtma topilmadi.", show_alert=True)
        return

    if order_details.get('acceptedBy') != driver_id:
        await query.answer("Siz faqat o'zingiz qabul qilgan buyurtmani qaytarishingiz mumkin.", show_alert=True)
        return

    try:
        # Buyurtma holatini 'pending'ga qaytarish
        data['orders'][order_index]['status'] = 'pending'
        data['orders'][order_index]['acceptedBy'] = None
        data['orders'][order_index]['acceptedUsername'] = None
        data['orders'][order_index]['acceptedAt'] = None
        await _save_db_data(data)

        # Haydovchiga balansni qaytarish (agar har bir qabul qilish uchun pul yechilgan bo'lsa)
        ORDER_COST = 100 # Bu yerda buyurtma uchun olingan pulni qaytarish kerak
        await update_driver_balance(int(driver_id), ORDER_COST) # Qaytarilgan pulni haydovchi balansiga qo'shish

        # Original xabarni yangilash (tugmalarni qayta tiklash)
        order_text = (
            f"🚕 \\*\\*Yangi Buyurtma\\!\\*\\*\n\n" # Escaped asterisks and exclamation mark
            f"🔹 \\*\\*Kimdan:\\*\\* {escape_markdown(order_details['fromRegion'])} "
            f"{f'\\({escape_markdown(order_details["fromDistrict"])}\\)' if order_details['fromDistrict'] else ''}\n" # Escaped parentheses
            f"🔸 \\*\\*Kimga:\\*\\* {escape_markdown(order_details['toRegion'])} "
            f"{f'\\({escape_markdown(order_details["toDistrict"])}\\)' if order_details['toDistrict'] else ''}\n" # Escaped parentheses
            f"📞 \\*\\*Telefon:\\*\\* `{escape_markdown(order_details['phoneNumber'])}`\n"
            f"📝 \\*\\*Izoh:\\*\\* {escape_markdown(order_details['comment'] or 'Yo\'q')}\n\n"
            f"🆔 Buyurtma ID: `{escape_markdown(order_id)}`\n"
            f"👤 Mijoz ID: `{escape_markdown(order_details['customerId'])}`\n\n"
            f"↩️ \\*\\*Haydovchi tomonidan qaytarildi\\.\\*\\*" # Escaped asterisks and period
        )
        keyboard = [
            [InlineKeyboardButton("✅ Qabul qilish", callback_data=f"accept_order_{order_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Guruhdagi xabarni yangilash
        if order_details.get('groupChatId') and order_details.get('groupMessageId'):
            try:
                await context.bot.edit_message_text(
                    chat_id=order_details['groupChatId'],
                    message_id=order_details['groupMessageId'],
                    text=order_text,
                    reply_markup=reply_markup,
                    parse_mode='MarkdownV2'
                )
            except Exception as e:
                logger.error(f"Guruhdagi buyurtma xabarini yangilashda xato (return_order): {e}")
                await query.message.reply_text(f"Buyurtma {escape_markdown(order_id)} siz tomondan qaytarildi\\. Guruhdagi xabarni yangilashda muammo yuz berdi\\.", parse_mode='MarkdownV2')
        else:
            await query.message.reply_text(f"Buyurtma {escape_markdown(order_id)} siz tomondan qaytarildi\\. Guruhdagi xabarni yangilashda muammo yuz berdi\\.", parse_mode='MarkdownV2')

        await query.answer("Buyurtma muvaffaqiyatli qaytarildi! Balansingizga pul qaytarildi.", show_alert=True)

        await context.bot.send_message(
            chat_id=order_details['customerId'],
            text=f"ℹ️ Sizning buyurtmangiz \\(ID: `{escape_markdown(order_id)}`\\) haydovchi " # Escaped parentheses
            f"\\*\\*{escape_markdown(query.from_user.username or query.from_user.first_name)}\\*\\* tomonidan qaytarildi\\." # Escaped asterisks and period
            f"\nIltimos, qayta buyurtma bering yoki boshqa haydovchi kutib turing\\.", parse_mode='MarkdownV2' # Escaped period
        )

    except Exception as e:
        logger.error(f"Buyurtmani qaytarishda xato: {e}")
        await query.answer("Buyurtmani qaytarishda xato yuz berdi. Iltimos, qayta urinib ko'ring.", show_alert=True)


async def complete_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str) -> None:
    """Haydovchi buyurtmani bajarganini tasdiqlaydi."""
    query = update.callback_query
    await query.answer()
    driver_id = str(query.from_user.id)

    data = await _load_db_data()
    order_found = False
    order_details = None
    order_index = -1

    for i, order in enumerate(data['orders']):
        if order['id'] == order_id:
            order_found = True
            order_details = order
            order_index = i
            break

    if not order_found:
        await query.answer("Bu buyurtma topilmadi.", show_alert=True)
        return

    if order_details.get('acceptedBy') != driver_id:
        await query.answer("Siz faqat o'zingiz qabul qilgan buyurtmani yakunlashingiz mumkin.", show_alert=True)
        return

    if order_details.get('status') == 'completed':
        await query.answer("Bu buyurtma allaqachon bajarilgan.", show_alert=True)
        return

    try:
        # Buyurtma holatini 'completed' ga o'zgartirish
        data['orders'][order_index]['status'] = 'completed'
        await _save_db_data(data)

        # Original xabarni yangilash (tugmalarni olib tashlash)
        completed_message_text = (
            f"🚕 \\*\\*Buyurtma Bajarildi\\!\\*\\*\n\n" # Escaped asterisks and exclamation mark
            f"🔹 \\*\\*Kimdan:\\*\\* {escape_markdown(order_details['fromRegion'])} "
            f"{f'\\({escape_markdown(order_details["fromDistrict"])}\\)' if order_details['fromDistrict'] else ''}\n"
            f"🔸 \\*\\*Kimga:\\*\\* {escape_markdown(order_details['toRegion'])} "
            f"{f'\\({escape_markdown(order_details["toDistrict"])}\\)' if order_details['toDistrict'] else ''}\n"
            f"📞 \\*\\*Telefon:\\*\\* `{escape_markdown(order_details['phoneNumber'])}`\n"
            f"📝 \\*\\*Izoh:\\*\\* {escape_markdown(order_details['comment'] or 'Yo\'q')}\n\n"
            f"🆔 Buyurtma ID: `{escape_markdown(order_id)}`\n"
            f"👤 Mijoz ID: `{escape_markdown(order_details['customerId'])}`\n"
            f"✅ \\*\\*Bajarildi:\\*\\* {escape_markdown(order_details['acceptedUsername'])} \\({escape_markdown(order_details['acceptedBy'])}\\)" # Escaped asterisks and parentheses
        )

        # Guruhdagi xabarni yangilash
        if order_details.get('groupChatId') and order_details.get('groupMessageId'):
            try:
                await context.bot.edit_message_text(
                    chat_id=order_details['groupChatId'],
                    message_id=order_details['groupMessageId'],
                    text=completed_message_text,
                    reply_markup=None, # Tugmalarni olib tashlash
                    parse_mode='MarkdownV2'
                )
            except Exception as e:
                logger.error(f"Guruhdagi buyurtma xabarini yangilashda xato (complete_order): {e}")
                await query.message.reply_text(f"Buyurtma {escape_markdown(order_id)} bajarildi deb belgilandi\\. Guruhdagi xabarni yangilashda muammo yuz berdi\\.", parse_mode='MarkdownV2')
        else:
            await query.message.reply_text(f"Buyurtma {escape_markdown(order_id)} bajarildi deb belgilandi\\. Guruhdagi xabarni yangilashda muammo yuz berdi\\.", parse_mode='MarkdownV2')

        await query.answer("Buyurtma muvaffaqiyatli bajarildi deb belgilandi!", show_alert=True)
        await context.bot.send_message(
            chat_id=order_details['customerId'],
            text=f"✅ Sizning buyurtmangiz \\(ID: `{escape_markdown(order_id)}`\\) haydovchi " # Escaped parentheses
            f"\\*\\*{escape_markdown(order_details['acceptedUsername'])}\\*\\* tomonidan muvaffaqiyatli yakunlandi\\." # Escaped asterisks and period
            f"\nXizmatingiz uchun rahmat\\!", parse_mode='MarkdownV2' # Escaped exclamation mark
        )

    except Exception as e:
        logger.error(f"Buyurtmani yakunlashda xato: {e}")
        await query.answer("Buyurtmani yakunlashda xato yuz berdi. Iltimos, qayta urinib ko'ring.", show_alert=True)

async def my_accepted_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Haydovchining qabul qilgan buyurtmalarini ko'rsatadi."""
    query = update.callback_query
    await query.answer()
    driver_id = str(query.from_user.id)

    data = await _load_db_data()
    accepted_orders = [
        order for order in data['orders']
        if order.get('acceptedBy') == driver_id and order.get('status') == 'accepted'
    ]

    if not accepted_orders:
        await query.edit_message_text(escape_markdown("Sizda hozircha qabul qilingan faol buyurtmalar yo'q."), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Orqaga", callback_data="driver_menu")]]), parse_mode='MarkdownV2')
        return

    # Avvalgi xabarni o'zgartirish (agar mavjud bo'lsa)
    await query.edit_message_text(escape_markdown("Sizning faol buyurtmalaringiz:"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Orqaga", callback_data="driver_menu")]]), parse_mode='MarkdownV2')

    # Har bir buyurtmani alohida xabar qilib yuborish
    for order in accepted_orders: # Use original accepted_orders to get order ID
        order_text = (
            f"Buyurtma ID: `{escape_markdown(order['id'])}`\n"
            f"Kimdan: {escape_markdown(order['fromRegion'])} "
            f"{f'\\({escape_markdown(order["fromDistrict"])}\\)' if order['fromDistrict'] else ''}\n"
            f"Kimga: {escape_markdown(order['toRegion'])} "
            f"{f'\\({escape_markdown(order["toDistrict"])}\\)' if order['toDistrict'] else ''}\n"
            f"Telefon: `{escape_markdown(order['phoneNumber'])}`\n"
            f"Izoh: {escape_markdown(order['comment'] or 'Yo\'q')}\n"
        )
        keyboard = [
            [InlineKeyboardButton("↩️ Buyurtmani qaytarish", callback_data=f"return_order_{order['id']}")],
            [InlineKeyboardButton("Buyurtma bajarildi", callback_data=f"complete_order_{order['id']}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text( # query.message.reply_text dan foydalanish, chunki edit_message_text allaqachon ishlatilgan
            text=order_text,
            reply_markup=reply_markup,
            parse_mode='MarkdownV2'
        )


# --- Admin funksiyalari ---

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin panel menyusini ko'rsatadi."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_states[user_id] = STATE_NONE # Holatni tiklash
    user_data[user_id] = {} # Ma'lumotlarni tozalash

    if not await is_admin(user_id):
        await query.edit_message_text(escape_markdown("Siz admin emassiz."), parse_mode='MarkdownV2')
        return

    keyboard = [
        [InlineKeyboardButton("📊 Statistika", callback_data="admin_stats")], # Yangi tugma
        [InlineKeyboardButton("Viloyatlarni boshqarish", callback_data="admin_manage_regions")],
        [InlineKeyboardButton("Tumanlarni boshqarish", callback_data="admin_manage_districts")],
        [InlineKeyboardButton("Marshrutlarni boshqarish", callback_data="admin_manage_routes")],
        [InlineKeyboardButton("Haydovchilarni boshqarish", callback_data="admin_manage_drivers")], # Bu endi balans/obunani ham o'z ichiga oladi
        [InlineKeyboardButton("Reklama yuborish", callback_data="admin_send_ad_start")],
        [InlineKeyboardButton("Bosh menyuga", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(escape_markdown("Admin Paneliga xush kelibsiz!"), reply_markup=reply_markup, parse_mode='MarkdownV2')

# --- Viloyatlarni boshqarish ---
async def admin_manage_regions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_states[user_id] = STATE_NONE

    if not await is_admin(user_id):
        await query.edit_message_text(escape_markdown("Siz admin emassiz."), parse_mode='MarkdownV2')
        return

    regions = await get_regions()
    regions_text = escape_markdown("Mavjud viloyatlar:\n")
    if regions:
        for i, region in enumerate(regions):
            regions_text += f"{i+1}\\. {escape_markdown(region['name'])} \\(ID: `{escape_markdown(region['id'])}`\\)\n" # Escaped period and parentheses
    else:
        regions_text += escape_markdown("Mavjud viloyatlar yo'q.")

    keyboard = [
        [InlineKeyboardButton("Viloyat qo'shish", callback_data="admin_add_region_start")],
        [InlineKeyboardButton("Viloyat o'chirish", callback_data="admin_delete_region_start")], # Yangi tugma
        [InlineKeyboardButton("Orqaga (Admin menyu)", callback_data="admin_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(regions_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Viloyatlar menyusini yuborishda xato: {e}")
        await query.message.reply_text(escape_markdown("Viloyatlar menyusini yuklashda xato yuz berdi. Iltimos, qayta urinib ko'ring."), parse_mode='MarkdownV2')

async def admin_add_region_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return
    await query.edit_message_text(escape_markdown("Qo'shmoqchi bo'lgan viloyat nomini kiriting:"))
    user_states[user_id] = STATE_ADMIN_ADD_REGION

async def admin_handle_add_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    # Bu funksiya endi handle_message orqali chaqiriladi, shuning uchun holatni bu yerda tekshirish shart emas
    if not await is_admin(user_id): return

    region_name = update.message.text.strip()
    if not region_name:
        await update.message.reply_text(escape_markdown("Viloyat nomi bo'sh bo'lishi mumkin emas. Qayta kiriting:"))
        return

    data = await _load_db_data()
    # Viloyat nomining unikal ekanligini tekshirish (faqat nom bo'yicha)
    if any(r['name'].lower() == region_name.lower() for r in data['regions']):
        await update.message.reply_text(escape_markdown(f"'{region_name}' nomli viloyat allaqachon mavjud. Boshqa nom kiriting:"))
        return

    new_region = {"id": str(uuid.uuid4()), "name": region_name}
    data['regions'].append(new_region)
    await _save_db_data(data)
    await update.message.reply_text(escape_markdown(f"✅ Viloyat '{region_name}' muvaffaqiyatli qo'shildi."), parse_mode='MarkdownV2')
    # update.message ni update.callback_query.message ga o'zgartirish kerak, chunki admin_manage_regions callback_query bilan ishlaydi
    # Yoki shunchaki admin_manage_regions funksiyasini qayta chaqirish
    user_states[user_id] = STATE_NONE
    # CallbackQuery obyektini yaratish, chunki admin_manage_regions callback_query kutadi
    dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
    dummy_query.answer.return_value = None
    await admin_manage_regions(dummy_query, context) # Menyuga qaytarish

async def admin_delete_region_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    regions = await get_regions()
    if not regions:
        await query.edit_message_text(escape_markdown("Hozircha viloyatlar yo'q. O'chirish mumkin emas."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    keyboard = [[InlineKeyboardButton(escape_markdown(region['name']), callback_data=f"delete_region_{region['id']}")] for region in regions]
    keyboard.append([InlineKeyboardButton(escape_markdown("Bekor qilish"), callback_data="admin_manage_regions")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(escape_markdown("O'chirmoqchi bo'lgan viloyatni tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_DELETE_REGION_SELECT

async def admin_delete_region_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, region_id: str) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    data = await _load_db_data()
    initial_regions_count = len(data['regions'])
    region_name = "Noma'lum"

    # Viloyatni topish va o'chirish
    for i, r in enumerate(data['regions']):
        if r['id'] == region_id:
            region_name = r['name']
            del data['regions'][i]
            break

    # Viloyatga tegishli tumanlarni o'chirish
    data['districts'] = [d for d in data['districts'] if d['regionId'] != region_id]

    # Viloyatga tegishli marshrutlarni o'chirish
    data['routes'] = [
        rt for rt in data['routes']
        if rt.get('fromRegionId') != region_id and rt.get('toRegionId') != region_id
    ]

    await _save_db_data(data)

    if len(data['regions']) < initial_regions_count:
        await query.edit_message_text(escape_markdown(f"✅ Viloyat '{region_name}' va unga tegishli tumanlar/marshrutlar muvaffaqiyatli o'chirildi."), parse_mode='MarkdownV2')
    else:
        await query.edit_message_text(escape_markdown(f"Viloyat '{region_name}' topilmadi yoki o'chirishda xato yuz berdi."), parse_mode='MarkdownV2')

    await admin_manage_regions(update, context) # Menyuga qaytarish
    user_states[user_id] = STATE_NONE

# --- Tumanlarni boshqarish ---
async def admin_manage_districts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_states[user_id] = STATE_NONE

    if not await is_admin(user_id):
        await query.edit_message_text(escape_markdown("Siz admin emassiz."), parse_mode='MarkdownV2')
        return

    data_db = await _load_db_data()
    districts = data_db['districts']
    regions = data_db['regions']

    district_list_text = "Hozirgi tumanlar:\n"
    if districts:
        for d in sorted(districts, key=lambda x: x['name']):
            region_name = next((r['name'] for r in regions if r['id'] == d['regionId']), "Noma'lum viloyat")
            district_list_text += f"- {escape_markdown(d['name'])} \\({escape_markdown(region_name)}\\)\n" # Escaped parentheses
    else:
        district_list_text = "Hozircha tumanlar yo'q."

    keyboard = [
        [InlineKeyboardButton("Tuman qo'shish", callback_data="admin_add_district_start")],
        [InlineKeyboardButton("Tuman o'chirish", callback_data="admin_delete_district_select_region_start")],
        [InlineKeyboardButton("Admin menyuga", callback_data="admin_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(escape_markdown(f"{district_list_text}\nAmalni tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')

async def admin_add_district_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    regions = await get_regions()
    if not regions:
        await query.edit_message_text(escape_markdown("Avval viloyatlarni qo'shishingiz kerak."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    keyboard = [[InlineKeyboardButton(escape_markdown(region['name']), callback_data=f"select_region_for_district_{region['id']}")] for region in regions]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(escape_markdown("Qaysi viloyatga tuman qo'shmoqchisiz?"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_SELECT_REGION_FOR_DISTRICT

async def admin_select_region_for_district_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, region_id: str) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    user_data[user_id]['selected_region_id_for_district'] = region_id

    data_db = await _load_db_data()
    region_name = next((r['name'] for r in data_db['regions'] if r['id'] == region_id), "Noma'lum")

    await query.edit_message_text(escape_markdown(f"'{region_name}' viloyatiga qo'shmoqchi bo'lgan tuman nomini kiriting:"))
    user_states[user_id] = STATE_ADMIN_ADD_DISTRICT

async def admin_handle_add_district(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    # Bu funksiya endi handle_message orqali chaqiriladi, shuning uchun holatni bu yerda tekshirish shart emas
    if not await is_admin(user_id): return

    region_id = user_data[user_id].get('selected_region_id_for_district')
    if not region_id:
        await update.message.reply_text(escape_markdown("Viloyat tanlanmagan. Qayta urinib ko'ring."))
        user_states[user_id] = STATE_NONE
        return

    district_name = update.message.text.strip()
    if not district_name:
        await update.message.reply_text(escape_markdown("Tuman nomi bo'sh bo'lishi mumkin emas. Qayta kiriting:"))
        return

    data = await _load_db_data()
    # Tuman nomining unikal ekanligini tekshirish (berilgan viloyat ichida)
    if any(d['name'].lower() == district_name.lower() and d['regionId'] == region_id for d in data['districts']):
        await update.message.reply_text(escape_markdown(f"'{district_name}' nomli tuman bu viloyatda allaqachon mavjud. Boshqa nom kiriting:"))
        return

    new_district = {"id": str(uuid.uuid4()), "name": district_name, "regionId": region_id}
    data['districts'].append(new_district)
    await _save_db_data(data)

    region_name = next((r['name'] for r in data['regions'] if r['id'] == region_id), "Noma'lum")
    await update.message.reply_text(escape_markdown(f"✅ Tuman '{district_name}' ({region_name}) muvaffaqiyatli qo'shildi."), parse_mode='MarkdownV2')
    user_states[user_id] = STATE_NONE
    # CallbackQuery obyektini yaratish, chunki admin_manage_districts callback_query kutadi
    dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
    dummy_query.answer.return_value = None
    await admin_manage_districts(dummy_query, context) # Menyuga qaytarish

async def admin_delete_district_select_region_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    regions = await get_regions()
    if not regions:
        await query.edit_message_text(escape_markdown("Hozircha viloyatlar yo'q. Tuman o'chirish uchun avval viloyat qo'shing."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    keyboard = [[InlineKeyboardButton(escape_markdown(region['name']), callback_data=f"delete_district_from_region_{region['id']}")] for region in regions]
    keyboard.append([InlineKeyboardButton(escape_markdown("Bekor qilish"), callback_data="admin_manage_districts")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(escape_markdown("Qaysi viloyatdan tuman o'chirmoqchisiz?"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_DELETE_DISTRICT_SELECT_REGION

async def admin_delete_district_select_district_start(update: Update, context: ContextTypes.DEFAULT_TYPE, region_id: str) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    user_data[user_id]['selected_region_id_for_district_deletion'] = region_id

    districts = await get_districts_by_region(region_id)
    if not districts:
        await query.edit_message_text(escape_markdown("Bu viloyatda tumanlar yo'q. O'chirish mumkin emas."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    data_db = await _load_db_data()
    region_name = next((r['name'] for r in data_db['regions'] if r['id'] == region_id), "Noma'lum")

    keyboard = [[InlineKeyboardButton(escape_markdown(d['name']), callback_data=f"delete_district_{d['id']}")] for d in districts]
    keyboard.append([InlineKeyboardButton(escape_markdown("Bekor qilish"), callback_data="admin_manage_districts")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(escape_markdown(f"'{region_name}' viloyatidan o'chirmoqchi bo'lgan tumanni tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_DELETE_DISTRICT_SELECT_DISTRICT

async def admin_delete_district_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, district_id: str) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    data = await _load_db_data()
    initial_districts_count = len(data['districts'])
    district_name = "Noma'lum"
    region_name = "Noma'lum viloyat"
    deleted_district_region_id = None

    # Tumanni topish va o'chirish
    for i, d in enumerate(data['districts']):
        if d['id'] == district_id:
            district_name = d['name']
            deleted_district_region_id = d['regionId']
            del data['districts'][i]
            break

    # Viloyat nomini topish
    if deleted_district_region_id:
        region_name = next((r['name'] for r in data['regions'] if r['id'] == deleted_district_region_id), region_name)

    # Tumanga tegishli marshrutlarni o'chirish (agar faqat shu tumanlar bilan bog'langan bo'lsa)
    data['routes'] = [
        rt for rt in data['routes']
        if not (rt.get('fromDistrictId') == district_id or rt.get('toDistrictId') == district_id)
    ]

    await _save_db_data(data)

    if len(data['districts']) < initial_districts_count:
        await query.edit_message_text(escape_markdown(f"✅ Tuman '{district_name}' ({region_name}) va unga tegishli marshrutlar muvaffaqiyatli o'chirildi."), parse_mode='MarkdownV2')
    else:
        await query.edit_message_text(escape_markdown(f"Tuman '{district_name}' topilmadi yoki o'chirishda xato yuz berdi."), parse_mode='MarkdownV2')

    user_states[user_id] = STATE_NONE
    await admin_manage_districts(update, context) # Menyuga qaytarish

# --- Marshrutlarni boshqarish ---
MAX_MESSAGE_LENGTH = 4000  # 4096 dan ozroq, xavfsizlik uchun

async def admin_manage_routes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_states[user_id] = STATE_NONE

    if not await is_admin(user_id):
        await query.edit_message_text(escape_markdown("Siz admin emassiz."), parse_mode='MarkdownV2')
        return

    data_db = await _load_db_data()
    routes = data_db['routes']
    regions = data_db['regions']
    districts = data_db['districts']

    routes_text = escape_markdown("Mavjud marshrutlar:\n\n")
    if routes:
        for route in routes:
            from_region_name = next((r['name'] for r in regions if r['id'] == route.get('fromRegionId')), "N/A")
            from_district_name = next((d['name'] for d in districts if d['id'] == route.get('fromDistrictId')), None)
            to_region_name = next((r['name'] for r in regions if r['id'] == route.get('toRegionId')), "N/A")
            to_district_name = next((d['name'] for d in districts if d['id'] == route.get('toDistrictId')), None)

            # FIX: Escape parentheses in group IDs
            groups = ", ".join([f"{escape_markdown(g['name'])} \\(ID:`{escape_markdown(g['id'])}`\\)" for g in route.get('groupIds', [])]) if route.get('groupIds') else escape_markdown("Yo'q")

            block = (
                f"ID: `{escape_markdown(route['id'])}`\n"
                f"  From: {escape_markdown(from_region_name)}"
                f"{f' \\({escape_markdown(from_district_name)}\\)' if from_district_name else ''} \\-\\> {escape_markdown(to_region_name)}" # Escaped hyphen
                f"{f' \\({escape_markdown(to_district_name)}\\)' if to_district_name else ''}\n"
                f"  Guruhlar: {groups}\n\\-\\-\\- \n" # Escaped hyphens
            )

            # Xabar uzunligini tekshirish
            if len(routes_text) + len(block) > MAX_MESSAGE_LENGTH:
                routes_text += escape_markdown("\n...\n")
                break
            routes_text += block
    else:
        routes_text += escape_markdown("Mavjud marshrutlar yo'q.")

    keyboard = [
        [InlineKeyboardButton("Marshrut qo'shish", callback_data="admin_add_route_start")],
        [InlineKeyboardButton("Marshrut o'chirish", callback_data="admin_delete_route_start")],
        [InlineKeyboardButton("Guruhni marshrutga ulash", callback_data="admin_add_group_to_route_start")],
        [InlineKeyboardButton("Guruhni marshrutdan uzish", callback_data="admin_disconnect_group_from_route_start")], # Qayta qo'shildi
        [InlineKeyboardButton("Orqaga (Admin menyu)", callback_data="admin_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(routes_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Marshrutlar menyusini yuborishda xato: {e}")
        await query.message.reply_text(escape_markdown("Marshrutlar menyusini yuklashda xato yuz berdi. Iltimos, qayta urinib ko'ring."), parse_mode='MarkdownV2')

# Admin: Marshrut qo'shish
async def admin_add_route_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_data[user_id] = {} # Marshrut ma'lumotlarini tozalash

    regions = await get_regions()
    if not regions:
        try:
            await query.edit_message_text(escape_markdown("Avval viloyatlarni qo'shishingiz kerak."), parse_mode='MarkdownV2')
        except Exception as e:
            logger.error(f"Marshrut qo'shish start xabarini yuborishda xato: {e}")
            await query.message.reply_text(escape_markdown("Marshrut qo'shish jarayonini boshlashda xato yuz berdi."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    keyboard = [[InlineKeyboardButton(escape_markdown(region['name']), callback_data=f"add_route_from_region_{region['id']}")] for region in regions]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(escape_markdown("Marshrutning jo'nab ketish viloyatini tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Marshrut jo'nab ketish viloyatini tanlash menyusini yuborishda xato: {e}")
        await query.message.reply_text(escape_markdown("Marshrut jo'nab ketish viloyatini tanlash menyusini yuklashda xato yuz berdi. Iltimos, qayta urinib ko'ring."), parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_ADD_ROUTE_FROM_REGION

async def admin_add_route_from_region_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, region_id: str) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data_db = await _load_db_data()
    region_name = next((r['name'] for r in data_db['regions'] if r['id'] == region_id), "N/A")
    user_data[user_id]['add_route_from_region_id'] = region_id
    user_data[user_id]['add_route_from_region_name'] = region_name

    districts = await get_districts_by_region(region_id)
    keyboard = []
    if districts:
        keyboard = [[InlineKeyboardButton(escape_markdown(d['name']), callback_data=f"add_route_from_district_{d['id']}")] for d in districts]
    keyboard.append([InlineKeyboardButton(escape_markdown("Tuman muhim emas"), callback_data="add_route_from_district_none")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(escape_markdown("Jo'nab ketish tumanini tanlang (ixtiyoriy):"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Marshrut jo'nab ketish tumanini tanlash menyusini yuborishda xato: {e}")
        await query.message.reply_text(escape_markdown("Marshrut jo'nab ketish tumanini tanlash menyusini yuklashda xato yuz berdi. Iltimos, qayta urinib ko'ring."), parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_ADD_ROUTE_FROM_DISTRICT

async def admin_add_route_from_district_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, district_id: str) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data_db = await _load_db_data()
    district_name = next((d['name'] for d in data_db['districts'] if d['id'] == district_id), None)

    user_data[user_id]['add_route_from_district_id'] = district_id if district_id != "none" else None
    user_data[user_id]['add_route_from_district_name'] = district_name if district_id != "none" else None

    regions = await get_regions()
    keyboard = [[InlineKeyboardButton(escape_markdown(region['name']), callback_data=f"add_route_to_region_{region['id']}")] for region in regions]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(escape_markdown("Marshrutning borish viloyatini tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Marshrut borish viloyatini tanlash menyusini yuborishda xato: {e}")
        await query.message.reply_text(escape_markdown("Marshrut borish viloyatini tanlash menyusini yuklashda xato yuz berdi. Iltimos, qayta urinib ko'ring."), parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_ADD_ROUTE_TO_REGION

async def admin_add_route_to_region_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, region_id: str) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data_db = await _load_db_data()
    region_name = next((r['name'] for r in data_db['regions'] if r['id'] == region_id), "N/A")

    user_data[user_id]['add_route_to_region_id'] = region_id
    user_data[user_id]['add_route_to_region_name'] = region_name

    districts = await get_districts_by_region(region_id)
    keyboard = []
    if districts:
        keyboard = [[InlineKeyboardButton(escape_markdown(d['name']), callback_data=f"add_route_to_district_{d['id']}")] for d in districts]
    keyboard.append([InlineKeyboardButton(escape_markdown("Tuman muhim emas"), callback_data="add_route_to_district_none")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(escape_markdown("Borish tumanini tanlang (ixtiyoriy):"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Marshrut borish tumanini tanlash menyusini yuborishda xato: {e}")
        await query.message.reply_text(escape_markdown("Marshrut borish tumanini tanlash menyusini yuklashda xato yuz berdi. Iltimos, qayta urinib ko'ring."), parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_ADD_ROUTE_TO_DISTRICT

async def admin_add_route_to_district_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, district_id: str) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data_db = await _load_db_data()
    district_name = next((d['name'] for d in data_db['districts'] if d['id'] == district_id), None)

    user_data[user_id]['add_route_to_district_id'] = district_id if district_id != "none" else None
    user_data[user_id]['add_route_to_district_name'] = district_name if district_id != "none" else None

    # Marshrutni saqlash
    from_region_id = user_data[user_id].get('add_route_from_region_id')
    from_district_id = user_data[user_id].get('add_route_from_district_id')
    to_region_id = user_data[user_id].get('add_route_to_region_id')
    to_district_id = user_data[user_id].get('add_route_to_district_id')

    data = await _load_db_data()
    new_route = {
        "id": str(uuid.uuid4()),
        "fromRegionId": from_region_id,
        "fromDistrictId": from_district_id,
        "toRegionId": to_region_id,
        "toDistrictId": to_district_id,
        "groupIds": [] # Bu marshrutga ulangan guruhlar
    }
    data['routes'].append(new_route)
    await _save_db_data(data)

    # Foydalanuvchiga xabar berish
    from_region_name = next((r['name'] for r in data['regions'] if r['id'] == from_region_id), "N/A")
    from_district_name = next((d['name'] for d in data['districts'] if d['id'] == from_district_id), None)
    to_region_name = next((r['name'] for r in data['regions'] if r['id'] == to_region_id), "N/A")
    to_district_name = next((d['name'] for d in data['districts'] if d['id'] == to_district_id), None)

    route_display = (
        f"{from_region_name} {f'({from_district_name})' if from_district_name else ''} -> "
        f"{to_region_name} {f'({to_district_name})' if to_district_name else ''}"
    )
    await query.edit_message_text(escape_markdown(f"✅ Marshrut '{route_display}' muvaffaqiyatli qo'shildi."), parse_mode='MarkdownV2')
    user_states[user_id] = STATE_NONE
    user_data[user_id] = {} # Ma'lumotlarni tozalash
    await admin_manage_routes(update, context) # Marshrutlar menyusini qayta ko'rsatish


async def admin_delete_route_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    routes = await get_routes()
    if not routes:
        try:
            await query.edit_message_text(escape_markdown("Hozircha o'chirish uchun marshrutlar mavjud emas."), parse_mode='MarkdownV2')
        except Exception as e:
            logger.error(f"Marshrut o'chirish start xabarini yuborishda xato: {e}")
            await query.message.reply_text(escape_markdown("Marshrut o'chirish jarayonini boshlashda xato yuz berdi."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    keyboard = []
    data_db = await _load_db_data()
    for route in routes:
        from_region_name = next((r['name'] for r in data_db['regions'] if r['id'] == route.get('fromRegionId')), "N/A")
        from_district_name = next((d['name'] for d in data_db['districts'] if d['id'] == route.get('fromDistrictId')), None)
        to_region_name = next((r['name'] for r in data_db['regions'] if r['id'] == route.get('toRegionId')), "N/A")
        to_district_name = next((d['name'] for d in data_db['districts'] if d['id'] == route.get('toDistrictId')), None)

        route_display = f"{escape_markdown(from_region_name)} {f'\\({escape_markdown(from_district_name)}\\)' if from_district_name else ''} \\-\\> {escape_markdown(to_region_name)} {f'\\({escape_markdown(to_district_name)}\\)' if to_district_name else ''}" # Escaped hyphen
        keyboard.append([InlineKeyboardButton(route_display, callback_data=f"delete_route_{route['id']}")])
    keyboard.append([InlineKeyboardButton("Orqaga", callback_data="admin_manage_routes")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(escape_markdown("O'chirmoqchi bo'lgan marshrutni tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Marshrut o'chirish menyusini yuborishda xato: {e}")
        await query.message.reply_text(escape_markdown("Marshrut o'chirish menyusini yuklashda xato yuz berdi. Iltimos, qayta urinib ko'ring."), parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_DELETE_ROUTE_SELECT

async def admin_delete_route_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, route_id: str) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    data = await _load_db_data()
    initial_routes_count = len(data['routes'])
    route_display_name = "Noma'lum marshrut"

    # Marshrutni topish va o'chirish
    for i, r in enumerate(data['routes']):
        if r['id'] == route_id:
            from_region_name = next((reg['name'] for reg in data['regions'] if reg['id'] == r.get('fromRegionId')), "N/A")
            from_district_name = next((dis['name'] for dis in data['districts'] if dis['id'] == r.get('fromDistrictId')), None)
            to_region_name = next((reg['name'] for reg in data['regions'] if reg['id'] == r.get('toRegionId')), "N/A")
            to_district_name = next((dis['name'] for dis in data['districts'] if dis['id'] == r.get('toDistrictId')), None)
            route_display_name = (
                f"{from_region_name} {f'({from_district_name})' if from_district_name else ''} -> "
                f"{to_region_name} {f'({to_district_name})' if to_district_name else ''}"
            )
            del data['routes'][i]
            break

    await _save_db_data(data)

    if len(data['routes']) < initial_routes_count:
        await query.edit_message_text(escape_markdown(f"✅ Marshrut '{route_display_name}' muvaffaqiyatli o'chirildi."), parse_mode='MarkdownV2')
    else:
        await query.edit_message_text(escape_markdown(f"Marshrut '{route_display_name}' topilmadi yoki o'chirishda xato yuz berdi."), parse_mode='MarkdownV2')

    user_states[user_id] = STATE_NONE
    await admin_manage_routes(update, context) # Yangilangan menyu


# Admin: Guruhni marshrutga ulash
async def admin_add_group_to_route_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    routes = await get_routes()
    if not routes:
        await query.edit_message_text(escape_markdown("Avval marshrutlarni qo'shishingiz kerak."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    data_db = await _load_db_data()
    keyboard = []

    for route in routes:
        route_id = route['id']  # Use route ID directly

        from_region_name = next((r['name'] for r in data_db['regions'] if r['id'] == route.get('fromRegionId')), "N/A")
        from_district_name = next((d['name'] for d in data_db['districts'] if d['id'] == route.get('fromDistrictId')), None)
        to_region_name = next((r['name'] for r in data_db['regions'] if r['id'] == route.get('toRegionId')), "N/A")
        to_district_name = next((d['name'] for d in data_db['districts'] if d['id'] == route.get('toDistrictId')), None)

        route_display = f"{from_region_name} {f'({from_district_name})' if from_district_name else ''} -> {to_region_name} {f'({to_district_name})' if to_district_name else ''}"

        keyboard.append([
            InlineKeyboardButton(escape_markdown(route_display), callback_data=f"admin_select_route_{route_id}")
        ])
    keyboard.append([InlineKeyboardButton(escape_markdown("Bekor qilish"), callback_data="admin_manage_routes")]) # Bekor qilish tugmasi
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(escape_markdown("Guruhni ulash uchun marshrutni tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_SELECT_ROUTE_FOR_GROUP

async def admin_select_route_for_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    callback_data = query.data
    if not callback_data.startswith("admin_select_route_"):
        await query.edit_message_text(escape_markdown("Noto'g'ri tugma."), parse_mode='MarkdownV2')
        return

    route_id = callback_data[len("admin_select_route_"):]
    if not route_id:
        await query.edit_message_text(escape_markdown("Ushbu marshrut mavjud emas yoki sessiya muddati tugagan."), parse_mode='MarkdownV2')
        return

    user_data[user_id]['selected_route_id'] = route_id
    user_states[user_id] = STATE_ADMIN_ADD_GROUP_ID

    await query.edit_message_text(escape_markdown("Guruhning ID'sini kiriting (Masalan, -1001234567890):"), parse_mode='MarkdownV2')

async def admin_handle_add_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    # Bu funksiya endi handle_message orqali chaqiriladi, shuning uchun holatni bu yerda tekshirish shart emas
    if not await is_admin(user_id): return

    group_id_str = update.message.text.strip()
    try:
        group_id = int(group_id_str)
        if not group_id_str.startswith('-100'):
            raise ValueError("Telegram guruh ID'si odatda '-100' bilan boshlanadi.")
    except ValueError:
        await update.message.reply_text(escape_markdown("Noto'g'ri guruh ID formati. Misol: -1001234567890"), parse_mode='MarkdownV2')
        return

    user_data[user_id]['group_id_to_add'] = group_id
    await update.message.reply_text(escape_markdown("Guruhning nomini kiriting (ixtiyoriy, misol: 'Farg'ona Taxi'):"), parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_ADD_GROUP_NAME

async def admin_handle_add_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    # Bu funksiya endi handle_message orqali chaqiriladi, shuning uchun holatni bu yerda tekshirish shart emas
    if not await is_admin(user_id): return

    group_name = update.message.text.strip()
    if not group_name:
        group_name = "Noma'lum guruh" # Agar nom kiritilmasa, standart nom

    route_id = user_data[user_id].get('selected_route_id')
    group_id = user_data[user_id].get('group_id_to_add')

    if not route_id or group_id is None:
        await update.message.reply_text(escape_markdown("Marshrut yoki guruh ID'si topilmadi. Qayta urinib ko'ring."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    data = await _load_db_data()
    route_found = False
    for route in data['routes']:
        if route['id'] == route_id:
            route_found = True
            if 'groupIds' not in route:
                route['groupIds'] = []

            # Guruh allaqachon qo'shilganligini tekshirish
            if any(g['id'] == str(group_id) for g in route['groupIds']):
                await update.message.reply_text(escape_markdown(f"Guruh `{group_id}` allaqachon ushbu marshrutga ulangan."), parse_mode='MarkdownV2')
                user_states[user_id] = STATE_NONE
                user_data[user_id] = {}
                return

            route['groupIds'].append({"id": str(group_id), "name": group_name})
            break

    if not route_found:
        await update.message.reply_text(escape_markdown("Tanlangan marshrut topilmadi."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    await _save_db_data(data)
    await update.message.reply_text(escape_markdown(f"✅ Guruh '{group_name}' (`{group_id}`) marshrutga muvaffaqiyatli ulandi."), parse_mode='MarkdownV2')
    user_states[user_id] = STATE_NONE
    user_data[user_id] = {}
    # CallbackQuery obyektini yaratish, chunki admin_manage_routes callback_query kutadi
    dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
    dummy_query.answer.return_value = None
    await admin_manage_routes(dummy_query, context) # Admin marshrutlar menyusiga qaytarish


# Admin: Guruhni marshrutdan uzish (Qayta qo'shildi)
async def admin_disconnect_group_from_route_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    data_db = await _load_db_data()
    routes_with_groups = [r for r in data_db['routes'] if r.get('groupIds')]

    if not routes_with_groups:
        await query.edit_message_text(escape_markdown("Guruhlari ulangan marshrutlar topilmadi."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    keyboard = []
    for route in routes_with_groups:
        from_region_name = next((r['name'] for r in data_db['regions'] if r['id'] == route.get('fromRegionId')), "N/A")
        from_district_name = next((d['name'] for d in data_db['districts'] if d['id'] == route.get('fromDistrictId')), None)
        to_region_name = next((r['name'] for r in data_db['regions'] if r['id'] == route.get('toRegionId')), "N/A")
        to_district_name = next((d['name'] for d in data_db['districts'] if d['id'] == route.get('toDistrictId')), None)

        route_display = f"{from_region_name} {f'({from_district_name})' if from_district_name else ''} -> {to_region_name} {f'({to_district_name})' if to_district_name else ''}"
        keyboard.append([
            InlineKeyboardButton(escape_markdown(route_display), callback_data=f"disconnect_group_select_route_{route['id']}")
        ])
    keyboard.append([InlineKeyboardButton(escape_markdown("Bekor qilish"), callback_data="admin_manage_routes")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(escape_markdown("Guruhni uzish uchun marshrutni tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    user_states[user_id] = STATE_NONE # No specific state needed, direct callback handling

async def admin_disconnect_group_select_route_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    callback_data = query.data
    route_id = callback_data.replace("disconnect_group_select_route_", "")

    data = await _load_db_data()
    route_found = next((r for r in data['routes'] if r['id'] == route_id), None)

    if not route_found or not route_found.get('groupIds'):
        await query.edit_message_text(escape_markdown("Marshrut topilmadi yoki ulangan guruhlar yo'q."), parse_mode='MarkdownV2')
        await admin_manage_routes(update, context)
        return

    user_data[user_id]['route_id_for_group_disconnect'] = route_id

    keyboard = []
    for group in route_found['groupIds']:
        keyboard.append([
            InlineKeyboardButton(escape_markdown(f"{group['name']} (ID: {group['id']})"), callback_data=f"disconnect_group_from_route_{route_id}_{group['id']}")
        ])
    keyboard.append([InlineKeyboardButton(escape_markdown("Bekor qilish"), callback_data="admin_manage_routes")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(escape_markdown("Uzmoqchi bo'lgan guruhni tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    user_states[user_id] = STATE_NONE # No specific state, direct callback handling

async def admin_disconnect_group_from_route_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    callback_data = query.data

    # Extract route_id and group_id from callback_data
    parts = callback_data.split('_')
    if len(parts) < 5: # disconnect_group_from_route_{route_id}_{group_id}
        await query.edit_message_text(escape_markdown("Noto'g'ri tugma ma'lumoti."), parse_mode='MarkdownV2')
        await admin_manage_routes(update, context)
        return

    route_id = parts[4] # Assuming route_id is the 5th part (index 4)
    group_id_to_remove = parts[5] # Assuming group_id is the 6th part (index 5)

    data = await _load_db_data()
    route_found = next((r for r in data['routes'] if r['id'] == route_id), None)

    if not route_found:
        await query.edit_message_text(escape_markdown("Marshrut topilmadi."), parse_mode='MarkdownV2')
    else:
        original_groups = route_found.get('groupIds', [])
        new_groups = [g for g in original_groups if g['id'] != group_id_to_remove]

        if len(new_groups) == len(original_groups):
            await query.edit_message_text(escape_markdown("Guruh topilmadi yoki allaqachon uzilgan."), parse_mode='MarkdownV2')
        else:
            route_found['groupIds'] = new_groups
            await _save_db_data(data)
            await query.edit_message_text(escape_markdown(f"✅ Guruh `{group_id_to_remove}` marshrutdan muvaffaqiyatli uzildi."), parse_mode='MarkdownV2')

    await admin_manage_routes(update, context)
    user_states[user_id] = STATE_NONE
    user_data[user_id] = {}


# --- Haydovchilarni boshqarish ---
async def admin_manage_drivers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_states[user_id] = STATE_NONE

    if not await is_admin(user_id):
        await query.edit_message_text(escape_markdown("Siz admin emassiz."), parse_mode='MarkdownV2')
        return

    data = await _load_db_data()
    drivers = data.get('drivers', [])
    driver_list_text = "\\*\\*Ro'yxatdan o'tgan haydovchilar:\\*\\*\n\n" # Escaped asterisks
    if not drivers:
        driver_list_text += escape_markdown("Ro'yxatda haydovchilar yo'q.")
    else:
        for driver_uid in drivers:
            driver_data = data['driver_profiles'].get(driver_uid, {"balance": 0, "subscriptionEndDate": None})
            sub_end_date = driver_data.get('subscriptionEndDate')
            sub_status = "Yo'q"
            if sub_end_date:
                sub_date_dt = datetime.fromisoformat(sub_end_date)
                if sub_date_dt > datetime.now():
                    sub_status = sub_date_dt.strftime("%Y-%m-%d %H:%M")
                else:
                    sub_status = "Muddati o'tgan"

            driver_list_text += (
                f"👤 ID: `{escape_markdown(driver_uid)}`\n"
                f"  💰 Balans: {escape_markdown(str(driver_data.get('balance', 0)))} so'm\n"
                f"  📅 Obuna: {escape_markdown(sub_status)}\n"
                f"\\-\\-\\- \n" # Escaped hyphens
            )

    keyboard = [
        [InlineKeyboardButton("Haydovchi qo'shish", callback_data="admin_add_driver_start")],
        [InlineKeyboardButton("Haydovchi o'chirish", callback_data="admin_remove_driver_start")],
        [InlineKeyboardButton("Balans to'ldirish", callback_data="admin_add_balance_start")], # Bu yerga ko'chirildi
        [InlineKeyboardButton("Obuna berish", callback_data="admin_add_subscription_start")], # Bu yerga ko'chirildi
        [InlineKeyboardButton("Orqaga (Admin menyu)", callback_data="admin_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.edit_message_text(driver_list_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Haydovchilar menyusini yuborishda xato: {e}")
        await query.message.reply_text(escape_markdown("Haydovchilar menyusini yuklashda xato yuz berdi. Iltimos, qayta urinib ko'ring."), parse_mode='MarkdownV2')


# Admin: Haydovchi qo'shish
async def admin_add_driver_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_states[user_id] = STATE_ADMIN_ADD_DRIVER_ID
    try:
        await query.edit_message_text(escape_markdown("Haydovchi qilib qo'shmoqchi bo'lgan foydalanuvchining ID'sini kiriting:"), parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Haydovchi qo'shish start xabarini yuborishda xato: {e}")
        await query.message.reply_text(escape_markdown("Haydovchi qo'shish jarayonini boshlashda xato yuz berdi."), parse_mode='MarkdownV2')

async def admin_handle_add_driver_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    # Bu funksiya endi handle_message orqali chaqiriladi, shuning uchun holatni bu yerda tekshirish shart emas
    # if user_states.get(user_id) != STATE_ADMIN_ADD_DRIVER_ID:
    #     return

    target_driver_id = update.message.text.strip()

    if not target_driver_id.isdigit():
        await update.message.reply_text(escape_markdown("Noto'g'ri ID. Faqat raqamlardan iborat bo'lishi kerak. Qayta kiriting:"), parse_mode='MarkdownV2')
        return

    if str(target_driver_id) in ADMIN_TELEGRAM_IDS:
        await update.message.reply_text(f"ID `{escape_markdown(target_driver_id)}` admin hisoblanadi\\. Adminlar haydovchi bo'la olmaydilar\\.", parse_mode='MarkdownV2') # Escaped period
        user_states[user_id] = STATE_NONE
        # CallbackQuery obyektini yaratish, chunki admin_manage_drivers callback_query kutadi
        dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
        dummy_query.answer.return_value = None
        await admin_manage_drivers(dummy_query, context)
        return

    data = await _load_db_data()
    if target_driver_id in data.get('drivers', []):
        await update.message.reply_text(f"ID `{escape_markdown(target_driver_id)}` allaqachon haydovchilar ro'yxatida mavjud\\.", parse_mode='MarkdownV2') # Escaped period
    else:
        data.setdefault('drivers', []).append(target_driver_id)
        # Agar profili bo'lmasa yaratish
        if target_driver_id not in data['driver_profiles']:
            data['driver_profiles'][target_driver_id] = {"balance": 0, "subscriptionEndDate": None}
        await _save_db_data(data)
        await update.message.reply_text(f"ID `{escape_markdown(target_driver_id)}` muvaffaqiyatli haydovchilar ro'yxatiga qo'shildi\\.", parse_mode='MarkdownV2') # Escaped period

    user_states[user_id] = STATE_NONE
    # CallbackQuery obyektini yaratish, chunki admin_manage_drivers callback_query kutadi
    dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
    dummy_query.answer.return_value = None
    await admin_manage_drivers(dummy_query, context)

# Admin: Haydovchini chiqarib yuborish
async def admin_remove_driver_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    data = await _load_db_data()
    drivers = data.get('drivers', [])
    if not drivers:
        await query.edit_message_text(escape_markdown("Hozircha haydovchilar yo'q. O'chirish mumkin emas."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    keyboard = [[InlineKeyboardButton(f"Haydovchi ID: {d_id}", callback_data=f"remove_driver_{d_id}")] for d_id in drivers]
    keyboard.append([InlineKeyboardButton("Bekor qilish", callback_data="admin_manage_drivers")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(escape_markdown("O'chirmoqchi bo'lgan haydovchi ID'sini tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_REMOVE_DRIVER_ID

async def admin_remove_driver_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, driver_id: str) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    data = await _load_db_data()
    initial_drivers_count = len(data.get('drivers', []))

    if driver_id in data.get('drivers', []):
        data['drivers'].remove(driver_id)
        # Haydovchi profilini ham o'chirish (ixtiyoriy, agar saqlash kerak bo'lmasa)
        if driver_id in data['driver_profiles']:
            del data['driver_profiles'][driver_id]
        await _save_db_data(data)
        await query.edit_message_text(escape_markdown(f"✅ Haydovchi ID `{driver_id}` muvaffaqiyatli o'chirildi."), parse_mode='MarkdownV2')
    else:
        await query.edit_message_text(escape_markdown(f"Haydovchi ID `{driver_id}` topilmadi."), parse_mode='MarkdownV2')

    user_states[user_id] = STATE_NONE
    await admin_manage_drivers(update, context)

# Admin: Balansga pul qo'shish
async def admin_add_balance_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    data = await _load_db_data()
    drivers = data.get('drivers', [])
    if not drivers:
        await query.edit_message_text(escape_markdown("Hozircha haydovchilar yo'q. Balans to'ldirish mumkin emas."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    keyboard = [[InlineKeyboardButton(f"Haydovchi ID: {d_id}", callback_data=f"select_driver_balance_{d_id}")] for d_id in drivers]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(escape_markdown("Balansini to'ldirmoqchi bo'lgan haydovchining ID'sini tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_ADD_DRIVER_BALANCE_ID

async def admin_select_driver_for_balance(update: Update, context: ContextTypes.DEFAULT_TYPE, driver_id: str) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    user_data[user_id]['selected_driver_id_for_balance'] = driver_id
    await query.edit_message_text(escape_markdown(f"Haydovchi ID `{driver_id}` uchun to'ldirmoqchi bo'lgan balans miqdorini kiriting (faqat butun son):"), parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_ADD_DRIVER_BALANCE_AMOUNT

async def admin_handle_add_balance_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    # Bu funksiya endi handle_message orqali chaqiriladi, shuning uchun holatni bu yerda tekshirish shart emas
    # if user_states.get(user_id) != STATE_ADMIN_ADD_DRIVER_BALANCE_AMOUNT:
    #     return

    amount_str = update.message.text.strip()
    try:
        amount = int(amount_str)
        if amount <= 0:
            raise ValueError("Miqdor musbat son bo'lishi kerak.")
    except ValueError:
        await update.message.reply_text(escape_markdown("Noto'g'ri miqdor. Faqat musbat butun son kiriting."), parse_mode='MarkdownV2')
        return

    driver_id_to_update = user_data[user_id].get('selected_driver_id_for_balance')
    if not driver_id_to_update:
        await update.message.reply_text(escape_markdown("Haydovchi ID tanlanmagan. Qayta urinib ko'ring."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    new_balance = await update_driver_balance(int(driver_id_to_update), amount)
    await update.message.reply_text(escape_markdown(f"✅ Haydovchi ID `{driver_id_to_update}` balansiga {amount} so'm qo'shildi. Yangi balans: {new_balance} so'm."), parse_mode='MarkdownV2')
    user_states[user_id] = STATE_NONE
    user_data[user_id] = {}
    # CallbackQuery obyektini yaratish, chunki admin_manage_drivers callback_query kutadi
    dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
    dummy_query.answer.return_value = None
    await admin_manage_drivers(dummy_query, context)

async def admin_add_subscription_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    data = await _load_db_data()
    drivers = data.get('drivers', [])
    if not drivers:
        await query.edit_message_text(escape_markdown("Hozircha haydovchilar yo'q. Obuna berish mumkin emas."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    keyboard = [[InlineKeyboardButton(f"Haydovchi ID: {d_id}", callback_data=f"select_driver_subscription_{d_id}")] for d_id in drivers]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(escape_markdown("Obuna bermoqchi bo'lgan haydovchining ID'sini tanlang:"), reply_markup=reply_markup, parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_ADD_DRIVER_SUBSCRIPTION_ID

async def admin_select_driver_for_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE, driver_id: str) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id): return

    user_data[user_id]['selected_driver_id_for_subscription'] = driver_id
    await query.edit_message_text(escape_markdown(f"Haydovchi ID `{driver_id}` uchun necha kunga obuna bermoqchisiz? (faqat butun son, masalan, 30):"), parse_mode='MarkdownV2')
    user_states[user_id] = STATE_ADMIN_ADD_DRIVER_SUBSCRIPTION_DAYS

async def admin_handle_add_subscription_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    # Bu funksiya endi handle_message orqali chaqiriladi, shuning uchun holatni bu yerda tekshirish shart emas
    # if user_states.get(user_id) != STATE_ADMIN_ADD_DRIVER_SUBSCRIPTION_DAYS:
    #     return

    days_str = update.message.text.strip()
    try:
        days = int(days_str)
        if days <= 0:
            raise ValueError("Kunlar soni musbat son bo'lishi kerak.")
    except ValueError:
        await update.message.reply_text(escape_markdown("Noto'g'ri kunlar soni. Faqat musbat butun son kiriting."), parse_mode='MarkdownV2')
        return

    driver_id_to_update = user_data[user_id].get('selected_driver_id_for_subscription')
    if not driver_id_to_update:
        await update.message.reply_text(escape_markdown("Haydovchi ID tanlanmagan. Qayta urinib ko'ring."), parse_mode='MarkdownV2')
        user_states[user_id] = STATE_NONE
        return

    new_end_date = await update_driver_subscription(int(driver_id_to_update), days)
    await update.message.reply_text(escape_markdown(f"✅ Haydovchi ID `{driver_id_to_update}` uchun obuna {days} kunga uzaytirildi. Yangi tugash sanasi: {new_end_date.strftime('%Y-%m-%d %H:%M')}."), parse_mode='MarkdownV2')
    user_states[user_id] = STATE_NONE
    user_data[user_id] = {}
    # CallbackQuery obyektini yaratish, chunki admin_manage_drivers callback_query kutadi
    dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
    dummy_query.answer.return_value = None
    await admin_manage_drivers(dummy_query, context)

# Admin: Buyurtmalarni boshqarish
async def admin_orders_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_to_edit = update.effective_message

    if update.callback_query:
        await update.callback_query.answer()
    user_id = message_to_edit.from_user.id
    user_states[user_id] = STATE_NONE

    data = await _load_db_data()
    orders = data['orders']

    orders_list_text = "\\*\\*Barcha buyurtmalar:\\*\\*\n\n" # Escaped asterisks
    if not orders:
        orders_list_text += escape_markdown("Hozircha buyurtmalar yo'q.")
    else:
        for order in orders:
            status_emoji = "⏳" if order.get('status') == 'pending' else "✅" if order.get('status') == 'accepted' else "❌" if order.get('status') == 'cancelled' else "✔️" # Completed
            orders_list_text += (
                f"{status_emoji} ID: `{escape_markdown(order['id'])}`\n"
                f"  From: {escape_markdown(order.get('fromRegion'))} {f'\\({escape_markdown(order.get("fromDistrict"))}\\)' if order.get('fromDistrict') else ''}\n" # Escaped parentheses
                f"  To: {escape_markdown(order.get('toRegion'))} {f'\\({escape_markdown(order.get("toDistrict"))}\\)' if order.get('toDistrict') else ''}\n" # Escaped parentheses
                f"  Tel: `{escape_markdown(order.get('phoneNumber'))}`\n"
                f"  Holat: `{escape_markdown(order.get('status'))}`\n"
                f"  Buyurtma bergan: `{escape_markdown(order.get('customerUsername', order.get('customerId')))}`\n"
            )
            if order.get('status') == 'accepted':
                 orders_list_text += f"  Qabul qilgan: `{escape_markdown(order.get('acceptedUsername', order.get('acceptedBy')))}`\n"

            # Inline tugmalarni har bir buyurtma uchun dinamik yaratish
            if order.get('status') in ['pending', 'accepted']:
                orders_list_text += f"  [Bekor qilish](https://t.me/{escape_markdown(context.bot.username)}?start=admin_cancel_order_{escape_markdown(order['id'])})\n" # Start parametri orqali to'g'ridan-to'g'ri harakat
            orders_list_text += "\\-\\-\\- \n" # Escaped hyphens

    keyboard = [
        [InlineKeyboardButton("Orqaga (Admin menyu)", callback_data="admin_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await message_to_edit.edit_text(orders_list_text, reply_markup=reply_markup, parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Buyurtmalar menyusini yuborishda xato: {e}")
        await message_to_edit.reply_text(escape_markdown("Buyurtmalar menyusini yuklashda xato yuz berdi. Iltimos, qayta urinib ko'ring."), parse_mode='MarkdownV2')

async def admin_cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str) -> None:
    query = update.callback_query # Agar callbackdan kelgan bo'lsa
    message = update.message # Agar startdan kelgan bo'lsa

    if query:
        await query.answer()
        user_id = query.from_user.id
        source_message = query.message
    else: # Agar message orqali kelgan bo'lsa
        user_id = message.from_user.id
        source_message = message

    if not await is_admin(user_id):
        await source_message.reply_text(escape_markdown("Sizda admin huquqlari yo'q."), parse_mode='MarkdownV2')
        return

    data = await _load_db_data()
    order_found = False
    order_details = None
    order_index = -1

    for i, order in enumerate(data['orders']):
        if order['id'] == order_id:
            order_found = True
            order_details = order
            order_index = i
            break

    if not order_found:
        await source_message.reply_text(escape_markdown("Buyurtma topilmadi."), parse_mode='MarkdownV2')
        return

    if order_details.get('status') == 'cancelled':
        await source_message.reply_text(escape_markdown("Bu buyurtma allaqachon bekor qilingan."), parse_mode='MarkdownV2')
        return
    if order_details.get('status') == 'completed':
        await source_message.reply_text(escape_markdown("Bu buyurtma allaqachon bajarilgan. Bekor qilib bo'lmaydi."), parse_mode='MarkdownV2')
        return

    try:
        data['orders'][order_index]['status'] = 'cancelled'
        await _save_db_data(data)

        # Guruhdagi xabarni o'chirish yoki o'zgartirish
        if order_details.get('groupChatId') and order_details.get('groupMessageId'):
            try:
                cancelled_message_text = (
                    f"🚕 \\~\\~Yangi Buyurtma\\!\\~\\~\n\n" # Escaped tildes and exclamation mark
                    f"🔹 Kimdan: {escape_markdown(order_details['fromRegion'])} "
                    f"{f'\\({escape_markdown(order_details["fromDistrict"])}\\)' if order_details['fromDistrict'] else ''}\n" # Escaped parentheses
                    f"🔸 Kimga: {escape_markdown(order_details['toRegion'])} "
                    f"{f'\\({escape_markdown(order_details["toDistrict"])}\\)' if order_details['toDistrict'] else ''}\n" # Escaped parentheses
                    f"📞 Telefon: `{escape_markdown(order_details['phoneNumber'])}`\n"
                    f"📝 Izoh: {escape_markdown(order_details['comment'] or 'Yo\'q')}\n\n"
                    f"🆔 Buyurtma ID: `{escape_markdown(order_id)}`\n"
                    f"👤 Mijoz ID: `{escape_markdown(order_details['customerId'])}`\n\n"
                    f"❌ \\*\\*BEKOR QILINDI\\*\\* \\(Admin tomonidan\\)" # Escaped asterisks and parentheses
                )
                await context.bot.edit_message_text(
                    chat_id=order_details['groupChatId'],
                    message_id=order_details['groupMessageId'],
                    text=cancelled_message_text,
                    reply_markup=None, # Tugmalarni olib tashlash
                    parse_mode='MarkdownV2'
                )
                if order_details.get('acceptedBy'): # Agar haydovchi qabul qilgan bo'lsa, balansini qaytarish
                    await update_driver_balance(int(order_details['acceptedBy']), 100) # Misol: 100 so'm qaytarish
                    await context.bot.send_message(
                        chat_id=int(order_details['acceptedBy']),
                        text=f"ℹ️ Siz qabul qilgan buyurtma \\(ID: `{escape_markdown(order_id)}`\\) admin tomonidan bekor qilindi\\. Balansingizga 100 so'm qaytarildi\\.", # Escaped parentheses and period
                        parse_mode='MarkdownV2'
                    )

            except Exception as e:
                logger.error(f"Guruhdagi buyurtma xabarini bekor qilishda xato: {e}")
                await source_message.reply_text(escape_markdown("Buyurtma ro'yxatdan chiqarildi. Guruhdagi xabarni yangilashda xato yuz berdi."), parse_mode='MarkdownV2')
        else:
            await source_message.reply_text(escape_markdown("Buyurtma ro'yxatdan chiqarildi. Guruh xabari topilmadi."), parse_mode='MarkdownV2')


        await source_message.reply_text(f"Buyurtma `{escape_markdown(order_id)}` ro'yxatdan chiqarildi\\.", parse_mode='MarkdownV2') # Escaped period
        await context.bot.send_message(
            chat_id=order_details['customerId'],
            text=f"❌ Sizning buyurtmangiz \\(ID: `{escape_markdown(order_id)}`\\) admin tomonidan bekor qilindi\\.", # Escaped parentheses and period
            parse_mode='MarkdownV2'
        )
    except Exception as e:
        logger.error(f"Buyurtmani bekor qilishda xato: {e}")
        await source_message.reply_text(escape_markdown("Buyurtmani bekor qilishda xato yuz berdi. Iltimos, qayta urinib ko'ring."), parse_mode='MarkdownV2')

    # Menyuni yangilash (agar u CallbackQuery bo'lsa)
    if query:
        await admin_orders_menu(update, context) # Buyurtmalar menyusini qayta ko'rsatish


# Admin: Reklama yuborish
async def admin_send_ad_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_states[user_id] = STATE_ADMIN_SEND_AD
    try:
        await query.edit_message_text(escape_markdown("Yuboriladigan reklama matnini kiriting:"), parse_mode='MarkdownV2')
    except Exception as e:
        logger.error(f"Reklama yuborish start xabarini yuborishda xato: {e}")
        await query.message.reply_text(escape_markdown("Reklama yuborish jarayonini boshlashda xato yuz berdi."), parse_mode='MarkdownV2')

async def admin_handle_send_ad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    # Bu funksiya endi handle_message orqali chaqiriladi, shuning uchun holatni bu yerda tekshirish shart emas
    # if user_states.get(user_id) != STATE_ADMIN_SEND_AD:
    #     return

    ad_message = update.message.text.strip()
    if not ad_message:
        await update.message.reply_text(escape_markdown("Reklama matni bo'sh bo'lishi mumkin emas. Qayta kiriting:"), parse_mode='MarkdownV2')
        return

    data = await _load_db_data()
    all_users_to_notify = set()

    # Barcha buyurtma bergan mijozlar
    for order in data['orders']:
        all_users_to_notify.add(order['customerId'])

    # Barcha haydovchilar
    for driver_id in data['driver_profiles'].keys():
        all_users_to_notify.add(driver_id)

    # Adminlarni reklama ro'yxatidan chiqarish
    all_users_to_notify = [uid for uid in all_users_to_notify if uid not in ADMIN_TELEGRAM_IDS]

    sent_count = 0
    for target_user_id in all_users_to_notify:
        try:
            await context.bot.send_message(chat_id=int(target_user_id), text=f"\\*\\*📢 Reklama:\\*\\*\n\n{escape_markdown(ad_message)}", parse_mode='MarkdownV2') # Escaped asterisks
            sent_count += 1
        except Exception as e:
            logger.warning(f"Reklama yuborishda xato (foydalanuvchi {target_user_id}): {e}")
            # Bot foydalanuvchiga xabar yubora olmasa, ehtimol u botni bloklagan

    await update.message.reply_text(f"Reklama {escape_markdown(str(sent_count))} ta noyob foydalanuvchiga yuborildi\\.", parse_mode='MarkdownV2') # Escaped period
    user_states[user_id] = STATE_NONE
    # CallbackQuery obyektini yaratish, chunki admin_menu callback_query kutadi
    dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
    dummy_query.answer.return_value = None
    await admin_menu(dummy_query, context) # Admin menyusini qayta ko'rsatish


# Admin: Statistika (Yangi funksiya)
async def admin_show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not await is_admin(user_id):
        await query.edit_message_text(escape_markdown("Siz admin emassiz."), parse_mode='MarkdownV2')
        return

    db = await _load_db_data()

    # Calculate total unique users (customers + drivers)
    all_user_ids = set()
    for order in db.get("orders", []):
        all_user_ids.add(order['customerId'])
    for driver_id in db.get("drivers", []):
        all_user_ids.add(driver_id)
    total_users = len(all_user_ids)

    total_drivers = len(db.get("drivers", []))
    total_orders = len(db.get("orders", []))

    # Optional: filter today's orders
    from datetime import datetime
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    today_orders = [
        o for o in db.get("orders", [])
        if o.get("createdAt", "") >= today_start
    ]

    message = (
        "📊 \\*\\*Statistika\\*\\*\n\n" # Escaped asterisks
        f"👥 Jami foydalanuvchilar: \\*\\*{escape_markdown(str(total_users))}\\*\\*\n" # Escaped asterisks
        f"🚖 Jami haydovchilar: \\*\\*{escape_markdown(str(total_drivers))}\\*\\*\n" # Escaped asterisks
        f"📦 Jami buyurtmalar: \\*\\*{escape_markdown(str(total_orders))}\\*\\*\n" # Escaped asterisks
        f"📅 Bugungi buyurtmalar: \\*\\*{escape_markdown(str(len(today_orders)))}\\*\\*\n" # Escaped asterisks
    )

    await query.edit_message_text(message, parse_mode='MarkdownV2', reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_menu")]
    ]))


# --- Umumiy CallbackQuery ishlovchisi ---
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline tugmalardan kelgan so'rovlarni boshqaradi."""
    query = update.callback_query
    user_id = query.from_user.id
    callback_data = query.data
    await query.answer() # So'rovni darhol javob berish

    logger.info(f"Callback data received: {callback_data} from user {user_id}")

    # Asosiy menyular
    if callback_data == "back_to_main":
        await start(update, context)
    elif callback_data == "customer_menu":
        await customer_menu(update, context)
    elif callback_data == "driver_menu":
        await driver_menu(update, context)
    elif callback_data == "admin_menu":
        await admin_menu(update, context)

    # Mijoz funksiyalari
    elif callback_data == "new_order":
        await new_order(update, context)
    # from_region_, from_district_, to_region_, to_district_ endi alohida CallbackQueryHandlerlar orqali ishlanadi
    elif callback_data == "comment_none":
        await handle_comment(update, context, comment_text=None) # Izohsiz buyurtma

    # Haydovchi funksiyalari
    # accept_order_, return_order_, complete_order_ endi alohida CallbackQueryHandlerlar orqali ishlanadi
    elif callback_data == "my_accepted_orders":
        await my_accepted_orders(update, context)


    # Admin funksiyalari (Viloyat)
    elif callback_data == "admin_manage_regions":
        await admin_manage_regions(update, context)
    elif callback_data == "admin_add_region_start":
        await admin_add_region_start(update, context)
    elif callback_data == "admin_delete_region_start":
        await admin_delete_region_start(update, context)

    # Admin funksiyalari (Tuman)
    elif callback_data == "admin_manage_districts":
        await admin_manage_districts(update, context)
    elif callback_data == "admin_add_district_start":
        await admin_add_district_start(update, context)
    elif callback_data == "admin_delete_district_select_region_start":
        await admin_delete_district_select_region_start(update, context)

    # Admin funksiyalari (Marshrut)
    elif callback_data == "admin_manage_routes":
        await admin_manage_routes(update, context)
    elif callback_data == "admin_add_route_start":
        await admin_add_route_start(update, context)
    elif callback_data == "admin_delete_route_start":
        await admin_delete_route_start(update, context)
    elif callback_data == "admin_add_group_to_route_start":
        await admin_add_group_to_route_start(update, context)
    elif callback_data == "admin_disconnect_group_from_route_start":
        await admin_disconnect_group_from_route_start(update, context)


    # Admin funksiyalari (Haydovchilarni boshqarish)
    elif callback_data == "admin_manage_drivers":
        await admin_manage_drivers(update, context)
    elif callback_data == "admin_add_driver_start":
        await admin_add_driver_start(update, context)
    elif callback_data == "admin_remove_driver_start":
        await admin_remove_driver_start(update, context)
    elif callback_data == "admin_add_balance_start": # Balans to'ldirish
        await admin_add_balance_start(update, context)
    elif callback_data == "admin_add_subscription_start": # Obuna berish
        await admin_add_subscription_start(update, context)

    # Admin funksiyalari (Reklama yuborish)
    elif callback_data == "admin_send_ad_start":
        await admin_send_ad_start(update, context)

    # Admin funksiyalari (Statistika)
    elif callback_data == "admin_stats":
        await admin_show_statistics(update, context)

    # Agar hech qaysi holatga tushmasa, start buyrug'iga qaytish (yoki xato xabari)
    else:
        logger.warning(f"Unknown callback data: {callback_data}")
        if update.effective_message:
            await start(update, context)


# --- Deep Linking funksiyasi ---
async def deep_linking_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deep linking orqali kelgan start buyrug'ini boshqaradi."""
    payload = ""
    if context.args:
        payload = context.args[0]
        logger.info(f"Deep linking payload: {payload}")

    # Agar payload buyurtmani qabul qilishni bildirsa
    if payload.startswith('accept_order_'):
        order_id = payload.replace('accept_order_', '')
        # CallbackQuery ni simulyatsiya qilish
        dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
        dummy_query.answer.return_value = None # asyncio.Future() obyektiga return_value qo'shish
        await accept_order(dummy_query, context, order_id)
    # Agar payload buyurtmani qaytarishni bildirsa
    elif payload.startswith('return_order_'):
        order_id = payload.replace('return_order_', '')
        # CallbackQuery ni simulyatsiya qilish
        dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
        dummy_query.answer.return_value = None
        await return_order(dummy_query, context, order_id)
    # Agar payload buyurtmani bajarishni bildirsa
    elif payload.startswith('complete_order_'):
        order_id = payload.replace('complete_order_', '')
        # CallbackQuery ni simulyatsiya qilish
        dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
        dummy_query.answer.return_value = None
        await complete_order(dummy_query, context, order_id)
    # Admin uchun deep-linklar (agar kerak bo'lsa)
    elif payload.startswith('admin_add_balance_'):
        driver_id = payload.replace('admin_add_balance_', '')
        if await is_admin(update.effective_user.id):
            dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
            dummy_query.answer.return_value = None
            await admin_select_driver_for_balance(dummy_query, context, driver_id) # To'g'ridan-to'g'ri driver tanlashga o'tish
        else:
            await update.message.reply_text(escape_markdown("Sizda admin huquqlari yo'q."), parse_mode='MarkdownV2')
    elif payload.startswith('admin_add_sub_'):
        driver_id = payload.replace('admin_add_sub_', '')
        if await is_admin(update.effective_user.id):
            dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
            dummy_query.answer.return_value = None
            await admin_select_driver_for_subscription(dummy_query, context, driver_id) # To'g'ridan-to'g'ri driver tanlashga o'tish
        else:
            await update.message.reply_text(escape_markdown("Sizda admin huquqlari yo'q."), parse_mode='MarkdownV2')
    elif payload.startswith('admin_remove_driver_'):
        driver_id = payload.replace('admin_remove_driver_', '')
        if await is_admin(update.effective_user.id):
            dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
            dummy_query.answer.return_value = None
            await admin_remove_driver_callback(dummy_query, context, driver_id) # To'g'ridan-to'g'ri o'chirish
        else:
            await update.message.reply_text(escape_markdown("Sizda admin huquqlari yo'q."), parse_mode='MarkdownV2')
    elif payload.startswith('admin_cancel_order_'):
        order_id = payload.replace('admin_cancel_order_', '')
        if await is_admin(update.effective_user.id):
            dummy_query = type('obj', (object,), {'answer': (lambda: asyncio.Future()), 'from_user': update.effective_user, 'message': update.effective_message})()
            dummy_query.answer.return_value = None
            await admin_cancel_order(dummy_query, context, order_id)
        else:
            await update.message.reply_text(escape_markdown("Sizda admin huquqlari yo'q."), parse_mode='MarkdownV2')
    else:
        await start(update, context) # Normal start buyrug'iga qaytish

# --- Matnli xabarlar ishlovchisi ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Matnli xabarlarni holatga qarab boshqaradi."""
    user_id = update.effective_user.id
    current_state = user_states.get(user_id)

    if current_state == STATE_AWAITING_PHONE_NUMBER:
        await handle_phone_number(update, context)
    elif current_state == STATE_AWAITING_COMMENT:
        await handle_comment(update, context, comment_text=update.message.text.strip())
    elif current_state == STATE_ADMIN_ADD_REGION:
        await admin_handle_add_region(update, context)
    elif current_state == STATE_ADMIN_ADD_DISTRICT:
        await admin_handle_add_district(update, context)
    elif current_state == STATE_ADMIN_ADD_GROUP_ID:
        await admin_handle_add_group_id(update, context)
    elif current_state == STATE_ADMIN_ADD_GROUP_NAME:
        await admin_handle_add_group_name(update, context)
    elif current_state == STATE_ADMIN_ADD_DRIVER_ID:
        await admin_handle_add_driver_id(update, context)
    elif current_state == STATE_ADMIN_ADD_DRIVER_BALANCE_AMOUNT:
        await admin_handle_add_balance_amount(update, context)
    elif current_state == STATE_ADMIN_ADD_DRIVER_SUBSCRIPTION_DAYS:
        await admin_handle_add_subscription_days(update, context)
    elif current_state == STATE_ADMIN_SEND_AD:
        await admin_handle_send_ad(update, context)
    else:
        # Agar hech qaysi holatga tushmasa
        await update.message.reply_text(escape_markdown("Tushunmadim. Iltimos, menyudan foydalaning yoki /start buyrug'ini bosing."), parse_mode='MarkdownV2')


async def handle_non_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Matn bo'lmagan xabarlarni (rasm, video, fayl) boshqaradi."""
    user_id = update.effective_user.id
    current_state = user_states.get(user_id)

    if current_state == STATE_AWAITING_PHONE_NUMBER:
        await update.message.reply_text(escape_markdown("Iltimos, telefon raqamingizni matn shaklida kiriting."), parse_mode='MarkdownV2')
    elif current_state == STATE_AWAITING_COMMENT:
        # Agar foydalanuvchi izoh o'rniga rasm/video yuborsa, uni izohsiz deb qabul qilish
        await update.message.reply_text(escape_markdown("Siz rasm/video yubordingiz. Izohsiz buyurtma deb qabul qilindi."), parse_mode='MarkdownV2')
        await handle_comment(update, context, comment_text=None)
    elif current_state == STATE_ADMIN_ADD_GROUP_ID:
        await update.message.reply_text(escape_markdown("Iltimos, guruh ID'sini matn shaklida kiriting."), parse_mode='MarkdownV2')
    elif current_state == STATE_ADMIN_ADD_GROUP_NAME:
        await update.message.reply_text(escape_markdown("Iltimos, guruh nomini matn shaklida kiriting."), parse_mode='MarkdownV2')
    elif current_state == STATE_ADMIN_ADD_REGION:
        await update.message.reply_text(escape_markdown("Iltimos, viloyat nomini matn shaklida kiriting."), parse_mode='MarkdownV2')
    elif current_state == STATE_ADMIN_ADD_DISTRICT:
        await update.message.reply_text(escape_markdown("Iltimos, tuman nomini matn shaklida kiriting."), parse_mode='MarkdownV2')
    elif current_state == STATE_ADMIN_ADD_DRIVER_ID:
        await update.message.reply_text(escape_markdown("Iltimos, haydovchi ID'sini matn shaklida kiriting."), parse_mode='MarkdownV2')
    elif current_state == STATE_ADMIN_ADD_DRIVER_BALANCE_AMOUNT:
        await update.message.reply_text(escape_markdown("Iltimos, balans miqdorini matn shaklida kiriting."), parse_mode='MarkdownV2')
    elif current_state == STATE_ADMIN_ADD_DRIVER_SUBSCRIPTION_DAYS:
        await update.message.reply_text(escape_markdown("Iltimos, kunlar sonini matn shaklida kiriting."), parse_mode='MarkdownV2')
    elif current_state == STATE_ADMIN_SEND_AD:
        await update.message.reply_text(escape_markdown("Iltimos, reklama matnini yuboring."), parse_mode='MarkdownV2')
    else:
        # Boshqa holatlarda, shunchaki xabar berish
        await update.message.reply_text(escape_markdown("Kechirasiz, men hozircha matnli xabarlardan tashqari boshqa turdagi xabarlarni qabul qila olmayman. Iltimos, faqat matnli xabar yuboring."), parse_mode='MarkdownV2')


# --- Asosiy funksiya ---
def main() -> None:
    """Botni ishga tushiradi."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Buyruq ishlovchilari
    application.add_handler(CommandHandler("start", deep_linking_start))
    application.add_handler(CommandHandler("help", help_command))

    # CallbackQuery ishlovchilari (umumiy handler birinchi bo'lishi kerak)
    application.add_handler(CallbackQueryHandler(button)) # Umumiy tugma ishlovchisi

    # Mijoz callbacklari (pattern bilan)
    application.add_handler(CallbackQueryHandler(select_from_region, pattern=r"^from_region_"))
    application.add_handler(CallbackQueryHandler(select_from_district, pattern=r"^from_district_"))
    application.add_handler(CallbackQueryHandler(select_to_region, pattern=r"^to_region_"))
    application.add_handler(CallbackQueryHandler(select_to_district, pattern=r"^to_district_"))

    # Haydovchi callbacklari (pattern bilan)
    application.add_handler(CallbackQueryHandler(accept_order, pattern=r"^accept_order_"))
    application.add_handler(CallbackQueryHandler(return_order, pattern=r"^return_order_"))
    application.add_handler(CallbackQueryHandler(complete_order, pattern=r"^complete_order_"))

    # Admin callbacklari (pattern bilan)
    application.add_handler(CallbackQueryHandler(admin_delete_region_callback, pattern=r"^delete_region_"))
    application.add_handler(CallbackQueryHandler(admin_select_region_for_district_callback, pattern=r"^select_region_for_district_"))
    application.add_handler(CallbackQueryHandler(admin_delete_district_select_district_start, pattern=r"^delete_district_from_region_"))
    application.add_handler(CallbackQueryHandler(admin_delete_district_callback, pattern=r"^delete_district_"))
    application.add_handler(CallbackQueryHandler(admin_add_route_from_region_callback, pattern=r"^add_route_from_region_"))
    application.add_handler(CallbackQueryHandler(admin_add_route_from_district_callback, pattern=r"^add_route_from_district_"))
    application.add_handler(CallbackQueryHandler(admin_add_route_to_region_callback, pattern=r"^add_route_to_region_"))
    application.add_handler(CallbackQueryHandler(admin_add_route_to_district_callback, pattern=r"^add_route_to_district_"))
    application.add_handler(CallbackQueryHandler(admin_delete_route_callback, pattern=r"^delete_route_"))
    application.add_handler(CallbackQueryHandler(admin_select_route_for_group_callback, pattern=r"^admin_select_route_")) # Guruhni marshrutga ulash
    # admin_disconnect_group_from_route_start is handled by the general `button` handler
    application.add_handler(CallbackQueryHandler(admin_disconnect_group_select_route_callback, pattern=r"^disconnect_group_select_route_")) # Guruhni marshrutdan uzish (marshrut tanlash)
    application.add_handler(CallbackQueryHandler(admin_disconnect_group_from_route_callback, pattern=r"^disconnect_group_from_route_")) # Guruhni marshrutdan uzish (guruh tanlash)
    application.add_handler(CallbackQueryHandler(admin_remove_driver_callback, pattern=r"^remove_driver_"))
    application.add_handler(CallbackQueryHandler(admin_select_driver_for_balance, pattern=r"^select_driver_balance_"))
    application.add_handler(CallbackQueryHandler(admin_select_driver_for_subscription, pattern=r"^select_driver_subscription_"))
    application.add_handler(CallbackQueryHandler(admin_show_statistics, pattern=r"^admin_stats$")) # Statistika

    # Matnli xabarlar ishlovchisi (holatlarni boshqarish uchun)
    # Bu handler COMMANDS va boshqa maxsus filterlardan keyin turishi kerak.
    # Endi `filters.User(user_states.keys())` dinamik bo'lmagani uchun olib tashlandi.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_message))

    # Boshqa xabar turlari (rasmlar, videolar va h.k.) uchun xabarni qayta ishlash
    application.add_handler(MessageHandler(filters.ALL & ~filters.TEXT & ~filters.COMMAND, handle_non_text_message))


    logger.info("Bot ishga tushmoqda...")
    # Botni polling rejimida ishga tushirish
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
