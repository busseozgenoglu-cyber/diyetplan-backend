"""
BERNZ - Shopify Kargo Takip WhatsApp Bildirim Sistemi
=====================================================
WhatsApp Web üzerinden kendi numaranızla mesaj gönderir.

⚠️ ÖNEMLİ UYARILAR:
- WhatsApp'ın kullanım koşullarına aykırı olabilir
- Çok fazla mesaj hesabın banlanmasına yol açabilir
- Günde 50-100 mesajı geçmemeniz önerilir
- Bilgisayar açık ve WhatsApp Web bağlı olmalı

Kurulum:
1. pip install -r requirements.txt
2. Chrome tarayıcısı yüklü olmalı
3. python main.py
4. İlk çalıştırmada QR kod taratın

Gereksinimler:
- Python 3.8+
- Chrome Browser
- Aktif internet bağlantısı
"""

import requests
import time
import json
import logging
import os
from datetime import datetime
from typing import Optional, Dict, List
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from config import (
    SHOPIFY_STORE_URL,
    SHOPIFY_ACCESS_TOKEN,
    CHECK_INTERVAL_SECONDS,
    MESSAGE_TEMPLATE,
    LOG_FILE,
    DAILY_MESSAGE_LIMIT,
    DELAY_BETWEEN_MESSAGES
)

# Logging ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class ShopifyClient:
    """Shopify API istemcisi"""
    
    def __init__(self, store_url: str, access_token: str):
        self.store_url = store_url.rstrip('/')
        self.access_token = access_token
        self.headers = {
            'X-Shopify-Access-Token': access_token,
            'Content-Type': 'application/json'
        }
        self.api_version = '2024-01'
    
    def _make_request(self, endpoint: str) -> Optional[Dict]:
        """API isteği yapar"""
        url = f"{self.store_url}/admin/api/{self.api_version}/{endpoint}"
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Shopify API hatası: {e}")
            return None
    
    def get_orders(self, status: str = 'any', limit: int = 50) -> List[Dict]:
        """Siparişleri getirir"""
        endpoint = f"orders.json?status={status}&limit={limit}"
        data = self._make_request(endpoint)
        return data.get('orders', []) if data else []
    
    def get_fulfillments(self, order_id: int) -> List[Dict]:
        """Sipariş fulfillment bilgilerini getirir"""
        endpoint = f"orders/{order_id}/fulfillments.json"
        data = self._make_request(endpoint)
        return data.get('fulfillments', []) if data else []


