"""Frost console: light frosted glass on "console paper" (a faint dot grid with soft mint and blue
light), ink-dark text, a mint accent, monospace for small machine-ish labels, a colour per channel.
Segoe Fluent Icons for icons."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient

# ----------------------------------------------------------------------------
# Frost palette
#   Light console paper, mint accent (Ken), violet (thinking), a colour per channel.
# ----------------------------------------------------------------------------

INK = "#0e1a24"
TEXT = INK
MUTED = "#6b7c8a"
FAINT = "#9aa9b5"
LINE = "#dbe5ec"
PAPER = "#edf3f6"
SURFACE = "#ffffff"
SURFACE_2 = "#f6f9fb"
SURFACE_3 = "#eef3f6"
BORDER = LINE
BORDER_STRONG = "#0fa37f"
ACCENT = "#0fa37f"  # mint: Ken
ACCENT_SOFT = "#e7f8f1"
ACCENT_2 = "#2f7fe0"  # blue: listening, calendar
VIOLET = "#7a5cf0"  # thinking
SUCCESS = "#10a35a"
DANGER = "#e0473f"
WARN = "#d98a0b"
CHANNEL = {"whatsapp": "#1faa59", "gmail": "#e0623f", "calendar": "#2f7fe0"}
BG = QColor(255, 255, 255, 228)  # the glass
BG_HEX = "#ffffff"

# ----------------------------------------------------------------------------
# Fonts and icons
# ----------------------------------------------------------------------------

FONT_FAMILY = "Segoe UI Variable Text"
MONO_FAMILY = "Cascadia Mono"
ICON_FAMILY = "Segoe Fluent Icons"
# Segoe Fluent Icons (Windows 11; Segoe MDL2 Assets has the same code points on Windows 10)
ICONS = {"sun": "", "bell": "", "refresh": "", "settings": "", "close": "",
         "power": "", "mic": "", "send": "", "stop": "", "speaker": "",
         "mute": "", "calendar": "", "link": "", "check": "", "edit": "",
         "chat": "", "mail": "", "sparkle": "", "play": "", "pause": "",
         "reset": "", "search": "", "voice": "", "person": "", "ear": "",
         "accounts": "", "memory": "", "info": "", "general": ""}


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def rgba(hex_color: str, alpha: float) -> str:
    """CSS rgba() for stylesheets. (Qt reads 8-digit hex as #AARRGGBB, not #RRGGBBAA.)"""
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def font(size: float = 10.0, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setStyleHint(QFont.StyleHint.SansSerif)
    f.setFamilies([FONT_FAMILY, "Segoe UI", "Arial"])
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


def mono(size: float = 8.5, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(MONO_FAMILY)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setFamilies([MONO_FAMILY, "Cascadia Code", "Consolas"])
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


def icon_font(size: float = 11.0) -> QFont:
    f = QFont(ICON_FAMILY)
    f.setFamilies([ICON_FAMILY, "Segoe MDL2 Assets"])
    f.setPointSizeF(size)
    return f


def accent_gradient(x1: float, y1: float, x2: float, y2: float) -> QLinearGradient:
    g = QLinearGradient(QPointF(x1, y1), QPointF(x2, y2))
    g.setColorAt(0, QColor(ACCENT))
    g.setColorAt(1, QColor("#14b88f"))
    return g


# ----------------------------------------------------------------------------
# Painting
#   The dot-grid paper, Ken's mark and the app icon are drawn in code: no image files.
# ----------------------------------------------------------------------------

def paint_paper(p: QPainter, rect: QRectF, radius: float) -> None:
    """Console paper: pale blue-grey with soft mint and blue light and a faint dot grid."""
    from PySide6.QtGui import QPainterPath

    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.save()
    p.setClipPath(path)
    p.fillRect(rect, QColor(PAPER))
    for cx, cy, r, col in ((0.08, 0.0, 0.55, (211, 245, 232, 255)), (0.96, 0.2, 0.5, (223, 230, 255, 255)),
                           (0.4, 1.05, 0.6, (226, 240, 250, 255))):
        g = QRadialGradient(QPointF(rect.left() + rect.width() * cx, rect.top() + rect.height() * cy),
                            max(rect.width(), rect.height()) * r)
        g.setColorAt(0, QColor(*col))
        g.setColorAt(1, QColor(col[0], col[1], col[2], 0))
        p.fillRect(rect, QBrush(g))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(195, 210, 220, 150))
    step = 14
    y = rect.top() + step / 2
    while y < rect.bottom():
        x = rect.left() + step / 2
        while x < rect.right():
            p.drawEllipse(QPointF(x, y), 0.8, 0.8)
            x += step
        y += step
    p.restore()


def paint_glyph(p: QPainter, size: float, color: QColor | None = None) -> None:
    """Ken's mark: a small linked constellation (7 nodes). Drawn in code, no image files."""
    s = size / 64
    nodes = [(32, 10), (52, 22), (50, 44), (32, 54), (13, 43), (12, 21), (32, 32)]
    links = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 0), (6, 0), (6, 2), (6, 4), (1, 6)]
    c = QColor(color or ACCENT)
    line = QColor(c)
    line.setAlphaF(0.5)
    p.setPen(QPen(line, max(1.0, 2.2 * s)))
    for a, b in links:
        p.drawLine(QPointF(nodes[a][0] * s, nodes[a][1] * s), QPointF(nodes[b][0] * s, nodes[b][1] * s))
    p.setPen(Qt.PenStyle.NoPen)
    for i, (x, y) in enumerate(nodes):
        p.setBrush(QColor(INK) if i == 6 else c)
        r = (6.5 if i == 6 else 4.6) * s
        p.drawEllipse(QPointF(x * s, y * s), r, r)


