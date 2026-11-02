#!/usr/bin/env python3
"""PySide6 lighting editor for the RAGNOK RK104 (wired). See PROTOCOL.md."""
import json, os, queue, sys

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFontDatabase, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QPlainTextEdit,
                               QFormLayout, QGridLayout, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QPushButton, QSizePolicy, QSlider, QStackedWidget,
                               QTabWidget,
                               QVBoxLayout, QWidget)

import rk104
import rk104_keys as rkeys
import rk104_setup

HERE = os.path.dirname(os.path.abspath(__file__))
# Layout coordinate space (from the vendor XML), cropped to the key area.
BG_X, BG_Y, BG_W, BG_H = 0, 40, 795, 212

# Clean legends for the vendor XML key names. "\n" = shifted over base.
LABELS = {
    "PRINT": "PrtSc", "SCROOL": "ScrLk", "pause": "Pause", "~`": "~\n`",
    "!1": "!\n1", "@2": "@\n2", "#3": "#\n3", "$4": "$\n4", "%5": "%\n5", "^6": "^\n6",
    "&7": "&\n7", "*8": "*\n8", "(9": "(\n9", ")0": ")\n0", "_-": "_\n-", "+=": "+\n=",
    "Backspace": "Backspace", "insert": "Ins", "home": "Home", "page up": "PgUp",
    "Num": "Num", "//": "/", "{[": "{\n[", "}}": "}\n]", "|\\": "|\n\\", "del": "Del",
    "end": "End", "page down": "PgDn", "CapsLock": "Caps", ":;": ":\n;", "\"'": "\"\n'",
    "Shift_L": "Shift", "Shift_R": "Shift", ",<": "<\n,", ".>": ">\n.", "/?": "?\n/",
    "Ctrl_L": "Ctrl", "Ctrl_R": "Ctrl", "Win_L": "Win", "Alt_L": "Alt", "Alt_R": "Alt",
    "APP": "Menu",
}
# Split-board keys that the XML stores as one rect spanning the gap:
# name -> list of (x, w) halves. Both halves share one LED.
SPLIT = {"B": [(208, 26), (254, 26)], "Space": [(165, 70), (250, 89)]}
SWATCHES = ["#ff0000", "#ff8000", "#ffff00", "#00ff00", "#00ffff", "#0000ff",
            "#8000ff", "#ff00ff", "#ffffff", "#000000"]


# ---------------------------------------------------------------- device worker
class DeviceWorker(QThread):
    """Runs device transactions off the GUI thread.

    Normal jobs are queued in order. Live (direct) frames are coalesced: only
    the newest pending frame is sent, so dragging the paint brush never backs up.
    """
    status = Signal(str)
    failed = Signal(str)
    problem = Signal(object)  # DeviceNotFound / DevicePermission -> setup help

    def __init__(self, dev):
        super().__init__()
        self.dev = dev
        self.jobs = queue.Queue()
        self._frame = None

    def submit(self, label, fn, *args):
        self.jobs.put((label, fn, args))

    def submit_frame(self, colors):
        self._frame = colors
        self.jobs.put(None)  # wake up

    def stop(self):
        self.jobs.put(StopIteration)
        self.wait()

    def run(self):
        while True:
            job = self.jobs.get()
            if job is StopIteration:
                self.dev.close()
                return
            if job is None:
                colors, self._frame = self._frame, None
                if colors is None:
                    continue
                job = ("Live preview", self.dev.set_direct, (colors,))
            label, fn, args = job
            try:
                fn(*args)
                self.status.emit(label + ": OK")
            except rk104.DeviceError as e:
                self.failed.emit("%s: %s" % (label, e))
                if isinstance(e, (rk104.DeviceNotFound, rk104.DevicePermission)):
                    self.problem.emit(e)
            except Exception as e:  # keep the worker alive
                self.failed.emit("%s: unexpected error %r" % (label, e))


