# FirstDependcy/ManagerPLC/plc_communication_base.py
import struct
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Any, Optional
from enum import Enum
import os
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from PyQt5.QtCore import QTimer, QObject, pyqtSignal, QThread
import threading


# 1. Lisans Enum ve Global Değişken Tanımı (Main veya Ayar dosyasından da gelebilir)
class DemoMode(Enum):
    Demo = 1  # Demo Modu
    Licence = 2  # Lisanslı Modu


CurrentLicence = DemoMode.Demo  # <--- Test için Demo modunda başlattık

class PLCType(Enum):
    MODBUS = "Modbus"
    SIEMENS = "Siemens"


class AsyncWorker(QObject):
    """Qt ile asyncio arasında köprü"""
    finished = pyqtSignal()
    value_updated = pyqtSignal(dict)  # Değişen değerler

    def __init__(self, plc_manager):
        super().__init__()
        self.plc_manager = plc_manager
        self.loop = None
        self.thread = None

    def start(self):
        """Ayrı bir thread'de asyncio event loop başlat"""
        self.thread = QThread()
        self.moveToThread(self.thread)
        self.thread.started.connect(self._run_async_loop)
        self.thread.start()

    def _run_async_loop(self):
        """Thread içinde asyncio loop çalıştır"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def stop(self):
        """Async loop'u durdur"""
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread:
            self.thread.quit()
            self.thread.wait()

    async def _async_read_and_notify(self, plc_manager):
        """Asenkron okuma ve bildirim"""
        await plc_manager._async_read_all_optimized()
        changed = plc_manager._check_changes_from_cache()
        if changed:
            self.value_updated.emit(changed)
        self.finished.emit()

    def trigger_read(self):
        """Timer'dan tetiklenen okuma işlemi"""
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._async_read_and_notify(self.plc_manager),
                self.loop
            )

