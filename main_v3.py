import sys
import math
import os
import threading
from typing import Dict, Any
import matplotlib
from OCC.Core.XCAFApp import XCAFApp_Application

matplotlib.use('Qt5Agg') # Sabit bir backend seç
matplotlib.interactive(False) # İnteraktif modu kodun başında zorla kapat

# 1. ADIM: Qt Platform hatasını Windows için kökten çözen blok (En başta olmalı)
conda_env_path = os.path.dirname(sys.executable)
plugin_path = os.path.join(conda_env_path, "Library", "plugins", "platforms")
if os.path.exists(plugin_path):
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = plugin_path

from PyQt5.QtWidgets import (
    QMainWindow, QApplication, QSplitter, QTreeView, QWidget,
    QVBoxLayout, QGroupBox, QLabel, QPushButton, QLineEdit, QMessageBox, QHBoxLayout
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
try:
    from ManagerPLC.plc_communication_base import PLCType, DemoMode
    from ManagerPLC.plc_factory import PLCManagerFactory

    PLC_LIBRARY_LOADED = True
except ImportError:
    PLC_LIBRARY_LOADED = False
    print("⚠️ PLC kütüphaneleri bulunamadı, arayüz simülasyon modunda başlatılıyor.")


class PyVistaIndustrialViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.FirstShow = False
        self.custom_data = {}
        self.resize(1500, 950)

        # 🚀 KİLİTLENMEYİ ÖNLER: Sadece bu sınıfa özel thread güvenliği kilidi
        self.data_lock = threading.Lock()

        self.current_trihedron = None  # O an ekranda aktif olan tek bir eksen okunu tutar
        self.active_trihedrons = {}  # Hangi parçanın oku açık takip etmek için (Parça ID -> Trihedron nesnesi)
        self.machine_actors = {}  # 🚀 DÜZELTME: Eksik olan PyVista aktör referans sözlüğü eklendi

        self.pipe_ais = None
        self._is_updating = False

        self.label_to_item = {}
        self.occ_lock = threading.Lock()
        self.last_step_done_state = False
        self.last_valid_lrb = (0.0, 0.0, 0.0)
        self.assembly_children = {}
        self.parent_assembly = {}
        self.assembly_visible = {}
        self.transform_cache = {}
        self.parent_child_relations = {}
        self.watch_window = None

        # İstatistik sayaçları
        self.read_count = 0
        self.change_count = 0
        self.update_count = 0

        # Test modu
        self.test_mode = False
        self.test_timer = None
        self.test_targets = [(200, 0, 90), (0, 90, 0), (500, -0, 90)]
        self.test_step = 0
        self.test_current_L = 0.0
        self.test_current_R = 0.0
        self.test_current_B = 0.0

        # STEP yükleme için
        self.item_to_ais = {}
        self.ais_to_item = {}
        self.group_to_ais = {}
        self.current_plc_type = None
        self.json_path = None
        self.step_file_path = None
        self.setWindowTitle("PyVista + OpenCASCADE Endüstriyel 3D İzleme Otomasyonu")

        # PLC Yönetim Değişkenleri
        self.plc_manager = None
        self.plc_connected = False

        # Kamera döndürme açısı (Sol ekran için)
        self.rotation_angle = 0.0
        self.BoolImportStep = False
        self.setup_ui()
        self.create_mock_machine_cad()

        # Değişen değerler için queue
        self.pending_values = {}
        self.value_update_timer = QTimer()
        self.value_update_timer.timeout.connect(self.process_pending_values)
        self.value_update_timer.start(50)

        # Kamera döndürme zamanlayıcısı (Canlı ekran efekti)
        self.rotation_timer = QTimer()
        self.rotation_timer.timeout.connect(self.rotate_left_camera)
        self.rotation_timer.start(100)



    def setup_ui(self):
        self.main_splitter = QSplitter(Qt.Horizontal)

        # ==================== 1. SÜTUN: TREE VIEW ====================
        self.tree_view = QTreeView()
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["Makine CAD Ağacı"])
        self.tree_view.setModel(self.model)
        self.main_splitter.addWidget(self.tree_view)

        # ==================== 2. SÜTUN: PYVISTA 3D EKRANLAR ====================
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

        # PLC Kontrol Grubu
        plc_group = QGroupBox("PLC Haberleşme Durumu")
        self.plc_lay = QVBoxLayout()

        self.status_led = QLabel()
        self.status_led.setFixedSize(16, 16)
        self.status_led.setStyleSheet("border-radius: 8px; background-color: gray;")
        status_layout = QHBoxLayout()
        status_layout.addWidget(self.status_led)
        self.status_label = QLabel("PLC Bağlantı Bekleniyor...")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        self.plc_lay.addLayout(status_layout)

        self.plc_status_lbl = QLabel("PLC Durumu: Bağlantı Yok")
        self.plc_status_lbl.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.plc_lay.addWidget(self.plc_status_lbl)

        self.plc_connect_btn = QPushButton("PLC'ye Bağlan")
        self.plc_connect_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 8px;")
        self.plc_connect_btn.clicked.connect(self.connect_plc)  # 🚀 DÜZELTME: Parantezler kaldırıldı
        self.plc_lay.addWidget(self.plc_connect_btn)

        plc_group.setLayout(self.plc_lay)
        self.control_layout.addWidget(plc_group)

        self.control_layout.addStretch()
        self.main_splitter.addWidget(self.control_panel)

        self.main_splitter.setSizes([250, 950, 300])

        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.addWidget(self.main_splitter)
        self.setCentralWidget(main_widget)

    def initialize_real_plc(self, file_path):
        """🚀 DÜZELTME: PLC başlatma - PyQt5 versiyonuyla aynı mantık"""
        self.step_file_path = file_path
        self.json_path = os.path.splitext(file_path)[0] + ".json"

        if self.plc_manager is None:
            if PLC_LIBRARY_LOADED:
                try:
                    # 1. Manager'ı oluştur
                    self.plc_manager = PLCManagerFactory.create_from_json(self.json_path)

                    # 2. 🚀 KRİTİK: PLC tipini al
                    self.current_plc_type = self.plc_manager.get_plc_type()  # BU ÖNEMLİ!

                    # 3. 🚀 KRİTİK: custom_data'yı doldur (PyQt5'teki gibi)
                    self.seperate_dict()  # <-- BURASI EKSİKTİ!

                    # 4. Manager'ı konfigüre et
                    if hasattr(self.plc_manager, 'configure_from_dict'):
                        self.plc_manager.configure_from_dict(self.custom_data)
                        self.plc_manager.optimize_groups()

                    # 5. Callback'i ayarla
                    if hasattr(self.plc_manager, 'set_value_callback'):
                        self.plc_manager.set_value_callback(self.on_plc_values_changed)

                    self.plc_status_lbl.setText(f"Kayıtlı PLC Tipi: {self.current_plc_type.value}")
                    self.plc_status_lbl.setStyleSheet("color: #d35400; font-weight: bold;")
                    print(f"✅ PLC Manager başarıyla oluşturuldu. Tip: {self.current_plc_type.value}")
                    print(f"📊 custom_data içeriği: {len(self.custom_data)} öğe")

                except Exception as e:
                    print(f"❌ PLC Başlatma Hatası: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                self.plc_status_lbl.setText("PLC Modülü: Simülasyon Aktif")

    def create_mock_machine_cad(self):
        self.plotter.subplot(0, 0)
        self.plotter.add_mesh(pv.Cube(), color="#95a5a6", name="init_mesh")
        self.plotter.view_isometric()

    def rotate_left_camera(self):
        if hasattr(self, 'plotter'):
            self.plotter.subplot(0, 0)
            self.plotter.camera.Azimuth(0.3)
            self.plotter.update()

    def select_and_load_step(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "STEP Dosyası Seç", "", "STEP Files (*.stp *.step)")
        if file_path:
            # 🚀 ÖNCE JSON VERİLERİNİ ÇEKELİM Kİ load_industrial_step PARÇALARI DOĞRU ID İLE EŞLEŞTİREBİLSİN!
            self.initialize_real_plc(file_path)
            self.load_industrial_step(file_path)

    def init_plc_manager(self):
        try:
            self.plc_manager = PLCManagerFactory.create_manager(
                self.current_plc_type,
                self.json_path
            )
            self.seperate_dict()
            print(f"✅ {self.current_plc_type.value} PLC Manager oluşturuldu")
        except Exception as e:
            print(f"❌ PLC Manager oluşturma hatası: {e}")
            self.plc_manager = None



    def seperate_dict(self):
        """PLC manager'dan custom_data'yı ayır"""
        if not self.plc_manager:
            print("⚠️ PLC Manager mevcut değil!")
            return

        config_keys = ["_plc_type", "_plc_type_enum", "host", "port", "timeout"]

        # 🚀 custom_data'yı temizle
        self.custom_data = {}

        for key, val in self.plc_manager.custom_data.items():
            if key in config_keys or key.startswith("_"):
                continue
            if isinstance(val, dict):
                self.custom_data[key] = val
                print(f"📌 custom_data eklendi: {key} -> {val.get('name', 'isimsiz')}")

        print(f"✅ {len(self.custom_data)} adet custom_data öğesi ayrıldı")
    def connect_plc(self):
        if not self.plc_manager:
            QMessageBox.warning(self, "Uyarı", "PLC Manager oluşturulamadı! Lütfen önce CAD yükleyin.")
            return

        host = "192.168.1.3"
        port = 502

        if self.current_plc_type == PLCType.MODBUS:
            conn_params = {'host': host, 'port': port}
        elif self.current_plc_type == PLCType.SIEMENS:
            conn_params = {'ip': host, 'rack': 0, 'slot': 1, 'port': 102}
        else:
            conn_params = {'host': host, 'port': port, 'protocol': 'modbus_tcp'}

        print(f"🔌 {self.current_plc_type.value} PLC'ye bağlanılıyor: {host}:{port}")

        if self.plc_manager.connect(conn_params):
            print("✅ PLC bağlantısı başarılı")
            self.update_connection_status(True)
            self.plc_connected = True

            # 🚀 ÖNCE: Configürasyonu yükle (custom_data dolu OLMALI)
            if hasattr(self.plc_manager, 'configure_from_dict'):
                if self.custom_data:  # custom_data boş mu kontrol et
                    self.plc_manager.configure_from_dict(self.custom_data)
                    self.plc_manager.optimize_groups()
                    print(f"✅ Konfigürasyon yüklendi: {len(self.custom_data)} öğe")
                else:
                    print("⚠️ UYARI: custom_data BOŞ! PLC verileri eşlenemeyecek!")

            # 🚀 SONRA: Callback'i ayarla
            if hasattr(self.plc_manager, 'set_value_callback'):
                self.plc_manager.set_value_callback(self.on_plc_values_changed)
                print("✅ Callback ayarlandı")

            # 🚀 EN SON: İzlemeyi başlat
            if hasattr(self.plc_manager, 'start_watching'):
                self.plc_manager.start_watching(100)
                self.plc_manager.enable_async_mode()
                print("✅ İzleme başlatıldı (100ms)")

            if self.plc_manager.is_connected and self.plc_manager.is_Licence == DemoMode.Demo:
                self.status_label.setText("PLC Bağlı & DEMO MOD AKTIF")

        else:
            print("❌ PLC bağlantısı başarısız")
            self.update_connection_status(False)
            QMessageBox.warning(self, "Bağlantı Hatası", f"PLC'ye bağlanılamadı!")

    def update_connection_status(self, connected):
        if connected:
            self.status_led.setStyleSheet("border-radius: 8px; background-color: #2ecc71;")
            self.status_label.setText("PLC Bağlandı")
            self.plc_status_lbl.setText("PLC Durumu: BAĞLANDI (Canlı)")
            self.plc_status_lbl.setStyleSheet("color: #27ae60; font-weight: bold;")
        else:
            self.status_led.setStyleSheet("border-radius: 8px; background-color: #e74c3c;")
            self.status_label.setText("PLC Bağlantı YOK")
            self.plc_status_lbl.setText("PLC Durumu: Bağlantı Yok")
            self.plc_status_lbl.setStyleSheet("color: #c0392b; font-weight: bold;")

    def on_plc_values_changed(self, changed_values: Dict[str, Any]):
        print(f"🔔 Callback çağrıldı! {len(changed_values)} değer")  # Bu mesajı görmelisin
        with self.data_lock:  # 🚀 Düzenlendi: self.data_lock kullanıldı
            for name, value in changed_values.items():
                self.pending_values[name] = value

    def process_pending_values(self):

        # 🚀 Düzenlendi: Kilit sadece veriyi güvenli kopyalamak için çok kısa süreli tutulur
        with self.data_lock:
            if not self.pending_values:
                return
            changed_values = self.pending_values.copy()
            self.pending_values.clear()

        # 🔓 KİLİT SERBEST BIRAKILDI: Alt satırlardaki ağır UI işlemleri yapılırken
        # PLC arka planda rahatça yeni verileri okumaya ve basmaya devam edebilir!

        if self.watch_window and self.watch_window.isVisible():
            self.watch_window.on_values_changed(changed_values)

        dirty_entries = set()
        color_changes = {}
        visibility_changes = {}

        for name, new_value in changed_values.items():
            # Word Swap kaynaklı hatalı sinyalleri yut
            if isinstance(new_value, (int, float)) and abs(new_value) > 1000000.0:
                continue

            # Durum (Status) kontrolü
            if "_status" in name:
                entry_str = name.replace("_status", "")
                entry_data = self.custom_data.get(entry_str)
                if entry_data and "color_mappings" in entry_data:
                    mappings = entry_data["color_mappings"]
                    val_str = str(int(new_value)) if isinstance(new_value, (int, float)) else str(new_value)
                    if val_str in mappings:
                        color_changes[entry_str] = mappings[val_str]
                continue

            # Görünürlük (Visible) kontrolü
            if "_visible" in name:
                entry_str = name.replace("_visible", "")
                entry_data = self.custom_data.get(entry_str)
                if entry_data:
                    visible_mappings = entry_data.get("visible_mappings", {})
                    if visible_mappings:
                        val_str = str(int(new_value)) if isinstance(new_value, (int, float)) else str(new_value)
                        if val_str in visible_mappings:
                            visibility_changes[entry_str] = visible_mappings[val_str]
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

        # PyVista Aktörlerine renk ve görünürlüğü uygula
        if color_changes:
            for entry_str, color_hex in color_changes.items():
                self.apply_color_to_entry(entry_str, color_hex)

        if visibility_changes:
            self.apply_visibility_to_entry(visibility_changes)

    def apply_color_to_entry(self, entry_str, color_hex):
        """PLC'den gelen rengi PyVista aktörlerine yansıtır"""
        if entry_str in self.machine_actors:
            for actor in self.machine_actors[entry_str]:
                if actor and hasattr(actor, 'GetProperty'):
                    prop = actor.GetProperty()
                    color_rgb = pv.Color(color_hex)
                    prop.SetColor(color_rgb.float_rgb)
            self.plotter.update()

    def apply_visibility_to_entry(self, visibility_changes):
        """PLC'den gelen görünürlük durumunu aktörlere yansıtır"""
        if not visibility_changes:
            return
        for entry_str, is_visible in visibility_changes.items():
            if entry_str in self.machine_actors:
                for actor in self.machine_actors[entry_str]:
                    if actor and hasattr(actor, 'SetVisibility'):
                        actor.SetVisibility(1 if is_visible else 0)
        self.plotter.update()

    def occ_shape_to_pyvista(self, shape, deflection=0.5):
        """OpenCASCADE TopoDS_Shape nesnesini PyVista PolyData mesh yapısına dönüştürür"""
        from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
        from OCC.Core.TopExp import TopExp_Explorer
        from OCC.Core.TopAbs import TopAbs_FACE
        from OCC.Core.BRep import BRep_Tool
        from OCC.Core.TopLoc import TopLoc_Location
        import pyvista as pv
        import numpy as np

        # Şekli doğrusal sapma (deflection) değeriyle üçgen kapla (Mesh oluştur)
        BRepMesh_IncrementalMesh(shape, deflection)

        vertices = []
        faces = []
        v_offset = 0

        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        while explorer.More():
            face = explorer.Current()
            explorer.Next()

            loc = TopLoc_Location()
            triangulation = BRep_Tool.Triangulation(face, loc)
            if triangulation is None:
                continue

            transf = loc.Transformation()
            nodes = triangulation.Nodes()
            num_nodes = triangulation.NbNodes()

            # Noktaları (Vertices) çıkar ve koordinat matrisine ekle
            for i in range(1, num_nodes + 1):
                p = nodes.Value(i).Transformed(transf)
                vertices.append([p.X(), p.Y(), p.Z()])

            # Yüzeyleri (Faces) VTK formatına uygun çıkar: [3, id1, id2, id3]
            triangles = triangulation.Triangles()
            num_triangles = triangulation.NbTriangles()
            for i in range(1, num_triangles + 1):
                tri = triangles.Value(i)
                n1, n2, n3 = tri.Get()
                faces.append([3, n1 - 1 + v_offset, n2 - 1 + v_offset, n3 - 1 + v_offset])

            v_offset += num_nodes

        if not vertices:
            return None

        pv_vertices = np.array(vertices, dtype=np.float32)
        pv_faces = np.hstack(faces).astype(np.int32)

        return pv.PolyData(pv_vertices, pv_faces)

    def add_label_to_tree(self, label, parent_item, parent_entry=None):
        # ... Üst kısımdaki etiket ismi, custom_data ve QStandardItem işlemleri tamamen AYNI kalıyor ...
        entry_str = label.EntryDumpToString()
        default_name = f"Etiket {entry_str}"
        try:
            name = self.shape_tool.GetLabelName(label).ToExtString()
            if name:
                default_name = name
        except:
            pass
        custom = self.custom_data.get(entry_str, {})
        custom_name = custom.get("name", "")
        display_text = custom_name if custom_name else default_name
        addr_parts = []
        for axis in ['tx', 'ty', 'tz', 'rx', 'ry', 'rz']:
            a = custom.get(f"Adress_{axis}")
            if a is not None:
                addr_parts.append(f"{axis.upper()}:{a}")

    def load_industrial_step(self, file_path):

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