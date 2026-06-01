import datetime
import json
import os
import random
import re
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import requests
from dotenv import load_dotenv
from gologin import GoLogin
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import WebDriverException

from app import database
from app.database import log_message_sent, log_account_message

load_dotenv()

bot_running = True
LOG_FILE = os.path.join("data", "bot.log")
LOG_LOCK = threading.Lock()
SESSION_LOCK = threading.Lock()
CURRENT_DRIVER = None
CURRENT_GLOGIN = None


def log(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    try:
        with LOG_LOCK:
            with open(LOG_FILE, "a", encoding="utf-8") as log_file:
                log_file.write(line + "\n")
    except Exception:
        pass


def set_active_session(gl, driver):
    global CURRENT_GLOGIN, CURRENT_DRIVER
    with SESSION_LOCK:
        CURRENT_GLOGIN = gl
        CURRENT_DRIVER = driver


def clear_active_session():
    global CURRENT_GLOGIN, CURRENT_DRIVER
    with SESSION_LOCK:
        CURRENT_GLOGIN = None
        CURRENT_DRIVER = None


def close_active_session():
    global CURRENT_GLOGIN, CURRENT_DRIVER
    with SESSION_LOCK:
        gl = CURRENT_GLOGIN
        driver = CURRENT_DRIVER
        CURRENT_GLOGIN = None
        CURRENT_DRIVER = None
    if driver:
        try:
            driver.quit()
        except Exception as e:
            log(f"⚠️ Failed to close browser: {e}")
    if gl:
        try:
            gl.stop()
        except Exception as e:
            log(f"⚠️ Failed to stop GoLogin profile: {e}")

def simulate_human_browsing(driver, min_seconds=5, max_seconds=10):
    min_seconds = max(0, min_seconds)
    max_seconds = max(0, max_seconds)
    if max_seconds < min_seconds:
        min_seconds, max_seconds = max_seconds, min_seconds
    duration = random.uniform(min_seconds, max_seconds) if max_seconds > min_seconds else min_seconds
    log(f"Emulating human browsing for {duration:.1f} seconds (scrolling)...")
    end_time = time.time() + duration
    while time.time() < end_time:
        if not bot_running:
            log("🛑 Stop requested during scrolling.")
            return False
        scroll_by = random.randint(100, 600)
        direction = random.choice([1, 1, 1, -1])  # Mostly scroll down
        try:
            driver.execute_script("window.scrollBy(0, arguments[0]);", scroll_by * direction)
        except WebDriverException as exc:
            log(f"⚠️ Scrolling interrupted: {exc}")
            return False
        except Exception as exc:
            log(f"⚠️ Scrolling error: {exc}")
            return False
        time.sleep(random.uniform(0.5, 2.0))
    return True

def resolve_spintax(text):
    while True:
        match = re.search(r'\{([^{}]*)\}', text)
        if not match:
            break
        choices = match.group(1).split('|')
        replacement = random.choice(choices)
        text = text[:match.start()] + replacement + text[match.end():]
    return text

def human_typing(driver, element, text):
    for char in text:
        if ord(char) > 0xFFFF:  # Handle emojis and characters outside the BMP
            driver.execute_script(
                "arguments[0].value += arguments[1];"
                "arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", 
                element, char
            )
        else:
            element.send_keys(char)
        time.sleep(random.uniform(0.02, 0.1))

def set_text_value(driver, element, text):
    driver.execute_script(
        "arguments[0].value = arguments[1];"
        "arguments[0].dispatchEvent(new Event('input', { bubbles: true }));"
        "arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
        element,
        text,
    )


def ensure_full_message(driver, element, message, max_attempts=2):
    for _ in range(max_attempts):
        current = element.get_attribute("value") or ""
        if current == message:
            return True
        set_text_value(driver, element, message)
        time.sleep(0.2)
    current = element.get_attribute("value") or ""
    if current != message:
        log(
            "⚠️ Message length mismatch after retries: "
            f"expected {len(message)}, got {len(current)}."
        )
        return False
    return True


def get_int_setting(name, default):
    raw_value = database.get_setting(name, "")
    if raw_value is None or str(raw_value).strip() == "":
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def get_range_setting(min_key, max_key, default_min, default_max, legacy_key=None):
    min_raw = (database.get_setting(min_key, "") or "").strip()
    max_raw = (database.get_setting(max_key, "") or "").strip()

    if not min_raw and not max_raw and legacy_key:
        legacy_value = get_int_setting(legacy_key, default_min)
        min_value = legacy_value
        max_value = legacy_value
    else:
        min_value = get_int_setting(min_key, default_min)
        max_value = get_int_setting(max_key, default_max)
        if not min_raw and max_raw:
            min_value = max_value
        if not max_raw and min_raw:
            max_value = min_value

    min_value = max(0, min_value)
    max_value = max(0, max_value)
    if max_value < min_value:
        min_value, max_value = max_value, min_value
    return min_value, max_value


def find_gologin_chrome_version():
    possible_paths = [
        os.path.expandvars(r"%LOCALAPPDATA%\gologin\chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\GoLogin\chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\GoLogin\chrome\Application\chrome.exe"),
        os.path.expandvars(r"%APPDATA%\gologin\chrome\Application\chrome.exe"),
    ]

    for chrome_path in possible_paths:
        if Path(chrome_path).exists():
            try:
                result = subprocess.run(
                    [chrome_path, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                match = re.search(r"(\d+\.\d+\.\d+\.\d+)", result.stdout)
                if match:
                    return match.group(1)
            except Exception:
                pass

    return "147.0.7727.137"


def download_chromedriver(version):
    chrome_driver_dir = Path.home() / ".wdm" / "drivers" / "chromedriver" / "win64" / version
    chrome_driver_dir.mkdir(parents=True, exist_ok=True)

    chromedriver_path = chrome_driver_dir / "chromedriver.exe"
    if chromedriver_path.exists():
        return str(chromedriver_path)

    url = (
        "https://storage.googleapis.com/chrome-for-testing-public/"
        f"{version}/win32/chromedriver-win32.zip"
    )

    response = requests.get(url, stream=True)
    response.raise_for_status()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
        for chunk in response.iter_content(chunk_size=8192):
            tmp_file.write(chunk)
        temp_zip = tmp_file.name

    with zipfile.ZipFile(temp_zip, "r") as zip_ref:
        zip_ref.extractall(chrome_driver_dir)

    os.unlink(temp_zip)

    extracted_dir = chrome_driver_dir / "chromedriver-win32"
    if extracted_dir.exists():
        for file in extracted_dir.iterdir():
            target = chrome_driver_dir / file.name
            if target.exists():
                target.unlink()
            file.rename(target)
        extracted_dir.rmdir()

    if chromedriver_path.exists():
        return str(chromedriver_path)

    return None

def init_gologin_session(profile_id, token, chrome_version, cookies_json=None):
    gl = GoLogin({
        "token": token,
        "profile_id": profile_id if profile_id else None,
    })

    chromedriver_path = download_chromedriver(chrome_version)
    if not chromedriver_path:
        raise RuntimeError("ChromeDriver not available")

    debugger_address = gl.start()

    options = Options()
    options.add_experimental_option("debuggerAddress", debugger_address)

    service = Service(executable_path=chromedriver_path)
    driver = webdriver.Chrome(service=service, options=options)
    driver.get("https://www.pornhub.com/")
    
    # Load and inject cookies
    cookies = None
    if cookies_json and cookies_json.strip():
        try:
            cookies = json.loads(cookies_json)
        except Exception as e:
            log(f"❌ Failed to parse cookies for profile {profile_id}: {e}")
    if cookies is None:
        cookies = []
    try:
        for cookie in cookies:
            cookie_dict = {
                'name': cookie['name'],
                'value': cookie['value'],
                'domain': cookie.get('domain', ''),
                'path': cookie.get('path', '/')
            }
            if 'expirationDate' in cookie:
                cookie_dict['expiry'] = int(cookie['expirationDate'])
            if 'secure' in cookie:
                cookie_dict['secure'] = cookie['secure']
            if 'httpOnly' in cookie:
                cookie_dict['httpOnly'] = cookie['httpOnly']
            
            if 'sameSite' in cookie and cookie['sameSite']:
                if cookie['sameSite'] == 'no_restriction':
                    cookie_dict['sameSite'] = 'None'
                else:
                    cookie_dict['sameSite'] = str(cookie['sameSite']).capitalize()
            
            driver.add_cookie(cookie_dict)
        if cookies:
            log("✅ Cookies injected successfully")
            # Refresh the page to apply the cookies
            driver.refresh()
    except Exception as e:
        log(f"❌ Failed to inject cookies: {e}")

    time.sleep(3)  # Wait briefly for the modal to render
    js_snippet = """
    const buttons = document.querySelectorAll('button');
    for (let btn of buttons) {
        if (btn.textContent.includes('18 jaar of ouder') || 
            btn.textContent.includes('Doorgaan') ||
            btn.textContent.includes('18 years or older') ||
            btn.textContent.includes('Continue') ||
            btn.textContent.includes('I am 18') ||
            btn.textContent.includes('Enter')) {
            btn.click();
            console.log('✅ Clicked button found by text content');
            break;
        }
    }
    """
    driver.execute_script(js_snippet)
    return gl, driver

def run_bot():
    global bot_running
    bot_running = True
    
    token = os.getenv("GOLOGIN_TOKEN")
    chrome_version = find_gologin_chrome_version()

    accounts = sorted(database.get_accounts(), key=lambda account: account.get("id") or 0)
    account_profiles = []
    for account in accounts:
        profile_id = (account.get("gologin_profile_id") or "").strip()
        cookies_json = (account.get("cookies_json") or "").strip()
        if not profile_id:
            continue
        if not cookies_json:
            account_label = account.get("name") or account.get("id")
            log(f"WARNING: Skipping account {account_label} (missing cookies).")
            continue
        account_profiles.append({
            "account_id": account.get("id"),
            "profile_id": profile_id,
            "cookies_json": cookies_json,
            "name": account.get("name") or "",
        })

    if not account_profiles:
        log("❌ No GoLogin profile IDs configured in Accounts.")
        return

    messages_per_account = get_int_setting("MESSAGES_PER_ACCOUNT", 15)
    messages_per_account = max(1, messages_per_account)
    message_delay_min_seconds, message_delay_max_seconds = get_range_setting(
        "MESSAGE_DELAY_MIN_SECONDS",
        "MESSAGE_DELAY_MAX_SECONDS",
        10,
        10,
        legacy_key="MESSAGE_DELAY_SECONDS",
    )
    scroll_min_seconds, scroll_max_seconds = get_range_setting(
        "SCROLL_MIN_SECONDS",
        "SCROLL_MAX_SECONDS",
        5,
        10,
    )

    spintax_template = database.get_message_template("SPINTAX_MESSAGE", "").strip()
    if not spintax_template:
        log("⚠️ No spintax message configured.")
    messages_sent_per_account = {
        account["profile_id"]: 0 for account in account_profiles
    }
    unavailable_profiles = set()

    def mark_profile_unavailable(profile_id, reason=None):
        if not profile_id or profile_id in unavailable_profiles:
            return
        unavailable_profiles.add(profile_id)
        if reason:
            log(reason)

    def is_account_available(account):
        profile_id = account["profile_id"]
        return (
            profile_id not in unavailable_profiles
            and messages_sent_per_account[profile_id] < messages_per_account
        )

    def has_available_accounts():
        return any(is_account_available(account) for account in account_profiles)

    def pick_random_account_index(exclude_index=None):
        if not account_profiles:
            return None
        available = [
            idx
            for idx, account in enumerate(account_profiles)
            if is_account_available(account)
        ]
        if exclude_index is not None and len(available) > 1 and exclude_index in available:
            available.remove(exclude_index)
        return random.choice(available) if available else None

    current_gl_index = None
    gl = None
    driver = None

    def start_new_session():
        nonlocal current_gl_index, gl, driver
        if not bot_running:
            return False
        attempts = len(account_profiles)
        while attempts > 0:
            next_index = pick_random_account_index()
            if next_index is None:
                break
            current_gl_index = next_index
            next_account = account_profiles[current_gl_index]
            profile_id = next_account["profile_id"]
            log(f"🚀 Starting GoLogin profile: {profile_id}")
            try:
                gl, driver = init_gologin_session(
                    profile_id,
                    token,
                    chrome_version,
                    next_account["cookies_json"],
                )
                set_active_session(gl, driver)
                return True
            except Exception as e:
                mark_profile_unavailable(
                    profile_id,
                    f"⚠️ Skipping profile {profile_id} due to error: {e}",
                )
                attempts -= 1
                continue
        log("🕒 No available accounts to start.")
        return False

    def shutdown_current_session(reason=None):
        nonlocal gl, driver
        if reason:
            log(reason)
        if driver:
            try:
                if current_gl_index is not None:
                    current_profile_id = account_profiles[current_gl_index]["profile_id"]
                    current_cookies = driver.get_cookies()
                    database.update_account_cookies_by_profile_id(
                        current_profile_id,
                        json.dumps(current_cookies),
                    )
            except Exception as e:
                log(f"❌ Failed to save cookies for profile {current_profile_id}: {e}")
            try:
                driver.quit()
            except Exception:
                pass
        if gl:
            try:
                gl.stop()
            except Exception:
                pass
        gl = None
        driver = None
        clear_active_session()

    def switch_account():
        nonlocal current_gl_index, gl, driver
        if not bot_running:
            return False
        if driver is None or gl is None or current_gl_index is None:
            return start_new_session()
        log("🔄 Switching to another GoLogin profile.")
        try:
            current_profile_id = account_profiles[current_gl_index]["profile_id"]
            current_cookies = driver.get_cookies()
            database.update_account_cookies_by_profile_id(
                current_profile_id,
                json.dumps(current_cookies),
            )
        except Exception as e:
            log(f"❌ Failed to save cookies for profile {current_profile_id}: {e}")
        try:
            driver.quit()
        except Exception:
            pass
        try:
            gl.stop()
        except Exception:
            pass
        return start_new_session()

    def restart_current_session():
        nonlocal gl, driver
        if not bot_running:
            return False
        if driver is None or gl is None or current_gl_index is None:
            return start_new_session()
        current_profile_id = ""
        try:
            current_profile_id = account_profiles[current_gl_index]["profile_id"]
            current_cookies = driver.get_cookies()
            database.update_account_cookies_by_profile_id(
                current_profile_id,
                json.dumps(current_cookies),
            )
        except Exception as e:
            log(f"❌ Failed to save cookies for profile {current_profile_id}: {e}")
        try:
            driver.quit()
        except Exception:
            pass
        try:
            gl.stop()
        except Exception:
            pass
        try:
            account = account_profiles[current_gl_index]
            log(f"🔄 Restarting GoLogin profile: {account['profile_id']}")
            gl, driver = init_gologin_session(
                account["profile_id"],
                token,
                chrome_version,
                account["cookies_json"],
            )
            set_active_session(gl, driver)
            return True
        except Exception as e:
            log(f"❌ Failed to restart GoLogin profile: {e}")
            return False

    def ensure_active_account():
        while True:
            if not bot_running:
                return False
            if not has_available_accounts():
                log("🛑 All accounts reached the DM limit. Stopping bot.")
                shutdown_current_session()
                stop_bot()
                return False
            if driver is None or gl is None or current_gl_index is None:
                if not start_new_session():
                    log("🛑 No available accounts to start. Waiting for manual restart.")
                    return False
            current_profile_id = account_profiles[current_gl_index]["profile_id"]
            if current_profile_id in unavailable_profiles:
                log("⚠️ Current profile marked unavailable. Switching.")
                if not switch_account():
                    log("🛑 No available accounts to switch. Stopping bot.")
                    shutdown_current_session()
                    stop_bot()
                    return False
                continue
            if messages_sent_per_account[current_profile_id] >= messages_per_account:
                log(f"🔄 Reached max DMs ({messages_per_account}) for current GoLogin profile.")
                if not switch_account():
                    log("🛑 All accounts reached the DM limit. Stopping bot.")
                    shutdown_current_session()
                    stop_bot()
                    return False
                continue
            return True

    try:
        while bot_running:
            if not has_available_accounts():
                log("🛑 DM limit reached for all accounts. Stopping bot.")
                shutdown_current_session()
                stop_bot()
                break
            urls_to_process = database.get_targets()
            if not urls_to_process:
                if driver or gl:
                    shutdown_current_session("🕒 No targets found. Closing browser session.")
                log("🕒 No targets found. Checking again in 60s.")
                time.sleep(60)
                continue

            if driver is None or gl is None or current_gl_index is None:
                if not start_new_session():
                    log("🕒 No available accounts to start. Checking again in 60s.")
                    time.sleep(60)
                    continue

            while urls_to_process:
                url = random.choice(urls_to_process)
                urls_to_process.remove(url)
                if not bot_running:
                    log("🛑 Bot stop requested. Exiting loop.")
                    break

                if not ensure_active_account():
                    break

                current_account_id = account_profiles[current_gl_index]["profile_id"]

                wait = WebDriverWait(driver, 15)
                try:
                    log(f"Navigating to {url}")
                    driver.get(url)
                    time.sleep(3)  # Wait for initial page load
                    
                    # Simulate human browsing (5-10 seconds of random scrolling)
                    if not simulate_human_browsing(driver, scroll_min_seconds, scroll_max_seconds):
                        log("⚠️ Skipping profile due to scrolling error.")
                        if not restart_current_session():
                            break
                        continue
                    
                    # Find and click the message button
                    msg_btn = wait.until(EC.presence_of_element_located((By.ID, "bSendMessage")))
                    # Scroll to element and click via JS to avoid interception from sticky headers/overlays
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", msg_btn)
                    time.sleep(1)
                    driver.execute_script("arguments[0].click();", msg_btn)
                    log("✅ Clicked message button")
                    
                    # Wait for textarea to appear
                    textarea = wait.until(EC.visibility_of_element_located((By.ID, "postMsgInput")))
                    try:
                        textarea.click()
                        textarea.send_keys(Keys.CONTROL, "a")
                        textarea.send_keys(Keys.DELETE)
                    except Exception:
                        pass
                    
                    # Resolve message through spintax and emulate human typing
                    message = resolve_spintax(spintax_template)
                    log(f"Typing message: {message[:30]}...") # Log first 30 chars
                    human_typing(driver, textarea, message)

                    time.sleep(0.5)
                    ensure_full_message(driver, textarea, message)
                    time.sleep(0.5)
                    
                    # Click the Send button
                    send_btn = wait.until(EC.presence_of_element_located((By.ID, "sendButton")))
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", send_btn)
                    time.sleep(0.5)
                    driver.execute_script("arguments[0].click();", send_btn)
                    log("✅ Message sent via Send button!")
                    
                    # Increment sent messages counter for current profile
                    messages_sent_per_account[current_account_id] += 1
                    
                    # Log message to database
                    log_message_sent()
                    log_account_message(current_account_id, url, message)
                    
                    # Mark as sent in database
                    database.mark_target_sent(url)
                    
                    # Wait between messages using the configured delay
                    delay = random.uniform(message_delay_min_seconds, message_delay_max_seconds)
                    log(f"Waiting {delay:.1f}s before next profile...")
                    time.sleep(delay)

                except Exception as e:
                    log(f"❌ Failed on profile {url}: {e}")
                    if isinstance(e, WebDriverException):
                        log("⚠️ WebDriver error detected. Restarting browser session.")
                        if restart_current_session():
                            continue
                    current_profile_id = ""
                    if current_gl_index is not None:
                        current_profile_id = account_profiles[current_gl_index]["profile_id"]
                    if current_profile_id:
                        mark_profile_unavailable(
                            current_profile_id,
                            f"⚠️ Error on profile {current_profile_id}. Switching account.",
                        )
                    if not switch_account():
                        log("🛑 No available accounts after error. Stopping bot.")
                        shutdown_current_session()
                        stop_bot()
                        break

            if bot_running:
                log("✅ Batch completed. Waiting for new targets...")
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_current_session()

def stop_bot():
    global bot_running
    bot_running = False
    log("🛑 Stop requested. Closing active browser profile.")
    close_active_session()

if __name__ == "__main__":
    run_bot()