class WhatsAppWebClient:
    """WhatsApp Web üzerinden mesaj gönderici"""
    
    def __init__(self, headless: bool = False):
        self.driver = None
        self.headless = headless
        self.session_dir = os.path.join(os.getcwd(), 'whatsapp_session')
        self.is_ready = False
        
    def start(self):
        """WhatsApp Web'i başlatır"""
        logger.info("🌐 WhatsApp Web başlatılıyor...")
        
        # Chrome ayarları
        options = Options()
        
        # Session'ı kaydet (her seferinde QR kod taramamak için)
        if not os.path.exists(self.session_dir):
            os.makedirs(self.session_dir)
        options.add_argument(f"--user-data-dir={self.session_dir}")
        
        if self.headless:
            options.add_argument("--headless=new")
        
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        
        # User agent
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        # Driver'ı başlat
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        
        # WhatsApp Web'e git
        self.driver.get("https://web.whatsapp.com")
        
        # QR kod veya ana ekran bekle
        self._wait_for_login()
    
    def _wait_for_login(self):
        """Giriş yapılmasını bekler"""
        logger.info("📱 WhatsApp Web'e giriş bekleniyor...")
        logger.info("   Eğer QR kod çıktıysa telefonunuzdan taratın.")
        
        try:
            # Ana ekranın yüklenmesini bekle (max 120 saniye)
            WebDriverWait(self.driver, 120).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[data-icon="new-chat-outline"]'))
            )
            logger.info("✅ WhatsApp Web'e giriş yapıldı!")
            self.is_ready = True
            time.sleep(3)  # Tam yüklenmesi için bekle
        except TimeoutException:
            logger.error("❌ Giriş zaman aşımına uğradı. QR kodu taradığınızdan emin olun.")
            self.is_ready = False
    
    def format_phone_number(self, phone: str) -> str:
        """Telefon numarasını formatlayın (90XXXXXXXXXX)"""
        phone = ''.join(filter(str.isdigit, phone))
        
        if phone.startswith('0'):
            phone = '90' + phone[1:]
        elif not phone.startswith('90') and len(phone) == 10:
            phone = '90' + phone
        
        return phone
    
    def send_message(self, to: str, message: str) -> bool:
        """WhatsApp Web üzerinden mesaj gönderir"""
        if not self.is_ready or not self.driver:
            logger.error("WhatsApp Web hazır değil!")
            return False
        
        phone = self.format_phone_number(to)
        
        try:
            # Direkt chat URL'i ile aç (numara kayıtlı olmasa bile çalışır)
            url = f"https://web.whatsapp.com/send?phone={phone}&text={self._encode_message(message)}"
            self.driver.get(url)
            
            # Mesaj kutusunun yüklenmesini bekle
            time.sleep(5)
            
            # "Telefon numarası geçersiz" kontrolü
            try:
                invalid_phone = self.driver.find_element(By.XPATH, "//*[contains(text(), 'Telefon numarası geçersiz') or contains(text(), 'Phone number shared via url is invalid')]")
                if invalid_phone:
                    logger.error(f"❌ Geçersiz telefon numarası: {phone}")
                    # OK butonuna bas
                    ok_button = self.driver.find_element(By.XPATH, "//div[@role='button']")
                    ok_button.click()
                    return False
            except NoSuchElementException:
                pass  # Hata mesajı yok, devam et
            
            # Gönder butonunu bekle ve tıkla
            send_button = WebDriverWait(self.driver, 30).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, '[data-icon="send"]'))
            )
            send_button.click()
            
            # Mesajın gönderildiğinden emin ol
            time.sleep(3)
            
            logger.info(f"✅ Mesaj gönderildi: {phone}")
            return True
            
        except TimeoutException:
            logger.error(f"❌ Zaman aşımı - mesaj gönderilemedi: {phone}")
            return False
        except Exception as e:
            logger.error(f"❌ Mesaj gönderme hatası ({phone}): {e}")
            return False
    
    def _encode_message(self, message: str) -> str:
        """Mesajı URL encode eder"""
        import urllib.parse
        return urllib.parse.quote(message)
    
    def close(self):
        """Tarayıcıyı kapatır"""
        if self.driver:
            self.driver.quit()
            logger.info("🔒 WhatsApp Web kapatıldı")