# ---------------------------------------------------------------- keyboard view
class KeyboardView(QWidget):
    changed = Signal()          # colours edited
    selectionChanged = Signal()
    keyClicked = Signal(object)  # Key, in mapping mode

    def __init__(self, keys):
        super().__init__()
        self.keys = keys
        self.colors = {k.light_index: QColor(0, 0, 0) for k in keys}
        self.selected = set()
        self.paint_mode = True
        self.mapping_mode = False
        self.overlay = {}  # light_index -> short text of remapped action
        self.brush = QColor(255, 0, 0)
        self._drag_start = None
        self._drag_rect = None
        self._painted = set()
        self.setMouseTracking(True)
        self.setMinimumSize(600, 170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self):
        return QSize(BG_W + 40, BG_H + 40)

    # geometry ----------------------------------------------------------------
    def _xform(self):
        m = 12
        s = min((self.width() - 2 * m) / BG_W, (self.height() - 2 * m) / BG_H)
        ox = (self.width() - BG_W * s) / 2 - BG_X * s
        oy = (self.height() - BG_H * s) / 2 - BG_Y * s
        return s, ox, oy

    def key_rects(self, k):
        s, ox, oy = self._xform()
        parts = SPLIT.get(k.name, [(k.x, k.w)])
        return [QRectF(ox + x * s, oy + k.y * s, w * s, k.h * s) for x, w in parts]

    def key_at(self, pos):
        for k in self.keys:
            if any(r.contains(pos) for r in self.key_rects(k)):
                return k
        return None

    # painting ----------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), QColor(24, 24, 28))
        s, _, _ = self._xform()
        font = p.font()
        off = QColor(44, 44, 50)
        for k in self.keys:
            c = QColor(self.colors[k.light_index])
            lit = (c.red() or c.green() or c.blue()) and not self.mapping_mode
            remap = self.overlay.get(k.light_index) if self.mapping_mode else None
            fill = c if lit else off
            luma = 0.299 * fill.red() + 0.587 * fill.green() + 0.114 * fill.blue()
            text = QColor(20, 20, 20) if luma > 140 else QColor(225, 225, 230)
            sel = k.light_index in self.selected
            label = LABELS.get(k.name, k.name)
            two = "\n" in label
            longest = max(len(t) for t in label.split("\n"))
            px = 10 if longest <= 2 else (8 if longest <= 4 else 6.5)
            font.setPixelSize(max(7, int(px * s)))
            p.setFont(font)
            for r in self.key_rects(k):
                r = r.adjusted(1.5, 1.5, -1.5, -1.5)
                p.setBrush(fill)
                p.setPen(QPen(QColor(255, 255, 255), max(2.0, 1.2 * s)) if sel
                         else QPen(fill.lighter(150) if lit else QColor(90, 90, 100), 1))
                p.drawRoundedRect(r, 3.5 * s, 3.5 * s)
                p.setPen(text)
                if remap:
                    p.setPen(QPen(QColor(255, 170, 40), 1.5))
                    p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(r, 3.5 * s, 3.5 * s)
                    f2 = p.font()
                    f2.setPixelSize(max(6, int(5.5 * s)))
                    p.setFont(f2)
                    p.setPen(QColor(150, 150, 160))
                    p.drawText(r.adjusted(2, 1, -2, 0), Qt.AlignTop | Qt.AlignLeft,
                               label.replace("\n", ""))
                    f2.setPixelSize(max(6, int((7 if len(remap) <= 6 else 5.5) * s)))
                    p.setFont(f2)
                    p.setPen(QColor(255, 190, 80))
                    p.drawText(r.adjusted(1, 0, -1, -2), Qt.AlignBottom | Qt.AlignHCenter
                               | Qt.TextWordWrap, remap)
                    p.setFont(font)
                elif two:
                    top, bot = label.split("\n")
                    h = r.height() / 2
                    p.drawText(QRectF(r.x(), r.y() + h * 0.15, r.width(), h), Qt.AlignCenter, top)
                    p.drawText(QRectF(r.x(), r.y() + h * 0.85, r.width(), h), Qt.AlignCenter, bot)
                else:
                    p.drawText(r, Qt.AlignCenter, label)
        if self._drag_rect:
            p.setBrush(QColor(80, 160, 255, 40))
            p.setPen(QPen(QColor(80, 160, 255), 1, Qt.DashLine))
            p.drawRect(self._drag_rect)

    # mouse -------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position()
        k = self.key_at(pos)
        if self.mapping_mode:
            if k:
                self.selected = {k.light_index}
                self.selectionChanged.emit()
                self.keyClicked.emit(k)
                self.update()
            return
        if self.paint_mode:
            self._painted = set()
            self._paint(k)
            return
        additive = e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)
        if k:
            if additive:
                self.selected ^= {k.light_index}
            else:
                self.selected = {k.light_index}
            self.selectionChanged.emit()
            self.update()
        else:
            if not additive:
                self.selected = set()
                self.selectionChanged.emit()
            self._drag_start = pos
            self._drag_rect = QRectF(pos, pos)
            self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        k = self.key_at(pos)
        self.setToolTip(k.name if k else "")
        if not (e.buttons() & Qt.LeftButton):
            return
        if self.mapping_mode:
            return
        if self.paint_mode:
            self._paint(k)
        elif self._drag_start is not None:
            self._drag_rect = QRectF(self._drag_start, pos).normalized()
            hit = {k.light_index for k in self.keys
                   if any(self._drag_rect.intersects(r) for r in self.key_rects(k))}
            self.selected = hit | (self.selected if e.modifiers() & Qt.ControlModifier else set())
            self.selectionChanged.emit()
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag_start = self._drag_rect = None
        self.update()

    def _paint(self, k):
        if k and k.light_index not in self._painted:
            self._painted.add(k.light_index)
            self.colors[k.light_index] = QColor(self.brush)
            self.update()
            self.changed.emit()

    # editing API ---------------------------------------------------------------
    def fill(self, color, only_selected=False):
        targets = self.selected if only_selected else self.colors.keys()
        for li in targets:
            self.colors[li] = QColor(color)
        self.update()
        self.changed.emit()

    def select_all(self):
        self.selected = set(self.colors)
        self.selectionChanged.emit()
        self.update()

    def rgb_map(self, scale=1.0):
        return {li: (int(c.red() * scale), int(c.green() * scale), int(c.blue() * scale))
                for li, c in self.colors.items()}

    def to_json(self):
        names = {k.light_index: k.name for k in self.keys}
        return {str(li): {"key": names[li], "color": c.name()} for li, c in self.colors.items()}

    def from_json(self, data):
        for li, v in data.items():
            li = int(li)
            if li in self.colors:
                self.colors[li] = QColor(v["color"] if isinstance(v, dict) else v)
        self.update()
        self.changed.emit()


