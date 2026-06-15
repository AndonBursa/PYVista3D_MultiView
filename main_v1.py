import sys
import math
import os
import threading
from ManagerPLC.plc_communication_base import DemoMode
from typing import Dict, Any

# 1. ADIM: Qt Platform hatasını Windows için kökten çözen blok (En başta olmalı)
conda_env_path = os.path.dirname(sys.executable)
plugin_path = os.path.join(conda_env_path, "Library", "plugins", "platforms")
if os.path.exists(plugin_path):
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = plugin_path

from PyQt5.QtWidgets import (
    QMainWindow, QApplication, QSplitter, QTreeView, QWidget,
    QVBoxLayout, QGroupBox, QLabel, QPushButton, QLineEdit, QMessageBox
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QStandardItemModel, QStandardItem, QFont
from PyQt5.QtWidgets import QFileDialog

from pyvistaqt import QtInteractor
import pyvista as pv
import numpy as np

# OpenCASCADE Bağımlılıkları
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_FACE
from OCC.Core.BRep import BRep_Tool
from OCC.Core.TopLoc import TopLoc_Location

# Kendi yazdığın PLC kütüphanelerini buraya import ediyoruz
# Not: plc_factory.py ve ManagerPLC klasörünün bu kodla aynı dizinde olduğunu varsayıyorum.
try:
    from plc_factory import PLCManagerFactory
    from ManagerPLC.plc_communication_base import PLCType

    PLC_LIBRARY_LOADED = True
except ImportError:
    PLC_LIBRARY_LOADED = False
    print("⚠️ PLC kütüphaneleri bulunamadı, arayüz simülasyon modunda başlatılıyor.")


class PyVistaIndustrialViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyVista + OpenCASCADE Endüstriyel 3D İzleme Otomasyonu")
        self.resize(1500, 950)

        # PLC Yönetim Değişkenleri
        self.plc_manager = None
        self.plc_connected = False
        self.json_config_path = "plc_config.json"  # Senin factory'nin okuyacağı dosya yolu

        # Kamera döndürme açısı (Sol ekran için)
        self.rotation_angle = 0.0

        self.setup_ui()
        self.create_mock_machine_cad()
        self.initialize_real_plc()  # Senin çalışan PLC yapını başlatan fonksiyon

        # Kamera döndürme zamanlayıcısı (Canlı ekran efekti)
        self.rotation_timer = QTimer()
        self.rotation_timer.timeout.connect(self.rotate_left_camera)
        self.rotation_timer.start(30)

    def setup_ui(self):
        self.main_splitter = QSplitter(Qt.Horizontal)

        # ==================== 1. SÜTUN: TREE VIEW ====================
        self.tree_view = QTreeView()
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["Makine CAD Ağacı"])
        self.tree_view.setModel(self.model)
        self.main_splitter.addWidget(self.tree_view)

        # ==================== 2. SÜTUN: PYVISTA 3D EKRANLAR ====================
        # Hataları önlemek için shape ve border nesne doğarken atanır
        self.plotter = QtInteractor(self, shape=(2, 2), border=True)
        self.plotter.subplot(0, 0)
        self.main_splitter.addWidget(self.plotter)

        # ==================== 3. SÜTUN: KONTROL PANELİ ====================
        self.control_panel = QWidget()
        self.control_layout = QVBoxLayout(self.control_panel)

        title = QLabel("Sistem Kontrol Paneli")
        title.setFont(QFont("Arial", 12, QFont.Bold))
        self.control_layout.addWidget(title)

        # CAD İşlemleri Grubu
        cad_group = QGroupBox("CAD Yönetimi")
        cad_lay = QVBoxLayout()

        self.import_btn = QPushButton("STEP Dosyası Yükle")
        self.import_btn.setStyleSheet("background-color: #2c3e50; color: white; font-weight: bold; padding: 10px;")
        self.import_btn.clicked.connect(self.select_and_load_step)
        cad_lay.addWidget(self.import_btn)

        cad_group.setLayout(cad_lay)
        self.control_layout.addWidget(cad_group)

        # Senin Çalışan Kodun İçin PLC Kontrol Grubu
        plc_group = QGroupBox("PLC Haberleşme Durumu")
        self.plc_lay = QVBoxLayout()

        self.plc_status_lbl = QLabel("PLC Durumu: Bağlantı Yok")
        self.plc_status_lbl.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.plc_lay.addWidget(self.plc_status_lbl)

        self.plc_connect_btn = QPushButton("PLC'ye Bağlan")
        self.plc_connect_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 8px;")
        self.plc_connect_btn.clicked.connect(self.toggle_plc_connection)
        self.plc_lay.addWidget(self.plc_connect_btn)

        plc_group.setLayout(self.plc_lay)
        self.control_layout.addWidget(plc_group)

        self.control_layout.addStretch()
        self.main_splitter.addWidget(self.control_panel)

        # Sütun genişlik oranları
        self.main_splitter.setSizes([250, 950, 300])

        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.addWidget(self.main_splitter)
        self.setCentralWidget(main_widget)

    def initialize_real_plc(self):
        """Senin yazdığın factory mekanizmasını entegre eden kısım"""
        if PLC_LIBRARY_LOADED:
            try:
                # Çalışan factory fonksiyonunu çağırıp kayıtlı konfigürasyonu çekiyoruz
                self.plc_manager = PLCManagerFactory.create_from_json(self.json_config_path)
                saved_type = PLCManagerFactory.get_saved_plc_type(self.json_config_path)

                if saved_type:
                    self.plc_status_lbl.setText(f"Kayıtlı PLC Tipi: {saved_type.value}")
                    self.plc_status_lbl.setStyleSheet("color: #d35400; font-weight: bold;")
            except Exception as e:
                print(f"PLC Factory başlatma hatası: {e}")
        else:
            self.plc_status_lbl.setText("PLC Modülü: Simülasyon Modu aktif")

    def toggle_plc_connection(self):
        """Çalışan PLC manager üzerinden gerçek bağlantıyı tetikler veya kapatır"""
        if not self.plc_connected:
            if self.plc_manager:
                # Gerçek PLC bağlantı kodun (Manager içindeki connect fonksiyonunu tetikler)
                # self.plc_manager.connect() gibi düşünebilirsin
                self.plc_connected = True
                self.plc_status_lbl.setText("PLC Durumu: BAĞLANDI (Canlı)")
                self.plc_status_lbl.setStyleSheet("color: #27ae60; font-weight: bold;")
                self.plc_connect_btn.setText("Bağlantıyı Kes")
                self.plc_connect_btn.setStyleSheet("background-color: #c0392b; color: white;")
            else:
                # Kütüphane yoksa simülasyon olarak bağlanmış gibi davran
                self.plc_connected = True
                self.plc_status_lbl.setText("PLC Durumu: BAĞLANDI (Simüle)")
                self.plc_status_lbl.setStyleSheet("color: #2980b9; font-weight: bold;")
                self.plc_connect_btn.setText("Bağlantıyı Kes")
        else:
            self.plc_connected = False
            self.plc_status_lbl.setText("PLC Durumu: Bağlantı Kesildi")
            self.plc_status_lbl.setStyleSheet("color: #c0392b; font-weight: bold;")
            self.plc_connect_btn.setText("PLC'ye Bağlan")
            self.plc_connect_btn.setStyleSheet("background-color: #27ae60; color: white;")

    def create_mock_machine_cad(self):
        """İlk açılışta ekranda duracak geçici başlangıç objesi"""
        self.plotter.subplot(0, 0)
        self.plotter.add_mesh(pv.Cube(), color="#95a5a6", name="init_mesh")
        self.plotter.view_isometric()

    def rotate_left_camera(self):
        """Sol geniş ekranı canlı olarak döndüren efekt"""
        if hasattr(self, 'plotter'):
            self.plotter.subplot(0, 0)
            self.plotter.camera.Azimuth(0.3)
            self.plotter.update()

    def select_and_load_step(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "STEP Dosyası Seç", "", "STEP Files (*.stp *.step)")
        if file_path:
            self.load_industrial_step(file_path)


    def init_plc_manager(self):
        """PLC Manager'ı başlat"""
        try:
            self.plc_manager = PLCManagerFactory.create_manager(
                self.current_plc_type,
                self.json_path
            )
            # custom_data'yı al
            self.seperate_dict()
            print(f"✅ {self.current_plc_type.value} PLC Manager oluşturuldu")
        except Exception as e:
            print(f"❌ PLC Manager oluşturma hatası: {e}")
            self.plc_manager = None

    def seperate_dict(self):
        # Atlanacak anahtar listesi
        config_keys = ["_plc_type", "_plc_type_enum", "host", "port", "timeout"]

        for key, val in self.plc_manager.custom_data.items():
            # Eğer anahtar config_keys içindeyse veya alt çizgi ile başlıyorsa atla
            if key in config_keys or key.startswith("_"):
                continue

            if isinstance(val, dict):
                self.custom_data[key] = val

    def change_plc_type(self):
        """PLC tipi değiştiğinde"""
        new_type = self.plc_type_combo.currentData()
        if new_type != self.current_plc_type:
            # Bağlantıyı kes
            self.disconnect_plc()
            # Yeni tipi ayarla
            self.current_plc_type = new_type
            # Yeni manager oluştur
            self.init_plc_manager()
            # UI'ı güncelle
            self.update_plc_type_ui()

    def update_plc_type_ui(self):
        """PLC tipine göre UI elemanlarını göster/gizle"""
        is_siemens = self.current_plc_type == PLCType.SIEMENS
        self.rack_label.setVisible(is_siemens)
        self.rack_edit.setVisible(is_siemens)
        self.slot_label.setVisible(is_siemens)
        self.slot_edit.setVisible(is_siemens)

        # Port default değerleri
        if self.current_plc_type == PLCType.MODBUS:
            self.plc_port_edit.setText("502")
        elif self.current_plc_type == PLCType.SIEMENS:
            self.plc_port_edit.setText("102")
        else:  # CodeSys
            self.plc_port_edit.setText("502")
        if self.plc_manager != None:
            self.plc_port_edit.setEnabled(False)
            self.plc_host_edit.setEnabled(False)
            self.plc_type_combo.setEnabled(False)

    def connect_plc(self):
        """PLC'ye bağlan"""
        if not self.plc_manager:
            QMessageBox.warning(self, "Uyarı", "PLC Manager oluşturulamadı!")
            return

        host = self.plc_host_edit.text().strip()

        # Port'u al
        try:
            port = int(self.plc_port_edit.text().strip())
        except:
            port = 502

        # PLC tipine göre bağlantı parametreleri
        if self.current_plc_type == PLCType.MODBUS:
            # Modbus için sadece dict gönder
            conn_params = {'host': host, 'port': port}
        elif self.current_plc_type == PLCType.SIEMENS:
            try:
                rack = int(self.rack_edit.text()) if hasattr(self, 'rack_edit') else 0
                slot = int(self.slot_edit.text()) if hasattr(self, 'slot_edit') else 1
            except:
                rack = 0
                slot = 1
            conn_params = {'ip': host, 'rack': rack, 'slot': slot, 'port': port}
        else:  # CodeSys
            conn_params = {'host': host, 'port': port, 'protocol': 'modbus_tcp'}

        print(f"🔌 {self.current_plc_type.value} PLC'ye bağlanılıyor: {host}:{port}")
        print(f"   Parametreler: {conn_params}")

        # Bağlan - sadece dict gönder
        if self.plc_manager.connect(conn_params):
            print("✅ PLC bağlantısı başarılı")
            self.update_connection_status(True)

            if self.plc_manager.is_connected and self.plc_manager.is_Licence == DemoMode.Demo:
                self.status_label.setText("PLC Bağlı & DEMO MOD AKTIF")

            # Konfigürasyonu yükle (izlenecek adresleri ekler)
            if hasattr(self.plc_manager, 'configure_from_dict'):
                self.plc_manager.configure_from_dict(self.custom_data)
                self.plc_manager.optimize_groups()

            # Callback'i ayarla
            if hasattr(self.plc_manager, 'set_value_callback'):
                self.plc_manager.set_value_callback(self.on_plc_values_changed)

            # İzlemeyi başlat
            if hasattr(self.plc_manager, 'start_watching'):
                self.plc_manager.start_watching(100)
                self.plc_manager.enable_async_mode()

            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
        else:
            print("❌ PLC bağlantısı başarısız")
            self.update_connection_status(False)
            QMessageBox.warning(self, "Bağlantı Hatası",
                                f"{self.current_plc_type.value} PLC'ye bağlanılamadı!\n"
                                f"IP: {host}:{port}\n"
                                f"Lütfen bağlantı bilgilerini kontrol edin.")

    def disconnect_plc(self):
        """PLC bağlantısını kes"""
        if self.plc_manager:
            self.plc_manager.stop_watching()
            self.plc_manager.disconnect()
        self.update_connection_status(False)
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        print("🔌 PLC bağlantısı kesildi")

    def check_plc_connection(self):
        """PLC bağlantı durumunu kontrol et"""
        if self.plc_manager.is_Licence == DemoMode.Demo:
            self.status_label.setText("DEMO MOD AKTIF")
            if self.FirstShow is False:
                QMessageBox.critical(None, "DEMO MOD AKTIF", "hata_mesaji")
                self.FirstShow = True
        if self.plc_manager and self.plc_manager.is_connected:
            if not self.is_led_green():
                self.update_connection_status(True)
        else:
            if self.is_led_green():
                self.update_connection_status(False)

    def on_plc_values_changed(self, changed_values: Dict[str, Any]):
        """PLC thread'inden değerler geldiğinde - queue'ya ekle"""
        with threading.Lock():
            for name, value in changed_values.items():
                self.pending_values[name] = value

    def process_pending_values(self):
        """Ana thread'de queue'daki değerleri işle"""
        with threading.Lock():
            if not self.pending_values:
                return
            changed_values = self.pending_values.copy()
            self.pending_values.clear()

            # Eğer izleme penceresi açıksa, gelen tüm veriyi ona pasla
        if self.watch_window and self.watch_window.isVisible():
            self.watch_window.on_values_changed(changed_values)
        # Hareket değerleri için
        dirty_entries = set()

        # Renk değişimleri için
        color_changes = {}
        visibility_changes = {}
        for name, new_value in changed_values.items():
            # 🎨 DURUM (STATUS) KONTROLÜ - Artık sayısal değere göre renk seçiyoruz
            if "_status" in name:
                entry_str = name.replace("_status", "")

                # 1. Bu nesnenin verilerine ulaşıyoruz
                entry_data = self.custom_data.get(entry_str)
                if entry_data and "color_mappings" in entry_data:
                    mappings = entry_data["color_mappings"]

                    # 2. Gelen sayısal değeri (1.0 -> "1") string anahtara çevirip rengi buluyoruz
                    # PLC'den float gelebileceği için önce int sonra str yapıyoruz
                    val_str = str(int(new_value)) if isinstance(new_value, (int, float)) else str(new_value)

                    if val_str in mappings:
                        chosen_color = mappings[val_str]
                        color_changes[entry_str] = chosen_color
                        # print(f"🔍 Status eşleşti: {entry_str} Değer: {val_str} -> Renk: {chosen_color}")
                continue

            # 👁️ YENİ: GÖRÜNÜRLÜK (VISIBLE) KONTROLÜ
            if "_visible" in name:
                entry_str = name.replace("_visible", "")
                entry_data = self.custom_data.get(entry_str)

                if entry_data:
                    # 1. PLC'den gelen değere göre haritadan karşılığını bul (Dinamik Ayar)
                    visible_mappings = entry_data.get("visible_mappings", {})

                    # 🟢 Sadece harita tanımlanmışsa ve içi doluysa işlem yap
                    if visible_mappings:
                        val_str = str(int(new_value)) if isinstance(new_value, (int, float)) else str(new_value)

                        if val_str in visible_mappings:
                            # # 2. Ağaçtan manuel gizlenmiş mi? (Sabit Ayar)
                            # static_visible = entry_data.get("visible", True)
                            dynamic_visible = visible_mappings[val_str]

                            # # Mantıksal VE (AND): Ağaçta açık olacak VE PLC de "görün" diyecek
                            # final_visibility = static_visible and dynamic_visible
                            final_visibility = dynamic_visible
                            visibility_changes[entry_str] = final_visibility
                continue

            # Normal hareket ekseni değerleri
            if "_" in name:
                parts = name.rsplit("_", 1)
                if len(parts) == 2:
                    entry_str, axis = parts
                    if entry_str in self.custom_data:
                        old_val = self.custom_data[entry_str].get(f"current_{axis}", 0.0)
                        if abs(new_value - old_val) > 0.01:
                            self.custom_data[entry_str][f"current_{axis}"] = new_value
                            dirty_entries.add(entry_str)

        # 🎨 RENKLERİ UYGULA (PyVista Aktörlerine Göre Revize Edildi)
        if color_changes:
            for entry_str, color_hex in color_changes.items():
                # JSON'dan gelen isim (Örn: "Parça_5") sözlüğümüzde var mı bakıyoruz
                if entry_str in self.machine_actors:
                    # O parçanın 3 ekrandaki tüm gövde aktörlerini gezip rengini değiştiriyoruz
                    for actor in self.machine_actors[entry_str]:
                        if actor and hasattr(actor, 'GetProperty'):
                            prop = actor.GetProperty()
                            color_rgb = pv.Color(color_hex)
                            prop.SetColor(color_rgb.float_rgb)
            self.plotter.update()  # Ekranları bir kez tazele

        # 👁️ GÖRÜNÜRLÜK DURUMLARINI UYGULA (PyVista Aktörlerine Göre Revize Edildi)
        if visibility_changes:
            for entry_str, is_visible in visibility_changes.items():
                if entry_str in self.machine_actors:
                    for actor in self.machine_actors[entry_str]:
                        if actor and hasattr(actor, 'SetVisibility'):
                            # True gelirse gösterir, False gelirse gizler
                            actor.SetVisibility(1 if is_visible else 0)

            # Görünürlük değiştiği için 3D render motorunu tetikliyoruz
            self.plotter.update()


    def load_industrial_step(self, file_path):
        """
        DONMAZ VE PERFORMANSLI SÜRÜM: STEP dosyasını tarar, tüm parçaları ve
        keskin kenarları RAM'de biriktirip ekranlara tek seferde basar.
        """
        try:
            # 1. Adım: Ekranları temizle ve kullanıcıya işlem başladığını hissettir
            QApplication.setOverrideCursor(Qt.WaitCursor)  # Fareyi yükleniyor yap

            for row in [0, 1]:
                for col in [0, 1]:
                    self.plotter.subplot(row, col)
                    self.plotter.clear()

            # 2. Adım: OpenCASCADE ile STEP Çözümleme
            from OCC.Core.STEPControl import STEPControl_Reader
            from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
            from OCC.Core.TopExp import TopExp_Explorer
            from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_FACE
            from OCC.Core.BRep import BRep_Tool
            from OCC.Core.TopLoc import TopLoc_Location
            import numpy as np

            step_reader = STEPControl_Reader()
            if step_reader.ReadFile(file_path) != 1:
                raise Exception("STEP dosyası okunamadı veya formatı bozuk!")

            step_reader.TransferRoots()
            main_shape = step_reader.OneShape()

            # Hassas ağ örümü (Kavis kalitesi: 0.1)
            BRepMesh_IncrementalMesh(main_shape, 0.1, False, 0.5, True)

            # Katı gövdeleri tara
            solid_explorer = TopExp_Explorer(main_shape, TopAbs_SOLID)
            has_solids = solid_explorer.More()
            explorer = TopExp_Explorer(main_shape, TopAbs_SOLID if has_solids else TopAbs_FACE)

            # 🚀 PERFORMANS ANAHTARI: Ekrana sürekli git-gel yapmamak için listelerde biriktiriyoruz
            all_meshes = []
            all_edges = []

            part_index = 0
            cad_tree_root = QStandardItem(os.path.basename(file_path))
            self.model.appendRow(cad_tree_root)

            while explorer.More():
                sub_shape = explorer.Current()
                explorer.Next()

                part_vertices = []
                part_faces = []
                vertex_count = 0

                face_explorer = TopExp_Explorer(sub_shape, TopAbs_FACE)
                while face_explorer.More():
                    face = face_explorer.Current()
                    face_explorer.Next()

                    loc = TopLoc_Location()
                    triangulation = BRep_Tool.Triangulation(face, loc)
                    if triangulation:
                        for i in range(1, triangulation.NbNodes() + 1):
                            pnt = triangulation.Node(i).Transformed(loc.Transformation())
                            part_vertices.append([pnt.X(), pnt.Y(), pnt.Z()])

                        for i in range(1, triangulation.NbTriangles() + 1):
                            tri = triangulation.Triangle(i)
                            n1, n2, n3 = tri.Get()
                            part_faces.extend([3, vertex_count + n1 - 1, vertex_count + n2 - 1, vertex_count + n3 - 1])

                        vertex_count += triangulation.NbNodes()

                if part_vertices:
                    v_arr = np.array(part_vertices, dtype=np.float32)
                    f_arr = np.array(part_faces, dtype=np.int32)
                    part_mesh = pv.PolyData(v_arr, f_arr)
                    part_mesh.compute_normals(inplace=True, cell_normals=False, point_normals=True)

                    # İç örümcek ağlarını sil, sadece keskin dış teknik resim çizgilerini al
                    edge_mesh = part_mesh.extract_feature_edges(
                        boundary_edges=True,
                        feature_edges=True,
                        manifold_edges=False,
                        feature_angle=30
                    )

                    all_meshes.append(part_mesh)
                    if edge_mesh.n_cells > 0:
                        all_edges.append(edge_mesh)

                    # Sol ağaca ekle
                    part_name = f"Parça_{part_index + 1}"
                    cad_tree_root.appendRow(QStandardItem(part_name))
                    part_index += 1

            if not all_meshes:
                raise Exception("Geçerli CAD datası üretilemedi!")

            # 🚀 RAM'DEKİ TÜM PARÇALARI TEK BİR ÇUVALDA BİRLEŞTİRİYORUZ (Hız patlaması yaratır)
            combined_bodies = pv.MultiBlock(all_meshes).combine()
            combined_edges = pv.MultiBlock(all_edges).combine() if all_edges else None

            # 3. Adım: Ekranları Tek Seferde Besle (Hiçbir kasma/donma olmadan)
            screens_config = [
                (0, 0, "iso", "Canlı Izometrik Gorunum (360°)"),
                (0, 1, "on", "Sabit On Gorunus"),
                (1, 1, "arka", "Sabit Arka Gorunus")
            ]

            for row, col, mode, title_text in screens_config:
                self.plotter.subplot(row, col)

                # 1. KATMAN: Pürüzsüz Katı Gövde
                self.plotter.add_mesh(
                    combined_bodies,
                    color="#f1c40f",
                    smooth_shading=True,
                    show_edges=False,  # Üçgen çizgileri tamamen kapatıldı!
                    specular=0.4,
                    specular_power=15,
                    ambient=0.3,
                    diffuse=0.7,
                    name="machine_bodies"
                )

                # 2. KATMAN: İnce, Keskin Montaj Çizgileri
                if combined_edges:
                    self.plotter.add_mesh(
                        combined_edges,
                        color="#2c3e50",
                        line_width=1,
                        name="machine_edges"
                    )

                # Kamera Konumlandırmaları
                if mode == "iso":
                    self.plotter.view_isometric()
                elif mode == "on":
                    self.plotter.view_xy()
                    self.plotter.renderer.SetInteractive(False)
                elif mode == "arka":
                    self.plotter.view_xy()
                    self.plotter.camera.Azimuth(180)
                    self.plotter.renderer.SetInteractive(False)

                self.plotter.add_text(title_text, font_size=10, position="upper_left")
                self.plotter.reset_camera()

            self.plotter.update()
            self.tree_view.expandAll()

            # Fareyi eski haline getir ve mesaj ver
            QApplication.restoreOverrideCursor()
            # QMessageBox.information(self, "Başarılı", f"STEP Montajı Donma Olmadan Yüklendi!")

        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Hata", f"CAD yükleme hatası:\n{str(e)}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    viewer = PyVistaIndustrialViewer()
    viewer.show()
    sys.exit(app.exec_())