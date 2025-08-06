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

def resource_path(relative_path):
    """ 获取资源的绝对路径，无论是从脚本运行还是从打包后的exe运行 """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

CONFIG_FILE = resource_path('config.ini')
LOG_FILE = 'watcher.log'
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:107.0) Gecko/20100101 Firefox/107.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15',
]

BJT = pytz.timezone('Asia/Shanghai')

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )

def load_config():
    try:
        config = configparser.ConfigParser(allow_no_value=True)
        config.optionxform = str

        if not os.path.exists(CONFIG_FILE):
            logging.error(f"找不到配置文件 {CONFIG_FILE}，程序将创建一个模板。")
            config['Telegram'] = {'bot_token': '', 'user_id': ''}
            config['Scraper'] = {
                'keywords': '币安,Alpha,积分,用户,空投',
                'nitter_instances': 'https://nitter.net\nhttps://nitter.1d4.us',
                'usernames': 'TwitterUser1,TwitterUser2'
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                config.write(f)
            return None

        config.read(CONFIG_FILE, encoding='utf-8')

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

def send_telegram_message(content, config):
    try:
        bot_token = config['Telegram']['bot_token']
        user_id = config['Telegram']['user_id']
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = {
            "chat_id": user_id,
            "text": content
        }
        response = requests.post(url, data=data)
        if response.status_code == 200:
            logging.info("Telegram 消息发送成功。")
        else:
            logging.error(f"Telegram 消息发送失败: {response.text}")
    except Exception as e:
        logging.error(f"发送 Telegram 消息时出错: {e}")

def send_wechat_message(content, config):
    try:
        webhook = config['WeChat'].get('wechat_webhook', '').strip()
        if not webhook:
            logging.warning("未配置企业微信 webhook，消息未发送。")
            return False

        data = {
            "msgtype": "text",
            "text": {"content": content}
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

def get_latest_tweets(p, nitter_instances, username, max_count=3):
    """
    使用 Playwright 从Nitter实例获取指定用户的最新推文，最多返回 max_count 条。
    返回列表：[(tweet_text, tweet_id), ...]
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

            tweet_divs = soup.find_all('div', class_='timeline-item', limit=max_count)
            if not tweet_divs:
                logging.warning(f"{url} 上未找到推文内容。")
                browser.close()
                continue

            tweets = []
            for div in tweet_divs:
                content_div = div.find('div', class_='tweet-content')
                tweet_text = content_div.text.strip() if content_div else ""

                link_tag = div.find('a', class_='tweet-link')
                tweet_id = link_tag['href'] if link_tag else ""

                if tweet_text and tweet_id:
                    tweets.append((tweet_text, tweet_id))

            browser.close()

            if tweets:
                logging.info(f"{username} 获取到 {len(tweets)} 条推文。")
                return tweets
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
    return []

def get_sleep_duration():
    return 60

def monitor_user(username, config, nitter_instances, keywords, last_tweet_ids_lock, last_tweet_ids):
    fail_count = 0
    fail_threshold = 5  # 连续失败5次则触发告警

    with sync_playwright() as p:
        while True:
            try:
                tweets = get_latest_tweets(p, nitter_instances, username, max_count=3)

                if tweets:
                    if fail_count >= fail_threshold:
                        logging.info(f"{username} 镜像恢复正常，清除失败计数。")
                    fail_count = 0

                    with last_tweet_ids_lock:
                        last_ids = last_tweet_ids.get(username, set())

                    new_tweets = []
                    for tweet_text, tweet_id in tweets:
                        if tweet_id not in last_ids:
                            new_tweets.append((tweet_text, tweet_id))

                    if new_tweets:
                        with last_tweet_ids_lock:
                            updated_ids = set(tweet_id for _, tweet_id in tweets)
                            last_tweet_ids[username] = updated_ids

                        for tweet_text, tweet_id in new_tweets:
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
                    fail_count += 1
                    logging.warning(f"{username} 获取推文失败，已连续失败 {fail_count} 次。")

                    if fail_count == fail_threshold:
                        alert_msg = f"⚠️【警告】{username} 从所有 Nitter 镜像连续获取失败 {fail_threshold} 次，可能镜像全部不可用！"
                        send_telegram_message(alert_msg, config)
                        send_wechat_message(alert_msg, config)

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

if __name__ == '__main__':
    main()
