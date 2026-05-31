import inspect
import json
import threading
from collections import deque
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app import bot
from app import database

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
TEMPLATE_EXPLICIT_REQUEST = "request" in inspect.signature(templates.TemplateResponse).parameters

bot_thread = None


def get_account_count(accounts):
    return len([a for a in accounts if (a.get("gologin_profile_id") or "").strip()])


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    stats = database.get_stats()
    is_running = bot.bot_running if bot_thread and bot_thread.is_alive() else False
    total_targets, remaining_targets = database.get_target_counts()
    active_browsers = 1 if is_running else 0
    client_ip = request.client.host if request.client else "LOCALHOST"
    accounts = database.get_accounts()
    account_count = get_account_count(accounts)
    pending_targets, sent_targets = database.get_targets_split()
    sent_targets_with_accounts = database.get_sent_targets_with_accounts()
    message_delay_min_seconds = database.get_setting("MESSAGE_DELAY_MIN_SECONDS", "").strip()
    message_delay_max_seconds = database.get_setting("MESSAGE_DELAY_MAX_SECONDS", "").strip()
    if not message_delay_min_seconds and not message_delay_max_seconds:
        legacy_delay = database.get_setting("MESSAGE_DELAY_SECONDS", "10")
        message_delay_min_seconds = legacy_delay
        message_delay_max_seconds = legacy_delay
    else:
        if not message_delay_min_seconds:
            message_delay_min_seconds = message_delay_max_seconds or "10"
        if not message_delay_max_seconds:
            message_delay_max_seconds = message_delay_min_seconds or "10"

    scroll_min_seconds = database.get_setting("SCROLL_MIN_SECONDS", "5").strip()
    scroll_max_seconds = database.get_setting("SCROLL_MAX_SECONDS", "10").strip()
    if not scroll_min_seconds and not scroll_max_seconds:
        scroll_min_seconds = "5"
        scroll_max_seconds = "10"
    else:
        if not scroll_min_seconds:
            scroll_min_seconds = scroll_max_seconds or "5"
        if not scroll_max_seconds:
            scroll_max_seconds = scroll_min_seconds or "10"
    messages_per_account = database.get_setting("MESSAGES_PER_ACCOUNT", "15")
    spintax_message = database.get_message_template("SPINTAX_MESSAGE", "")

    context = {
        "request": request,
        "stats": stats,
        "is_running": is_running,
        "total_targets": total_targets,
        "remaining_targets": remaining_targets,
        "account_count": account_count,
        "active_browsers": active_browsers,
        "client_ip": client_ip,
        "accounts": accounts,
        "pending_targets": pending_targets,
        "sent_targets": sent_targets,
        "sent_targets_with_accounts": sent_targets_with_accounts,
        "sent_targets_json": json.dumps(sent_targets),
        "message_delay_min_seconds": message_delay_min_seconds,
        "message_delay_max_seconds": message_delay_max_seconds,
        "messages_per_account": messages_per_account,
        "scroll_min_seconds": scroll_min_seconds,
        "scroll_max_seconds": scroll_max_seconds,
        "spintax_message": spintax_message,
    }
    if TEMPLATE_EXPLICIT_REQUEST:
        return templates.TemplateResponse(request, "index.html", context)
    return templates.TemplateResponse("index.html", context)


@app.get("/logs", response_class=PlainTextResponse)
async def get_logs(lines: int = 200):
    line_limit = min(max(lines, 1), 1000)
    try:
        with open(bot.LOG_FILE, "r", encoding="utf-8", errors="replace") as handle:
            tail_lines = deque(handle, maxlen=line_limit)
        return PlainTextResponse("".join(tail_lines), headers={"Cache-Control": "no-store"})
    except FileNotFoundError:
        return PlainTextResponse("")
    except Exception:
        return PlainTextResponse("")


@app.post("/logs/clear", response_class=PlainTextResponse)
async def clear_logs():
    try:
        with bot.LOG_LOCK:
            with open(bot.LOG_FILE, "w", encoding="utf-8") as handle:
                handle.write("")
        return PlainTextResponse("OK", headers={"Cache-Control": "no-store"})
    except Exception:
        return PlainTextResponse("", status_code=500)

@app.post("/start")
async def start_bot():
    global bot_thread
    if not bot_thread or not bot_thread.is_alive():
        bot.bot_running = True
        bot_thread = threading.Thread(target=bot.run_bot)
        bot_thread.start()
    return RedirectResponse(url="/", status_code=303)

@app.post("/stop")
async def stop_bot():
    bot.stop_bot()
    return RedirectResponse(url="/", status_code=303)

@app.post("/save_settings")
async def save_settings(request: Request):
    form_data = await request.form()
    allowed_settings = {
        "MESSAGE_DELAY_SECONDS",
        "MESSAGE_DELAY_MIN_SECONDS",
        "MESSAGE_DELAY_MAX_SECONDS",
        "SCROLL_MIN_SECONDS",
        "SCROLL_MAX_SECONDS",
        "MESSAGES_PER_ACCOUNT",
    }
    for key, value in form_data.items():
        if isinstance(value, str):
            value = value.replace("\r", "")
        if key == "SPINTAX_MESSAGE":
            database.set_message_template(key, value)
            continue
        if key in allowed_settings:
            database.set_setting(key, value)
    return RedirectResponse(url="/", status_code=303)


@app.post("/accounts/save_all")
async def save_all_accounts(request: Request):
    form_data = await request.form()
    account_ids = form_data.getlist("account_id")
    names = form_data.getlist("account_name")
    cookies_list = form_data.getlist("account_cookies")
    profile_ids = form_data.getlist("account_profile_id")

    row_count = max(len(account_ids), len(names), len(cookies_list), len(profile_ids))
    for index in range(row_count):
        account_id = account_ids[index] if index < len(account_ids) else ""
        name = names[index].strip() if index < len(names) else ""
        cookies_text = cookies_list[index].strip() if index < len(cookies_list) else ""
        profile_id = profile_ids[index].strip() if index < len(profile_ids) else ""
        if not name or not cookies_text:
            continue
        if account_id:
            database.update_account(account_id, name, cookies_text, profile_id)
        else:
            database.add_account(name, cookies_text, profile_id)

    return RedirectResponse(url="/", status_code=303)


@app.post("/accounts/delete")
async def delete_account(request: Request):
    form_data = await request.form()
    account_id = (form_data.get("account_id") or "").strip()
    if account_id:
        database.delete_account(account_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/targets/save_all")
async def save_all_targets(request: Request):
    form_data = await request.form()
    urls = form_data.getlist("target_url")
    database.save_targets(urls)

    return RedirectResponse(url="/", status_code=303)