class NotificationTracker:
    """Gönderilen bildirimleri takip eder"""
    
    def __init__(self, file_path: str = 'sent_notifications.json'):
        self.file_path = file_path
        self.sent = self._load()
        self.ignored_file = 'ignored_trackings.json'
        self.ignored_trackings = self._load_ignored()
    
    def _load(self) -> Dict:
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    
    def _load_ignored(self) -> set:
        """Başlangıçta yok sayılan tracking kodlarını yükle"""
        try:
            with open(self.ignored_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return set(data.get('tracking_keys', []))
        except (FileNotFoundError, json.JSONDecodeError):
            return set()
    
    def _save_ignored(self):
        """Yok sayılan tracking kodlarını kaydet"""
        with open(self.ignored_file, 'w', encoding='utf-8') as f:
            json.dump({
                'tracking_keys': list(self.ignored_trackings), 
                'created_at': datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    
    def is_initialized(self) -> bool:
        """Sistem daha önce başlatılmış mı"""
        return os.path.exists(self.ignored_file)
    
    def add_ignored_tracking(self, order_id: int, tracking_number: str):
        """Sipariş+tracking kombinasyonunu yok sayılanlar listesine ekle"""
        key = f"{order_id}_{tracking_number}"
        self.ignored_trackings.add(key)
    
    def save_ignored_orders(self):
        """Yok sayılanları dosyaya kaydet"""
        self._save_ignored()
    
    def is_ignored(self, order_id: int, tracking_number: str) -> bool:
        """Bu sipariş+tracking kombinasyonu yok sayılanlar listesinde mi?"""
        key = f"{order_id}_{tracking_number}"
        return key in self.ignored_trackings
    
    def _save(self):
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(self.sent, f, ensure_ascii=False, indent=2)
    
    def is_sent(self, order_id: int, tracking_number: str) -> bool:
        key = f"{order_id}_{tracking_number}"
        return key in self.sent
    
    def mark_sent(self, order_id: int, tracking_number: str, phone: str):
        key = f"{order_id}_{tracking_number}"
        self.sent[key] = {
            'order_id': order_id,
            'tracking_number': tracking_number,
            'phone': phone,
            'sent_at': datetime.now().isoformat()
        }
        self._save()
    
    def get_today_count(self) -> int:
        """Bugün gönderilen mesaj sayısını döner"""
        today = datetime.now().date().isoformat()
        count = 0
        for key, value in self.sent.items():
            if value.get('sent_at', '').startswith(today):
                count += 1
        return count


class BernzNotifier:
    """Ana bildirim sistemi"""
    
    def __init__(self):
        self.shopify = ShopifyClient(SHOPIFY_STORE_URL, SHOPIFY_ACCESS_TOKEN)
        self.whatsapp = WhatsAppWebClient(headless=False)  # Görünür mod
        self.tracker = NotificationTracker()
    
    def initialize_existing_orders(self):
        """Halihazırda kargoya verilmiş siparişlerin tracking kodlarını yok say"""
        if self.tracker.is_initialized():
            ignored_count = len(self.tracker.ignored_trackings)
            logger.info(f"✅ Sistem daha önce başlatılmış. {ignored_count} eski kargo kodu yok sayılıyor.")
            return
        
        logger.info("🔄 İlk çalıştırma - mevcut durum taranıyor...")
        logger.info("📋 Siparişler alınıyor...")
        
        try:
            orders = self.shopify.get_orders(status='any', limit=250)
        except Exception as e:
            logger.error(f"❌ Siparişler alınamadı: {e}")
            return
        
        logger.info(f"📋 {len(orders)} sipariş bulundu, taranıyor...")
        
        ignored_count = 0
        pending_count = 0
        
        for i, order in enumerate(orders):
            if (i + 1) % 10 == 0:
                logger.info(f"   İşleniyor: {i + 1}/{len(orders)}")
            
            fulfillment_status = order.get('fulfillment_status')
            
            # Sadece zaten kargoya verilmiş olanların TRACKING KODLARINI yok say
            if fulfillment_status in ['fulfilled', 'partial']:
                try:
                    fulfillments = self.shopify.get_fulfillments(order['id'])
                    for fulfillment in fulfillments:
                        tracking_number = fulfillment.get('tracking_number')
                        if tracking_number:
                            self.tracker.add_ignored_tracking(order['id'], tracking_number)
                            ignored_count += 1
                except Exception as e:
                    logger.warning(f"   Sipariş {order['name']} fulfillment alınamadı: {e}")
            else:
                pending_count += 1
        
        self.tracker.save_ignored_orders()
        logger.info(f"✅ {ignored_count} mevcut kargo kodu yok sayıldı.")
        logger.info(f"📦 {pending_count} bekleyen sipariş takip edilecek.")
        logger.info("📢 Yeni kargo kodu eklendiğinde bildirim gönderilecek.")
    
    def format_message(self, customer_name: str, order_number: str, 
                       tracking_number: str, tracking_url: str) -> str:
        """Mesaj şablonunu doldurur"""
        return MESSAGE_TEMPLATE.format(
            customer_name=customer_name,
            order_number=order_number,
            tracking_number=tracking_number,
            tracking_url=tracking_url
        )
    
    def process_order(self, order: Dict) -> bool:
        """Tek bir siparişi işler"""
        order_id = order['id']
        order_number = order['name']
        
        # Günlük limit kontrolü
        today_count = self.tracker.get_today_count()
        if today_count >= DAILY_MESSAGE_LIMIT:
            logger.warning(f"⚠️ Günlük mesaj limiti doldu ({DAILY_MESSAGE_LIMIT})")
            return False
        
        # Müşteri bilgileri
        customer = order.get('customer', {})
        customer_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        if not customer_name:
            customer_name = "Değerli Müşterimiz"
        
        # Telefon numarası
        shipping = order.get('shipping_address', {})
        phone = shipping.get('phone') or customer.get('phone')
        
        if not phone:
            logger.warning(f"Sipariş {order_number}: Telefon numarası bulunamadı")
            return False
        
        # Kargo bilgileri
        fulfillments = self.shopify.get_fulfillments(order_id)
        
        for fulfillment in fulfillments:
            tracking_number = fulfillment.get('tracking_number')
            tracking_url = fulfillment.get('tracking_url', '')
            
            if not tracking_number:
                continue
            
            # Bu tracking kodu başlangıçta yok sayılanlar listesinde mi?
            if self.tracker.is_ignored(order_id, tracking_number):
                continue
            
            # Bu tracking kodu için daha önce bildirim gönderilmiş mi?
            if self.tracker.is_sent(order_id, tracking_number):
                continue
            
            if not tracking_url:
                tracking_url = f"https://www.suratkargo.com.tr/KargoTakip?Ession={tracking_number}"
            
            message = self.format_message(
                customer_name=customer_name,
                order_number=order_number,
                tracking_number=tracking_number,
                tracking_url=tracking_url
            )
            
            if self.whatsapp.send_message(phone, message):
                self.tracker.mark_sent(order_id, tracking_number, phone)
                logger.info(f"📦 Sipariş {order_number} için bildirim gönderildi (Kargo: {tracking_number})")
                
                # Mesajlar arası bekleme (ban'dan kaçınmak için)
                logger.info(f"⏳ {DELAY_BETWEEN_MESSAGES} saniye bekleniyor...")
                time.sleep(DELAY_BETWEEN_MESSAGES)
                return True
        
        return False
    
    def check_orders(self):
        """Tüm siparişleri kontrol eder"""
        logger.info("🔍 Siparişler kontrol ediliyor...")
        
        orders = self.shopify.get_orders(status='any', limit=50)
        
        notifications_sent = 0
        for order in orders:
            fulfillment_status = order.get('fulfillment_status')
            if fulfillment_status in ['fulfilled', 'partial']:
                if self.process_order(order):
                    notifications_sent += 1
        
        today_count = self.tracker.get_today_count()
        logger.info(f"✅ Kontrol tamamlandı. {notifications_sent} bildirim gönderildi. (Bugün toplam: {today_count})")
    
    def run(self):
        """Ana döngü"""
        logger.info("=" * 50)
        logger.info("🚀 BERNZ WhatsApp Web Bildirim Sistemi")
        logger.info(f"📱 Kontrol aralığı: {CHECK_INTERVAL_SECONDS} saniye")
        logger.info(f"📊 Günlük limit: {DAILY_MESSAGE_LIMIT} mesaj")
        logger.info("=" * 50)
        
        # Mevcut siparişleri atla (ilk çalıştırma)
        self.initialize_existing_orders()
        
        # WhatsApp Web'i başlat
        self.whatsapp.start()
        
        if not self.whatsapp.is_ready:
            logger.error("WhatsApp Web başlatılamadı!")
            return
        
        try:
            while True:
                try:
                    self.check_orders()
                except Exception as e:
                    logger.error(f"❌ Hata: {e}")
                
                logger.info(f"⏳ {CHECK_INTERVAL_SECONDS} saniye bekleniyor...")
                time.sleep(CHECK_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logger.info("\n👋 Program sonlandırılıyor...")
        finally:
            self.whatsapp.close()


def main():
    """Ana fonksiyon"""
    notifier = BernzNotifier()
    notifier.run()


if __name__ == '__main__':
    main()
