# siemens_manager.py
import struct

import snap7
from typing import Any, Optional
import re

from ManagerPLC.plc_communication_base import BasePLCManager, PLCType


class SiemensManager(BasePLCManager):
    def __init__(self, json_path: str, initial_config: dict = None):
        super().__init__(json_path, initial_config)

        self.ip_address = self.custom_data.get("host", "192.168.1.100")
        # self.port = self.custom_data.get("port", 502)
        self.host = self.custom_data.get("host", "192.168.1.100")


        self.client = None
        self.register_map = {}

        self.client = None
        self.register_map = {}

        self.rack = 0
        self.slot = 1

        self.plc_type = PLCType.SIEMENS

    def get_plc_type(self) -> PLCType:
        return PLCType.SIEMENS

    def connect(self, connection_params: dict = None) -> bool:
        if connection_params:
            self.ip_address = connection_params.get('ip', self.ip_address)
            self.rack = connection_params.get('rack', self.rack)
            self.slot = connection_params.get('slot', self.slot)

        try:
            self.client = snap7.client.Client()
            self.client.connect(self.ip_address, self.rack, self.slot)
            self.is_connected = self.client.get_connected()
            if self.is_connected:
                print(f"✅ Siemens bağlantısı kuruldu: {self.ip_address} (Rack:{self.rack}, Slot:{self.slot})")
            return self.is_connected
        except Exception as e:
            print(f"❌ Siemens bağlantı hatası: {e}")
            return False
        # except Exception as e:
        #     # 🎯 İŞTE ARADIĞIN SİHİRLİ DOKUNUŞ: PLC bulunamadıysa sanal modu aç
        #     print(f"⚠️ {e}")
        #     self.client = VirtualS7Client()  # Gerçek client yerine simülatörü ata!
        #     self.is_connected = True
        #     self.virtual_mode = True
        #     print("🚀 Sanal Siemens Sürücüsü Devreye Girdi! Testler başlayabilir.")
        #     return self.is_connected

    def disconnect(self) -> bool:
        if self.client:
            self.client.disconnect()
            self.is_connected = False
            print("🔌 Siemens bağlantısı kesildi")
        return True


    def parse_siemens_address(self, address_str: str) -> dict:
        """Siemens adresini parse et"""
        # DB10.DBD20 -> {'type': 'db', 'db_num': 10, 'area': 'dbd', 'offset': 20}
        # MW50 -> {'type': 'm', 'area': 'mw', 'offset': 50}
        # MB25.X5 -> {'type': 'm', 'area': 'mb', 'offset': 25, 'bit': 5}

        db_pattern = r'DB(\d+)\.(DBD|DBW|DBB)(\d+)(?:\.X(\d+))?'
        m_pattern = r'(MD|MW|MB)(\d+)(?:\.X(\d+))?'

        match = re.search(db_pattern, address_str.upper())
        if match:
            return {
                'type': 'db',
                'db_num': int(match.group(1)),
                'area': match.group(2).lower(),
                'offset': int(match.group(3)),
                'bit': int(match.group(4)) if match.group(4) else None
            }

        match = re.search(m_pattern, address_str.upper())
        if match:
            return {
                'type': 'm',
                'area': match.group(1).lower(),
                'offset': int(match.group(2)),
                'bit': int(match.group(3)) if match.group(3) else None
            }

        return None

    def read_address_old(self, address: str, bit_index: Optional[int] = None) -> Any:
        """Siemens adresinden oku"""
        if not self.is_connected:
            print("❌ Siemens bağlantısı yok")
            return None

        parsed = self.parse_siemens_address(address)
        if not parsed:
            print(f"❌ Geçersiz Siemens adresi: {address}")
            return None

        try:
            if parsed['type'] == 'db':
                if parsed['area'] == 'dbd':  # Double Word (32-bit)
                    data = self.client.db_read(parsed['db_num'], parsed['offset'], 4)
                    value = struct.unpack('>f', data)[0]  # Float
                elif parsed['area'] == 'dbw':  # Word (16-bit)
                    data = self.client.db_read(parsed['db_num'], parsed['offset'], 2)
                    value = struct.unpack('>H', data)[0]
                else:  # dbb (Byte)
                    data = self.client.db_read(parsed['db_num'], parsed['offset'], 1)
                    value = data[0]

            else:  # Merkezi M
                if parsed['area'] == 'md':  # Double Word
                    data = self.client.mb_read(parsed['offset'], 4)
                    value = struct.unpack('>f', data)[0]
                elif parsed['area'] == 'mw':  # Word
                    data = self.client.mb_read(parsed['offset'], 2)
                    value = struct.unpack('>H', data)[0]
                else:  # mb (Byte)
                    data = self.client.mb_read(parsed['offset'], 1)
                    value = data[0]

            # Bit varsa
            if parsed['bit'] is not None:
                value = (value >> parsed['bit']) & 1

            return value

        except Exception as e:
            print(f"❌ Siemens okuma hatası: {address} - {e}")
            return None

    def read_address(self, address: str, bit_index: Optional[int] = None, cnt: int = 1) -> Any:
        """
        📌 HEM DB HEM MERKER (M) DESTEKLİ SIEMENS OKUMA:
        Siemens adresinden okuma yapar. (Örn: DB12.DBD20 veya MW100, MD20, MB5)
        cnt > 1 ise geriye [25, 120, 112, 205] gibi düz bir ham byte listesi döner.
        """
        if not getattr(self, 'is_connected', False) or not self.client:
            return None

        address_upper = address.upper().strip()

        # -----------------------------------------------------------------
        # 🟢 1. DURUM: SIEMENS M (MERKER) ALANI İSE (Örn: MW100, MD20, MB5)
        # -----------------------------------------------------------------
        if address_upper.startswith("M"):
            # Regex ile M'den sonraki harfi (B/W/D) ve sayısal offseti yakala
            # Örn: MW100 -> area='W', offset=100  |  MB5 -> area='B', offset=5
            m_match = re.match(r"M([BWDXbwdx])?(\d+)", address_upper)
            if not m_match:
                print(f"❌ Geçersiz Siemens Merker adresi: {address}")
                return None

            area = (m_match.group(1) or 'B').lower()  # Eğer harf yoksa (M10 gibi) varsayılan Byte'tır
            start_offset = int(m_match.group(2))
            is_db = False

        # -----------------------------------------------------------------
        # 🔵 2. DURUM: SIEMENS DB ALANI İSE (Örn: DB12.DBD24)
        # -----------------------------------------------------------------
        elif address_upper.startswith("DB"):
            db_match = re.match(r"DB(\d+)\.DB([XBWDxbdw])(\d+)", address_upper)
            if not db_match:
                print(f"❌ Geçersiz Siemens DB adresi: {address}")
                return None

            db_num = int(db_match.group(1))
            area = db_match.group(2).lower()
            start_offset = int(db_match.group(3))
            is_db = True

        else:
            print(f"❌ Bilinmeyen Siemens Adres Tipi: {address}")
            return None

        # --- Okunacak byte adetini belirle ---
        if cnt > 1:
            byte_count = cnt
        else:
            if area == 'd':
                byte_count = 4  # Double Word / Real
            elif area == 'w':
                byte_count = 2  # Word
            else:
                byte_count = 1  # Byte / Bit

        try:
            # --- Donanımdan Ham Byte Okuması ---
            if is_db:
                # DB okuması
                buffer = self.client.db_read(db_num, start_offset, byte_count)
            else:
                # Merker (M) okuması
                buffer = self.client.mb_read(start_offset, byte_count)

            if not buffer:
                return None

            # 🎯 SEÇENEK A UYUMU: Toplu okumada düz liste dönüyoruz
            if cnt > 1:
                return list(buffer)

            # Tekil okumada struct ile çözüp tek değer dönüyoruz
            if area == 'd':
                return struct.unpack('>f', buffer)[0]  # Real / Float
            elif area == 'w':
                return struct.unpack('>H', buffer)[0]  # Word / Int
            else:
                return buffer[0]  # Byte

        except Exception as e:
            print(f"❌ Siemens okuma hatası ({address}): {e}")
            return None
    def write_address(self, address: str, value: Any, bit_index: Optional[int] = None) -> bool:
        """Siemens adresine yaz"""
        # Benzer şekilde yazma implementasyonu
        pass

    def configure_from_dict(self, config_dict: dict):
        """Konfigürasyondan Siemens izlenecek adreslerini çıkar ve LİSANSLAMA esaslı görev sayısını hesapla"""
        self._watch_items.clear()

        # Tekrarlı adresleri engellemek ve benzersiz PLC tag sayısını bulmak için küme (set)
        unique_addresses = set()
        total_assigned_tasks = 0  # 🎯 Lisanslamaya esas olacak toplam atanan görev/aksiyon sayısı

        for part_name, part_data in config_dict.items():
            if not isinstance(part_data, dict):
                continue

            # 1️⃣ HAREKET EKSENLERİ AYIKLAMA
            for axis in ['tx', 'ty', 'tz', 'rx', 'ry', 'rz']:
                addr_key = f"Adress_{axis}"
                if addr_key in part_data and part_data[addr_key]:
                    address_str = str(part_data[addr_key]).strip()
                    if not address_str:
                        continue

                    # Teknik olarak benzersiz adresi kaydet (Haberleşme optimizasyonu için)
                    unique_addresses.add(address_str.upper())

                    # 🎯 Nesneye bir hareket görevi atanmış demektir, lisans sayacını arttırıyoruz
                    total_assigned_tasks += 1

                    # 🎯 OTOMATİK TİP TESPİTİ DÜZELTİLDİ: Config'de tip yoksa adresten anla
                    raw_type = part_data.get(f"type_{axis}")  # Varsayılan vermiyoruz ki boşsa alttaki blok çalışsın

                    if raw_type == "Int/Word":
                        detected_type = "Word"
                    elif raw_type == "Dint":
                        detected_type = "Dint"
                    elif raw_type == "Real":
                        detected_type = "Real"
                    elif raw_type == "Long":
                        detected_type = "Long"
                    elif raw_type == "Bool":
                        detected_type = "Bit"
                    else:
                        detected_type = raw_type

                    # Eğer JSON'dan tip bilgisi hiç gelmediyse (None veya boşsa) adresten tahmin et
                    if not detected_type:
                        if "DBD" in address_str.upper():
                            detected_type = "Real"
                        elif "DBW" in address_str.upper():
                            detected_type = "Word"
                        elif "DBB" in address_str.upper():
                            detected_type = "Byte"
                        elif "DBX" in address_str.upper():
                            detected_type = "Bit"
                        else:
                            detected_type = "Real"  # Hiçbiri tutmazsa safe-fallback

                    self.add_watch_item(
                        name=f"{part_name}_{axis}",
                        address=address_str,
                        type=detected_type,  # Tamamen dinamik ve otomatik!
                        bit_index=part_data.get(f"Adress_{axis}_bit"),
                        scaling=part_data.get(f"scale_{axis}", 1.0)
                    )

            # 2️⃣ DURUM (STATUS) ADRESİ AYIKLAMA
            if "status_address" in part_data and part_data["status_address"]:
                status_addr_str = str(part_data["status_address"]).strip()

                if status_addr_str:
                    unique_addresses.add(status_addr_str.upper())

                    # 🎯 Nesneye bir renklendirme/durum görevi atanmış demektir, lisans sayacını arttırıyoruz
                    total_assigned_tasks += 1

                    # 🎯 OTOMATİK TİP TESPİTİ (Status için)
                    raw_status_type = part_data.get("type_status")

                    if raw_status_type == "Int/Word":
                        detected_status_type = "Word"
                    elif raw_status_type == "Dint":
                        detected_status_type = "Dint"
                    elif raw_status_type == "Real":
                        detected_status_type = "Real"
                    elif raw_status_type == "Long":
                        detected_status_type = "Long"
                    elif raw_status_type == "Bool":
                        detected_status_type = "Bit"
                    else:
                        detected_status_type = raw_status_type

                    # Eğer JSON'dan tip bilgisi gelmediyse adresten tahmin et
                    if not detected_status_type:
                        if "DBD" in status_addr_str.upper():
                            detected_status_type = "Real"
                        elif "DBW" in status_addr_str.upper():
                            detected_status_type = "Word"
                        elif "DBB" in status_addr_str.upper():
                            detected_status_type = "Byte"
                        elif "DBX" in status_addr_str.upper():
                            detected_status_type = "Bit"
                        else:
                            detected_status_type = "Word"

                    self.add_watch_item(
                        name=f"{part_name}_status",
                        address=status_addr_str,
                        type=detected_status_type,  # Otomatik belirlendi
                        bit_index=part_data.get("status_bit_index"),
                        color_mappings=part_data.get("color_mappings", {}),
                        scaling=1.0
                    )


            # 🟢 3️⃣ GÖRÜNÜRLÜK (VISIBLE) ADRESİ AYIKLAMA (YENİ EKLENEN KISIM)
            visible_addr = part_data.get("visible_address")
            if visible_addr is not None and str(visible_addr).strip() != "":
                    unique_addresses.add(str(visible_addr).strip().upper())

                    # 🎯 Nesneye bir görünürlük görevi atanmış demektir, lisans sayacını arttırıyoruz
                    total_assigned_tasks += 1

                    # Dialog'da "visible_type" olarak kaydetmiştik, yoksa varsayılan "Bit" kabul et
                    visible_type = part_data.get("visible_type", "Bit")

                    self.add_watch_item(
                        name=f"{part_name}_visible",  # 👁️ İşte process_pending_values'un beklediği isim!
                        address=visible_addr,
                        type=visible_type,
                        bit_index=part_data.get("visible_bit_index"),
                        scaling=1.0
                    )

        # 🎯 Taban sınıftan (`BasePLCManager`) miras alınan ortak değişkene hesaplanan değeri eşitliyoruz
        self.total_assigned_tasks = total_assigned_tasks

        # 📊 LİSANSLAMA VE İSTATİSTİK RAPORU
        print("==================================================")
        print("       💳 SIEMENS LİSANS VE PERFORMANS RAPORU     ")
        print("==================================================")
        print(f" 🔑 [LİSANS] Atanan Toplam Görev/Nesne Sayısı : {self.total_assigned_tasks}")
        print(f" ⚙️ [PLC] Okunacak Benzersiz Tag Sayısı       : {len(unique_addresses)}")
        print("==================================================")

        # self.optimize_groups()
        #
        # # Hem fonksiyon çıktısı olarak dönüyoruz hem de sınıfta saklıyoruz
        # return self.total_assigned_tasks

    def optimize_groups(self):
        """
        Gelen tüm dağınık adresleri analiz eder, aynı DB içindekileri
        sıralar ve count (adet) vererek okunacak paket planlarını (optimized_groups) çıkarır.
        """
        if not self._watch_items:
            self.optimized_groups = []
            self.read_groups = []
            return

        MAX_GAP = 20  # Araya girebilecek maksimum boş byte sayısı
        MAX_BLOCK_SIZE = 220  # Tek seferde okunacak maksimum güvenli byte boyutu

        TYPE_SIZES = {'X': 1, 'B': 1, 'W': 2, 'D': 4}
        parsed_items = []
        pattern = re.compile(r"DB(\d+)\.DB([XBWDxbdw])(\d+)", re.IGNORECASE)

        # 1. Tüm adresleri matematiksel byte aralıklarına dök
        for item in self._watch_items:
            address_str = str(item['address'])
            match = pattern.match(address_str)

            if match:
                db_num = int(match.group(1))
                data_type = match.group(2).upper()
                start_byte = int(match.group(3))
                size = TYPE_SIZES.get(data_type, 1)

                parsed_items.append({
                    'db_num': db_num,
                    'start_byte': start_byte,
                    'end_byte': start_byte + size,
                    'data_type': data_type,
                    'original_item': item
                })

        # 2. DB numaralarına göre haritala
        db_map = {}
        for pi in parsed_items:
            db_map.setdefault(pi['db_num'], []).append(pi)

        # Her optimizasyonda listeyi sıfırlayalım
        self.optimized_groups = []

        # 3. Her DB'yi kendi içinde sırala ve count paketlerini oluştur
        for db_num, items in db_map.items():
            # Hem byte adresine hem de bit indeksine göre sıralama
            items.sort(key=lambda x: (x['start_byte'], x['original_item'].get('bit_index') or 0))

            current_group = {
                'db_num': db_num,
                'start_byte': items[0]['start_byte'],
                'end_byte': items[0]['end_byte'],
                'items': [items[0]['original_item']]  # Orijinal item'ları saklıyoruz
            }

            for item in items[1:]:
                gap = item['start_byte'] - current_group['end_byte']
                potential_size = item['end_byte'] - current_group['start_byte']

                if gap <= MAX_GAP and potential_size <= MAX_BLOCK_SIZE:
                    current_group['end_byte'] = max(current_group['end_byte'], item['end_byte'])
                    current_group['items'].append(item['original_item'])
                else:
                    # Grubun toplam byte count değerini hesapla ve gruba ekle
                    current_group['count'] = current_group['end_byte'] - current_group['start_byte']
                    self.optimized_groups.append(current_group)

                    # Yeni grubu başlat
                    current_group = {
                        'db_num': db_num,
                        'start_byte': item['start_byte'],
                        'end_byte': item['end_byte'],
                        'items': [item['original_item']]
                    }

            # Döngü bittiğinde eldeki son grubu da count hesaplayarak ekle
            current_group['count'] = current_group['end_byte'] - current_group['start_byte']
            self.optimized_groups.append(current_group)

        # 4. read_groups listesini hazırla (read_address fonksiyonunun anlayacağı dile çevir)
        self.read_groups = []
        for og in self.optimized_groups:
            # Dinamik olarak başlangıç adres stringini oluşturuyoruz (Örn: DB12.DBB20)
            base_address_str = f"DB{og['db_num']}.DBB{og['start_byte']}"

            self.read_groups.append({
                'base_address': base_address_str,
                'byte_count': og['count'],  # Artık direkt og['count'] üzerinden alabiliyoruz
                'start_byte': og['start_byte'],
                'sub_items': og['items']
            })

        print(
            f"✅ Siemens Optimizasyonu Tamamlandı: {len(self.optimized_groups)} adet bağımsız grup (count paketi) oluşturuldu.")

    def get_full_address(self, widget):
        """Widget'dan tam adresi al - AddressDialog için"""
        if hasattr(widget, 'get_full_address'):
            return widget.get_full_address()
        return None

    def get_bit_index(self, widget):
        """Widget'dan bit index'ini al - AddressDialog için"""
        if hasattr(widget, 'get_bit_index'):
            return widget.get_bit_index()
        return None

    def get_data_type(self, widget):
        """Widget'dan tam adresi al - AddressDialog için"""
        if hasattr(widget, 'get_data_type'):
            return widget.get_data_type()
        return None