# ---------------------------------------------------------------- widgets
class ColorButton(QPushButton):
    colorChanged = Signal(QColor)

    def __init__(self, color):
        super().__init__()
        self.setFixedSize(56, 28)
        self.set_color(QColor(color))
        self.clicked.connect(self._pick)

    def set_color(self, c):
        self.color = QColor(c)
        self.setStyleSheet("background:%s; border:1px solid #888; border-radius:4px" % self.color.name())
        self.colorChanged.emit(self.color)

    def _pick(self):
        c = QColorDialog.getColor(self.color, self, "Pick colour")
        if c.isValid():
            self.set_color(c)


def slider(lo, hi, val):
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(val)
    s.setTickPosition(QSlider.TicksBelow)
    s.setTickInterval(1)
    s.setPageStep(1)
    return s


# ---------------------------------------------------------------- setup help
class SetupDialog(QDialog):
    """Explains why the keyboard can't be reached and shows the fix for this distro."""

    def __init__(self, parent, dev):
        super().__init__(parent)
        self.dev = dev
        self.setWindowTitle("RK104 setup")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.RichText)
        self.cmd = QPlainTextEdit()
        self.cmd.setReadOnly(True)
        self.cmd.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.cmd.setMaximumHeight(110)
        self.cmd.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.text)
        lay.addWidget(self.cmd)
        bb = QDialogButtonBox()
        self.copy_btn = bb.addButton("Copy command", QDialogButtonBox.ActionRole)
        retry = bb.addButton("Retry", QDialogButtonBox.AcceptRole)
        bb.addButton("Close", QDialogButtonBox.RejectRole)
        self.copy_btn.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(self.cmd.toPlainText()))
        retry.clicked.connect(self.retry)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def show_error(self, err):
        a = rk104_setup.diagnose(err)
        self.setWindowTitle(a.title)
        self.text.setText("<h3>%s</h3>%s" % (a.title, a.text))
        self.cmd.setPlainText(a.command)
        self.cmd.setVisible(bool(a.command))
        self.copy_btn.setVisible(bool(a.command))
        self.show()
        self.raise_()

    def retry(self):
        try:
            with self.dev.lock:
                self.dev.open()
        except rk104.DeviceError as e:
            self.show_error(e)
            return
        self.parent().statusBar().showMessage("Keyboard connected", 4000)
        self.accept()


