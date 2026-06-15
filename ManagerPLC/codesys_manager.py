# codesys_manager.py
import socket
from typing import Any, Optional
import re

from ManagerPLC.plc_communication_base import BasePLCManager, PLCType


class CodeSysManager(BasePLCManager):
    def __init__(self, json_path: str, initial_config: dict = None):
        super().__init__(json_path, initial_config)

        self.host = self.custom_data.get("host", "192.168.1.100")
        self.port = self.custom_data.get("port", 502)
        self.plc_type = PLCType.MODBUS

        self.client = None
        self.register_map = {}


        # self.socket = None
        # self.host = "192.168.1.200"
        # self.port = 502  # Modbus TCP default port
        self.protocol = "modbus_tcp"  # veya "ethernet_ip"
        # self.register_map = {}
        # self.plc_type = PLCType.CODESYS

        # CodeSys spesifik adres formatları
        self.address_patterns = {
            'merkezi': r'([MIQ])([WBD])(\d+)(?::X(\d+))?',
            'bit_noktali': r'([MIQ])(\d+)\.(\d+)',
            'double_word': r'([MIQ])D(\d+)'
        }

    def get_plc_type(self) -> PLCType:
        return PLCType.CODESYS

    def connect(self, connection_params: dict = None) -> bool:
        """CodeSys PLC'ye bağlan"""
        if connection_params:
            self.host = connection_params.get('host', self.host)
            self.port = connection_params.get('port', self.port)
            self.protocol = connection_params.get('protocol', self.protocol)

        try:
            if self.protocol == "modbus_tcp":
                # Modbus TCP üzerinden bağlan
                from pymodbus.client import ModbusTcpClient
                self.client = ModbusTcpClient(self.host, port=self.port)
                self.is_connected = self.client.connect()
            elif self.protocol == "ethernet_ip":
                # Ethernet/IP üzerinden bağlan (opsiyonel)
                # Burada cpppo veya pycomm3 kullanılabilir
                import cpppo
                self.client = None  # Ethernet/IP implementasyonu
                self.is_connected = False
                print("⚠️ Ethernet/IP protokolü henüz implemente edilmedi")
            else:
                # Direkt socket (basit durumlar için)
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.settimeout(5)
                self.socket.connect((self.host, self.port))
                self.is_connected = True

            if self.is_connected:
                print(f"✅ CodeSys bağlantısı kuruldu: {self.host}:{self.port} ({self.protocol})")
            return self.is_connected

        except Exception as e:
            print(f"❌ CodeSys bağlantı hatası: {e}")
            return False

    def disconnect(self) -> bool:
        """CodeSys PLC bağlantısını kes"""
        try:
            if hasattr(self, 'client') and self.client:
                if self.protocol == "modbus_tcp":
                    self.client.close()
                else:
                    self.client = None

            if self.socket:
                self.socket.close()
                self.socket = None

            self.is_connected = False
            print("🔌 CodeSys bağlantısı kesildi")
            return True
        except Exception as e:
            print(f"❌ CodeSys bağlantı kesme hatası: {e}")
            return False

    def parse_codesys_address(self, address_str: str) -> dict:
        """CodeSys adresini parse et

        Desteklenen formatlar:
        - MW10 - Word adres (16-bit)
        - MB20 - Byte adres (8-bit)
        - MD100 - Double Word adres (32-bit)
        - MW10:X3 - Word'ün 3. biti
        - I0.3 - Input byte 0 bit 3
        - Q5.2 - Output byte 5 bit 2
        - M100 - Merkezi bit
        - ID200 - Input Double Word
        - QD300 - Output Double Word
        """
        address_str = address_str.upper().strip()

        # Pattern 1: M/I/Q W/B/D offset :X bit
        pattern1 = r'([MIQ])([WBD])(\d+)(?::X(\d+))?'
        match = re.search(pattern1, address_str)
        if match:
            return {
                'type': match.group(1),  # M, I, Q
                'data_type': match.group(2),  # W, B, D
                'offset': int(match.group(3)),
                'bit': int(match.group(4)) if match.group(4) else None,
                'format': 'standard'
            }

        # Pattern 2: I/Q 0.3 (bit noktalı format)
        pattern2 = r'([MIQ])(\d+)\.(\d+)'
        match = re.search(pattern2, address_str)
        if match:
            return {
                'type': match.group(1),  # I, Q
                'data_type': 'X',  # Bit tipi
                'byte_offset': int(match.group(2)),
                'bit': int(match.group(3)),
                'offset': int(match.group(2)),  # byte offset
                'format': 'bit_dotted'
            }

        # Pattern 3: I/Q D offset (Double Word)
        pattern3 = r'([MIQ])D(\d+)'
        match = re.search(pattern3, address_str)
        if match:
            return {
                'type': match.group(1),
                'data_type': 'D',
                'offset': int(match.group(2)),
                'format': 'double_word'
            }

        # Pattern 4: Sadece M (bit tipi)
        pattern4 = r'([MIQ])(\d+)'
        match = re.search(pattern4, address_str)
        if match and match.group(1) != 'D':  # D harfi ile karışmasın
            return {
                'type': match.group(1),
                'data_type': 'X',  # Bit tipi
                'offset': int(match.group(2)),
                'format': 'bit_simple'
            }

        print(f"⚠️ Parse edilemeyen CodeSys adresi: {address_str}")
        return None

    def read_address(self, address: str, bit_index: Optional[int] = None) -> Any:
        """CodeSys adresinden değer oku"""
        if not self.is_connected:
            print("❌ CodeSys bağlantısı yok")
            return None

        """Adresten değer oku - pymodbus 3.x uyumlu"""
        if not self.is_connected or not self.client:
            return None

        try:
            if isinstance(address, int):
                # Holding Register (4xxxx)
                if 40001 <= address <= 49999:
                    reg_addr = address - 40001
                    # pymodbus 3.x: sadece address ve count
                    result = self.client.read_holding_registers(address=reg_addr, count=1)
                    if not result.isError() and hasattr(result, 'registers') and result.registers:
                        value = result.registers[0]
                        if bit_index is not None:
                            value = (value >> bit_index) & 1
                        return value

                # Input Register (3xxxx)
                elif 30001 <= address <= 39999:
                    reg_addr = address - 30001
                    result = self.client.read_input_registers(address=reg_addr, count=1)
                    if not result.isError() and hasattr(result, 'registers') and result.registers:
                        value = result.registers[0]
                        if bit_index is not None:
                            value = (value >> bit_index) & 1
                        return value

                # Coil (0xxxx)
                elif 1 <= address <= 9999:
                    result = self.client.read_coils(address=address, count=1)
                    if not result.isError() and hasattr(result, 'bits') and result.bits:
                        return 1 if result.bits[0] else 0

                # Varsayılan Holding Register
                else:
                    result = self.client.read_holding_registers(address=address, count=1)
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



    def _read_socket(self, parsed: dict, bit_index: Optional[int] = None) -> Any:
        """Direkt socket üzerinden oku (basit protokol)"""
        if not self.socket:
            return None

        # Basit bir protokol örneği
        # Komut formatı: READ,<type>,<offset>,<count>
        cmd = f"READ,{parsed['data_type']},{parsed['offset']},1\n"
        self.socket.send(cmd.encode())
        response = self.socket.recv(1024).decode().strip()

        try:
            values = response.split(',')
            if len(values) > 1:
                value = int(values[1])
                if parsed.get('bit') is not None:
                    value = (value >> parsed['bit']) & 1
                return value
        except:
            pass

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
        """Konfigürasyondan izlenecek adresleri çıkar - ModbusManager ile AYNI"""
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
                        address=part_data[addr_key],  # CodeSys'te string adres (örn: "MW10")
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

        print(f"📡 CodeSys: {len(self._watch_items)} adres izlenecek")



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
    def test_connection(self) -> dict:
        """Bağlantı testi yap"""
        test_results = {
            'connected': self.is_connected,
            'protocol': self.protocol,
            'host': self.host,
            'port': self.port
        }

        if self.is_connected:
            # Test adreslerini dene
            test_addresses = ["MW0", "MB0", "M0"]
            for addr in test_addresses:
                try:
                    value = self.read_address(addr)
                    test_results[f'test_{addr}'] = value is not None
                except:
                    test_results[f'test_{addr}'] = False

        return test_results