def app_icon(size: int = 64) -> QIcon:
    """Ken's mark on a white round tile."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(ACCENT), max(1, size / 32)))
    p.setBrush(QColor("white"))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.translate(size * 0.12, size * 0.12)
    paint_glyph(p, size * 0.76)
    p.end()
    return QIcon(pm)


# ----------------------------------------------------------------------------
# Switch images
# ----------------------------------------------------------------------------

def switch_images(folder) -> tuple[str, str]:
    """Toggle-switch images (with a knob) for QCheckBox indicators; QSS can't draw the knob."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, on in (("switch3_off.png", False), ("switch3_on.png", True)):
        scale = 2  # crisp on high-DPI screens
        pm = QPixmap(34 * scale, 18 * scale)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ACCENT) if on else QColor("#cdd8df"))
        p.drawRoundedRect(0, 0, 34 * scale, 18 * scale, 9 * scale, 9 * scale)
        p.setBrush(QColor("white"))
        knob_x = (34 - 16) * scale if on else 2 * scale
        p.drawEllipse(knob_x, 2 * scale, 14 * scale, 14 * scale)
        p.end()
        path = folder / name
        pm.save(str(path))
        paths.append(path.as_posix())
    return paths[0], paths[1]


def switch_stylesheet(folder) -> str:
    off, on = switch_images(folder)
    return (f"QCheckBox::indicator {{ width: 34px; height: 18px; background: transparent; image: url({off}); }}"
            f"QCheckBox::indicator:checked {{ image: url({on}); }}")


# ----------------------------------------------------------------------------
# The app-wide stylesheet
# ----------------------------------------------------------------------------

_MONO = f'"{MONO_FAMILY}", "Consolas"'
_ICON = f'"{ICON_FAMILY}", "Segoe MDL2 Assets"'

