import csv
import json
import logging
import os
import random
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from sbvirtualdisplay import Display
from seleniumbase import Driver
from selenium.webdriver.common.by import By

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

OZON_URL = "https://www.ozon.ru/"
OZON_ORDERS_URL = "https://www.ozon.ru/my/orderlist"

ALLOWED_COOKIE_DOMAINS = {
    ".ozon.ru",
    "ozon.ru",
    "www.ozon.ru",
}

AUTH_COOKIE_NAMES = {
    "__Secure-access-token",
    "__Secure-refresh-token",
    "__Secure-user-id",
    "__Secure-sid",
}

CSV_FIELDS = [
    "sku",
    "title",
    "price",
    "rating",
    "reviews_total",
    "cover_image",
    "photos_seller",
    "videos_seller",
    "color",
    "material",
    "art_set",
    "has_rich_content",
]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


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


# Загружает и проверяет настройки из .env.
def load_config():
    if not ENV_FILE.is_file():
        raise FileNotFoundError(
            "Файл .env не найден.\n"
            "Создайте .env со следующими настройками:\n\n"
            "SKU_FILE=sku_list.txt\n"
            "RESULTS_FILE=results.csv\n"
            "CHECK_AUTH=false\n"
            "PAUSE_SECONDS=1\n"
            "PAUSE_JITTER_SECONDS=0.3\n"
            "USE_XVFB=false"
        )

    load_dotenv(ENV_FILE)

    required = [
        "SKU_FILE",
        "RESULTS_FILE",
        "CHECK_AUTH",
        "PAUSE_SECONDS",
        "PAUSE_JITTER_SECONDS",
        "USE_XVFB",
    ]

    missing = [
        name
        for name in required
        if not (os.getenv(name) or "").strip()
    ]

    if missing:
        raise ValueError(
            "В .env не указаны настройки:\n"
            + "\n".join(f"- {name}" for name in missing)
        )

    pause_seconds = float(os.getenv("PAUSE_SECONDS"))
    pause_jitter_seconds = float(os.getenv("PAUSE_JITTER_SECONDS"))

    if pause_seconds < 0:
        raise ValueError("PAUSE_SECONDS не может быть отрицательным")

    if pause_jitter_seconds < 0:
        raise ValueError(
            "PAUSE_JITTER_SECONDS не может быть отрицательным"
        )

    return {
        "sku_file": BASE_DIR / os.getenv("SKU_FILE"),
        "results_file": BASE_DIR / os.getenv("RESULTS_FILE"),
        "cookies_file": BASE_DIR / "cookies.json",
        "use_xvfb": os.getenv("USE_XVFB").strip().lower()
        in {"1", "true", "yes"},
        "check_auth": os.getenv("CHECK_AUTH").strip().lower()
        in {"1", "true", "yes"},
        "pause_seconds": pause_seconds,
        "pause_jitter_seconds": pause_jitter_seconds,
    }


