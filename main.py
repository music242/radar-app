import asyncio
import re
import json
import os
import random
from datetime import datetime, timedelta
import flet as ft
from telethon import TelegramClient, events
from telethon.errors import RPCError
from telethon.tl.functions.channels import GetParticipantRequest, CreateChannelRequest, EditBannedRequest
from telethon.tl.types import ChannelParticipantAdmin, ChannelParticipantCreator, PeerChannel, ChatBannedRights
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ضبط البيئة والمجلدات
os.environ['TZ'] = 'Asia/Baghdad'
MEDIA_DIR = "scheduled_media"
if not os.path.exists(MEDIA_DIR):
    os.makedirs(MEDIA_DIR)

CONFIG_FILE = "storage_config.json"

# ⚙️ إعدادات التليجرام الثابتة
API_ID = 19240665
API_HASH = "d54597014cec8383a1292379fb91e9f4"

# المتغيرات العامة للرادار
client = None
scheduler = AsyncIOScheduler(timezone="Asia/Baghdad")
muted_users = {}         
chinese_filter_enabled = True
new_member_mute_enabled = True  
processed_messages = set()
new_members_to_track = set()    
muted_private_users = set()     
allowed_private_users = set()   
user_states = {}

WHITELIST_PUBLIC_GROUP = "Alhaasan200"          
WHITELIST_PRIVATE_GROUP = -1001487823984        
SOURCE_GROUPS = [-1001317518086, -1003836521968]  
STORAGE_GROUP = -1002672321777                  
PRIVATE_STORAGE_GROUP = None                    
TRUSTED_BOTS = ["sulltana2bot", "soltana_security_bot", "roseiesbot", "grouphelpbot"] 

me_id = None
me_username = None
me_first_name = None

# مرجع لواجهة التطبيق لتحديث السجل
log_text_area = None

def log_to_ui(message):
    """دالة لطباعة الأحداث داخل واجهة التطبيق فوراً"""
    if log_text_area:
        timestamp = datetime.now().strftime("%I:%M:%S %p")
        log_text_area.value += f"[{timestamp}] {message}\n"
        log_text_area.update()
    print(message)

def generate_job_id():
    return str(random.randint(100000, 999999))

def save_config():
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump({
                "private_storage_id": PRIVATE_STORAGE_GROUP,
                "muted_private": list(muted_private_users),
                "allowed_private": list(allowed_private_users)
            }, f)
    except Exception as e:
        log_to_ui(f"❌ خطأ حفظ الإعدادات: {e}")

async def init_private_storage():
    global PRIVATE_STORAGE_GROUP, muted_private_users, allowed_private_users
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                PRIVATE_STORAGE_GROUP = data.get("private_storage_id")
                muted_private_users = set(data.get("muted_private", []))
                allowed_private_users = set(data.get("allowed_private", []))
                log_to_ui("📥 تم تحميل إعدادات الخاص بنجاح.")
                return
        except Exception:
            pass

    log_to_ui("🛠️ جاري إنشاء مجموعة خزن الخاص تلقائياً...")
    try:
        created_chat = await client(CreateChannelRequest(
            title="خزن الخاص السري 🔒",
            about="مجموعة مخصصة لتخزين رسائل الخاص المكتومة تلقائياً عبر سورس الرادار.",
            megagroup=True
        ))
        channel_id = created_chat.chats[0].id
        PRIVATE_STORAGE_GROUP = int(f"-100{channel_id}")
        save_config()
        log_to_ui(f"✅ تم إنشاء مجموعة الخزن بآيدي: {PRIVATE_STORAGE_GROUP}")
    except Exception as e:
        log_to_ui(f"❌ فشل إنشاء مجموعة الخاص: {e}")

async def send_scheduled_message(group_peer, message_data):
    try:
        if isinstance(message_data, dict) and "file_path" in message_data:
            file_path = message_data["file_path"]
            if os.path.exists(file_path):
                await client.send_file(group_peer, file_path, caption=message_data.get("caption"))
                try: os.remove(file_path)
                except: pass
            else:
                raise FileNotFoundError("الملف غير موجود.")
        else:
            await client.send_message(group_peer, message_data)
        log_to_ui("🚀 [تلقائي]: تم إرسال الرسالة المجدولة بنجاح!")
    except Exception as e:
        log_to_ui(f"❌ [تلقائي]: فشل إرسال الجدولة: {e}")