STYLESHEET = f"""
* {{ color: {INK}; font-family: "{FONT_FAMILY}", "Segoe UI"; font-size: 10pt; }}
QToolTip {{ background: {INK}; color: #e9fff6; border: none; padding: 5px 8px; border-radius: 6px; font-family: {_MONO}; font-size: 8.5pt; }}
QDialog {{ background: {PAPER}; }}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 4px 1px; }}
QScrollBar::handle:vertical {{ background: rgba(14,26,36,0.16); border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(14,26,36,0.32); }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; height: 0; }}

QPushButton {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 8px; padding: 6px 13px; color: {INK};
}}
QPushButton:hover {{ border: 1px solid {ACCENT}; color: {ACCENT}; }}
QPushButton:pressed {{ background: {ACCENT_SOFT}; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE_2}; border: 1px solid {LINE}; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: #0c8f6f; border: 1px solid #0c8f6f; color: white; }}
QPushButton#primary:disabled {{ background: #bfe5d8; border: 1px solid #bfe5d8; color: white; }}
QPushButton#ghost {{ background: {SURFACE}; border: 1px solid {LINE}; color: {MUTED}; }}
QPushButton#ghost:hover {{ color: {INK}; border: 1px solid {FAINT}; }}
QPushButton#danger {{ background: transparent; border: 1px solid transparent; color: {MUTED}; }}
QPushButton#danger:hover {{ background: rgba(224,71,63,0.08); color: {DANGER}; border: 1px solid rgba(224,71,63,0.3); }}
QPushButton#icon {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 8px; padding: 0; color: {MUTED};
    font-family: {_ICON}; font-size: 10pt;
}}
QPushButton#icon:hover {{ color: {ACCENT}; border: 1px solid {ACCENT}; }}
QPushButton#icon:checked {{ color: {ACCENT}; background: {ACCENT_SOFT}; border: 1px solid {ACCENT}; }}
QPushButton#mic {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 9px; padding: 0; color: {MUTED};
    font-family: {_ICON}; font-size: 10.5pt;
}}
QPushButton#mic:hover {{ color: {ACCENT}; border: 1px solid {ACCENT}; }}
QPushButton#mic[recording="true"] {{ background: rgba(224,71,63,0.10); border: 1px solid {DANGER}; color: {DANGER}; }}
QPushButton#send {{
    background: {ACCENT}; border: none; border-radius: 9px; padding: 0; color: white;
    font-family: {_ICON}; font-size: 10.5pt;
}}
QPushButton#send:hover {{ background: #0c8f6f; }}
QPushButton#send[stopping="true"] {{ background: {DANGER}; }}

QPlainTextEdit, QTextEdit, QLineEdit {{ background: transparent; border: none; selection-background-color: rgba(15,163,127,0.30); }}
QFrame#inputBar {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame#inputBar[focused="true"] {{ border: 1px solid {ACCENT}; }}
QLineEdit#search {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 8px; padding: 4px 8px; font-family: {_MONO}; font-size: 8.5pt; }}
QLineEdit#search:focus {{ border: 1px solid {ACCENT}; }}

QPushButton#quickChat {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 7px; padding: 2px 8px;
    color: {MUTED}; font-family: {_MONO}; font-size: 8.5pt;
}}
QPushButton#quickChat:hover {{ border: 1px solid {ACCENT}; color: {ACCENT}; }}
QPushButton#suggestion {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; padding: 8px 12px; color: {INK}; text-align: left;
}}
QPushButton#suggestion:hover {{ border: 1px solid {ACCENT}; color: {ACCENT}; }}
QPushButton#targetTag {{
    background: rgba(31,170,89,0.10); border: 1px solid rgba(31,170,89,0.5); border-radius: 7px;
    padding: 2px 8px; color: #138a47; font-family: {_MONO}; font-size: 8.5pt;
}}
QPushButton#chipButton {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 7px; padding: 4px 10px; color: {INK}; font-size: 9pt;
}}
QPushButton#chipButton:checked {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT}; color: {ACCENT}; }}
QPushButton#navButton {{
    background: transparent; border: none; border-radius: 8px; padding: 7px 10px; color: {MUTED}; text-align: left;
}}
QPushButton#navButton:hover {{ color: {INK}; background: rgba(14,26,36,0.05); }}
QPushButton#navButton:checked {{ background: {INK}; color: #e9fff6; }}
QPushButton#timerControl {{
    background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: 6px; padding: 0; color: {MUTED};
    font-family: {_ICON}; font-size: 8pt;
}}
QTextEdit#draftText {{ background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: 8px; padding: 5px; }}
QTextEdit#draftText:focus {{ border: 1px solid {ACCENT}; }}
QFrame#draftCard, QFrame#tile {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#draftCard:hover {{ border: 1px solid {FAINT}; }}

QLabel#bubbleUser {{
    background: {INK}; color: #e9fff6; border-radius: 13px; border-bottom-right-radius: 3px; padding: 8px 12px;
}}
QLabel#bubbleAssistant {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 13px; border-bottom-left-radius: 3px; padding: 8px 12px;
}}
QLabel#notice {{ color: {FAINT}; font-family: {_MONO}; font-size: 8.5pt; padding: 2px 8px; }}
QLabel#errorNotice {{ color: {DANGER}; font-family: {_MONO}; font-size: 8.5pt; padding: 2px 8px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#faint {{ color: {FAINT}; font-size: 8.5pt; }}
QLabel#meta {{ color: {FAINT}; font-family: {_MONO}; font-size: 8pt; }}
QLabel#prompt {{ color: {ACCENT}; font-family: {_MONO}; font-size: 8pt; }}
QLabel#statusLine {{ color: {MUTED}; font-family: {_MONO}; font-size: 8pt; }}
QLabel#title {{ font-size: 12.5pt; font-weight: 600; }}
QLabel#subtitle {{ color: {ACCENT}; font-family: {_MONO}; font-size: 8pt; }}
QLabel#sectionLabel {{ color: {ACCENT}; font-family: {_MONO}; font-size: 8pt; }}
QLabel#tileLabel {{ color: {MUTED}; font-size: 8.5pt; }}
QLabel#crumb {{ font-family: {_MONO}; font-size: 7.8pt; letter-spacing: 0.4px; }}
QLabel#kbd {{
    color: {MUTED}; background: {SURFACE}; border: 1px solid {LINE}; border-bottom: 2px solid {LINE};
    border-radius: 4px; padding: 0 4px; font-family: {_MONO}; font-size: 7.5pt;
}}

QComboBox, QDoubleSpinBox, QTimeEdit {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 8px; padding: 4px 9px; min-height: 20px;
}}
QComboBox:hover, QTimeEdit:hover {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {LINE}; selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT}; outline: 0; }}
QCheckBox {{ spacing: 8px; }}
QSlider::groove:horizontal {{ height: 4px; background: #e1ebf0; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; border: 2px solid {ACCENT}; width: 10px; height: 10px; margin: -5px 0; border-radius: 7px; }}

QMenu {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 6px 16px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 8px; }}
"""
