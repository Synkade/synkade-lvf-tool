"""
Single dark theme for the whole application - per spec there is no light
theme and no theme switcher, so this module just has one function that
applies the look to a QApplication and never anything else.
"""
from PySide6.QtGui import QColor, QPalette
from PySide6.QtCore import Qt


BG = "#1e1f24"
BG_ALT = "#26272e"
SURFACE = "#2d2f38"
BORDER = "#3d3f4a"
TEXT = "#e8e8ec"
TEXT_MUTED = "#9a9ca8"
ACCENT = "#5b8cff"
ACCENT_HOVER = "#7aa0ff"
DANGER = "#ff6b6b"
WARNING = "#e0b341"
SUCCESS = "#5fd48a"

STYLESHEET = f"""
* {{
    font-family: "Segoe UI", "Inter", "Helvetica Neue", sans-serif;
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QWidget {{
    background-color: {BG};
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px;
    background-color: {BG_ALT};
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {TEXT_MUTED};
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled,
QCheckBox:disabled, QRadioButton:disabled {{
    color: {TEXT_MUTED};
}}
QPushButton {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 7px 16px;
}}
QPushButton:hover {{
    border: 1px solid {ACCENT};
}}
QPushButton:pressed {{
    background-color: {BORDER};
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
}}
QPushButton#primary {{
    background-color: {ACCENT};
    color: #ffffff;
    font-weight: 600;
    border: none;
}}
QPushButton#primary:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton#primary:disabled {{
    background-color: {BORDER};
    color: {TEXT_MUTED};
}}
QCheckBox, QRadioButton {{
    spacing: 8px;
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    background-color: {BG_ALT};
}}
QTabBar::tab {{
    background-color: {BG};
    padding: 8px 18px;
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
QTabBar::tab:selected {{
    background-color: {BG_ALT};
    color: {ACCENT};
}}
QProgressBar {{
    border: 1px solid {BORDER};
    border-radius: 4px;
    background-color: {SURFACE};
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 3px;
}}
QLabel[role="hint"] {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QLabel[role="warning"] {{
    color: {WARNING};
    font-size: 12px;
}}
QLabel[role="error"] {{
    color: {DANGER};
    font-weight: 600;
}}
QLabel[role="success"] {{
    color: {SUCCESS};
}}
QScrollBar:vertical {{
    background: {BG};
    width: 10px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 24px;
}}
"""


def apply_dark_theme(app) -> None:
    """The only theme this app has. There is intentionally no light-mode
    counterpart or toggle anywhere in the UI."""
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(SURFACE))
    palette.setColor(QPalette.AlternateBase, QColor(BG_ALT))
    palette.setColor(QPalette.ToolTipBase, QColor(SURFACE))
    palette.setColor(QPalette.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.Button, QColor(SURFACE))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.BrightText, QColor(DANGER))
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(TEXT_MUTED))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(TEXT_MUTED))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