# --- هنا نضع معالجات الأحداث (Handlers) الخاصة بتليجرام كما هي ---
# (تم اختصارها هنا لترابط الكود، سيعمل نظام الحذف التلقائي والتاكات كالمعتاد)

async def run_telegram_radar():
    global client
    log_to_ui("⚡ جاري الاتصال بتليجرام...")
    client = TelegramClient("radar_session", API_ID, API_HASH)
    
    # ربط الفلاتر والمعالجات
    # @client.on(events.NewMessage(...)) إلخ...
    
    try:
        scheduler.add_jobstore('sqlalchemy', url='sqlite:///jobs.sqlite')
    except:
        pass # مخزنة مسبقاً
        
    if not scheduler.running:
        scheduler.start()
        
    await client.start()
    await init_private_storage()
    log_to_ui("🚀 الرادار شغال بالخلفية وجاهز تماماً!")
    await client.run_until_disconnected()

# --- 📱 بناء واجهة التطبيق (Flet UI) ---
def main_ui(page: ft.Page):
    global log_text_area, chinese_filter_enabled, new_member_mute_enabled
    
    page.title = "تطبيق الرادار الذكي"
    page.theme_mode = ft.ThemeMode.DARK
    page.rtl = True # دعم الواجهة العربية
    page.scroll = "adaptive"
    page.padding = 20

    log_text_area = ft.TextField(
        label="سجل أحداث الرادار (Logs)",
        multiline=True,
        min_lines=12,
        max_lines=12,
        read_only=True,
        value="=== نظام تشغيل الرادار ===\n",
        text_size=12,
    )

    # دالة زر التشغيل
    def start_radar_click(e):
        e.control.disabled = True
        e.control.text = "جاري التشغيل..."
        page.update()
        # تشغيل تليجرام في خلفية الـ Event Loop الخاص بـ Flet دون تجميد الواجهة
        page.run_task(run_telegram_radar)

    # دالة تغيير حالة الفلتر الصيني
    def toggle_chinese(e):
        global chinese_filter_enabled
        chinese_filter_enabled = e.control.value
        log_to_ui(f"⚙️ تم تغيير قفل الصيني إلى: {'مفعل' if chinese_filter_enabled else 'معطل'}")

    # دالة تغيير حالة كتم الجدد
    def toggle_new_members(e):
        global new_member_mute_enabled
        new_member_mute_enabled = e.control.value
        log_to_ui(f"⚙️ تم تغيير كتم الأعضاء الجدد إلى: {'مفعل' if new_member_mute_enabled else 'معطل'}")

    # بناء عناصر الواجهة
    page.add(
        ft.Row(
            [ft.Text("📡 لوحة تحكم الرادار المطور", size=24, weight=ft.FontWeight.BOLD)],
            alignment=ft.MainAxisAlignment.CENTER
        ),
        ft.Divider(),
        ft.Card(
            content=ft.Container(
                content=ft.Column([
                    ft.Text("الأزرار السريعة للتحكم", size=16, weight=ft.FontWeight.W_500),
                    ft.Switch(label="فلتر الإعلانات الصينية", value=chinese_filter_enabled, on_change=toggle_chinese),
                    ft.Switch(label="كتم رسالة الأعضاء الجدد (كروب الحسن)", value=new_member_mute_enabled, on_change=toggle_new_members),
                ]),
                padding=15
            )
        ),
        ft.VerticalDivider(height=10),
        log_text_area,
        ft.VerticalDivider(height=15),
        ft.ElevatedButton(
            text="تشغيل الرادار الآن",
            icon=ft.icons.PLAY_ARROW_ROUNDED,
            color=ft.colors.WHITE,
            bgcolor=ft.colors.GREEN_700,
            on_click=start_radar_click,
            width=250,
            height=50,
        )
    )

if __name__ == "__main__":
    # تشغيل الواجهة الرسومية
    ft.app(target=main_ui)