# Загружает сохраненные cookies из cookies.json в браузер
def load_cookies(driver, cookies_file: Path):
    if not cookies_file.is_file():
        logger.warning(
            "Cookies не обнаружены (%s). Продолжаем без них.",
            cookies_file,
        )
        return False

    raw_text = cookies_file.read_text(encoding="utf-8-sig").strip()

    if not raw_text:
        logger.warning("Файл cookies пустой. Продолжаем без них.")
        return False

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.warning(
            "Файл cookies содержит некорректный JSON. "
            "Продолжаем без них."
        )
        return False

    if not isinstance(payload, dict):
        logger.warning(
            "Неожиданный формат cookies.json. Продолжаем без cookies."
        )
        return False

    cookies = payload.get("cookies")

    if not isinstance(cookies, list) or not cookies:
        logger.warning(
            "Сохраненные cookies не обнаружены. Продолжаем без них."
        )
        return False

    # Selenium может добавить cookie только после открытия нужного домена
    driver.get(OZON_URL)
    time.sleep(2)

    loaded = 0
    skipped = 0
    expired = 0
    now = int(time.time())

    for source_cookie in cookies:
        domain = source_cookie.get("domain", "")

        if domain not in ALLOWED_COOKIE_DOMAINS:
            skipped += 1
            continue

        expiry = source_cookie.get("expiry")

        if expiry is not None and int(expiry) <= now:
            logger.warning(
                "Cookie %s уже истекла",
                source_cookie.get("name"),
            )
            expired += 1
            continue

        cookie = {
            "name": source_cookie["name"],
            "value": source_cookie["value"],
            "path": source_cookie.get("path", "/"),
        }

        if domain:
            cookie["domain"] = domain

        if expiry is not None:
            cookie["expiry"] = int(expiry)

        if "secure" in source_cookie:
            cookie["secure"] = bool(source_cookie["secure"])

        if "httpOnly" in source_cookie:
            cookie["httpOnly"] = bool(source_cookie["httpOnly"])

        same_site = source_cookie.get("sameSite")
        if same_site in {"Strict", "Lax", "None"}:
            cookie["sameSite"] = same_site

        try:
            driver.add_cookie(cookie)
            loaded += 1
        except Exception as exc:
            logger.warning(
                "Не удалось загрузить cookie %s: %s",
                source_cookie.get("name"),
                exc,
            )

    logger.info(
        "Cookies: загружено=%s, пропущено=%s, истекло=%s",
        loaded,
        skipped,
        expired,
    )

    if loaded == 0:
        logger.warning(
            "Ни одну сохраненную cookie загрузить не удалось. "
            "Продолжаем без них."
        )
        return False

    driver.refresh()
    time.sleep(2)

    current_names = {
        cookie["name"]
        for cookie in driver.get_cookies()
    }

    # Cookies, которые удалось загрузить в браузер.
    present = sorted(AUTH_COOKIE_NAMES & current_names)

    # Cookies, которых не хватает в браузере.
    missing = sorted(AUTH_COOKIE_NAMES - current_names)

    logger.info(
        "Авторизационные cookies в браузере: %s",
        ", ".join(present) if present else "не найдены",
    )

    if missing:
        logger.warning(
            "Не загружены cookies: %s",
            ", ".join(missing),
        )

    return True



# Сначала пытаемся получить комплектацию из отдельного блока между заголовками "Комплектация" и "Характеристики".
# Если такого блока нет - ищем ее среди характеристик товара.
def get_art_set(driver):

    return driver.execute_script(
        """
        const headings = [...document.querySelectorAll(
            "h1, h2, h3, h4, [role='heading']"
        )];

        const start = headings.find(
            el => el.textContent.trim() === "Комплектация"
        );

        const end = headings.find(
            el => el.textContent.trim() === "Характеристики"
        );

        if (start && end) {
            const range = document.createRange();
            range.setStartAfter(start);
            range.setEndBefore(end);

            const fragment = range.cloneContents();

            fragment.querySelectorAll("a, button").forEach(el => {
                el.remove();
            });

            let text = fragment.textContent
                .replace(/\\s+/g, " ")
                .trim();

            text = text
                .split(/\\s+/)
                .filter(part => !part.startsWith("#"))
                .join(" ")
                .trim();

            if (text) {
                return text;
            }
        }

        const rows = [...document.querySelectorAll("dl")]
            .map(dl => ({
                name: (
                    dl.querySelector("dt")?.textContent ?? ""
                ).trim().toLowerCase(),
                value: (
                    dl.querySelector("dd")?.textContent ?? ""
                ).trim()
            }))
            .filter(row => row.name && row.value);

        const wantedNames = [
            "комплектация",
            "состав комплекта"
        ];

        for (const wanted of wantedNames) {
            const row = rows.find(row => row.name === wanted);

            if (row) {
                return row.value;
            }
        }

        return "";
        """
    )


# Проверка наличия изображений, таблиц или списков в описании
def check_rich_content(driver):
    return driver.execute_script(
        """
        const headings = [...document.querySelectorAll(
            "h1, h2, h3, h4, [role='heading']"
        )];

        const description = headings.find(
            el => el.textContent.trim() === "Описание"
        );

        const characteristics = headings.find(
            el => el.textContent.trim() === "Характеристики"
        );

        if (!description || !characteristics) {
            return null;
        }

        const range = document.createRange();
        range.setStartAfter(description);
        range.setEndBefore(characteristics);

        const richElements = [
            ...document.querySelectorAll("img, table, ul, ol")
        ].filter(el => range.intersectsNode(el));

        return richElements.length > 0;
        """
    )


