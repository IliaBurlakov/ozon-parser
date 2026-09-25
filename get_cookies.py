import json
import logging
import time
from pathlib import Path

from seleniumbase import Driver
from selenium.webdriver.common.by import By


BASE_DIR = Path(__file__).resolve().parent

DATA_OZON_URL = "https://data.ozon.ru/"
OZON_ORDERS_URL = "https://www.ozon.ru/my/orderlist"

COOKIES_FILE = BASE_DIR / "cookies.json"

AUTH_TIMEOUT_SECONDS = 240


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


# Запускает браузер для ручной авторизации
def start_browser():
    driver = Driver(
        uc=True,
        headless=False,
        chromium_arg=(
            "--disable-blink-features=AutomationControlled,"
            "--no-sandbox"
        ),
    )

    return driver


# Открывает data.ozon.ru, откуда начинается авторизация
def open_login_page(driver):
    logger.info("Открываем %s", DATA_OZON_URL)

    driver.get(DATA_OZON_URL)
    time.sleep(4)

    logger.info("Текущий URL: %s", driver.current_url)


# Показывает пользователю шаги для ручной авторизации
def show_login_instructions():
    logger.info("=" * 60)
    logger.info("Выполните авторизацию в открытом окне Chrome:")
    logger.info("1. Нажмите «Перейти к аналитике».")
    logger.info("2. Введите номер телефона.")
    logger.info("3. Введите код подтверждения.")
    logger.info("4. Дождитесь возврата на data.ozon.ru.")
    logger.info("=" * 60)


# Ждет перехода на Ozon ID и возврата обратно на data.ozon.ru
def wait_for_authorization(driver, timeout_seconds):
    logger.info("Ожидаем завершения авторизации...")

    entered_sso = False
    start_time = time.time()

    while time.time() - start_time < timeout_seconds:
        current_url = driver.current_url.lower()

        if "sso.ozon.ru" in current_url:
            entered_sso = True

        if entered_sso and "data.ozon.ru" in current_url:
            logger.info(
                "Возврат на data.ozon.ru после авторизации получен."
            )
            return True

        if current_url == "about:blank":
            logger.warning("Страница браузера была закрыта.")
            return False

        time.sleep(2)

    logger.warning("Время ожидания авторизации истекло.")
    return False


# Проверяет авторизацию по странице личного кабинета Ozon
def verify_auth_session(driver):
    logger.info("Проверяем авторизацию...")

    try:
        driver.get(OZON_ORDERS_URL)
        time.sleep(3)

        page_text = driver.find_element(By.TAG_NAME, "body").text.lower()

        if "вы не авторизованы" in page_text:
            logger.warning("Сессия не авторизована.")
            return False

        logger.info("Сессия авторизована.")
        return True

    except Exception:
        logger.exception("Не удалось проверить авторизацию.")
        return False


# Сохраняет cookies текущей авторизованной сессии в JSON
def save_cookies(driver, cookies_file):
    time.sleep(2)

    session_data = {
        "saved_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(),
        ),
        "cookies": driver.get_cookies(),
    }

    cookies_file.write_text(
        json.dumps(
            session_data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    logger.info(
        "Cookies сохранены: %s",
        cookies_file.resolve(),
    )


# Закрывает браузер
def close_browser(driver):
    driver.quit()


def main():
    driver = start_browser()

    try:
        open_login_page(driver)
        show_login_instructions()

        authorized = wait_for_authorization(
            driver,
            AUTH_TIMEOUT_SECONDS,
        )

        if not authorized:
            logger.error("Авторизация не была завершена.")
            return

        if not verify_auth_session(driver):
            logger.error(
                "Авторизация не подтверждена. Cookies не сохраняем."
            )
            return

        save_cookies(driver, COOKIES_FILE)

    except Exception:
        logger.exception("Ошибка во время авторизации.")

    finally:
        close_browser(driver)


if __name__ == "__main__":
    main()
