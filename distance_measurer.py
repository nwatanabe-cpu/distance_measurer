from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand, QgsMapToolIdentify
from qgis.core import (
    QgsDistanceArea, QgsCoordinateTransform,
    QgsProject, QgsPointXY, QgsWkbTypes, QgsRectangle, QgsFeatureRequest, QgsGeometry
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction, QLabel
from qgis.PyQt.QtGui import QIcon, QColor
import os

# --- 共通のラベル管理関数 ---
def get_display_label(canvas):
    label = QLabel(canvas)
    label.setStyleSheet("""
        background-color: rgba(0, 0, 0, 160); 
        color: #00FF00; 
        padding: 8px; 
        font-weight: bold; 
        font-size: 16pt; 
        border-radius: 8px;
        border: 1px solid white;
    """)
    label.move(10, 50)
    label.hide()
    return label

def create_distance_area(source_crs):
    """入力CRSに合わせた測地計算オブジェクトを作成する"""
    distance_area = QgsDistanceArea()
    distance_area.setSourceCrs(
        source_crs,
        QgsProject.instance().transformContext()
    )
    distance_area.setEllipsoid('GRS80')
    return distance_area

# ─────────────────────────────────────────────
# 距離計測ツール（左クリックで折れ線、Escで1点戻る、右クリックでリセット）
# ─────────────────────────────────────────────
class CustomDistanceTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, iface, label):
        super().__init__(canvas)
        self.canvas = canvas
        self.iface = iface
        self.label = label

        # 赤い折れ線（描画用・マップ座標）
        self.rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.LineGeometry)
        self.rubber_band.setColor(QColor(255, 0, 0))
        self.rubber_band.setWidth(2)

        self.map_points = []    # マップ座標（描画用）
        self.da = create_distance_area(
            self.canvas.mapSettings().destinationCrs()
        )

    def canvasPressEvent(self, e):
        if e.button() == Qt.LeftButton:
            map_pt = QgsPointXY(self.toMapCoordinates(e.pos()))

            self.map_points.append(map_pt)
            self.rubber_band.addPoint(map_pt)
            self.label.show()

        elif e.button() == Qt.RightButton:
            self.reset()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            if self.map_points:
                self.map_points.pop()
                # ラバーバンドを再描画
                self.rubber_band.reset(QgsWkbTypes.LineGeometry)
                for p in self.map_points:
                    self.rubber_band.addPoint(p)
                if not self.map_points:
                    self.label.hide()
                else:
                    self._update_label(self.map_points)
            return
        super().keyPressEvent(e)

    def canvasMoveEvent(self, e):
        if not self.map_points:
            return
        curr_map = QgsPointXY(self.toMapCoordinates(e.pos()))

        # ラバーバンド再描画（確定点 + マウス現在位置）
        self.rubber_band.reset(QgsWkbTypes.LineGeometry)
        for p in self.map_points:
            self.rubber_band.addPoint(p)
        self.rubber_band.addPoint(curr_map)

        temp = self.map_points + [curr_map]
        self._update_label(temp)

    def _update_label(self, pts):
        if len(pts) >= 2:
            self.da = create_distance_area(
                self.canvas.mapSettings().destinationCrs()
            )
            dist = self.da.measureLine(pts)
            self.label.setText(f"距離: {dist:.2f} m")
        else:
            self.label.setText("次の点をクリック...")
        self.label.adjustSize()

    def reset(self):
        self.map_points = []
        self.rubber_band.reset(QgsWkbTypes.LineGeometry)
        self.label.hide()

    def deactivate(self):
        self.reset()
        super().deactivate()


