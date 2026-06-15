# plc_factory.py
import json
import os



from ManagerPLC.plc_communication_base import BasePLCManager, PLCType
from ManagerPLC.modbus_manager import ModbusManager
from ManagerPLC.siemens_manager import SiemensManager
from typing import List  # Bu satırı ekleyin


class PLCManagerFactory:
    @staticmethod
    def create_from_json(json_path: str) -> BasePLCManager:
        """JSON'u bir kez oku ve Manager'ı o veriyle oluştur"""
        if not os.path.exists(json_path):
            print(f"⚠️ {json_path} bulunamadı, boş konfigürasyonla başlatılıyor.")
            # Dosya yoksa varsayılan tip (örn: MODBUS) ile devam et
            return ModbusManager(json_path, initial_config={})

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                custom_data = json.load(f)  # DOSYA BURADA BİR KEZ OKUNDU

            # Tipi belirle
            saved_type_str = custom_data.get("_plc_type", "Modbus")

            # Enum'a çevir
            plc_type = PLCType.MODBUS  # Varsayılan
            for t in PLCType:
                if t.value == saved_type_str:
                    plc_type = t
                    break

            # Manager'ı oluştururken yüklü veriyi de gönderiyoruz
            return PLCManagerFactory.create_manager(plc_type, json_path, custom_data)

        except Exception as e:
            print(f"❌ Factory yükleme hatası: {e}")
            return ModbusManager(json_path, initial_config={})

    @staticmethod
    def create_manager(plc_type: PLCType, json_path: str, config: dict = None) -> BasePLCManager:
        if plc_type == PLCType.MODBUS:
            return ModbusManager(json_path, initial_config=config)
        elif plc_type == PLCType.SIEMENS:
            return SiemensManager(json_path, initial_config=config)
        # elif plc_type == PLCType.CODESYS:
        #     return CodeSysManager(json_path, initial_config=config)
        else:
            raise ValueError(f"Desteklenmeyen PLC tipi: {plc_type}")

    @staticmethod
    def get_saved_plc_type(json_path: str) -> PLCType:
        """JSON dosyasından kayıtlı PLC tipini oku"""
        if not os.path.exists(json_path):
            return None

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            saved_type = data.get("_plc_type")
            saved_type_enum = data.get("_plc_type_enum")

            if saved_type:
                for plc_type in PLCType:
                    if plc_type.value == saved_type:
                        return plc_type
                    elif plc_type.name == saved_type_enum:
                        return plc_type

            return None

        except Exception as e:
            print(f"❌ PLC tipi okuma hatası: {e}")
            return None

    @staticmethod
    def get_available_plc_types() -> List[PLCType]:
        return [PLCType.MODBUS, PLCType.SIEMENS] #, PLCType.CODESYS]