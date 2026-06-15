# modbus_manager.py

from ManagerPLC.plc_communication_base import BasePLCManager, PLCType

# FirstDependcy/ManagerPLC/modbus_manager.py
from typing import Any, Optional
# from .plc_communication_base import BasePLCManager, PLCType


class ModbusDataType:
    COIL = "coil"
    DISCRETE_INPUT = "discrete"
    INPUT_REGISTER = "input_register"
    HOLDING_REGISTER = "holding_register"


class ModbusManager(BasePLCManager):
    def __init__(self, json_path: str, initial_config: dict = None):
        super().__init__(json_path, initial_config)

        self.host = self.custom_data.get("host", "192.168.1.100")
        self.port = self.custom_data.get("port", 502)
        self.plc_type = PLCType.MODBUS

        self.client = None
        self.register_map = {}


    def get_plc_type(self) -> PLCType:
        return PLCType.MODBUS

    def connect(self, connection_params: dict = None) -> bool:
        """PLC'ye bağlan"""
        try:
            from pymodbus.client import ModbusTcpClient

            if connection_params and isinstance(connection_params, dict):
                self.host = connection_params.get('host', self.host)
                self.port = connection_params.get('port', self.port)

            print(f"🔌 Modbus bağlantı deneniyor: {self.host}:{self.port}")

            self.client = ModbusTcpClient(self.host, port=self.port)
            self.is_connected = self.client.connect()

            if self.is_connected:
                print(f"✅ Modbus bağlantısı kuruldu: {self.host}:{self.port}")
                # Test okuma - pymodbus 3.x için doğru kullanım
                try:
                    # pymodbus 3.x'te read_holding_registers sadece address ve count alır
                    result = self.client.read_holding_registers(1, 1)
                    if not result.isError():
                        print("✅ Modbus iletişim testi BAŞARILI")
                    else:
                        print(f"⚠️ Modbus test okuma hatası: {result}")
                except Exception as e:
                    print(f"⚠️ Modbus test okuma hatası: {e}")
            else:
                print(f"❌ Modbus bağlantı hatası: {self.host}:{self.port}")

            return self.is_connected

        except Exception as e:
            print(f"❌ Modbus bağlantı hatası: {e}")
            self.is_connected = False
            return False

    def disconnect(self) -> bool:
        """PLC bağlantısını kes"""
        if self.client:
            try:
                self.client.close()
            except:
                pass
            self.client = None
        self.is_connected = False
        print("🔌 Modbus bağlantısı kesildi")
        return True

    def read_address(self, address: Any, bit_index: Optional[int] = None,cnt:int=1) -> Any:
        """Adresten değer oku - pymodbus 3.x uyumlu"""
        if not self.is_connected or not self.client:
            return None

        try:
            if isinstance(address, int):
                # Holding Register (4xxxx)
                if self.AdressControl(address) == 0:
                    return None
                if 40001 <= address <= 49999:
                    reg_addr = address - 40001

                    result = self.client.read_holding_registers(address=reg_addr, count=cnt)

                    if not result.isError() and hasattr(result, 'registers') and result.registers:
                        # 1. Eğer sadece 1 tane istendiyse, direkt o değeri/biti döndür
                        if cnt == 1:
                            value = result.registers[0]
                            # if bit_index is not None:
                            #     return (value >> bit_index) & 1
                            return value
                        return result.registers

                elif 30001 <= address <= 39999:
                    reg_addr = address - 30001
                    result = self.client.read_input_registers(address=reg_addr, count=cnt)
                    if not result.isError() and hasattr(result, 'registers') and result.registers:
                        # Eğer sadece 1 tane istendiyse, direkt o değeri döndür
                        if cnt == 1:
                            return result.registers[0]
                        # Birden fazla istendiyse, listenin tamamını döndür (İşçi döngüsü çözsün)
                        return result.registers

                # Coil (0xxxx)
                elif 1 <= address <= 9999:
                    result = self.client.read_coils(address=address, count=cnt)
                    if not result.isError() and hasattr(result, 'bits') and result.bits:
                        return 1 if result.bits[0] else 0

                # Varsayılan Holding Register
                else:
                    result = self.client.read_holding_registers(address=address, count=cnt)
                    if not result.isError() and hasattr(result, 'registers') and result.registers:
                        value = result.registers[0]
                        if bit_index is not None:
                            value = (value >> bit_index) & 1
                        return value

            elif isinstance(address, str):
                try:
                    return self.read_address(int(address), bit_index)
                except:
                    pass

            return None

        except Exception as e:
            print(f"❌ Modbus okuma hatası ({address}): {e}")
            return None

    def write_address(self, address: Any, value: Any, bit_index: Optional[int] = None) -> bool:
        """Adrese değer yaz - pymodbus 3.x uyumlu"""
        if not self.is_connected or not self.client:
            return False

        try:
            if isinstance(address, int):
                # Holding Register (4xxxx)
                if 40001 <= address <= 49999:
                    reg_addr = address - 40000
                    if bit_index is not None:
                        # Önce mevcut değeri oku
                        result = self.client.read_holding_registers(reg_addr, 1)
                        if not result.isError() and hasattr(result, 'registers'):
                            current = result.registers[0]
                            if value:
                                current |= (1 << bit_index)
                            else:
                                current &= ~(1 << bit_index)
                            result = self.client.write_register(reg_addr, current)
                            return not result.isError()
                    else:
                        result = self.client.write_register(reg_addr, int(value))
                        return not result.isError()

                # Coil (0xxxx)
                elif 1 <= address <= 9999:
                    result = self.client.write_coil(address, bool(value))
                    return not result.isError()

            return False

        except Exception as e:
            print(f"❌ Modbus yazma hatası ({address}): {e}")
            return False

    def read_holding_register(self, address: int) -> Optional[int]:
        """Tek bir holding register oku - yardımcı metod"""
        result = self.client.read_holding_registers(address, 1)
        if not result.isError() and hasattr(result, 'registers') and result.registers:
            return result.registers[0]
        return None

    def read_input_register(self, address: int) -> Optional[int]:
        """Tek bir input register oku - yardımcı metod"""
        result = self.client.read_input_registers(address, 1)
        if not result.isError() and hasattr(result, 'registers') and result.registers:
            return result.registers[0]
        return None

    def read_coil(self, address: int) -> Optional[bool]:
        """Tek bir coil oku - yardımcı metod"""
        result = self.client.read_coils(address, 1)
        if not result.isError() and hasattr(result, 'bits') and result.bits:
            return result.bits[0]
        return None

    def configure_from_dict(self, config_dict: dict):
        """Konfigürasyondan izlenecek adresleri çıkar ve LİSANSLAMA esaslı görev sayısını hesapla"""
        self._watch_items.clear()

        unique_addresses = set()
        total_assigned_tasks = 0  # 🎯 Lisanslamaya esas olacak toplam atanan görev/aksiyon sayısı

        for part_name, part_data in config_dict.items():
            if not isinstance(part_data, dict):
                continue

            # 1️⃣ HAREKET EKSENLERİ AYIKLAMA
            for axis in ['tx', 'ty', 'tz', 'rx', 'ry', 'rz']:
                addr_key = f"Adress_{axis}"
                addr_val = part_data.get(addr_key)

                if addr_val is not None and str(addr_val).strip() != "":
                    # Teknik olarak benzersiz adresi kaydet (Haberleşme optimizasyonu için)
                    unique_addresses.add(str(addr_val).strip().upper())

                    # 🎯 Nesneye bir görev atanmış demektir, lisans sayacını arttırıyoruz
                    total_assigned_tasks += 1

                    self.add_watch_item(
                        name=f"{part_name}_{axis}",
                        address=addr_val,
                        type=part_data.get(f"type_{axis}"),
                        bit_index=part_data.get(f"Adress_{axis}_bit"),
                        scaling=part_data.get(f"scale_{axis}", 1.0)
                    )

            # 2️⃣ DURUM (STATUS) ADRESİ AYIKLAMA
            status_addr = part_data.get("status_address")
            if status_addr is not None and str(status_addr).strip() != "":
                unique_addresses.add(str(status_addr).strip().upper())

                # 🎯 Nesneye bir renklendirme/durum görevi atanmış demektir, lisans sayacını arttırıyoruz
                total_assigned_tasks += 1

                status_type = part_data.get("type_status", "Int/Word")

                self.add_watch_item(
                    name=f"{part_name}_status",
                    address=status_addr,
                    type=status_type,
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


        # 📊 LİSANSLAMA VE İSTATİSTİK RAPORU
        self.total_assigned_tasks = total_assigned_tasks
        print("==================================================")
        print("       💳 YAZILIM LİSANS VE PERFORMANS RAPORU     ")
        print("==================================================")
        print(f" 🔑 [LİSANS] Atanan Toplam Görev/Nesne Sayısı : {total_assigned_tasks}")
        print(f" ⚙️ [PLC] Okunacak Benzersiz Tag Sayısı       : {len(unique_addresses)}")
        print("==================================================")

        # Burada lisans kontrolü yapabilirsin:
        # if total_assigned_tasks > self.MAX_LICENSED_OBJECTS:
        #     self.show_license_warning_or_stop()



    def optimize_groups(self):
        """_watch_items'dan optimized_groups oluştur (Çoklu Register Desteği ile)"""
        if not self._watch_items:
            return

        # --- 1. VERİ TİPLERİNİN REGISTER BOYUTLARI ---
        # Modbus'ta 1 Register = 16-bit (1 Word) yer kaplar.
        TYPE_SIZES = {
            'Real': 2,  # 32-bit Float -> 2 Register
            'Dint': 2,  # 32-bit Integer -> 2 Register
            'Long': 4,  # 64-bit Integer -> 4 Register
            'Int': 1,  # 16-bit Standart Integer -> 1 Register
            'int': 1
        }

        def get_item_size(item):
            """Item'ın tipine göre kaç register kapladığını döndürür"""
            item_type = item.get('type', 'int')
            # Eğer listede olmayan bilinmeyen bir tip gelirse güvenli liman olarak 1 kabul et
            return TYPE_SIZES.get(item_type, 1)

        # Adresleri küçükten büyüğe sırala
        sorted_items = sorted(self._watch_items, key=lambda x: x['address'])

        # Her optimizasyonda listeyi sıfırlayalım ki mükerrer kayıt olmasın
        self.optimized_groups = []

        i = 0
        while i < len(sorted_items):
            current_item = sorted_items[i]
            current_addr = current_item['address']
            current_size = get_item_size(current_item)

            # Blok okuma için ardışıl elemanları toplayacağımız liste
            consecutive_items = [current_item]
            j = i + 1

            # Bir sonraki elemanın tam olarak hangi adreste BAŞLAMASINI bekliyoruz?
            # Örn: Mevcut 40026 ve Real(2) ise, sonraki eleman tam 40028'de başlamalı.
            expected_addr = current_addr + current_size

            while j < len(sorted_items):
                next_item = sorted_items[j]

                # Eğer sıradaki eleman tam beklediğimiz adrese oturuyorsa gruba dahil et
                if next_item['address'] == expected_addr:
                    consecutive_items.append(next_item)
                    # Beklenen adresi, yeni eklenen elemanın boyutu kadar ileri kaydır
                    expected_addr += get_item_size(next_item)
                    j += 1
                else:
                    # Arada boşluk var veya adres uymuyor, bu bloğu burada kes
                    break

            # --- 2. GRUP DEĞERLERİNİ HESAPLAMA ---
            start_addr = consecutive_items[0]['address']
            last_item = consecutive_items[-1]
            last_item_size = get_item_size(last_item)

            # End address: Son elemanın başladığı adres + kapladığı yer - 1
            # Örn: Son eleman 40030'da ve Real(2) ise bittiği yer: 40030 + 2 - 1 = 40031
            end_addr = last_item['address'] + last_item_size - 1

            # Toplam okunacak register sayısı (Count)
            total_count = end_addr - start_addr + 1

            group = {
                'start_address': start_addr,
                'end_address': end_addr,
                'count': total_count,
                'items': consecutive_items
            }
            self.optimized_groups.append(group)

            # Ana döngüyü, iç döngünün kaldığı (j) indexe zıplatıyoruz
            i = j

        print(f"✅ Optimizasyon tamamlandı: {len(self._watch_items)} item → {len(self.optimized_groups)} grup")

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
    def AdressControl(self, address):

        if 40001 <= address <= 49999 or 30001 <= address <= 39999 or 1 <= address <= 9999:
            return 1
        else:
            return 0