# ─────────────────────────────────────────────
# 面積計測ツール（ドラッグ範囲でポリゴン地物を選択、Ctrl+クリックで追加選択）
# ─────────────────────────────────────────────
class CustomAreaTool(QgsMapToolIdentify):
    def __init__(self, canvas, iface, label):
        super().__init__(canvas)
        self.canvas = canvas
        self.iface = iface
        self.label = label
        self.selected_info = {}  # fid: {'area': float, 'geom': QgsGeometry}

        # 選択地物ハイライト（赤）
        self.highlight = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.highlight.setColor(QColor(255, 0, 0, 100))
        self.highlight.setStrokeColor(QColor(255, 0, 0, 200))
        self.highlight.setWidth(2)

        # ドラッグ枠線（青）
        self.rect_rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.rect_rubber_band.setColor(QColor(0, 0, 255, 30))
        self.rect_rubber_band.setStrokeColor(QColor(0, 0, 255, 150))
        self.rect_rubber_band.setWidth(1)
        self.start_point = None

        self.da = None

    def canvasPressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.start_point = self.toMapCoordinates(e.pos())
            self.rect_rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        elif e.button() == Qt.RightButton:
            self.reset_selection()

    def canvasMoveEvent(self, e):
        if self.start_point:
            curr = self.toMapCoordinates(e.pos())
            rect = QgsRectangle(self.start_point, curr)
            self.rect_rubber_band.setToGeometry(QgsGeometry.fromRect(rect), None)
            self.rect_rubber_band.show()

    def canvasReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or not self.start_point:
            return

        end_point = self.toMapCoordinates(e.pos())
        layer = self.iface.activeLayer()
        if not layer or layer.geometryType() != QgsWkbTypes.PolygonGeometry:
            self.rect_rubber_band.hide()
            self.start_point = None
            return

        # マップCRS上の選択矩形（クリックの場合はピクセル換算でバッファを付与）
        map_rect = QgsRectangle(self.start_point, end_point)
        if map_rect.isEmpty():
            pixel_size = self.canvas.mapUnitsPerPixel() * 5
            map_rect.grow(pixel_size)

        # レイヤーCRSへ矩形を変換してからフィルタに使う
        layer_crs = layer.crs()
        map_crs = self.canvas.mapSettings().destinationCrs()
        rect_tr = QgsCoordinateTransform(map_crs, layer_crs, QgsProject.instance())
        try:
            layer_rect = rect_tr.transformBoundingBox(map_rect)
        except Exception:
            layer_rect = map_rect

        # Ctrlなしなら選択リセット
        if not (e.modifiers() & Qt.ControlModifier):
            self.selected_info = {}

        self.da = create_distance_area(layer_crs)
        map_tr = QgsCoordinateTransform(
            layer_crs,
            map_crs,
            QgsProject.instance()
        )

        request = QgsFeatureRequest().setFilterRect(layer_rect)
        for feat in layer.getFeatures(request):
            fid = feat.id()
            geom = feat.geometry()
            if fid in self.selected_info:
                del self.selected_info[fid]
            else:
                area = self.da.measureArea(geom)
                display_geom = QgsGeometry(geom)
                try:
                    display_geom.transform(map_tr)
                except Exception:
                    display_geom = geom
                self.selected_info[fid] = {'area': area, 'geom': display_geom}

        self.rect_rubber_band.hide()
        self.start_point = None
        self.update_highlight_and_label()

    def update_highlight_and_label(self):
        self.highlight.reset(QgsWkbTypes.PolygonGeometry)
        if self.selected_info:
            total_m2 = sum(v['area'] for v in self.selected_info.values())
            for v in self.selected_info.values():
                self.highlight.addGeometry(v['geom'], None)
            area_ha = total_m2 / 10000.0
            self.label.setText(f"合計({len(self.selected_info)}件): {area_ha:.4f} ha")
            self.label.adjustSize()
            self.label.show()
            self.highlight.show()
        else:
            self.label.hide()
            self.highlight.hide()

    def reset_selection(self):
        self.selected_info = {}
        self.highlight.reset(QgsWkbTypes.PolygonGeometry)
        try:
            self.label.hide()
        except RuntimeError:
            pass

    def deactivate(self):
        self.reset_selection()
        super().deactivate()