import re
import struct
class BasePLCManager(ABC):
    def __init__(self, json_path: str, initial_config: dict = None):
        self.FirstReadValues = False
        self.json_path = json_path
        self.custom_data = initial_config if initial_config is not None else {}

        self.is_connected = False
        self.total_assigned_tasks = 0
        # Callback sistemi
        self._value_callback = None
        self._watch_timer = None
        self._watch_items = []
        self._last_values = {}
        self.optimized_groups = []  # Ardışıl gruplar (sadece okuma optimizasyonu)
        self.values_lookup = {}  # {address: value} cache

        # Async ile ilgili yeni özellikler
        self.is_reading = False
        self.is_Licence = CurrentLicence
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.async_worker = None
        self._use_async = False  # Varsayılan async kapalı (eski sistemle uyum)
    @abstractmethod
    def connect(self, connection_params: dict) -> bool:
        pass

    @abstractmethod
    def disconnect(self) -> bool:
        pass

    @abstractmethod
    def read_address(self, address: Any, bit_index: Optional[int] = None,count:int=1) -> Any:
        pass

    @abstractmethod
    def write_address(self, address: Any, value: Any, bit_index: Optional[int] = None) -> bool:
        pass

    @abstractmethod
    def get_plc_type(self) -> PLCType:
        pass

    @abstractmethod
    def optimize_groups(self):
        pass


    def enable_async_mode(self):
        """Async modu aktifleştir (timer bloklamasın)"""
        self._use_async = True
        self.async_worker = AsyncWorker(self)
        self.async_worker.value_updated.connect(self._on_async_values_changed)
        self.async_worker.start()
        print("✅ Async mod aktifleştirildi")

    def disable_async_mode(self):
        """Async modu kapat (eski sisteme dön)"""
        if self.async_worker:
            self.async_worker.stop()
            self.async_worker = None
        self._use_async = False
        print("✅ Async mod kapatıldı")

    def _on_async_values_changed(self, changed_values):
        """Async okuma sonucu değişen değerler geldiğinde"""
        if self._value_callback:
            self._value_callback(changed_values)

    # ========== ASYNC METODLAR ==========

    async def _async_read_all_optimized(self):
        """Asenkron olarak tüm değerleri oku (Thread pool'da)"""
        if self.is_reading:
            return  # Zaten okuma devam ediyor

        self.is_reading = True
        try:
            loop = asyncio.get_event_loop()
            # Thread pool'da bloklayıcı Modbus okuması yap
            await loop.run_in_executor(self.executor, self._read_all_optimized_sync)
        finally:
            self.is_reading = False

    def _read_all_optimized_sync(self):
        self.values_lookup.clear()
        self.FirstReadValues = True

        # --- SIEMENS İÇİN BİG-ENDIAN UNPACK FORMATLARI ---
        # >f: 32-bit Float (Real)
        # >i: 32-bit Signed Integer (Dint)
        # >h: 16-bit Signed Integer (Int)
        # >B: 8-bit Unsigned Byte
        SIEMENS_FORMATS = {
            'Real': '>f',
            'Dint': '>i',
            'Int': '>h',
            'Byte': '>B'
        }

        for group in self.optimized_groups:

            # =================================================================
            # 1. DURUM: SIEMENS PLC OPTİMİZASYON DÖNGÜSÜ
            # =================================================================
            if 'db_num' in group:
                db_num = group['db_num']
                start_byte = group['start_byte']
                byte_count = group['count']
                sub_items = group['items']

                base_addr = f"DB{db_num}.DBB{start_byte}"

                # PLC'den ham byte listesini oku (Örn: [66, 72, 0, 0])
                raw_bytes = self.read_address(base_addr, None, byte_count)

                if raw_bytes:
                    if not isinstance(raw_bytes, list):
                        raw_bytes = [raw_bytes]

                    # 🟢 [LOG EKLEME]: Okunan Ham Byte Verisini Yazdır
                    print(f"📥 [PLC Ham Veri] -> Adres: DB{db_num}.DBB{start_byte} | Okunan Byte Listesi: {raw_bytes}")

                    byte_array = bytes(raw_bytes)

                    for item in sub_items:
                        match = re.search(r"\d+$", item['address'])
                        if not match:
                            continue

                        item_start_byte = int(match.group())
                        offset = item_start_byte - start_byte

                        item_type = item.get('type', 'Real')
                        fmt = SIEMENS_FORMATS.get(item_type, '>f')
                        size = struct.calcsize(fmt)

                        item_bytes = byte_array[offset: offset + size]

                        if len(item_bytes) == size:
                            # Byte -> Float dönüşümü
                            parsed_value = struct.unpack(fmt, item_bytes)[0]

                            if isinstance(parsed_value, float):
                                parsed_value = round(parsed_value, 4)

                            # 🟢 [LOG EKLEME]: Çözümlenmiş Gerçek Değeri Yazdır
                            print(f"✨ [Çözümlendi] -> {item['address']} ({item_type}) = {parsed_value}")

                            self.values_lookup[item['address']] = parsed_value

            # =================================================================
            # 2. DURUM: MODBUS OPTİMİZASYON DÖNGÜSÜ (ORİJİNAL KODUNUZ)
            # =================================================================
            else:
                if group['count'] > 1:
                    start_addr = group['start_address']
                    count = group['count']
                    values = self.read_address(start_addr, 0, count)

                    if values and len(values) == count:
                        for i, value in enumerate(values):
                            address = start_addr + i
                            self.values_lookup[address] = value
                else:
                    item = group['items'][0]
                    address = item['address']
                    value = self.read_address(address, None, 1)
                    if value:
                        self.values_lookup[address] = value[0] if isinstance(value, list) else value


    def _check_changes_from_cache(self):
        changed = {}
        WORD_SWAP = True

        items_to_process = self._watch_items
        if 'CurrentLicence' in globals() and 'DemoMode' in globals():
            if globals()['CurrentLicence'] == globals()['DemoMode'].Demo:
                items_to_process = self._watch_items[:8]

        for item in items_to_process:
            address = item['address']
            bit_index = item.get('bit_index')
            scaling = item.get('scaling', 1.0)
            names = item['names']
            data_type = item.get('type', 'Real')

            value = None

            # =================================================================
            # 🟢 1. SENARYO: PLC TİPİ SIEMENS İSE
            # =================================================================
            if self.get_plc_type() == PLCType.SIEMENS:
                value = self.values_lookup.get(address)
                if value is None:
                    value = 0  # Siemens için de eksik veri varsa 0 kabul et

            # =================================================================
            # 🔵 2. SENARYO: PLC TİPİ MODBUS İSE (Geliştirilmiş Hata Korumalı Alan)
            # =================================================================
            else:
                try:
                    modbus_addr = int(address)
                except:
                    continue

                if data_type in ['Real', 'real', 'Dint', 'dint']:
                    reg_count = 2
                elif data_type in ['Long', 'long']:
                    reg_count = 4
                else:
                    reg_count = 1

                    # --- CACHE'DEN ARDIŞIK REGISTER'LARI TOPLA ---
                regs = []
                for offset in range(reg_count):
                    # 🎯 KRİTİK DEĞİŞİKLİK: .get(adres, 0) kullanarak
                    # Eğer adres cache'de (lookup) yoksa hata fırlatma, direkt 0 kabul et!
                    reg_val = self.values_lookup.get(modbus_addr + offset, 0)
                    regs.append(reg_val)

                # Artık eksik veri aramayı durdurup 'continue' ile döngüyü kırmıyoruz.
                # Olmayan veri yerine [0, 0] dolduruldu ve aşağıda çözülecek.

                # --- WORD'LERİ BİRLEŞTİR VE GERÇEK TİPE DÖNÜŞTÜR ---
                try:
                    if reg_count == 1:
                        value = regs[0]
                    elif reg_count == 2:
                        if WORD_SWAP:
                            payload = struct.pack('>HH', regs[1], regs[0])
                        else:
                            payload = struct.pack('>HH', regs[0], regs[1])

                        if data_type in ['Real', 'real']:
                            value = struct.unpack('>f', payload)[0]
                        else:
                            value = struct.unpack('>i', payload)[0]
                    elif reg_count == 4:
                        if WORD_SWAP:
                            payload = struct.pack('>HHHH', regs[3], regs[2], regs[1], regs[0])
                        else:
                            payload = struct.pack('>HHHH', regs[0], regs[1], regs[2], regs[3])
                        value = struct.unpack('>q', payload)[0]
                except Exception as e:
                    # Olası bir struct paketleme hatasında sistemi durdurma, 0 bas geç
                    value = 0.0 if data_type in ['Real', 'real'] else 0

            # =================================================================
            # 🟡 3. ORTAK ADIM: BİT, ÖLÇEKLENDİRME VE DEĞİŞİM KONTROLÜ
            # =================================================================
            if value is not None:
                if bit_index is not None:
                    if isinstance(value, (int, float)):
                        value = (int(value) >> bit_index) & 1

                try:
                    value = float(value) * scaling
                    if isinstance(value, float):
                        value = round(value, 4)
                except:
                    pass

                for name in names:
                    last = self._last_values.get(name)
                    if last != value:
                        changed[name] = value
                        self._last_values[name] = value
                        print(f"📥 [Modbus Ham Veri] -> Adres: {address} | Değer: {value}")

        return changed

    # def _check_changes_from_cache(self):
    #     # Değişen değerleri toplamak için boş sözlüğü fonksiyon başında tanımlıyoruz
    #     changed = {}
    #
    #     # Sınıf içinde WORD_SWAP tanımlı değilse güvenli liman olarak False kabul et
    #     WORD_SWAP = getattr(self, "WORD_SWAP", False)
    #
    #     # Demo kısıtlaması: Global değişkene göre listeyi kırpıyoruz
    #     items_to_process = self._watch_items
    #     if 'CurrentLicence' in globals() and 'DemoMode' in globals():
    #         if globals()['CurrentLicence'] == globals()['DemoMode'].Demo:
    #             items_to_process = self._watch_items[:8]
    #
    #     for item in items_to_process:  # Döngü kırpılmış liste üzerinden döner
    #         address = item['address']
    #         bit_index = item.get('bit_index')
    #         scaling = item.get('scaling', 1.0)
    #         names = item['names']
    #         data_type = item.get('type', 'Real')
    #
    #         value = None
    #
    #         # =================================================================
    #         # 🟢 1. SENARYO: PLC TİPİ SIEMENS İSE (Direkt Adres Eşleşmesi)
    #         # =================================================================
    #         if self.get_plc_type() == PLCType.SIEMENS:
    #             # _read_all_optimized_sync veriyi direkt 'DB200.DBD4' formatında kaydettiği için
    #             # karmaşık byte döngülerine girmeden direkt adresten değeri çekiyoruz.
    #             value = self.values_lookup.get(address)
    #
    #             # Eğer değer henüz okunmadıysa atla
    #             if value is None:
    #                 continue
    #
    #         # =================================================================
    #         # 🔵 2. SENARYO: PLC TİPİ MODBUS İSE (Register Tabanlı Kodunuz)
    #         # =================================================================
    #         else:
    #             WORD_SWAP =  True
    #             if data_type in ['Real', 'real', 'Dint', 'dint']:
    #                 reg_count = 2  # 32-bit (2 adet 16-bit register)
    #             elif data_type in ['Long', 'long']:
    #                 reg_count = 4  # 64-bit (4 adet 16-bit register)
    #             else:
    #                 reg_count = 1  # 16-bit standart Int / Word
    #
    #             # --- CACHE'DEN ARDIŞIK REGISTER'LARI TOPLA ---
    #             regs = []
    #             has_missing = False
    #             for offset in range(reg_count):
    #                 reg_val = self.values_lookup.get(address + offset)
    #                 if reg_val is None:
    #                     has_missing = True
    #                     break
    #                 regs.append(reg_val)
    #
    #             if has_missing:
    #                 continue
    #
    #             # --- WORD'LERİ BİRLEŞTİR VE GERÇEK TİPE DÖNÜŞTÜR ---
    #             try:
    #                 if reg_count == 1:
    #                     value = regs[0]
    #                     # 🟢 [LOG EKLEME]: 16-bit Standart Register Okuma Logu
    #                     print(f"📥 [Modbus Ham Veri] -> Adres: {address} | Değer: {regs}")
    #
    #                 elif reg_count == 2:
    #                     if WORD_SWAP:
    #                         payload = struct.pack('>HH', regs[1], regs[0])  # CDAB formatı
    #                     else:
    #                         payload = struct.pack('>HH', regs[0], regs[1])  # ABCD formatı
    #
    #                     if data_type in ['Real', 'real']:
    #                         value = struct.unpack('>f', payload)[0]
    #                     else:
    #                         value = struct.unpack('>i', payload)[0]
    #
    #                     # 🟢 [LOG EKLEME]: 32-bit (Real/Dint) Birleştirme Logu
    #                     print(f"📥 [Modbus Ham Veri] -> Başlangıç Adresi: {address} ({reg_count} Register) | "
    #                           f"Toplanan Register Değerleri: {regs} | WORD_SWAP: {WORD_SWAP}")
    #
    #                 elif reg_count == 4:
    #                     if WORD_SWAP:
    #                         payload = struct.pack('>HHHH', regs[3], regs[2], regs[1], regs[0])
    #                     else:
    #                         payload = struct.pack('>HHHH', regs[0], regs[1], regs[2], regs[3])
    #                     value = struct.unpack('>q', payload)[0]
    #
    #                     # 🟢 [LOG EKLEME]: 64-bit (Long) Birleştirme Logu
    #                     print(f"📥 [Modbus Ham Veri] -> Başlangıç Adresi: {address} ({reg_count} Register) | "
    #                           f"Toplanan Register Değerleri: {regs}")
    #
    #                 # 🟢 [LOG EKLEME]: Çözümlenmiş Gerçek Nihai Değeri Yazdır
    #                 # (Hassasiyet yuvarlamasını printf logunda temiz görmek için burada da yapıyoruz)
    #                 log_value = round(value, 4) if isinstance(value, float) else value
    #                 print(f"✨ [Modbus Çözümlendi] -> Adres: {address} | Tip: {data_type} | Gerçek Değer = {log_value}")
    #                 print("-" * 70)  # Okunabilirliği artırmak için ayırıcı çizgi
    #
    #             except Exception as e:
    #                 print(f"❌ Cache Modbus çözme hatası ({address}): {e}")
    #                 continue
    #
    #         # =================================================================
    #         # 🟡 3. ORTAK ADIM: BİT, ÖLÇEKLENDİRME VE DEĞİŞİM KONTROLÜ
    #         # =================================================================
    #         if bit_index is not None and value is not None:
    #             if isinstance(value, (int, float)):
    #                 value = (int(value) >> bit_index) & 1
    #
    #         try:
    #             if value is not None:
    #                 value = float(value) * scaling
    #                 # Hassasiyet hatalarını önlemek için float değerleri yuvarla
    #                 if isinstance(value, float):
    #                     value = round(value, 4)
    #         except:
    #             pass
    #
    #         # Değişim kontrolü ve _last_values güncellemesi (WatchWindow burayı okuyor)
    #         if value is not None:
    #             for name in names:
    #                 last = self._last_values.get(name)
    #                 if last != value:
    #                     changed[name] = value
    #                     self._last_values[name] = value
    #
    #     return changed


    # ========== TIMER METODU (GÜNCELLENDİ) ==========     Önder Coşkun  PLC değişimleri buradan tetiklenir

    def _check_and_notify(self):
        """Timer her çalıştığında (Qt ana thread'inde)"""
        if not self.is_connected:
            return

        if self._use_async and self.async_worker:
            # ASYNC MOD: Timer hemen döner, okuma arka planda
            self.async_worker.trigger_read()
            # Timer burada hemen döner! ✅
        else:
            # SYNC MOD: Eski sistem gibi çalışır (bloklayıcı)
            if self.FirstReadValues is False:
                self.read_all_optimized()
                self.FirstReadValues = True

            changed = self._check_changes_from_cache()

            if changed and self._value_callback:
                self._value_callback(changed)

    # ========== MEVCUT METODLAR (GÜNCELLENDİ) ==========

    def read_all_optimized(self):
        """Senkron okuma (eski sistem)"""
        self._read_all_optimized_sync()

    def start_watching(self, interval_ms: int = 500):
        """İzlemeyi başlat"""
        if self._watch_timer:
            self._watch_timer.stop()

        self._watch_timer = QTimer()
        self._watch_timer.timeout.connect(self._check_and_notify)
        self._watch_timer.start(interval_ms)

        mode = "ASYNC" if self._use_async else "SYNC"
        print(f"📡 İzleme başladı: {interval_ms} ms aralıkla, {len(self._watch_items)} adres, MOD: {mode}")

    def stop_watching(self):
        """İzlemeyi durdur"""
        if self._watch_timer:
            self._watch_timer.stop()
            self._watch_timer = None

        if self._use_async and self.async_worker:
            self.disable_async_mode()
        self.FirstReadValues = False
        print("📡 İzleme durduruldu")




    def _load_initial_config(self):
            """JSON'u tek bir seferde oku ve sınıfa yükle"""
            if os.path.exists(self.json_path):
                try:
                    with open(self.json_path, 'r', encoding='utf-8') as f:
                        # 'json' ismini değişken olarak kullanmadığınızdan emin olun (modül olan json)
                        self.custom_data = json.load(f)

                    saved_type = self.custom_data.get("_plc_type")
                    if saved_type:
                        print(f"📁 Konfigürasyon yüklendi. PLC Tipi: {saved_type}")
                except Exception as e:
                    print(f"❌ Konfigürasyon yükleme hatası: {e}")

    def _load_plc_type(self):
        """JSON'dan kayıtlı PLC tipini yükle"""
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                saved_type = data.get("_plc_type")
                if saved_type:
                    print(f"📁 Kayıtlı PLC tipi: {saved_type}")
            except:
                pass

    def save_plc_type(self):
        """PLC tipini JSON'a kaydet"""
        try:
            # Mevcut veriyi oku
            data = {}
            if os.path.exists(self.json_path):
                with open(self.json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

            # PLC tipini ekle/güncelle
            data["_plc_type"] = self.get_plc_type().value
            data["_plc_type_enum"] = self.get_plc_type().name

            # Kaydet
            with open(self.json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            print(f"💾 PLC tipi kaydedildi: {self.get_plc_type().value}")
        except Exception as e:
            print(f"❌ PLC tipi kaydedilemedi: {e}")

    def set_value_callback(self, callback: callable):
        """
        Değer değiştiğinde çağrılacak fonksiyonu ayarlar.

        Args:
            callback: function(changed_values: dict) -> None
                      changed_values = {'name1': yeni_deger, 'name2': yeni_deger2}
        """
        self._value_callback = callback
        # self._value_callback(callback)

    # def add_watch_item(self, name: str, address: Any, bit_index: int = None,color_mappings: dict = None, scaling: float = 1.0):
    #     """İzlenecek adres ekler"""
    #     self._watch_items.append({
    #         'name': name,
    #         'address': address,
    #         'bit_index': bit_index,
    #         'color_mappings':color_mappings,
    #         'scaling': scaling
    #     })
    def add_watch_item(self, name: str, address: Any, type:str, bit_index: int = None, color_mappings: dict = None,
                       scaling: float = 1.0):
        """İzlenecek adres ekler - Aynı adres varsa isimleri birleştirir"""

        # Önce bu adres ve bit_index daha önce eklenmiş mi kontrol et
        for item in self._watch_items:
            if item['address'] == address and item['bit_index'] == bit_index:
                # Bulduk! Eğer bu isim listede yoksa ekle
                if name not in item['names']:
                    item['names'].append(name)
                return  # Mevcut öğeyi güncelledik, fonksiyondan çıkabiliriz

        # Eğer adres bulunamadıysa yeni bir kayıt oluştur
        # Dikkat: Artık 'name' yerine 'names' (liste) kullanıyoruz
        self._watch_items.append({
            'names': [name],  # İsimleri liste olarak tutuyoruz
            'address': address,
            'type'   : type,   # real, int, dint
            'bit_index': bit_index,
            'color_mappings': color_mappings,
            'scaling': scaling
        })

    def load_custom_data(self):
        self.custom_data = {}
        if not os.path.exists(self.json_path):
            print(f"ℹ️ JSON dosyası bulunamadı: {self.json_path}")
            return

        try:
            with open(self.json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            saved_type = data.get("_plc_type")
            current_type = self.get_plc_type().value

            if saved_type and saved_type != current_type:
                print(f"⚠️ PLC tipi uyuşmazlığı: dosya={saved_type}, mevcut={current_type}")
                return

            for key, val in data.items():
                if key == "_plc_type":
                    continue
                if isinstance(val, dict):
                    self.custom_data[key] = val
                elif isinstance(val, list):
                    self.custom_data[key] = {"name": val[0] if val else "", "visible": True}
                else:
                    self.custom_data[key] = {"name": "", "visible": True}

            print(f"✅ {current_type} JSON: {len(self.custom_data)} kayıt")

        except Exception as e:
            print(f"❌ JSON hatası: {e}")

    def save_custom_data(self):
        try:
            save_data = {
                "_plc_type": self.get_plc_type().value,
                **self.custom_data
            }
            with open(self.json_path, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)
            print(f"💾 JSON kaydedildi: {self.json_path}")
        except Exception as e:
            print(f"❌ Kayıt hatası: {e}")

    def configure_from_dict(self, config_dict: dict):
        """Konfigürasyondan izlenecek adresleri çıkarır"""
        self._watch_items.clear()

        for part_name, part_data in config_dict.items():
            if not isinstance(part_data, dict):
                continue

            # Hareket eksenleri
            for axis in ['tx', 'ty', 'tz', 'rx', 'ry', 'rz']:
                addr_key = f"Adress_{axis}"
                if addr_key in part_data and part_data[addr_key]:
                    self.add_watch_item(
                        name=f"{part_name}_{axis}",
                        address=part_data[addr_key],
                        bit_index=part_data.get(f"Adress_{axis}_bit"),
                        scaling=part_data.get(f"scale_{axis}", 1.0)
                    )

            # Durum adresi
            if "status_address" in part_data and part_data["status_address"]:
                self.add_watch_item(
                    name=f"{part_name}_status",
                    address=part_data["status_address"],
                    bit_index=part_data.get("status_bit_index"),
                    scaling=1.0
                )

        print(f"📡 {self.get_plc_type().value}: {len(self._watch_items)} adres izlenecek")

    def get_initial_values(self) -> dict:
        """Tüm watch item'ların ilk değerlerini al"""
        initial_values = {}
        for item in self._watch_items:
            name = item['name']
            address = item['address']
            bit_index = item.get('bit_index')
            scaling = item.get('scaling', 1.0)

            value = self.read_address(address, bit_index)
            if value is not None:
                try:
                    value = float(value) * scaling
                except:
                    pass
                initial_values[name] = value
                self._last_values[name] = value
        return initial_values