# Получение JSON текущей карточки через fetch в браузере
def get_product_json(driver):
    return driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];

        const endpoint =
            "/api/entrypoint-api.bx/page/json/v2?url=" +
            encodeURIComponent(window.location.pathname);

        fetch(endpoint)
            .then(async response => {
                if (!response.ok) {
                    throw new Error("HTTP " + response.status);
                }

                return response.json();
            })
            .then(data => {
                done({
                    ok: true,
                    data: data
                });
            })
            .catch(error => {
                done({
                    ok: false,
                    error: String(error)
                });
            });
        """
    )


# Преобразует строковое значение цены в целое число
def parse_price(price_text):
    if not price_text:
        return ""

    digits = re.sub(r"[^\d]", "", str(price_text))

    if not digits:
        return ""

    return int(digits)


# Убирает лишние запятые и приводит пробелы между значениями к одному формату
def normalize_text(text: str):
    if not text:
        return ""

    text = str(text).strip()
    text = re.sub(r"\s*,(?:\s*,)+\s*", ", ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    return text.strip(" ,")


# Разбирает материал и цвет из коротких характеристик
def parse_characteristics(state: dict, item_data: dict):
    for characteristic in state.get("characteristics") or []:
        title_parts = (characteristic.get("title", {}).get("textRs") or [])

        name = "".join(
            part.get("content", "")
            for part in title_parts
        ).strip().lower()

        values = characteristic.get("values") or []

        value = ", ".join(
            str(part.get("text", "")).strip()
            for part in values
            if part.get("text")
        )
        value = normalize_text(value)

        if not value:
            continue

        if name == "материал":
            item_data["material"] = value
        elif name == "цвет":
            item_data["color"] = value


# Ищет цвет товара в aspects и возвращает его текстовое значение
def parse_color_aspect(state: dict):
    for aspect in state.get("aspects") or []:
        if aspect.get("aspectKey") != "Color":
            continue

        parts = aspect.get("descriptionRs") or []
        color = "".join(
            str(part.get("content", ""))
            for part in parts
            if part.get("type") != "textGray"
        ).strip()

        return normalize_text(color)

    return ""

# Перебирает widgetStates и заполняет item_data данными из нужных виджетов
def parse_widget_states(widget_states: dict, item_data: dict):
    actual_sku = None

    for key, raw_state in widget_states.items():
        if not raw_state:
            continue

        try:
            state = (
                json.loads(raw_state)
                if isinstance(raw_state, str)
                else raw_state
            )
        except json.JSONDecodeError:
            logger.warning(
                "Не удалось разобрать JSON виджета: %s",
                key,
            )
            continue

        if not isinstance(state, dict):
            continue

        if key.startswith("webProductHeading-"):
            item_data["title"] = state.get("title") or ""

        elif key.startswith("webPrice-"):
            price_text = state.get("price") or state.get("cardPrice")
            item_data["price"] = parse_price(price_text)

        elif key.startswith("webReviewProductScore-"):
            item_data["rating"] = state.get("totalScore", "")
            item_data["reviews_total"] = state.get("reviewsCount", "")

        elif key.startswith("webGallery-"):
            actual_sku = str(state.get("sku") or "")
            item_data["cover_image"] = state.get("coverImage") or ""

            images = state.get("images") or []
            videos = state.get("videos") or []

            item_data["photos_seller"] = len(images)
            item_data["videos_seller"] = len(videos)

        elif key.startswith("webShortCharacteristics-"):
            parse_characteristics(state, item_data)

        elif key.startswith("webAspects-"):
            color = parse_color_aspect(state)
            if color:
                item_data["color"] = color

    return actual_sku

# Логируем данные, полученные по конкретному товару
def log_product_data(item_data: dict):
    logger.info("Название: %s", item_data["title"])
    logger.info("Цена: %s ₽", item_data["price"])
    logger.info("Рейтинг: %s", item_data["rating"])
    logger.info("Отзывов: %s", item_data["reviews_total"])
    logger.info(
        "Фотографий: %s | Видео: %s",
        item_data["photos_seller"],
        item_data["videos_seller"],
    )
    logger.info("Цвет: %s", item_data["color"])
    logger.info("Материал: %s", item_data["material"])



def parse_sku_via_browser(sku: str, driver):
    """
    Открывает карточку товара по SKU и собирает основные данные.
    Функция получает JSON страницы через браузер, разбирает widgetStates,
    проверяет, что найден нужный товар, затем дополнительно получает
    rich content и комплектацию из DOM страницы.
    Возвращает словарь с данными товара или None, если товар не удалось обработать.
    """

    url = f"https://www.ozon.ru/product/{sku}/"
    logger.info("Переход на страницу товара: %s", url)

    item_data = {field: "" for field in CSV_FIELDS}
    item_data["sku"] = sku

    try:
        driver.get(url)

        logger.info(
            "Фактический URL после перехода: %s",
            driver.current_url,
        )

        current_path = urlparse(driver.current_url).path
        if current_path.startswith("/search/"):
            logger.warning(
                "SKU %s: Ozon перенаправил на поиск. Пропускаем.",
                sku,
            )
            return None

        result = get_product_json(driver)

        if not result or not result.get("ok"):
            error = result.get("error") if result else "Пустой ответ"
            logger.error(
                "Не удалось получить JSON для SKU %s: %s",
                sku,
                error,
            )
            return None

        widget_states = result["data"].get("widgetStates") or []

        if not isinstance(widget_states, dict):
            logger.error("widgetStates имеет недопустимый формат")
            return None

        logger.info("Получено виджетов: %s", len(widget_states))

        actual_sku = parse_widget_states(widget_states, item_data)

        if actual_sku != str(sku):
            logger.error(
                "Несовпадение SKU! Запрошен %s, в JSON получен %s",
                sku,
                actual_sku,
            )
            return None

        if not item_data["title"]:
            logger.error("Не найдено название товара %s", sku)
            return None

        if item_data["price"] == "":
            logger.error("Не найдена цена товара %s", sku)
            return None

        log_product_data(item_data)

        rich_content = check_rich_content(driver)
        item_data["has_rich_content"] = rich_content is True
        logger.info("Rich content: %s", item_data["has_rich_content"])

        item_data["art_set"] = get_art_set(driver)
        logger.info(
            "Комплектация: %s",
            item_data["art_set"] or "не указана",
        )

        return item_data

    except Exception:
        logger.exception("Ошибка при обработке SKU %s", sku)
        return None

# Загружает список SKU, из файла, указанного в .env
def load_sku_list(file_path: Path):
    if not file_path.is_file():
        raise FileNotFoundError(f"Файл со списком SKU не найден: {file_path}")

    content = file_path.read_text(encoding="utf-8-sig").strip()

    if not content:
        raise ValueError(f"Файл со списком SKU пустой: {file_path}")

    tokens = re.split(r"[,\s]+", content)
    sku_list = []
    seen = set()

    for token in tokens:
        if not token:
            continue

        if not token.isdigit() or int(token) == 0:
            raise ValueError(
                f"Некорректный SKU в {file_path.name}: {token!r}"
            )

        if token in seen:
            continue

        seen.add(token)
        sku_list.append(token)

    if not sku_list:
        raise ValueError(f"В файле {file_path.name} нет корректных SKU")

    logger.info(
        "Из файла %s загружено %s уникальных SKU",
        file_path.name,
        len(sku_list),
    )

    return sku_list


# Загружает из существующего CSV список SKU, которые уже были обработаны
def load_completed_skus(file_path: Path):
    if not file_path.is_file() or file_path.stat().st_size == 0:
        return set()

    with file_path.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        reader = csv.DictReader(file)

        if reader.fieldnames != CSV_FIELDS:
            raise ValueError(
                "Существующий CSV имеет другой набор столбцов: "
                f"{file_path}"
            )

        return {
            row["sku"]
            for row in reader
            if row.get("sku")
        }


# Выводит итог по неудачным SKU и сохраняет их в отдельный файл
def report_failed_skus(failed_skus, failed_skus_file):
    logger.info("=" * 60)
    logger.info(
        "Не удалось обработать товаров: %s",
        len(failed_skus),
    )

    if not failed_skus:
        logger.info("Все попытки обработки завершились успешно.")
        logger.info("=" * 60)
        return

    logger.warning(
        "SKU, которые не удалось обработать:\n%s",
        ", ".join(failed_skus),
    )

    failed_skus_file.write_text(
        "\n".join(failed_skus) + "\n",
        encoding="utf-8",
    )

    logger.info(
        "Список неудачных SKU сохранен: %s",
        failed_skus_file.resolve(),
    )
    logger.info("=" * 60)


# Запускает виртуальный дисплей при необходимости и создает браузер.
def start_browser(use_xvfb):
    display = None

    try:
        if use_xvfb:
            logger.info("Запускаем виртуальный дисплей Xvfb...")

            display = Display(
                visible=0,
                size=(1920, 1080),
            )
            display.start()

        driver = Driver(
            uc=True,
            headless=False,
            chromium_arg=(
                "--disable-blink-features=AutomationControlled,"
                "--no-sandbox"
            ),
        )

        driver.set_script_timeout(40)
        return driver, display

    except Exception:
        if display is not None:
            display.stop()
        raise


# Закрывает браузер и дисплей при необходимости
def close_browser(driver, display):
    try:
        driver.quit()
    finally:
        if display is not None:
            display.stop()
            logger.info("Виртуальный дисплей Xvfb остановлен.")


# Подготавливает браузерную сессию: загружает cookies и при включенном CHECK_AUTH проверяет авторизацию.
def prepare_browser_session(driver, cookies_file, check_auth):
    logger.info("Проверяем сохраненные cookies...")
    cookies_loaded = load_cookies(driver, cookies_file)

    if cookies_loaded:
        logger.info("Cookies загружены. Начинаем парсинг.")
    else:
        logger.info("Начинаем парсинг без сохраненных cookies.")

    if check_auth and cookies_loaded:
        authenticated = verify_auth_session(driver)
        logger.info(
            "Результат проверки авторизации: %s",
            authenticated,
        )

# Создает случайную паузу перед следующим SKU
def pause_before_next_item(index, total, pause_seconds, pause_jitter_seconds):
    if index >= total:
        return

    pause = random.uniform(
        max(0.0, pause_seconds - pause_jitter_seconds),
        pause_seconds + pause_jitter_seconds,
    )

    if pause <= 0:
        return

    logger.info("Пауза перед следующим SKU: %.2f сек.", pause)
    time.sleep(pause)


def main():
    config = load_config()

    sku_file = config["sku_file"]
    results_file = config["results_file"]
    cookies_file = config["cookies_file"]
    use_xvfb = config["use_xvfb"]
    check_auth = config["check_auth"]
    pause_seconds = config["pause_seconds"]
    pause_jitter_seconds = config["pause_jitter_seconds"]

    failed_skus_file = results_file.with_name(
        f"{results_file.stem}_failed.txt"
    )

    results_exist = (
            results_file.is_file()
            and results_file.stat().st_size > 0
    )

    sku_list = load_sku_list(sku_file)
    completed_skus = load_completed_skus(results_file)

    driver, display = start_browser(use_xvfb)

    succeeded = 0
    skipped = 0
    already_done = 0
    failed_skus = []

    try:
        prepare_browser_session(driver, cookies_file, check_auth)

        mode = "a" if results_exist else "w"
        encoding = "utf-8-sig" if mode == "w" else "utf-8"

        with results_file.open(
            mode,
            newline="",
            encoding=encoding,
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=CSV_FIELDS,
            )

            if mode == "w":
                writer.writeheader()
                file.flush()

            total = len(sku_list)

            for index, sku in enumerate(sku_list, start=1):
                if sku in completed_skus:
                    already_done += 1
                    logger.info(
                        "[%s/%s] SKU %s уже есть в CSV. Пропускаем.",
                        index,
                        total,
                        sku,
                    )
                    continue

                logger.info(
                    "[%s/%s] Обрабатываем SKU %s",
                    index,
                    total,
                    sku,
                )

                item = parse_sku_via_browser(sku, driver)

                if item is not None:
                    writer.writerow(item)
                    file.flush()

                    completed_skus.add(sku)
                    succeeded += 1

                    logger.info(
                        "[УСПЕХ] SKU %s сохранен в CSV",
                        sku,
                    )
                else:
                    skipped += 1
                    failed_skus.append(sku)

                    logger.warning(
                        "[ПРОПУСК] SKU %s не обработан",
                        sku,
                    )

                pause_before_next_item(index, total, pause_seconds, pause_jitter_seconds)

    finally:
        try:
            logger.info(
                "Итог запуска: новых=%s, "
                "не обработано=%s, уже было в CSV=%s",
                succeeded,
                skipped,
                already_done,
            )
            logger.info(
                "Результаты: %s",
                results_file.resolve(),
            )
            report_failed_skus(failed_skus, failed_skus_file)
        finally:
            close_browser(driver, display)


if __name__ == "__main__":
    main()