# ─────────────────────────────────────────────
# ライン延長計測ツール（ドラッグ範囲でライン地物を選択、Ctrl+クリックで追加選択）
# ─────────────────────────────────────────────
class CustomLineTool(QgsMapToolIdentify):
    def __init__(self, canvas, iface, label):
        super().__init__(canvas)
        self.canvas = canvas
        self.iface = iface
        self.label = label
        self.selected_info = {}  # fid: {'length': float, 'geom': QgsGeometry}

        # 選択地物ハイライト（オレンジ）
        self.highlight = QgsRubberBand(self.canvas, QgsWkbTypes.LineGeometry)
        self.highlight.setColor(QColor(255, 140, 0, 200))
        self.highlight.setWidth(3)

        # ドラッグ枠線（青）
        self.rect_rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.rect_rubber_band.setColor(QColor(0, 0, 255, 30))
        self.rect_rubber_band.setStrokeColor(QColor(0, 0, 255, 150))
        self.rect_rubber_band.setWidth(1)
        self.start_point = None

        self.da = None

    def canvasPressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.start_point = self.toMapCoordinates(e.pos())
            self.rect_rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        elif e.button() == Qt.RightButton:
            self.reset_selection()

    def canvasMoveEvent(self, e):
        if self.start_point:
            curr = self.toMapCoordinates(e.pos())
            rect = QgsRectangle(self.start_point, curr)
            self.rect_rubber_band.setToGeometry(QgsGeometry.fromRect(rect), None)
            self.rect_rubber_band.show()

    def canvasReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or not self.start_point:
            return

        end_point = self.toMapCoordinates(e.pos())
        layer = self.iface.activeLayer()
        if not layer or layer.geometryType() != QgsWkbTypes.LineGeometry:
            self.rect_rubber_band.hide()
            self.start_point = None
            return

        # マップCRS上の選択矩形（クリックの場合はピクセル換算でバッファを付与）
        map_rect = QgsRectangle(self.start_point, end_point)
        if map_rect.isEmpty():
            pixel_size = self.canvas.mapUnitsPerPixel() * 5
            map_rect.grow(pixel_size)

        # レイヤーCRSへ矩形を変換してからフィルタに使う
        layer_crs = layer.crs()
        map_crs = self.canvas.mapSettings().destinationCrs()
        rect_tr = QgsCoordinateTransform(map_crs, layer_crs, QgsProject.instance())
        try:
            layer_rect = rect_tr.transformBoundingBox(map_rect)
        except Exception:
            layer_rect = map_rect

        # Ctrlなしなら選択リセット
        if not (e.modifiers() & Qt.ControlModifier):
            self.selected_info = {}

        self.da = create_distance_area(layer_crs)
        map_tr = QgsCoordinateTransform(
            layer_crs,
            map_crs,
            QgsProject.instance()
        )

        request = QgsFeatureRequest().setFilterRect(layer_rect)
        for feat in layer.getFeatures(request):
            fid = feat.id()
            geom = feat.geometry()
            if fid in self.selected_info:
                # 既選択ならトグルで解除
                del self.selected_info[fid]
            else:
                length = self.da.measureLength(geom)
                display_geom = QgsGeometry(geom)
                try:
                    display_geom.transform(map_tr)
                except Exception:
                    display_geom = geom
                self.selected_info[fid] = {'length': length, 'geom': display_geom}

        self.rect_rubber_band.hide()
        self.start_point = None
        self.update_highlight_and_label()

    def update_highlight_and_label(self):
        self.highlight.reset(QgsWkbTypes.LineGeometry)
        if self.selected_info:
            total_m = sum(v['length'] for v in self.selected_info.values())
            for v in self.selected_info.values():
                self.highlight.addGeometry(v['geom'], None)
            self.label.setText(f"総延長({len(self.selected_info)}件): {total_m:.2f} m")
            self.label.adjustSize()
            self.label.show()
            self.highlight.show()
        else:
            self.label.hide()
            self.highlight.hide()

    def reset_selection(self):
        self.selected_info = {}
        self.highlight.reset(QgsWkbTypes.LineGeometry)
        try:
            self.label.hide()
        except RuntimeError:
            pass

    def deactivate(self):
        self.reset_selection()
        super().deactivate()


# ─────────────────────────────────────────────
# プラグイン管理クラス
# ─────────────────────────────────────────────
class DistanceMeasurePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = self.iface.mapCanvas()
        self.display_label = None
        self.dist_tool = None
        self.area_tool = None
        self.line_tool = None
        self.plugin_dir = os.path.dirname(__file__)

    def initGui(self):
        self.display_label = get_display_label(self.canvas)
        icon_path  = os.path.join(self.plugin_dir, 'icon.png')
        icon_path2 = os.path.join(self.plugin_dir, 'icon2.png')
        icon_path3 = os.path.join(self.plugin_dir, 'icon3.png')

        self.dist_action = QAction(QIcon(icon_path), "距離計測(m)", self.iface.mainWindow())
        self.dist_action.triggered.connect(self.run_dist_tool)
        self.iface.addToolBarIcon(self.dist_action)

        self.area_action = QAction(QIcon(icon_path2), "面積計測(ha)", self.iface.mainWindow())
        self.area_action.triggered.connect(self.run_area_tool)
        self.iface.addToolBarIcon(self.area_action)

        self.line_action = QAction(QIcon(icon_path3), "ライン延長計測(m)", self.iface.mainWindow())
        self.line_action.triggered.connect(self.run_line_tool)
        self.iface.addToolBarIcon(self.line_action)

    def unload(self):
        self.iface.removeToolBarIcon(self.dist_action)
        self.iface.removeToolBarIcon(self.area_action)
        self.iface.removeToolBarIcon(self.line_action)
        if self.display_label:
            self.display_label.deleteLater()

    def _get_label(self):
        try:
            self.display_label.hide()
        except RuntimeError:
            self.display_label = get_display_label(self.canvas)
        return self.display_label

    def run_dist_tool(self):
        label = self._get_label()
        if self.dist_tool is None:
            self.dist_tool = CustomDistanceTool(self.canvas, self.iface, label)
        else:
            self.dist_tool.label = label
        self.canvas.setMapTool(self.dist_tool)

    def run_area_tool(self):
        label = self._get_label()
        if not self.iface.activeLayer():
            return
        if self.area_tool is None:
            self.area_tool = CustomAreaTool(self.canvas, self.iface, label)
        else:
            self.area_tool.label = label
        self.canvas.setMapTool(self.area_tool)

    def run_line_tool(self):
        label = self._get_label()
        if not self.iface.activeLayer():
            return
        if self.line_tool is None:
            self.line_tool = CustomLineTool(self.canvas, self.iface, label)
        else:
            self.line_tool.label = label
        self.canvas.setMapTool(self.line_tool)
