import math
import os
import sys

# Conda ortamının içerisindeki orijinal Qt eklenti klasörünü Windows'a dikte ediyoruz
conda_env_path = os.path.dirname(sys.executable)
plugin_path = os.path.join(conda_env_path, "Library", "plugins", "platforms")

if os.path.exists(plugin_path):
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = plugin_path
from PyQt5.QtWidgets import (
    QMainWindow, QApplication, QSplitter, QTreeView, QWidget,
    QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QPushButton,
    QLineEdit, QComboBox, QMessageBox, QFileDialog
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QStandardItemModel, QStandardItem, QFont
from pyvistaqt import QtInteractor
import pyvista as pv


class PyVistaIndustrialViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyVista 3-Ekranlı Canlı PLC & Endüstriyel İzleme Otomasyonu")
        self.resize(1400, 900)

        # Simüle PLC Değişkenleri
        self.plc_connected = False
        self.rotation_angle = 0.0
        self.plc_L_value = 0.0
        self.plc_B_value = 0.0

        # UI ve 3D Alan Kurulumu
        self.setup_ui()

        # Test amaçlı 3D sahte makine parçaları oluştur ve ekranlara yükle
        self.create_mock_machine_cad()

        # Zamanlayıcılar (Timers)
        # 1. Sol ekranı 360 derece döndüren timer
        self.rotation_timer = QTimer()
        self.rotation_timer.timeout.connect(self.rotate_left_camera)
        self.rotation_timer.start(30)  # ~33 FPS

        # 2. PLC'den canlı veri akışını simüle eden timer
        self.plc_timer = QTimer()
        self.plc_timer.timeout.connect(self.simulate_plc_data_stream)

    def setup_ui(self):
        # Ana Bölücü (3 Sütun)
        self.main_splitter = QSplitter(Qt.Horizontal)

        # ==================== SÜTUN 1: MODEL YAPISI (TREE VIEW) ====================
        self.tree_view = QTreeView()
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["Makine CAD Ağacı"])

        # Örnek Ağaç Yapısı Doldurma
        root_node = self.model.invisibleRootItem()
        ana_gövde = QStandardItem("Ana Şasi [ID: 0:1]")
        hareketli_kafa = QStandardItem("Bükme Kafası [ID: 0:2]")
        slider_eksen = QStandardItem("İlerleme Ekseni [ID: 0:3]")

        root_node.appendRow(ana_gövde)
        ana_gövde.appendRow(hareketli_kafa)
        ana_gövde.appendRow(slider_eksen)

        self.tree_view.setModel(self.model)
        self.tree_view.expandAll()
        self.main_splitter.addWidget(self.tree_view)

        # ==================== 2. SÜTUN: PYVISTA 3D EKRANLAR ====================
        # Ekran bölme (shape) ve çizgi (border) ayarlarını nesne oluşturulurken içeriye paslıyoruz
        self.plotter = QtInteractor(self, shape=(2, 2), border=True)


        # ÖNEMLİ: Sol taraftaki (0,0) hücresini dikeyde birleştirmek için 
        # PyVista'nın entegre subplot mimarisini kullanıyoruz.
        self.plotter.subplot(0, 0)

        self.main_splitter.addWidget(self.plotter)

        # ==================== SÜTUN 3: PLC KONTROL PANELİ ====================
        self.control_panel = QWidget()
        self.control_layout = QVBoxLayout(self.control_panel)

        title = QLabel("PLC Kontrol Paneli")
        title.setFont(QFont("Arial", 12, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        self.control_layout.addWidget(title)

        # PLC Bağlantı Grubu
        plc_group = QGroupBox("Bağlantı Ayarları")
        plc_lay = QVBoxLayout()

        # setup_ui içerisine eklenecek:
        self.import_btn = QPushButton("STEP Dosyası Yükle")
        self.import_btn.setStyleSheet("background-color: #34495e; color: white; font-weight: bold; margin-top: 5px;")
        self.import_btn.clicked.connect(self.select_and_load_step)
        plc_lay.addWidget(self.import_btn)

        self.status_led = QLabel("● PLC Bağlantısı Yok")
        self.status_led.setStyleSheet("color: red; font-weight: bold;")
        plc_lay.addWidget(self.status_led)

        plc_lay.addWidget(QLabel("IP Adresi:"))
        plc_lay.addWidget(QLineEdit("192.168.1.3"))

        self.connect_btn = QPushButton("PLC'ye Bağlan (Simüle)")
        self.connect_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        self.connect_btn.clicked.connect(self.toggle_plc_connection)
        plc_lay.addWidget(self.connect_btn)

        plc_group.setLayout(plc_lay)
        self.control_layout.addWidget(plc_group)

        # Canlı Değerler Grubu
        values_group = QGroupBox("Anlık PLC Metrikleri")
        values_lay = QVBoxLayout()
        self.lbl_l = QLabel("L (İlerleme): 0.00 mm")
        self.lbl_b = QLabel("B (Bükme): 0.00 °")
        values_lay.addWidget(self.lbl_l)
        values_lay.addWidget(self.lbl_b)
        values_group.setLayout(values_lay)
        self.control_layout.addWidget(values_group)

        self.control_layout.addStretch()
        self.main_splitter.addWidget(self.control_panel)

        # Sütun Genişlik Oranlarını Ayarla
        self.main_splitter.setSizes([250, 850, 300])

        # Ana Widget'ı Pencereye Yerleştir
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.addWidget(self.main_splitter)
        self.setCentralWidget(main_widget)



    def create_mock_machine_cad(self):
        """STEP dosyası yerine test amaçlı 3D geometriler üretip ekranlara basar"""
        # 1. Parçaları Üret (Gövde, Piston, Silindir)
        self.mesh_chassis = pv.Cube(center=(0, 0, 0), x_length=10, y_length=4, z_length=2)
        self.mesh_tool = pv.Cylinder(center=(4, 0, 2), direction=(0, 0, 1), radius=1.5, height=2)
        self.mesh_slider = pv.Cube(center=(-2, 0, 1.2), x_length=2, y_length=2, z_length=0.5)

        # ---- EKRAN 1: SOL GENİŞ EKRAN (Serbest İzometrik Perspektif) ----
        self.plotter.subplot(0, 0)
        self.plotter.add_mesh(self.mesh_chassis, color="#7f8c8d", name="sasi")
        self.actor_tool_sol = self.plotter.add_mesh(self.mesh_tool, color="#e74c3c", name="kafa")
        self.actor_slider_sol = self.plotter.add_mesh(self.mesh_slider, color="#3498db", name="slider")
        self.plotter.view_isometric()
        self.plotter.add_text("Canlı Izometrik Gorunum (360°)", font_size=10, position="upper_left")

        # ---- EKRAN 2: SAĞ ÜST EKRAN (Ön Görünüş - XY) ----
        self.plotter.subplot(0, 1)
        self.plotter.add_mesh(self.mesh_chassis, color="#7f8c8d", name="sasi")
        self.actor_tool_on = self.plotter.add_mesh(self.mesh_tool, color="#e74c3c", name="kafa")
        self.actor_slider_on = self.plotter.add_mesh(self.mesh_slider, color="#3498db", name="slider")
        self.plotter.view_xy()  # Ön görünüşe kilitler

        # 🔗 YENİ KİLİTLEME YÖNTEMİ: Bu hücrenin mouse hareketlerini yakalamasını engeller
        self.plotter.renderer.SetInteractive(False)
        self.plotter.add_text("Sabit On Gorunus", font_size=10, position="upper_left")

        # ---- EKRAN 3: SAĞ ALT EKRAN (Arka Görünüş - Ters XY) ----
        self.plotter.subplot(1, 1)
        self.plotter.add_mesh(self.mesh_chassis, color="#7f8c8d", name="sasi")
        self.actor_tool_arka = self.plotter.add_mesh(self.mesh_tool, color="#e74c3c", name="kafa")
        self.actor_slider_arka = self.plotter.add_mesh(self.mesh_slider, color="#3498db", name="slider")
        self.plotter.view_xy()
        self.plotter.camera.Azimuth(180)  # Tam arkaya çevirir

        # 🔗 YENİ KİLİTLEME YÖNTEMİ: Bu hücrenin mouse hareketlerini yakalamasını engeller
        self.plotter.renderer.SetInteractive(False)
        self.plotter.add_text("Sabit Arka Gorunus", font_size=10, position="upper_left")

        self.plotter.update()

    def rotate_left_camera(self):
        """Sol geniş ekrandaki kamerayı kendi ekseninde yumuşakça döndürür"""
        self.plotter.subplot(0, 0)  # Sol ekrana odaklan
        self.plotter.camera.Azimuth(0.4)  # Her adımda 0.4 derece döndür
        self.plotter.update()

    def toggle_plc_connection(self):
        """PLC Bağlantısını başlatır / keser"""
        if not self.plc_connected:
            self.plc_connected = True
            self.status_led.setText("● PLC Bağlı (Veri Akıyor)")
            self.status_led.setStyleSheet("color: green; font-weight: bold;")
            self.connect_btn.setText("Bağlantıyı Kes")
            self.connect_btn.setStyleSheet("background-color: #c0392b; color: white;")
            self.plc_timer.start(50)  # Her 50ms'de bir PLC verisi aksın
        else:
            self.plc_connected = False
            self.status_led.setText("● PLC Bağlantısı Yok")
            self.status_led.setStyleSheet("color: red; font-weight: bold;")
            self.connect_btn.setText("PLC'ye Bağlan (Simüle)")
            self.connect_btn.setStyleSheet("background-color: #27ae60; color: white;")
            self.plc_timer.stop()

    def simulate_plc_data_stream(self):
        """PLC'den gelen canlı veriye göre parçaları 3 ekranda birden kaydırır/döndürür"""
        # Zaman bazlı sinüs dalgalarıyla sahte eksen hareketleri üretiyoruz
        self.rotation_angle += 0.05

        # 1. İlerleme Ekseni (Slider) İleri-Geri kaysın (-2.0 ile +2.0 arası)
        self.plc_L_value = math.sin(self.rotation_angle) * 2.0
        self.lbl_l.setText(f"L (İlerleme): {self.plc_L_value * 50:7.2f} mm")

        # 2. Bükme Kafası kendi ekseninde dönsün
        self.plc_B_value = (self.rotation_angle * 20) % 360
        self.lbl_b.setText(f"B (Bükme): {self.plc_B_value:7.2f} °")

        # 🚀 GÜNCELLEME: Tüm alt ekranlardaki (Perspektif, Ön, Arka) aktörleri hareket ettiriyoruz
        actors_list = [
            (self.actor_slider_sol, self.actor_tool_sol),
            (self.actor_slider_on, self.actor_tool_on),
            (self.actor_slider_arka, self.actor_tool_arka)
        ]

        for idx, (slider_actor, tool_actor) in enumerate(actors_list):
            # İlgili subplot'u seçerek grafik motoruna o alanda işlem yapacağını bildiriyoruz
            row = 0 if idx < 2 else 1
            col = 0 if idx == 0 else 1
            self.plotter.subplot(row, col)

            # Slider'ı X ekseninde kaydır
            slider_actor.position = (self.plc_L_value, 0, 0)

            # Bükme kafasını Z ekseninde döndür
            tool_actor.orientation = (0, 0, self.plc_B_value)

        # Tüm ekran kartı tamponunu tek seferde yenile
        self.plotter.update()





    def select_and_load_step(self):
        """Kullanıcıya dosya seçtirir ve yükleme sürecini başlatır"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "STEP Dosyası Seç", "", "STEP Dosyaları (*.stp *.step)"
        )
        if file_path:
            self.load_industrial_step(file_path)

    def load_industrial_step(self, file_path):
        """
        STEP dosyasını OpenCASCADE ile okur, RAM üzerinde PyVista mesh yapısına çevirir.
        Eski test aktörlerini temizler ve 3 ekrana gerçekçi ışık/gölge (PBR) ile basar.
        """
        try:
            # 1. Adım: Ekranlardaki eski test küplerini/silindirlerini kesin olarak temizle
            for row in [0, 1]:
                for col in [0, 1]:
                    self.plotter.subplot(row, col)
                    self.plotter.clear()

            # ==================== GERÇEKÇİ CAD OKUMA VE DÖNÜŞTÜRME MOTORU ====================
            from OCC.Core.STEPControl import STEPControl_Reader
            from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
            from OCC.Core.TopExp import TopExp_Explorer
            from OCC.Core.TopAbs import TopAbs_FACE
            from OCC.Core.BRep import BRep_Tool
            from OCC.Core.TopLoc import TopLoc_Location
            import numpy as np

            # STEP dosyasını diski yormadan hafızaya yükle
            step_reader = STEPControl_Reader()
            status = step_reader.ReadFile(file_path)
            if status != 1:
                raise Exception("STEP dosyası okunamadı veya dosya formatı uyumsuz!")

            step_reader.TransferRoots()
            shape = step_reader.OneShape()

            # 🛠️ ÇÖZÜNÜRLÜK AYARI: 0.1 veya 0.2 değeri, vidalar ve kavisler dahil
            # makinenin tüm detaylarını keskinleştirir. (Sayı küçüldükçe detay ve kalite artar)
            # Detay seviyesi: 0.02 mm lineer sapma, 0.5° açısal sapma
            BRepMesh_IncrementalMesh(shape, 0.02, True, 0.5)

            all_vertices = []
            all_faces = []
            vertex_count = 0

            # Modelin içindeki tüm matematiksel yüzeyleri (Face) tara ve poligon ağlarına çevir
            explorer = TopExp_Explorer(shape, TopAbs_FACE)
            while explorer.More():
                face = explorer.Current()
                explorer.Next()

                loc = TopLoc_Location()
                triangulation = BRep_Tool.Triangulation(face, loc)
                if triangulation:
                    # 3D Nokta koordinatlarını (X, Y, Z) matrise kaydet
                    for i in range(1, triangulation.NbNodes() + 1):
                        pnt = triangulation.Node(i).Transformed(loc.Transformation())
                        all_vertices.append([pnt.X(), pnt.Y(), pnt.Z()])

                    # Üçgen yüzey dizilimlerini (Faces) PyVista formatına uyarla [3, id1, id2, id3]
                    for i in range(1, triangulation.NbTriangles() + 1):
                        tri = triangulation.Triangle(i)
                        n1, n2, n3 = tri.Get()
                        all_faces.extend([3, vertex_count + n1 - 1, vertex_count + n2 - 1, vertex_count + n3 - 1])

                    vertex_count += triangulation.NbNodes()

            if not all_vertices:
                raise Exception("STEP dosyasından işlenebilir 3D mesh datası üretilemedi!")

            # Array'leri NumPy formatına mühürle ve PyVista PolyData objesini oluştur
            vertices = np.array(all_vertices, dtype=np.float32)
            faces = np.array(all_faces, dtype=np.int32)
            combined_mesh = pv.PolyData(vertices, faces)
            # =================================================================================

            # ---- 1. EKRAN: SOL GENİŞ EKRAN (Canlı Dönen Perspektif Görünüm) ----
            self.plotter.subplot(0, 0)
            self.plotter.add_mesh(
                combined_mesh,
                color="#f1c40f",  # Endüstriyel şık sarı tonu
                pbr=True,  # Gerçekçi fiziksel tabanlı ışık yansıması (GÖLGELERİ AÇAR)
                metallic=0.5,  # Yüzeye hafif çelik/metal hissi verir
                roughness=0.4,  # Işığın yüzeyde ne kadar pürüzsüz yayılacağını belirler
                # show_edges=True,  # üçgen kenarlarını gösterir
                name="step_model"
            )
            self.plotter.view_isometric()
            self.plotter.add_text("Canlı Izometrik Gorunum (360°)", font_size=10, position="upper_left")

            # ---- 2. EKRAN: SAĞ ÜST EKRAN (Kilitli Ön Görünüş - XY) ----
            self.plotter.subplot(0, 1)
            self.plotter.add_mesh(
                combined_mesh,
                color="#f1c40f",
                pbr=True,
                metallic=0.5,
                roughness=0.4,
                name="step_model"
            )
            self.plotter.view_xy()
            self.plotter.renderer.SetInteractive(False)  # Mouse ile dönmesini engeller
            self.plotter.add_text("Sabit On Gorunus", font_size=10, position="upper_left")

            # ---- 3. EKRAN: SAĞ ALT EKRAN (Kilitli Arka Görünüş - Ters XY) ----
            self.plotter.subplot(1, 1)
            self.plotter.add_mesh(
                combined_mesh,
                color="#f1c40f",
                pbr=True,
                metallic=0.5,
                roughness=0.4,
                name="step_model"
            )
            self.plotter.view_xy()
            self.plotter.camera.Azimuth(180)  # Kamerayı tam 180 derece arkaya çevirir
            self.plotter.renderer.SetInteractive(False)  # Mouse ile dönmesini engeller
            self.plotter.add_text("Sabit Arka Gorunus", font_size=10, position="upper_left")

            # 🚀 STÜDYO IŞIKLANDIRMASI VE OTOMATİK EKRANA SIĞDIRMA (ZOOM-IN)
            for row, col in [(0, 0), (0, 1), (1, 1)]:
                self.plotter.subplot(row, col)

                # QtInteractor üzerinde doğrudan enable_light_kit() çalışmadığı için
                # doğrudan alt render motoruna (renderer) erişip ışıkları açıyoruz:
                self.plotter.renderer.enable_light_kit()

                self.plotter.reset_camera()  # Kamerayı modele yaklaştırır

            # Grafik kartı tamponunu (Buffer) tazele
            self.plotter.update()

            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(self, "Başarılı", "STEP Dosyası detaylı ve ışıklandırılmış olarak yüklendi!")

        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Hata", f"STEP dosyası yüklenirken bir hata oluştu:\n{str(e)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    viewer = PyVistaIndustrialViewer()
    viewer.show()
    sys.exit(app.exec_())