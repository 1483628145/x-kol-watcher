import configparser
import logging
import os
import random
import sys
import time
from datetime import datetime, timedelta

import pytz
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


import threading
import time
import logging
from playwright.sync_api import sync_playwright

def resource_path(relative_path):
    """ 获取资源的绝对路径，无论是从脚本运行还是从打包后的exe运行 """
    try:
        # PyInstaller 创建一个临时文件夹，并把路径存储在 _MEIPASS 中
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

# --- 全局配置 ---
CONFIG_FILE = resource_path('config.ini')
LOG_FILE = 'watcher.log'
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:107.0) Gecko/20100101 Firefox/107.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15',
]
# 设置北京时区
BJT = pytz.timezone('Asia/Shanghai')

def setup_logging():
    """配置日志记录，同时输出到文件和控制台"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )

def load_config():
    """加载配置文件，仅保留 Telegram 和 Scraper 部分"""
    try:
        config = configparser.ConfigParser(allow_no_value=True)
        config.optionxform = str  # 保持键大小写

        if not os.path.exists(CONFIG_FILE):
            logging.error(f"找不到配置文件 {CONFIG_FILE}，程序将创建一个模板。")
            config['Telegram'] = {'bot_token': '', 'user_id': ''}
            config['Scraper'] = {'keywords': '币安,Alpha,积分,用户,空投'}
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                config.write(f)

        config.read(CONFIG_FILE, encoding='utf-8')

        # 检查必要配置
        if 'Telegram' not in config or 'Scraper' not in config:
            logging.error("配置文件缺少 [Telegram] 或 [Scraper] 部分。")
            return None

        if not config['Telegram'].get('bot_token') or not config['Telegram'].get('user_id'):
            logging.warning("Telegram 配置不完整，请填写 config.ini 中的 bot_token 和 user_id")
            return None

        return config

    except Exception as e:
        logging.error(f"加载配置时出错: {e}")
        return None


# TG提醒
def send_telegram_message(content, config):
    """发送 Telegram 通知"""
    try:
        bot_token = config['Telegram']['bot_token']
        user_id = config['Telegram']['user_id']
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = {
            "chat_id": user_id,
            "text": content
            # 不使用 parse_mode，纯文本发送
        }
        response = requests.post(url, data=data)
        if response.status_code == 200:
            logging.info("Telegram 消息发送成功。")
        else:
            logging.error(f"Telegram 消息发送失败: {response.text}")
    except Exception as e:
        logging.error(f"发送 Telegram 消息时出错: {e}")


# 微信提醒
def send_wechat_message(content, config):
    """通过企业微信机器人发送通知"""
    try:
        webhook = config['WeChat'].get('wechat_webhook', '').strip()
        if not webhook:
            logging.warning("未配置企业微信 webhook，消息未发送。")
            return False

        data = {
            "msgtype": "text",
            "text": {
                "content": content
            }
        }
        response = requests.post(webhook, json=data, timeout=10)
        if response.status_code == 200 and response.json().get("errcode") == 0:
            logging.info("企业微信消息发送成功。")
            return True
        else:
            logging.error(f"企业微信消息发送失败: {response.text}")
            return False
    except Exception as e:
        logging.error(f"发送企业微信消息时出错: {e}")
        return False



def get_latest_tweet(p, nitter_instances, username):
    """
    使用 Playwright 从Nitter实例获取指定用户的最新推文。
    """
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    random.shuffle(nitter_instances)

    for instance in nitter_instances:
        try:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=random.choice(USER_AGENTS))
            
            url = f"{instance}/{username}"
            logging.info(f"正在尝试从 {url} 获取推文...")
            page.goto(url, timeout=30000)
            page.wait_for_selector('div.timeline-item', timeout=30000)

            html_content = page.content()
            soup = BeautifulSoup(html_content, 'html.parser')
            
            latest_tweet_div = soup.find('div', class_='timeline-item')
            if not latest_tweet_div:
                logging.warning(f"{url} 上未找到推文内容。")
                browser.close()
                continue
            
            content_div = latest_tweet_div.find('div', class_='tweet-content')
            tweet_text = content_div.text.strip() if content_div else ""

            link_tag = latest_tweet_div.find('a', class_='tweet-link')
            tweet_id = link_tag['href'] if link_tag else ""

            browser.close()

            if tweet_text and tweet_id:
                logging.info(f"{username} 最新推文 ID: {tweet_id}")
                return tweet_text, tweet_id
            else:
                continue

        except PlaywrightTimeoutError:
            logging.error(f"访问 {instance} 超时。")
        except Exception as e:
            logging.error(f"获取 {username} 推文失败: {e}")
        finally:
            if 'browser' in locals() and browser.is_connected():
                browser.close()

    logging.error(f"{username} 所有 Nitter 实例均失败。")
    return None, None




def get_sleep_duration():
    """根据当前北京时间计算下一次检查前的休眠秒数"""
    now_bjt = datetime.now(BJT)
    hour = now_bjt.hour
    minute = now_bjt.minute

    # 晚上11:02到次日早上10:00，暂停
    if (hour == 23 and minute >= 2) or hour > 23 or hour < 10:
        pause_until = now_bjt.replace(hour=10, minute=0, second=0, microsecond=0)
        if now_bjt.hour >= 23:
            pause_until += timedelta(days=1)
        
        sleep_seconds = (pause_until - now_bjt).total_seconds()
        logging.info(f"现在是休眠时间，将暂停直到北京时间 {pause_until.strftime('%Y-%m-%d %H:%M:%S')}")
        return sleep_seconds

    # 下午3点到晚上11点 (15:00 - 23:01)
    if 15 <= hour < 23:
        # 整点前后 (xx:58 - yy:02)
        if minute >= 58 or minute <= 1:
            logging.info("处于关键时间段，30秒后检查。")
            return 30  # 30秒一次
        else:
            logging.info("处于普通高峰时段，1分钟后检查。")
            return 60  # 1分钟一次
    
    # 其他时间 (早上10:00 - 下午15:00)
    logging.info("处于普通时段，5分钟后检查。")
    return 300  # 5分钟一次




def monitor_user(username, config, nitter_instances, keywords, last_tweet_ids_lock, last_tweet_ids):
    with sync_playwright() as p:
        while True:
            try:
                tweet_text, tweet_id = get_latest_tweet(p, nitter_instances, username)

                if tweet_text and tweet_id:
                    with last_tweet_ids_lock:
                        last_id = last_tweet_ids.get(username)

                    if tweet_id != last_id:
                        with last_tweet_ids_lock:
                            last_tweet_ids[username] = tweet_id

                        logging.info(f"{username} 发布了新推文: {tweet_text[:60]}...")

                        if any(kw.lower() in tweet_text.lower() for kw in keywords):
                            message = f"【{username}】发现关键词推文：\n\n{tweet_text}"
                            send_telegram_message(message, config)
                            send_wechat_message(message, config)
                        else:
                            logging.info(f"{username} 新推文未命中关键词，跳过。")
                    else:
                        logging.info(f"{username} 无新推文。")
                else:
                    logging.info(f"{username} 获取推文失败。")

            except Exception as e:
                logging.error(f"用户 {username} 监控异常: {e}")

            time.sleep(get_sleep_duration())

def main():
    setup_logging()
    logging.info("程序启动，开始多线程监控多个推特账号...")

    config = load_config()
    if not config:
        logging.error("无法加载配置，程序退出。")
        return

    nitter_instances = [url.strip() for url in config['Scraper']['nitter_instances'].split('\n') if url.strip()]
    keywords = [kw.strip() for kw in config['Scraper']['keywords'].split(',')]
    usernames = [u.strip() for u in config['Scraper']['usernames'].split(',') if u.strip()]

    if not nitter_instances or not keywords or not usernames:
        logging.error("配置文件中缺少 Nitter 实例、关键词或用户列表。")
        return

    logging.info(f"关键词: {keywords}")
    logging.info(f"推特用户: {usernames}")
    logging.info(f"Nitter 实例: {nitter_instances}")

    last_tweet_ids = {}
    last_tweet_ids_lock = threading.Lock()

    threads = []
    for username in usernames:
        t = threading.Thread(
            target=monitor_user,
            args=(username, config, nitter_instances, keywords, last_tweet_ids_lock, last_tweet_ids),
            daemon=True
        )
        t.start()
        threads.append(t)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("程序被手动中断，正在退出...")


'''




def main():
    setup_logging()
    logging.info("程序启动，开始监控多个推特账号...")

    config = load_config()
    if not config:
        logging.error("无法加载配置，程序退出。")
        return

    nitter_instances = [url.strip() for url in config['Scraper']['nitter_instances'].split('\n') if url.strip()]
    keywords = [kw.strip() for kw in config['Scraper']['keywords'].split(',')]
    usernames = [u.strip() for u in config['Scraper']['usernames'].split(',') if u.strip()]

    if not nitter_instances or not keywords or not usernames:
        logging.error("配置文件中缺少 Nitter 实例、关键词或用户列表。")
        return

    logging.info(f"关键词: {keywords}")
    logging.info(f"推特用户: {usernames}")
    logging.info(f"Nitter实例: {nitter_instances}")

    # 每个用户一个 tweet_id
    last_tweet_ids = {user: None for user in usernames}

    with sync_playwright() as p:
        while True:
            try:
                for username in usernames:
                    tweet_text, tweet_id = get_latest_tweet(p, nitter_instances, username)

                    if tweet_text and tweet_id:
                        if tweet_id != last_tweet_ids[username]:
                            last_tweet_ids[username] = tweet_id
                            logging.info(f"{username} 发布了新推文: {tweet_text[:60]}...")

                            if any(kw.lower() in tweet_text.lower() for kw in keywords):
                                message = f"【{username}】发现关键词推文：\n\n{tweet_text}"
                                send_telegram_message(message, config)
                                send_wechat_message(message, config)
                            else:
                                logging.info(f"{username} 新推文未命中关键词，跳过。")
                        else:
                            logging.info(f"{username} 无新推文。")
                    else:
                        logging.info(f"{username} 获取推文失败。")

                time.sleep(get_sleep_duration())

            except KeyboardInterrupt:
                logging.info("程序被手动中断。")
                break
            except Exception as e:
                logging.error(f"主循环异常: {e}")
                time.sleep(60)



'''


if __name__ == '__main__':
    main() 