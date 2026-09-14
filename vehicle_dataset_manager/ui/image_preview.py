"""Modal, aspect-preserving image preview; backdrop click or Escape closes."""
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout


class ImagePreview(QDialog):
    def __init__(self, path: str, parent=None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("車輛圖片放大預覽")
        self.setModal(True)
        self.setStyleSheet("QDialog { background: #202020; } QLabel { color: white; background: transparent; }")
        size = parent.size() if parent is not None else QSize(1000, 700)
        self.resize(size)
        if parent is not None:
            self.move(parent.mapToGlobal(QPoint(0, 0)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 24)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.image, 1, Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("點圖片外的空白區域或按 Esc 關閉")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(hint)
        bounds = QSize(max(1, size.width() - 64), max(1, size.height() - 100))
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        original = reader.size()
        if original.isValid() and (original.width() > bounds.width() or original.height() > bounds.height()):
            reader.setScaledSize(original.scaled(bounds, Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        self.has_image = not image.isNull()
        if self.has_image:
            pixmap = QPixmap.fromImage(image).scaled(bounds, Qt.AspectRatioMode.KeepAspectRatio,
                                                    Qt.TransformationMode.SmoothTransformation)
            self.image.setPixmap(pixmap)
            self.image.setFixedSize(pixmap.size())
        else:
            self.image.setText("無法讀取圖片，請先檢查工作區照片。")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and (
            not self.has_image or not self.image.geometry().contains(event.position().toPoint())
        ):
            self.reject()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()  # Previewing must not confirm a group.
            return
        super().keyPressEvent(event)
