import re, sys, os, time, json, shutil, threading, smtplib

from bs4 import BeautifulSoup
from urllib.parse import urljoin
from openpyxl import load_workbook
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

# Selenium imports
from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


class LocalChScraper:
    def __init__(self, excel_path):
        self.excel_path = excel_path
        self.driver = self._init_driver()
        self.final_data = {}

    # -------------------- DRIVER --------------------
    def _init_driver(self):
        options = Options()
        options.add_argument("--start-maximized")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--headless=new")  # Run in headless mode
        options.add_argument("--disable-gpu")  # Disable GPU acceleration (optional)
        options.add_argument("--window-size=1920,1080")
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--disable-blink-features=AutomationControlled")

        service = Service()
        driver = webdriver.Edge(service=service, options=options)
        driver.implicitly_wait(6)  # slightly reduced implicit wait
        return driver

    # -------------------- LOGGING --------------------
    def log(self, message, category_suffix="_log"):
        """Log message with IST timestamp to unique per-run log file."""
        if not hasattr(self, "run_id"):
            self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        ist_time = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        timestamp = ist_time.strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] {message}"
        print(log_message)

        log_filename = f"logs/localch_live{category_suffix}_{self.run_id}.log"
        os.makedirs("logs", exist_ok=True)
        with open(log_filename, "a", encoding="utf-8") as f:
            f.write(log_message + "\n")

    # -------------------- CATEGORY NAME REGEX CLEANER --------------------
    def extract_category_name(self, text, lang):
        """
        Cleans heading text and extracts only the raw category name.
        Works across all supported languages automatically.
        """
        print(f"🔍 Extracting category name from [{lang.upper()}]: '{text}'")

        original_text = text.strip()
        text = re.sub(r'\s+', ' ', original_text)

        # All possible multilingual prefixes (covers EN, DE, FR, IT)
        multilingual_prefixes = [
            r"^Top\s+cities\s+for\s+",              # English
            r"^Top\s+Städte\s+für\s+",              # German
            r"^Top\s+villes\s+pour\s+",             # French
            r"^Città\s+più\s+importanti\s+per\s+",  # Italian
        ]

        # Remove any prefix found (regardless of lang)
        for pattern in multilingual_prefixes:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)

        # Clean leftover filler (like "in", "pour", etc.)
        text = re.sub(r"\s+(?:in|pour|für|per)\s*$", "", text, flags=re.IGNORECASE)

        # Final cleanup
        clean_name = text.strip(" .,-")

        # Normalize spacing and capitalization
        clean_name = re.sub(r'\s+', ' ', clean_name).strip()
        clean_name = clean_name[0].upper() + clean_name[1:] if clean_name else clean_name

        self.log(f"🧩 Cleaned category for [{lang.upper()}]: '{original_text}' → '{clean_name}'")
        return clean_name

  
    # -------------------- WAIT HELPERS --------------------
    def _wait_for_clickable(self, by, selector, timeout=8):
        return WebDriverWait(self.driver, timeout).until(EC.element_to_be_clickable((by, selector)))

    def _wait_for_presence(self, by, selector, timeout=8):
        return WebDriverWait(self.driver, timeout).until(EC.presence_of_element_located((by, selector)))

    def _safe_click(self, element):
        """Try normal click, then JS click as fallback."""
        try:
            element.click()
        except Exception:
            try:
                self.driver.execute_script("arguments[0].click();", element)
            except Exception:
                pass

    def _close_overlays(self):
        """Try to remove overlays/popups that block clicks."""
        try:
            self.driver.execute_script("""
                const popup = document.getElementById('notification-title');
                if (popup) popup.style.display = 'none';
                const modals = document.querySelectorAll('[role="dialog"], .modal, .overlay');
                modals.forEach(m => m.style.display = 'none');
            """)
        except Exception:
            pass

   

    # -------------------- FETCH MULTILANG CATEGORY NAMES --------------------
    def fetch_multilang_categories(self):
        lang_codes = ["en", "de", "fr", "it"]
        lang_labels = {"de": "DE", "fr": "FR", "it": "IT", "en": "EN"}
        category_translations = {}

        for lang in lang_codes:
            try:
                self._close_overlays()
                self.log(f"🌍 Switching to language [{lang.upper()}]")

                # Capture old heading
                try:
                    old_text = self.driver.find_element(By.TAG_NAME, "h1").text.strip()
                except Exception:
                    old_text = ""

                # Check current language to avoid unnecessary switching
                try:
                    current_lang = self.driver.find_element(By.CSS_SELECTOR, "button[aria-label='current language']").text.strip()
                except Exception:
                    current_lang = ""

                if current_lang.upper() != lang_labels[lang]:
                    # Open dropdown
                    dropdown = self._wait_for_clickable(By.CSS_SELECTOR, "button[aria-label='open menu']", timeout=8)
                    self._safe_click(dropdown)
                    time.sleep(0.5)

                    # Wait for language option
                    lang_option = WebDriverWait(self.driver, 8).until(
                        EC.visibility_of_element_located(
                            (By.XPATH, f"//ul[@role='listbox']//li//*[normalize-space(text())='{lang_labels[lang]}']")
                        )
                    )

                    # Click safely
                    try:
                        lang_option.click()
                    except:
                        self.driver.execute_script("arguments[0].click();", lang_option)

                    # Wait for heading change, but don't fail if it doesn't change
                    try:
                        WebDriverWait(self.driver, 8).until(
                            lambda d: d.find_element(By.TAG_NAME, "h1").text.strip() != old_text
                        )
                    except TimeoutException:
                        self.log(f"⚠️ Heading did not change for {lang.upper()}, continuing with current text...")

                # Extract category name
                heading = self.driver.find_element(By.TAG_NAME, "h1").text.strip()
                clean_name = self.extract_category_name(heading, lang)
                category_translations[f"name_{lang}"] = clean_name
                self.log(f"✅ Extracted [{lang.upper()}]: {clean_name}")

                time.sleep(0.5)

            except Exception as e:
                # self.driver.save_screenshot("debug_fetch_multilang.png")
                self.log(f"⚠️ Failed to fetch category for {lang.upper()}: {e}")
                category_translations[f"name_{lang}"] = None

        # Switch back to English at the end if not already in EN
        try:
            self._close_overlays()
            current_lang = self.driver.find_element(By.CSS_SELECTOR, "button[aria-label='current language']").text.strip()
            if current_lang.upper() != "EN":
                dropdown = self._wait_for_clickable(By.CSS_SELECTOR, "button[aria-label='open menu']", timeout=5)
                self._safe_click(dropdown)
                en_option = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//ul[@role='listbox']//li//*[normalize-space(text())='EN']")
                    )
                )
                self._safe_click(en_option)
                self.log("🔁 Switched back to English (finalized)")
                time.sleep(0.5)
        except Exception as e:
            self.log(f"⚠️ Failed to switch back to EN: {e}")

        return category_translations

    # -------------------- PARSE CATEGORY URL --------------------
    def _parse_category_url(self, url):
        try:
            parts = url.split('/')
            lang = parts[3]
            last_part = parts[-1]
            slug, name = last_part.split(',', 1)
            return {"language": lang.strip(), "slug": slug.strip(), "name": name.strip()}
        except Exception as e:
            self.log(f"⚠️ Error parsing URL '{url}': {e}")
            return None

    # -------------------- GET CATEGORY LETTERS --------------------
    def get_category_letters(self):
        """Fetch all active A-Z category links from the main categories page."""
        base_url = "https://www.local.ch/en/categories"
        self.driver.get(base_url)

        self.log("🌐 Visiting main categories page")

        # Wait for page to load
        try:
            WebDriverWait(self.driver, 8).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except Exception:
            pass

        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        letter_links = []

        # Find heading first
        heading = soup.find("h2", string=re.compile(r"Categories from A-Z", re.I))
        if heading:
            container = heading.find_next_sibling("div")
            if container:
                for a in container.find_all("a", href=True):
                    text = a.get_text(strip=True).upper()
                    if text.isalpha():  # only A-Z
                        href = a["href"]
                        full_url = urljoin(base_url, href)
                        letter_links.append({"letter": text, "url": full_url})
                        self.log(f"✅ Found letter link: {text} -> {full_url}")

        self.log(f"🔠 Total enabled letters: {len(letter_links)}")
        return letter_links

    def get_categories_for_letter(self, letter_data):
        """Extract category URLs for a specific letter (A, B, etc.)."""
        letter = letter_data["letter"]
        url = letter_data["url"]

        try:
            cookie_button =  WebDriverWait(self.driver, 2).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(@class,'ot-sdk-btn') or contains(text(),'Accept')]"))
            )
            cookie_button.click()
            time.sleep(1)
        except TimeoutException:
            print("No cookie popup found")

        self.log(f"➡️ Visiting letter page {letter}: {url}")
        self.driver.get(url)
        try:
            WebDriverWait(self.driver, 6).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except Exception:
            pass

        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        categories = []

        # Dynamically find the div containing categories
        container = soup.find("div", {"class": re.compile(r"c\d+")})
        if container:
            for a in container.find_all("a", href=True):
                name = a.get_text(strip=True)
                href = a["href"]
                # Only process real category links
                if href and href != "/en":
                    full_url = urljoin("https://www.local.ch", href)
                    slug = href.rstrip("/").split("/")[-1]
                    categories.append({"name": name, "slug": slug, "language": "en"})
                    self.log(f"📂 Found category: {name} -> {full_url}")

        self.log(f"✅ Found {len(categories)} categories for letter {letter}")
        return categories


    # -------------------- GET LETTERS --------------------
    def get_letters(self, cat_data):
        cat = cat_data["slug"]
        lang = cat_data["language"]
        first_letter = cat[0].lower()
        # Determine base path based on language
        category_path = "categories"
        if lang == "it":
            category_path = "categorie"
        elif lang == "de":
            category_path = "kategorien"

        base_url = f"https://www.local.ch/{lang}/{category_path}/{first_letter}/{cat}"

        self.log(f"🔍 Checking letters for: {cat} ({lang})")
        self.log(f"Base URL: {base_url}")

        self.driver.get(base_url)
        try:
            WebDriverWait(self.driver, 6).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except Exception:
            pass

        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        letters = []

        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            if len(text) == 1 and text.isalpha():
                letters.append(text)

        letters = sorted(list(set(letters)))
        self.log(f"✅ Letters found: {letters}")
        return letters

    # -------------------- GET CITIES --------------------
    def get_cities_for_letter(self, cat_data, letter):
        cat = cat_data["slug"]
        lang = cat_data["language"]
        first_letter = cat[0].lower()
        category_path = "categories"
        if lang == "it":
            category_path = "categorie"
        elif lang == "de":
            category_path = "kategorien"

        url = f"https://www.local.ch/{lang}/{category_path}/{first_letter}/{cat}/{letter}"

        self.log(f"➡️ Opening letter page: {url}")
        self.driver.get(url)
        try:
            WebDriverWait(self.driver, 6).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except Exception:
            pass

        cities = []
        try:
            city_anchors = self.driver.find_elements(By.XPATH, "//a[contains(text(), 'in ')]")
            if not city_anchors:
                city_anchors = self.driver.find_elements(By.XPATH, "//div[contains(@class,'cC')]//a[@href]")

            for a in city_anchors:
                try:
                    text = a.text.strip()
                    href = a.get_attribute("href")
                    if href and text:
                        city_name = text.split("in ")[-1] if "in " in text else text.split(".")[-1].strip()
                        city_url = urljoin(url, href)
                        cities.append({"name": city_name, "url": city_url})
                except Exception as e:
                    self.log(f"⚠️ Error parsing city link: {e}")

        except Exception as e:
            self.log(f"⚠️ Error finding cities: {e}")

        self.log(f"✅ Found {len(cities)} cities for letter '{letter}'")
        return cities

    # -------------------- EMAIL EXTRACTION --------------------
    def extract_email_from_detail(self):
        # try to scroll a bit to reveal email
        for _ in range(3):
            try:
                self.driver.execute_script("window.scrollBy(0, window.innerHeight / 3);")
                time.sleep(0.3)
            except Exception:
                pass

        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        email_tag = soup.find("a", {"data-testid": "contact-link"}, href=lambda x: x and x.startswith("mailto:"))
        if email_tag:
            return email_tag.get_text(strip=True)
        return None

    # -------------------- ADDRESS EXTRACTION --------------------
    def extract_address_from_detail(self):
        try:
            # Wait until address section appears
            address_section = WebDriverWait(self.driver, 6).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-cy='detail-map-preview']"))
            )

            # Use the driver element (faster than re-parsing) to get address pieces
            address_spans = address_section.find_elements(By.XPATH, ".//span[not(@class='sh')]")

            # Combine all visible address fragments
            address_parts = []
            for span in address_spans:
                text = span.text.strip()
                if text:
                    # Replace non-breaking space
                    text = text.replace(u'\xa0', ' ')
                    address_parts.append(text)

            address_text = ", ".join(address_parts) if address_parts else None
            return address_text

        except Exception as e:
            self.log(f"⚠️ Failed to extract address: {e}")
            return None

    # -------------------- City Extraction --------------------
    def clean_city_name(self, raw_name, language):
        """Extract city name for French entries, or return original otherwise."""
        if language == "fr" and "à " in raw_name:
            try:
                return raw_name.split("à ", 1)[-1].strip()
            except Exception:
                return raw_name.strip()
        if language == "it" and "a " in raw_name:
            try:
                return raw_name.split("a ", 1)[-1].strip()
            except Exception:
                return raw_name.strip()
        return raw_name.strip()

    # -------------------- VISIT CITY --------------------
    def visit_city_pages(self, cities, category_name, category_slug, column, language):
        suffix = f"_{category_slug[0].lower()}"

        for city in cities:
            try:
                clean_city = self.clean_city_name(city["name"], language)
                self.driver.get(city["url"])
                # small wait for page body
                try:
                    WebDriverWait(self.driver, 6).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                except Exception:
                    pass

                self.log(f"\n🌆 Opened city: {clean_city}\n", category_suffix=suffix)

                page_number = 1
                while True:
                    self.log(f"📄 Scraping page {page_number} for {clean_city}", category_suffix=suffix)

                    # get business cards
                    business_cards = self.driver.find_elements(By.XPATH, "//article[contains(@data-testid, 'list-element-desktop')]")
                    total_cards = len(business_cards)

                    self._recover_from_application_error()
                    
                    self.log(f"🔍 Found {total_cards} businesses on page {page_number}.", category_suffix=suffix)

                    if not business_cards:
                        break

                    for index in range(1, total_cards + 1):
                        try:
                            # refresh list each iteration to avoid stale elements
                            business_cards = self.driver.find_elements(By.XPATH, "//article[contains(@data-testid, 'list-element-desktop')]")
                            business = business_cards[index - 1]

                            # Flexible selector for the business title
                            h2_element = None
                            try:
                                h2_element = business.find_element(By.XPATH, ".//h2[@data-testid='title']")
                            except Exception:
                                try:
                                    h2_element = business.find_element(By.XPATH, ".//h2[contains(@class,'lk')]")
                                except Exception:
                                    self.log(f"⚠️ No title element for business #{index} — skipping.", category_suffix=suffix)
                                    continue

                            # Scroll into view and click
                            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", h2_element)
                            time.sleep(0.3)
                            try:
                                WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable((By.XPATH, ".")), message=None)
                            except Exception:
                                pass

                            try:
                                h2_element.click()
                            except Exception:
                                try:
                                    self.driver.execute_script("arguments[0].click();", h2_element)
                                except Exception:
                                    pass

                            # Wait until detail loads (look for h1)
                            try:
                                WebDriverWait(self.driver, 6).until(EC.presence_of_element_located((By.TAG_NAME, "h1")))
                            except Exception:
                                pass

                            # Extract data
                            email = self.extract_email_from_detail()
                            address = self.extract_address_from_detail()

                            if not email:
                                self.log(f"⚠️ No email found — skipping professional #{index}", category_suffix=suffix)
                                try:
                                    self.driver.back()
                                except Exception:
                                    pass
                                time.sleep(0.6)
                                continue

                            detail_soup = BeautifulSoup(self.driver.page_source, "html.parser")
                            business_title = detail_soup.find("h1").get_text(strip=True) if detail_soup.find("h1") else None
                            # business_rating_tag = detail_soup.select_one("span[data-testid='average-rating']")
                            # business_rating = business_rating_tag.get_text(strip=True) if business_rating_tag else None

                            business_rating = None

                            # Select the main ratings section, ignoring teaser sliders
                            rating_section = detail_soup.select_one("div[data-testid='ratings-section']")
                            if rating_section:
                                rating_tag = rating_section.select_one("span[data-testid='average-rating']")
                                if rating_tag:
                                    business_rating = rating_tag.get_text(strip=True)

                            # Ensure we don’t accidentally get ratings from teaser sliders
                            if not business_rating:
                                self.log("⚠️ No valid main rating found (skipped teaser ratings).")

                            business_data = {
                                "title": business_title,
                                "address": address,
                                "rating": business_rating,
                                "email": email,
                                "category": category_name,
                                "city": clean_city,
                                "url": self.driver.current_url
                            }

                            # Save data under the correct suffix (category letter)
                            self.final_data.setdefault(column, {}).setdefault(category_name, {}).setdefault("language", language)
                            self.final_data[column][category_name].setdefault(clean_city, []).append(business_data)

                            # Ensure 'scraping_data' directory exists
                            output_dir = os.path.join(os.getcwd(), "scraping_data")
                            os.makedirs(output_dir, exist_ok=True)

                            # Save inside scraping_data folder
                            filename = f"localch_live{suffix}.json"
                            file_path = os.path.join(output_dir, filename)

                            # Additional check for application error
                            self._recover_from_application_error()

                            self.save_to_json(self.final_data, file_path)

                            
                            # filename = f"localch_live{suffix}.json"
                            # self.save_to_json(self.final_data, filename)
                            self.log(f"💾 Saved business: {business_title}", category_suffix=suffix)

                            # go back to list
                            try:
                                self.driver.back()
                            except Exception:
                                pass
                            time.sleep(0.8)

                        except Exception as e:
                            self.log(f"⚠️ Error scraping business #{index} in {clean_city}: {e}", category_suffix=suffix)
                            try:
                                self.driver.back()
                            except Exception:
                                pass
                            time.sleep(0.8)

                    # Handle pagination safely
                    try:
                        next_page_anchor = self.driver.find_element(
                            By.XPATH, "//a[.//button[@id='load-next-page' and not(@disabled)]]"
                        )
                        next_url = next_page_anchor.get_attribute("href")
                        if not next_url:
                            self.log("✅ No next page URL found — finishing pagination.", category_suffix=suffix)
                            break

                        self.log(f"➡️ Navigating to next page: {next_url}", category_suffix=suffix)
                        self.driver.get(next_url)
                        page_number += 1
                        # wait small amount for new page
                        time.sleep(1.0)

                    except Exception as e:
                        self.log(f"✅ No next page found or error navigating: {e}", category_suffix=suffix)
                        break

            except Exception as e:
                self.log(f"⚠️ Error visiting {city['url']}: {e}", category_suffix=suffix)

    # -------------------- SAVE JSON --------------------
    def save_to_json(self, data, filename):
        formatted = {"categories": []}

        for col, categories in data.items():
            for category_name, category_data in categories.items():
                language = category_data.get("language", "en")
                translations = category_data.get("translations", {})

                category_entry = {
                    "name_en": translations.get("name_en"),
                    "name_de": translations.get("name_de"),
                    "name_fr": translations.get("name_fr"),
                    "name_it": translations.get("name_it"),
                    "slug": category_name.lower().replace(" ", "-"),
                    "language": language,
                    "cities": []
                }

                # Skip non-city keys
                for city_name, professionals in category_data.items():
                    if city_name in ("language", "translations"):
                        continue
                    if not isinstance(professionals, list):
                        continue

                    city_entry = {"name": city_name, "professionals": []}
                    for business in professionals:
                        city_entry["professionals"].append({
                            "title": business.get("title"),
                            "address": business.get("address"),
                            "rating": business.get("rating"),
                            "email": business.get("email"),
                            "category": business.get("category"),
                            "city": city_name,
                            "url": business.get("url")
                        })
                    category_entry["cities"].append(city_entry)

                formatted["categories"].append(category_entry)

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(formatted, f, indent=2, ensure_ascii=False)

        # ✅ Start backup scheduler once per file
        if not hasattr(self, "_backup_started"):
            self._backup_started = set()

        if filename not in self._backup_started:
            try:
                print(f"[🕒] Starting automatic backups every 30 minutes for {filename}")
                self.schedule_backup(filename)
                self._backup_started.add(filename)
            except Exception as e:
                print(f"[⚠️] Failed to schedule backup for {filename}: {e}")

    # -------------------- RUN --------------------
    def run(self):
        letter_links = self.get_category_letters()

        for letter_data in letter_links:
            letter = letter_data["letter"]
            suffix = f"_{letter.lower()}"
            self.final_data[letter] = {}

            categories = self.get_categories_for_letter(letter_data)
            if not categories:
                self.log(f"⚠️ No categories found for letter {letter}", category_suffix=suffix)
                continue

            for cat_data in categories:
                slug = cat_data["slug"]
                name = cat_data["name"]
                lang = cat_data["language"]

                self.log(f"\n🔍 Category: {name} ({lang})", category_suffix=suffix)

                # Continue using your existing logic:
                letters = self.get_letters(cat_data)

                # Fetch category names in 4 languages (this will switch languages and return to EN)
                translations = self.fetch_multilang_categories()
                self.final_data[letter][name] = {"translations": translations, "language": lang}

                if not letters:
                    self.log(f"⚠️ No subletters found for {name}", category_suffix=suffix)
                    continue

                for subletter in letters:
                    cities = self.get_cities_for_letter(cat_data, subletter)
                    if not cities:
                        continue

                    self.visit_city_pages(cities, name, slug, letter, lang)

        self.driver.quit()

    # -------------------- MAIN --------------------
    # export MAILTRAP_HOST="sandbox.smtp.mailtrap.io"
    # export MAILTRAP_PORT="587"
    # export MAILTRAP_USER="<your_mailtrap_username>"
    # export MAILTRAP_PASS="<your_mailtrap_password>"
    # export ALERT_EMAIL_FROM="alerts@localch.com"
    # export ALERT_EMAIL_TO="you@example.com"

    def send_error_email(self, subject, body):
        """Send alert email via Mailtrap SMTP."""
        host = "sandbox.smtp.mailtrap.io"
        port = int("587")
        username = "67dd00c85d8c1c"
        password = "6183effe0f8c29"
        sender = "alerts@localch.com"
        recipient = "sharma.ankur1620@gmail.com"

        msg = MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient

        try:
            with smtplib.SMTP(host, port) as server:
                server.starttls()
                server.login(username, password)
                server.sendmail(sender, [recipient], msg.as_string())
            self.log(f"📧 Mailtrap alert sent: {subject}")
        except Exception as e:
            self.log(f"⚠️ Failed to send Mailtrap alert: {e}")

    def _recover_from_application_error(self, max_refresh=2):
        """
        Detect if 'Application error' or client-side crash page is shown,
        and try refreshing up to `max_refresh` times.
        Returns True if recovered successfully, False otherwise.
        """
        try:
            for attempt in range(max_refresh):
                html = self.driver.page_source.lower()

                if "application error" in html or "client-side exception" in html:
                    self.log(f"⚠️ Application error detected (attempt {attempt + 1}/{max_refresh}) → refreshing page...")
                    self.driver.refresh()
                    time.sleep(4)

                else:
                    return True  # page looks fine

            self.log("❌ Page still broken after refresh attempts.")
            return False

        except Exception as e:
            self.log(f"⚠️ Exception during error recovery: {e}")
            return False

    def schedule_backup(self,json_file_path):
        """
        Creates timestamped backups of the given JSON file every 30 minutes.
        Automatically detects the category suffix (A–Z) using regex and
        places backups inside backup_json/Category_X folders.
        """
        if not os.path.exists(json_file_path):
            print(f"[⚠️] File {json_file_path} not found for backup.")
            return

        base_name = os.path.basename(json_file_path)
        name_without_ext = os.path.splitext(base_name)[0]

        # Extract suffix like "_a" or "_z" using regex
        match = re.search(r"_([a-zA-Z])$", name_without_ext)
        if match:
            suffix = match.group(1).upper()
        else:
            suffix = "Unknown"

        # ✅ Root backup folder
        root_backup = os.path.join(os.path.dirname(json_file_path), "backup_json")
        os.makedirs(root_backup, exist_ok=True)

        # ✅ Category folder inside backup_json
        folder_name = f"Category_{suffix}"
        backup_dir = os.path.join(root_backup, folder_name)
        os.makedirs(backup_dir, exist_ok=True)

        # ✅ Timestamped backup file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(backup_dir, f"{name_without_ext}_{timestamp}.json")

        try:
            shutil.copy2(json_file_path, backup_file)
            print(f"[✅] Backup created: {backup_file}")
        except Exception as e:
            print(f"[❌] Failed to create backup: {e}")
            # scraper.send_error_email("🚨 Local.ch Scraper Backup Failed", e)
        

        # Schedule again in 10 minutes (1800 seconds)
        threading.Timer(600, self.schedule_backup, args=[json_file_path]).start()


if __name__ == "__main__":
    scraper = LocalChScraper(excel_path="categories.xlsx")
    try:
        scraper.run()
    except Exception as e:
        error_message = f"❌ Scraper crashed!\n\nError: {e}\nTime: {datetime.now()}"
        scraper.log(error_message)
        scraper.send_error_email("🚨 Local.ch Scraper Failed", error_message)
        raise  # Optional: re-raise so system logs show failure