# ---------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self, dry_run=False, detect_delay=0):
        super().__init__()
        self.setWindowTitle("RK104 Lighting")
        self.worker = DeviceWorker(rk104.RK104(dry_run))
        self.worker.status.connect(lambda m: self.statusBar().showMessage(m, 4000))
        self.worker.failed.connect(self._error)
        self.worker.problem.connect(self._setup_help)
        self.worker.start()
        self.setup_dlg = SetupDialog(self, self.worker.dev)
        if not dry_run:
            self._countdown = detect_delay
            QTimer.singleShot(0, self._tick_detect)

        self.kb = KeyboardView(rk104.load_layout())
        self.kb.changed.connect(self._on_colors_changed)
        self.kb.selectionChanged.connect(self._on_selection)

        tabs = QTabWidget()
        tabs.addTab(self._effect_tab(), "Effects")
        tabs.addTab(self._perkey_tab(), "Per-key")
        tabs.addTab(self._mapping_tab(), "Key mapping")
        tabs.setMaximumHeight(260)
        tabs.currentChanged.connect(self._on_tab)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addWidget(self.kb, 1)
        lay.addWidget(tabs)
        self.setCentralWidget(central)

        m = self.menuBar().addMenu("&File")
        for text, fn in (("&Open scheme…", self._load), ("&Save scheme…", self._save),
                         ("&Quit", self.close)):
            a = QAction(text, self)
            a.triggered.connect(fn)
            m.addAction(a)
        self.statusBar().showMessage("Dry run: packets go to stdout" if dry_run else "Ready")

    # --- tabs ---------------------------------------------------------------
    def _effect_tab(self):
        w = QWidget()
        f = QFormLayout(w)
        self.mode = QComboBox()
        for i, n in enumerate(rk104.MODES):
            self.mode.addItem("%d  %s" % (i, n), i)
        self.mode.setCurrentIndex(1)
        self.fx_color = ColorButton("#ff0000")
        self.fx_colorful = QCheckBox("Multicolour / rainbow")
        self.fx_bright = slider(0, rk104.MAX_BRIGHTNESS, rk104.MAX_BRIGHTNESS)
        self.fx_speed = slider(0, rk104.MAX_SPEED, 3)
        self.fx_dir = QComboBox()
        self.fx_dir.addItems(["0", "1", "2", "3"])
        apply = QPushButton("Apply effect")
        apply.clicked.connect(self._apply_effect)
        row = QHBoxLayout()
        row.addWidget(self.fx_color)
        row.addWidget(self.fx_colorful)
        row.addStretch()
        f.addRow("Mode", self.mode)
        f.addRow("Colour", row)
        f.addRow("Brightness", self.fx_bright)
        f.addRow("Speed", self.fx_speed)
        f.addRow("Direction", self.fx_dir)
        f.addRow(apply)
        return w

    def _perkey_tab(self):
        w = QWidget()
        g = QGridLayout(w)

        self.brush = ColorButton("#ff0000")
        self.brush.colorChanged.connect(lambda c: setattr(self.kb, "brush", QColor(c)))
        sw = QHBoxLayout()
        sw.addWidget(QLabel("Brush"))
        sw.addWidget(self.brush)
        for hexc in SWATCHES:
            b = QPushButton()
            b.setFixedSize(22, 22)
            b.setStyleSheet("background:%s; border:1px solid #666; border-radius:3px" % hexc)
            b.clicked.connect(lambda _=False, c=hexc: self.brush.set_color(QColor(c)))
            sw.addWidget(b)
        sw.addStretch()
        g.addLayout(sw, 0, 0, 1, 4)

        self.paint_cb = QCheckBox("Paint mode (click/drag to paint; off = select)")
        self.paint_cb.setChecked(True)
        self.paint_cb.toggled.connect(lambda on: setattr(self.kb, "paint_mode", on))
        g.addWidget(self.paint_cb, 1, 0, 1, 4)

        self.sel_label = QLabel("0 selected")
        btns = [("Fill selected", lambda: self.kb.fill(self.brush.color, True)),
                ("Select all", self.kb.select_all),
                ("Fill all", lambda: self.kb.fill(self.brush.color)),
                ("Clear all", lambda: self.kb.fill(QColor(0, 0, 0)))]
        for i, (t, fn) in enumerate(btns):
            b = QPushButton(t)
            b.clicked.connect(fn)
            g.addWidget(b, 2, i)
        g.addWidget(self.sel_label, 3, 0)

        g.addWidget(QLabel("Brightness"), 4, 0)
        self.pk_bright = slider(0, rk104.MAX_BRIGHTNESS, rk104.MAX_BRIGHTNESS)
        self.pk_bright.valueChanged.connect(self._on_colors_changed)
        g.addWidget(self.pk_bright, 4, 1, 1, 3)

        self.live = QCheckBox("Live preview (real-time, not saved)")
        self.live.toggled.connect(self._on_colors_changed)
        save = QPushButton("Save to keyboard")
        save.setStyleSheet("font-weight:bold")
        save.clicked.connect(self._save_custom)
        g.addWidget(self.live, 5, 0, 1, 2)
        g.addWidget(save, 5, 2, 1, 2)
        return w

    # --- actions ------------------------------------------------------------
    def _apply_effect(self):
        c = self.fx_color.color
        args = (self.mode.currentData(), (c.red(), c.green(), c.blue()),
                self.fx_colorful.isChecked(), self.fx_bright.value(),
                self.fx_speed.value(), self.fx_dir.currentIndex())
        self.live.setChecked(False)
        self.worker.submit("Effect %s" % rk104.MODES[args[0]], self.worker.dev.set_effect, *args)

    def _save_custom(self):
        self.worker.submit("Saved per-key lighting", self.worker.dev.set_custom,
                           self.kb.rgb_map(), self.pk_bright.value())

    def _on_colors_changed(self, *_):
        if self.live.isChecked():
            self.worker.submit_frame(self.kb.rgb_map(self.pk_bright.value() / rk104.MAX_BRIGHTNESS))

    # --- key mapping ---------------------------------------------------------
    def _mapping_tab(self):
        self.keymap = self._load_keymap()  # {layer: {key_index: action}}
        self.map_key = None
        w = QWidget()
        g = QGridLayout(w)

        self.layer = QComboBox()
        self.layer.addItems(["Base layer", "Fn layer (while holding Fn)"])
        self.layer.currentIndexChanged.connect(self._refresh_overlay)
        self.map_sel = QLabel("Click a key on the keyboard")
        self.map_sel.setStyleSheet("font-weight:bold")
        g.addWidget(self.layer, 0, 0)
        g.addWidget(self.map_sel, 0, 1, 1, 3)

        self.act_type = QComboBox()
        self.act_type.addItems(["Default", "Key", "Mouse", "Media", "Disabled"])
        self.act_stack = QStackedWidget()
        self.act_stack.addWidget(QLabel("Key does what it normally does"))
        keyw = QWidget()
        kl = QHBoxLayout(keyw)
        kl.setContentsMargins(0, 0, 0, 0)
        self.mod_cbs = []
        for bit, name in rkeys.MODS:
            cb = QCheckBox(name)
            cb.setProperty("bit", bit)
            self.mod_cbs.append(cb)
            kl.addWidget(cb)
        self.hid_combo = QComboBox()
        for code, name in sorted(rkeys.HID.items()):
            self.hid_combo.addItem("%s  (0x%02X)" % (name, code), code)
        kl.addWidget(self.hid_combo, 1)
        self.act_stack.addWidget(keyw)
        self.mouse_combo = QComboBox()
        self.mouse_combo.addItems(list(rkeys.MOUSE))
        self.act_stack.addWidget(self.mouse_combo)
        self.media_combo = QComboBox()
        for name, code in rkeys.CONSUMER.items():
            self.media_combo.addItem(name, code)
        self.act_stack.addWidget(self.media_combo)
        self.act_stack.addWidget(QLabel("Key sends nothing (untested)"))
        self.act_type.currentIndexChanged.connect(self.act_stack.setCurrentIndex)
        g.addWidget(QLabel("Action"), 1, 0)
        g.addWidget(self.act_type, 1, 1)
        g.addWidget(self.act_stack, 1, 2, 1, 2)

        assign = QPushButton("Assign to key")
        assign.clicked.connect(self._assign)
        write = QPushButton("Write mapping to keyboard")
        write.setStyleSheet("font-weight:bold")
        write.clicked.connect(self._write_keymap)
        reset = QPushButton("Reset all to default…")
        reset.clicked.connect(self._reset_keymap)
        g.addWidget(assign, 2, 1)
        g.addWidget(write, 2, 2)
        g.addWidget(reset, 2, 3)
        self.map_info = QLabel()
        self.map_info.setStyleSheet("color:#aaa")
        g.addWidget(self.map_info, 3, 0, 1, 4)
        g.setRowStretch(4, 1)

        self.kb.keyClicked.connect(self._on_key_clicked)
        self._refresh_overlay()
        return w

    def _on_tab(self, idx):
        self.kb.mapping_mode = idx == 2
        self.kb.selected = set()
        self.map_key = None
        self.map_sel.setText("Click a key on the keyboard")
        self._refresh_overlay()

    def _on_key_clicked(self, k):
        self.map_key = k
        if k.name == "Fn":
            self.map_sel.setText("Fn can't be remapped (you would lose the Fn layer)")
            self.map_key = None
            return
        act = self.keymap[self.layer.currentIndex()].get(k.key_index)
        self.map_sel.setText("%s → %s" % (LABELS.get(k.name, k.name).replace("\n", " "),
                                          rkeys.describe(act) if act else "Default"))
        # load current action into the editor
        if not act:
            self.act_type.setCurrentIndex(0)
        elif act["type"] == "key":
            self.act_type.setCurrentIndex(1)
            for cb in self.mod_cbs:
                cb.setChecked(bool(act.get("mod", 0) & cb.property("bit")))
            self.hid_combo.setCurrentIndex(self.hid_combo.findData(act["code"]))
        elif act["type"] == "mouse":
            self.act_type.setCurrentIndex(2)
            self.mouse_combo.setCurrentText(act["name"])
        elif act["type"] == "consumer":
            self.act_type.setCurrentIndex(3)
            self.media_combo.setCurrentIndex(self.media_combo.findData(act["code"]))
        else:
            self.act_type.setCurrentIndex(4)

    def _assign(self):
        k = self.map_key
        if not k:
            return
        t = self.act_type.currentIndex()
        layer = self.keymap[self.layer.currentIndex()]
        if t == 0:
            layer.pop(k.key_index, None)
        else:
            if t == 1:
                act = {"type": "key", "code": self.hid_combo.currentData(),
                       "mod": sum(cb.property("bit") for cb in self.mod_cbs if cb.isChecked())}
            elif t == 2:
                act = {"type": "mouse", "name": self.mouse_combo.currentText()}
            elif t == 3:
                act = {"type": "consumer", "code": self.media_combo.currentData()}
            else:
                act = {"type": "disabled"}
            layer[k.key_index] = act
        self._save_keymap()
        self._on_key_clicked(k)
        self._refresh_overlay()

    def _refresh_overlay(self, *_):
        layer = self.keymap[self.layer.currentIndex()]
        by_ki = {k.key_index: k.light_index for k in self.kb.keys}
        self.kb.overlay = {by_ki[ki]: rkeys.describe(a, short=True)
                           for ki, a in layer.items() if ki in by_ki}
        n0, n1 = len(self.keymap[0]), len(self.keymap[1])
        self.map_info.setText("%d remapped on base layer, %d on Fn layer. Changes are not sent "
                              "until you press “Write mapping to keyboard”." % (n0, n1))
        self.kb.update()

    def _write_keymap(self):
        for layer in (0, 1):
            entries = {ki: rkeys.encode(a) for ki, a in self.keymap[layer].items()}
            self.worker.submit("Key mapping (%s layer)" % ("Fn" if layer else "base"),
                               self.worker.dev.set_keymap, layer, entries)

    def _reset_keymap(self):
        if QMessageBox.question(self, "Reset key mapping",
                                "Restore every key on both layers to its default?") \
                != QMessageBox.Yes:
            return
        self.keymap = {0: {}, 1: {}}
        self._save_keymap()
        self._refresh_overlay()
        self.worker.submit("Key mapping reset", self.worker.dev.reset_keymap)

    # The keyboard can't report its mapping back, so the GUI keeps its own copy.
    KEYMAP_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
                               "rk104", "keymap.json")

    def _load_keymap(self):
        try:
            with open(self.KEYMAP_FILE) as fh:
                raw = json.load(fh)
            return {int(l): {int(k): v for k, v in m.items()} for l, m in raw.items()}
        except (OSError, ValueError):
            return {0: {}, 1: {}}

    def _save_keymap(self):
        os.makedirs(os.path.dirname(self.KEYMAP_FILE), exist_ok=True)
        with open(self.KEYMAP_FILE, "w") as fh:
            json.dump(self.keymap, fh, indent=1)

    def _on_selection(self):
        self.sel_label.setText("%d selected" % len(self.kb.selected))

    def _tick_detect(self):
        # Optional startup delay (--detect-delay N) so you can unplug the
        # keyboard to see the setup help.
        if self._countdown > 0:
            self.statusBar().showMessage("Checking for keyboard in %d s…" % self._countdown)
            self._countdown -= 1
            QTimer.singleShot(1000, self._tick_detect)
        else:
            self._check_device()

    def _check_device(self):
        try:
            with self.worker.dev.lock:
                self.worker.dev.open()
            self.statusBar().showMessage("Keyboard connected", 4000)
        except rk104.DeviceError as e:
            self._setup_help(e)

    def _setup_help(self, err):
        self.setup_dlg.show_error(err)

    def _error(self, msg):
        self.statusBar().showMessage(msg, 8000)
        self.live.setChecked(False)

    def _save(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Save scheme", "", "Scheme (*.json)")
        if fn:
            with open(fn if fn.endswith(".json") else fn + ".json", "w") as fh:
                json.dump(self.kb.to_json(), fh, indent=1)

    def _load(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open scheme", "", "Scheme (*.json)")
        if fn:
            try:
                with open(fn) as fh:
                    self.kb.from_json(json.load(fh))
            except (OSError, ValueError, KeyError) as e:
                QMessageBox.warning(self, "Open scheme", str(e))

    def closeEvent(self, e):
        self.worker.stop()
        super().closeEvent(e)


def main():
    # Harmless Qt Wayland text-input focus warning; silence it.
    rules = os.environ.get("QT_LOGGING_RULES", "")
    os.environ["QT_LOGGING_RULES"] = ";".join(filter(None, [rules, "qt.qpa.wayland.textinput=false"]))
    app = QApplication(sys.argv)
    import argparse
    ap = argparse.ArgumentParser(description="RK104 lighting / key mapping GUI")
    ap.add_argument("--dry-run", action="store_true", help="print packets instead of sending")
    ap.add_argument("--detect-delay", type=int, default=0, metavar="SECONDS",
                    help="wait before checking for the keyboard (to test setup help)")
    args, _ = ap.parse_known_args()
    w = MainWindow(dry_run=args.dry_run, detect_delay=args.detect_delay)
    w.resize(1000, 640)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
