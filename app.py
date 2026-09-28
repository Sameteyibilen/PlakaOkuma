#!/usr/bin/env python3
"""Bursa Çimento — Akıllı Kantar masaüstü penceresi."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("TEMP", str(ROOT / ".tmp"))
os.environ.setdefault("TMP", str(ROOT / ".tmp"))
os.environ.setdefault("EASYOCR_MODULE_PATH", str(ROOT / ".EasyOCR"))
os.environ.setdefault("HF_HOME", str(ROOT / ".hf"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PLAKA_SURE", "0")
(ROOT / ".tmp").mkdir(parents=True, exist_ok=True)

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox
from tkinter import ttk
import operator_ui

OUT_DIR = ROOT / "live_ocr"
CAMS = (
    ("153", "Giriş", "Plaka"),
    ("165", "Çıkış", "Plaka"),
)

BG = operator_ui.BG
HEADER = operator_ui.HEADER
PANEL = operator_ui.PANEL
PANEL2 = "#162D40"
EDGE = operator_ui.EDGE
GOLD = "#C9A227"
CYAN = operator_ui.CYAN
TEXT = "#F1F5F9"
MUTED = operator_ui.MUTED
GREEN = "#34D399"
ORANGE = "#F59E0B"
RED = "#F87171"
YELLOW = "#FBBF24"
ACCENT = "#60A5FA"
PLATE_BG = "#F4F1E8"
PLATE_FG = "#141414"
TR_BLUE = "#003399"
FOOT = "#0A1018"

APP_NAME = "Akıllı Kantar"
SITE_NAME = "Bursa Çimento"
VERSION = "1.3"

STATUS_TR = {
    "WAITING_ENTRY_WEIGHT": "AĞIRLIK BEKLENİYOR",
    "WAITING_DELIVERY_NOTE": "İRSALİYE BEKLENİYOR",
    "IN_FACILITY": "TESİSTE",
    "WAITING_EXIT": "ÇIKIŞ BEKLENİYOR",
    "WAITING_EXIT_WEIGHT": "BOŞ TARTIM BEKLENİYOR",
    "MATCHED": "EŞLEŞTİ",
    "COMPLETED": "TAMAMLANDI",
    "MANUAL_REVIEW": "MÜDAHALE GEREKLİ",
    "ERROR": "HATA",
}
REASON_TR = {
    "OPEN_VISIT_NOT_FOUND": "Bu aracın açık giriş kaydı bulunamadı.",
    "MULTIPLE_OPEN_VISITS": "Bu plakaya ait birden fazla açık kayıt bulundu.",
    "INVALID_NET_WEIGHT": "Dolu ve boş ağırlık değerleri kontrol edilmeli.",
    "MISSING_FULL_WEIGHT": "Dolu tartım alınamadı.",
    "PLATE_NOT_DETECTED": "Plaka tespit edilemedi.",
    "AMBIGUOUS_PLATE": "Birden fazla plaka adayı var; otomatik seçim yapılmadı.",
    "LOW_CONFIDENCE": "Plaka okuması yeterince güçlü değil.",
    "RECOVERY_OCCUPIED": "Uygulama yeniden başlatıldı, kantar üzerinde araç var.",
    "STABILIZATION_TIMEOUT": "Ağırlık zamanında oturmadı.",
}
WAIT_TR = {
    "WAITING_PLATE": "Plaka bekleniyor",
    "WAITING_WEIGHT": "Ağırlık bekleniyor",
    "WAITING_IRSALIYE": "İrsaliye bekleniyor",
    "WAITING": "Eşleştirme yapılıyor",
    "READY_TO_PASS": "Araç geçebilir",
    "COMPLETED": "Tartım tamamlandı",
    "WAITING_SCALE_CLEAR": "Kantarın boşalması bekleniyor",
}
EVENT_TR = {
    "PLATE_DETECTED": "PLAKA OKUNDU",
    "WEIGHT_STABLE": "AĞIRLIK STABİL",
    "FULL_WEIGHT_CAPTURED": "DOLU TARTIM ALINDI",
    "EMPTY_WEIGHT_CAPTURED": "BOŞ TARTIM ALINDI",
    "NET_CALCULATED": "NET HESAPLANDI",
    "IRSALIYE_MATCHED": "İRSALİYE EŞLEŞTİRİLDİ",
    "IRSALIYE_CHANGED": "İRSALİYE DEĞİŞTİRİLDİ",
    "PLATE_CHANGED": "PLAKA DÜZENLENDİ",
    "READY_TO_PASS": "ARAÇ GEÇEBİLİR",
    "EXIT_DETECTED": "ÇIKIŞ PLAKASI OKUNDU",
    "COMPLETED": "İŞLEM TAMAMLANDI",
    "MANUAL_REVIEW": "OPERATÖR MÜDAHALESİ",
    "VISIT_REUSED": "MEVCUT KAYIT KULLANILDI",
    "MANUAL_VISIT_MATCH": "MANUEL EŞLEŞTİRME",
    "PLATE_CANDIDATE": "PLAKA ADAYI",
    "PLATE_CONFIRMED": "PLAKA DOĞRULANDI",
    "VEHICLE_ENTERING": "ARAÇ KANTARA ÇIKIYOR",
    "WEIGHT_STABILIZING": "AĞIRLIK OTURUYOR",
    "WEIGHT_CAPTURED": "TARTIM ALINDI",
    "WAITING_SCALE_CLEAR": "KANTAR BOŞALMASI BEKLENİYOR",
    "SCALE_EMPTY": "KANTAR BOŞ",
    "PLATE_CANDIDATE_EXPIRED": "PLAKA ADAYI SÜRESİ DOLDU",
}
SCALE_TR = {
    "STABLE": "STABİL",
    "UNSTABLE": "AĞIRLIK OTURUYOR",
    "NO_WEIGHT": "BOŞ",
    "COMMUNICATION_ERROR": "KANTAR BAĞLANTISI YOK",
}
SCALE_STATE_UI = {
    "SCALE_EMPTY": ("⚪ KANTAR HAZIR", "ARAÇ BEKLENİYOR", YELLOW),
    "PLATE_CANDIDATE": ("🟡 PLAKA ALGILANDI", "", YELLOW),
    "VEHICLE_ENTERING": ("🟡 ARAÇ KANTARA ÇIKIYOR", "Tartım alınmaz", YELLOW),
    "WEIGHT_RISING": ("🟡 ARAÇ KANTARA ÇIKIYOR", "Ağırlık yükseliyor", YELLOW),
    "WEIGHT_STABILIZING": ("🟡 AĞIRLIK OTURUYOR", "", YELLOW),
    "WEIGHT_STABLE": ("🟢 AĞIRLIK STABİL", "", GREEN),
    "WEIGHT_CAPTURED": ("🟢 TARTIM ALINDI", "", GREEN),
    "VEHICLE_LEAVING": ("🟡 ARACIN KANTARDAN AYRILMASI BEKLENİYOR", "", YELLOW),
    "WAITING_SCALE_CLEAR": ("🟡 ARACIN KANTARDAN AYRILMASI BEKLENİYOR", "", YELLOW),
    "MANUAL_REVIEW": ("🔴 OPERATÖR MÜDAHALESİ GEREKLİ", "", RED),
    "COMM_ERROR": ("🔴 KANTAR BAĞLANTISI YOK", "Son kilo korunur, boş sayılmaz", RED),
    "RECOVERY": ("🔴 UYGULAMA YENİDEN BAŞLATILDI", "Kantar üzerinde mevcut araç var", RED),
}


def _fmt_kg(kg) -> str:
    if kg is None or kg == "":
        return "—"
    try:
        from kantar_ifs import format_kg

        return format_kg(float(kg))
    except (TypeError, ValueError):
        return str(kg)


def _fmt_clock(iso: str | None) -> str:
    if not iso:
        return "—"
    text = str(iso)
    if "T" in text:
        return text.split("T", 1)[1][:5]
    if " " in text:
        return text.split(" ", 1)[1][:5]
    return text[:5] if len(text) >= 5 else text


def _status_tr(code: str | None) -> str:
    return STATUS_TR.get(str(code or ""), str(code or "—"))


def _reason_tr(code: str | None) -> str:
    if not code:
        return ""
    return REASON_TR.get(str(code), str(code))


def _plates_same(a: str, b: str) -> bool:
    from irsaliye import plate_key

    aa, bb = plate_key(a), plate_key(b)
    return bool(aa) and aa == bb


def _kill_stale_ocr() -> None:
    me = os.getpid()
    parent = os.getppid()
    cmd = (
        f"$skip = @({me},{parent}); "
        "Get-CimInstance Win32_Process | Where-Object { "
        "$_.CommandLine -match 'live_plate_ocr\\.py' "
        "-and ($_.CommandLine -notmatch 'app\\.py') "
        "-and $skip -notcontains $_.ProcessId "
        "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
        "-ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass


def _open_path(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        pass


def _python_exe() -> Path:
    p = Path(sys.executable)
    if p.name.lower() == "pythonw.exe":
        cand = p.with_name("python.exe")
        if cand.exists():
            return cand
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    return venv if venv.exists() else p


def _single_instance() -> bool:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, True, "Local\\PlakaOkumaDesktopApp")
    return int(kernel32.GetLastError()) != 183


def _dpi_aware() -> None:
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _ensure_icon() -> Path | None:
    path = ROOT / "akantar.ico"
    if path.exists():
        return path
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGBA", (256, 256), (11, 18, 32, 255))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((8, 8, 248, 248), 48, fill=(201, 162, 39, 255))
        d.rounded_rectangle((28, 28, 228, 228), 36, fill=(14, 22, 36, 255))
        try:
            font = ImageFont.truetype("segoeuib.ttf", 92)
        except Exception:
            font = ImageFont.load_default()
        d.text((128, 118), "BC", font=font, fill=(232, 197, 71, 255), anchor="mm")
        img.save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
        return path
    except Exception:
        return None


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{SITE_NAME}  ·  {APP_NAME}")
        self.geometry("1600x1000")
        self.minsize(1280, 900)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        ico = _ensure_icon()
        if ico:
            try:
                self.iconbitmap(str(ico))
            except Exception:
                pass

        self.q: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        self.worker: threading.Thread | None = None
        self.proc: subprocess.Popen | None = None
        self.running = False
        self.history_files: dict[str, str] = {}
        self.history_irs: dict[str, dict] = {}
        self._irs_matches: list[dict] = []
        self._irs_current: dict | None = None
        self._irs_plate: str = ""
        self._irs_pick_id: str = ""
        self._irs_user_pick: bool = False
        self._visit_browse: bool = False
        self._last_iid: str | None = None
        self._visit_cam: str = ""
        self._active_visit_id: str | None = None
        self._visit_filter = "all"
        self._visit_query = ""
        self._readings: list[str] = []
        self._scale: dict = {
            "id": "",
            "kg": None,
            "status": "NO_WEIGHT",
            "name": "",
            "seated": False,
            "empty": False,
            "ok": None,
            "scale_gate": "",
            "auto_state": "",
            "selected_plate": "",
            "cycle_id": "",
            "debug": False,
            "samples": [],
            "candidates": [],
            "reason": "",
        }
        self._last_sound: str | None = None
        self._filter_btns: dict[str, tk.Button] = {}
        self.cam_dot: dict[str, tk.Label] = {}
        self.cam_state: dict[str, tk.Label] = {}
        self.cam_preview: dict[str, tk.Label] = {}
        self._watch_photo: dict[str, tk.PhotoImage] = {}
        self._watch_win: dict[str, tk.Toplevel] = {}
        self.cam_ok: set[str] = set()
        self.n_read = 0
        self.n_match = 0
        self._row_n = 0

        self._build()
        self.after(120, self._poll)
        self.after(300, self._tick_clock)
        self.after(400, self._tick_watch)
        self.after(800, self._refresh_visits_tick)

    def _build(self) -> None:
        operator_ui.build(self)
        self.gate_title.trace_add("write", lambda *args: operator_ui.gate_style(self))

    def _style_ttk(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background=PANEL,
            foreground=TEXT,
            fieldbackground=PANEL,
            borderwidth=0,
            rowheight=34,
            font=("Segoe UI", -14),
        )
        style.configure(
            "Treeview.Heading",
            background=PANEL2,
            foreground=MUTED,
            relief="flat",
            font=("Segoe UI", -12, "bold"),
            padding=(8, 8),
        )
        style.map(
            "Treeview",
            background=[("selected", "#1E3A5F")],
            foreground=[("selected", TEXT)],
        )
        style.map("Treeview.Heading", background=[("active", EDGE)])
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=PANEL2,
            troughcolor=BG,
            bordercolor=BG,
            arrowcolor=MUTED,
        )
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    def _card(self, parent, **pack) -> tk.Frame:
        return operator_ui.card(parent, **pack)

    def _body(self) -> None:
        mid = tk.Frame(self, bg=BG)
        self.records_panel = mid
        mid.pack(fill="both", expand=True, padx=20, pady=(4, 8))
        left = tk.Frame(mid, bg=BG)
        left.pack(fill="both", expand=True)
        filt = tk.Frame(left, bg=BG)
        filt.pack(fill="x", pady=(0, 10))
        tk.Label(filt, text="Son araç hareketleri", font=("Segoe UI", -20, "bold"), fg=TEXT, bg=BG).pack(
            side="left", padx=(0, 20)
        )
        for key, lab in (
            ("all", "Tümü"),
            ("facility", "Tesiste"),
            ("done", "Tamamlandı"),
            ("review", "Müdahale"),
            ("irs", "İrsaliye bekliyor"),
        ):
            b = tk.Button(
                filt,
                text=lab,
                command=lambda k=key: self._set_visit_filter(k),
                fg=HEADER if key == "all" else TEXT,
                bg=CYAN if key == "all" else PANEL2,
                relief="flat",
                padx=10,
                pady=4,
                font=("Segoe UI", -12, "bold"),
                cursor="hand2",
                bd=0,
            )
            b.pack(side="left", padx=(0, 6))
            self._filter_btns[key] = b
        search_box = tk.Frame(filt, bg=BG)
        search_box.pack(side="right")
        tk.Label(search_box, text="Plaka / irsaliye", font=self.font_tiny, fg=MUTED, bg=BG).pack(side="left", padx=(8, 6))
        self.search_var = tk.StringVar()
        search = tk.Entry(
            search_box,
            textvariable=self.search_var,
            bg=PANEL2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=self.font_small,
            width=16,
            highlightthickness=1,
            highlightbackground=EDGE,
            highlightcolor=CYAN,
        )
        search.pack(side="left", ipady=4)
        search.bind("<KeyRelease>", lambda _e: self._on_search())
        table_card = self._card(left, fill="both", expand=True)
        cols = ("plaka", "irsaliye", "durum", "dolu", "bos", "net", "giris", "cikis")
        tree_wrap = tk.Frame(table_card, bg=PANEL)
        tree_wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_wrap, columns=cols, show="headings", height=4, selectmode="browse"
        )
        heads = {
            "plaka": ("PLAKA", 118),
            "irsaliye": ("İRSALİYE", 150),
            "durum": ("DURUM", 160),
            "dolu": ("DOLU", 80),
            "bos": ("BOŞ", 80),
            "net": ("NET", 80),
            "giris": ("GİRİŞ", 64),
            "cikis": ("ÇIKIŞ", 64),
        }
        for cid, (title, w) in heads.items():
            self.tree.heading(cid, text=title)
            self.tree.column(cid, width=w, minwidth=52, anchor="center" if cid != "durum" else "w")
        sb = ttk.Scrollbar(
            tree_wrap, orient="vertical", command=self.tree.yview, style="Dark.Vertical.TScrollbar"
        )
        self.tree.configure(yscrollcommand=sb.set)
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(tree_wrap, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.tag_configure("odd", background="#102436")
        self.tree.tag_configure("even", background=PANEL)
        self.tree.tag_configure("hit", foreground=TEXT)
        self.tree.tag_configure("miss", foreground=TEXT)
        self.tree.tag_configure("review", foreground=RED)
        self.tree.tag_configure("done", foreground=GREEN)
        self.tree.tag_configure("wait", foreground=YELLOW)
        self.tree.bind("<Double-1>", self._open_row)
        self.tree.bind("<ButtonRelease-1>", self._copy_row)
        self.tree.bind("<<TreeviewSelect>>", self._on_visit_select)
        self.tree.bind("<Button-3>", self._visit_menu)
        actions = tk.Frame(table_card, bg=PANEL)
        actions.pack(side="bottom", fill="x", padx=8, pady=6, before=tree_wrap)
        self._btn(actions, "Detay", self._show_detail, "ghost")
        self._btn(actions, "Plaka Düzenle", self._edit_plate, "ghost")
        self._btn(actions, "İrsaliye Getir", self._fetch_irs_for_row, "primary")
        self.btn_solve = self._btn(actions, "Sorunu Çöz", self._solve_review, "danger")
        self.list_count = tk.StringVar(value="")
        tk.Label(
            actions, textvariable=self.list_count, font=self.font_tiny, fg=MUTED, bg=PANEL
        ).pack(side="right", padx=8)

        self.diagnostics = tk.Toplevel(self)
        self.diagnostics.title("Sistem günlüğü ve son okumalar")
        self.diagnostics.geometry("860x620")
        self.diagnostics.configure(bg=BG)
        self.diagnostics.protocol("WM_DELETE_WINDOW", self.diagnostics.withdraw)
        self.diagnostics.withdraw()
        right = tk.Frame(self.diagnostics, bg=BG)
        right.pack(fill="both", expand=True, padx=16, pady=16)
        tk.Label(right, text="SON OKUMALAR", font=self.font_sec, fg=MUTED, bg=BG).pack(
            anchor="w", pady=(0, 6)
        )
        read_card = self._card(right, fill="x")
        self.read_list = tk.Listbox(
            read_card,
            height=5,
            bg=PANEL,
            fg=TEXT,
            relief="flat",
            highlightthickness=0,
            font=("Consolas", 9),
            activestyle="none",
            selectbackground=PANEL2,
        )
        self.read_list.pack(fill="x", padx=8, pady=8)
        tk.Label(right, text="SİSTEM GÜNLÜĞÜ", font=self.font_sec, fg=MUTED, bg=BG).pack(
            anchor="w", pady=(10, 6)
        )
        log_card = self._card(right, fill="both", expand=True)
        log_wrap = tk.Frame(log_card, bg=PANEL)
        log_wrap.pack(fill="both", expand=True)
        self.log = tk.Text(
            log_wrap,
            height=12,
            bg=PANEL,
            fg="#C5D0DC",
            insertbackground=TEXT,
            relief="flat",
            font=("Consolas", 9),
            wrap="word",
            bd=0,
            padx=12,
            pady=10,
        )
        lsb = ttk.Scrollbar(
            log_wrap, orient="vertical", command=self.log.yview, style="Dark.Vertical.TScrollbar"
        )
        self.log.configure(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")
        self.log.tag_configure("ok", foreground=GREEN)
        self.log.tag_configure("err", foreground=RED)
        self.log.tag_configure("warn", foreground=YELLOW)
        self.log.tag_configure("info", foreground="#C5D0DC")
        self.log.configure(state="disabled")

    def _footer(self) -> None:
        bar = tk.Frame(self, bg=FOOT)
        bar.pack(side="bottom", fill="x")
        self._btn(bar, "Sistem günlüğü", self._show_diagnostics, "ghost")
        self.status_var = tk.StringVar(value="Hazır — Başlat ile izlemeyi açın")
        tk.Label(
            bar, textvariable=self.status_var, font=self.font_tiny, fg=MUTED, bg=FOOT, anchor="w"
        ).pack(side="left", padx=18, pady=8)
        tk.Label(
            bar,
            text=f"{SITE_NAME}  ·  {APP_NAME}  ·  v{VERSION}",
            font=self.font_tiny,
            fg="#4B5568",
            bg=FOOT,
        ).pack(side="right", padx=18)

    def _show_diagnostics(self) -> None:
        self.diagnostics.deiconify()
        self.diagnostics.lift()

    def _show_manual_irs(self) -> None:
        self.manual_window.deiconify()
        self.manual_window.lift()
        self.irs_manual.focus_set()

    def _btn(self, parent, text, cmd, kind: str):
        return operator_ui.button(parent, text, cmd, kind)

    def _show_reports(self) -> None:
        import csv
        from tkinter import filedialog

        records = list(self._store().all_visits())
        win = tk.Toplevel(self)
        win.title("Araç raporları")
        win.geometry("560x300")
        win.configure(bg=PANEL)
        completed = [v for v in records if v.get("status") == "COMPLETED"]
        total = sum(float(v.get("net_weight") or 0) for v in completed)
        for line in ("Tüm kayıtların özeti", f"Toplam araç kaydı: {len(records)}",
                     f"Tamamlanan: {len(completed)}", f"Tamamlanan net ağırlık: {_fmt_kg(total)}"):
            operator_ui.label(win, line, 18).pack(anchor="w", padx=24, pady=12)

        def export():
            path = filedialog.asksaveasfilename(parent=win, defaultextension=".csv",
                initialfile="kantar-raporu.csv", filetypes=[("CSV", "*.csv")])
            if not path:
                return
            with open(path, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream, delimiter=";")
                writer.writerow(("Plaka", "İrsaliye", "Durum", "Dolu", "Boş", "Net", "Giriş", "Çıkış"))
                for record in records:
                    values = self._row_values(record)
                    writer.writerow("'" + str(value) if str(value).startswith(("=", "+", "-", "@")) else value for value in values)
            messagebox.showinfo("Rapor", "CSV raporu kaydedildi.", parent=win)
        self._btn(win, "CSV olarak kaydet", export, "primary").pack_configure(padx=24)

    def _tick_clock(self) -> None:
        self.clock_var.set(datetime.now().strftime("%d.%m.%Y   %H:%M:%S"))
        self.after(400, self._tick_clock)

    def _refresh_stats(self) -> None:
        self.stat_read.set(f"{self.n_read:02d}")
        self.stat_match.set(f"{self.n_match:02d}")
        self.stat_cam.set(f"{len(self.cam_ok)} / 2")

    def _set_live(self, text: str, color: str) -> None:
        self.live_var.set(f"  {text}")
        self.live_dot.configure(fg=color)

    def _append_log(self, text: str) -> None:
        raw = text.rstrip()
        low = raw.lower()
        tag = "info"
        if "hata" in low or "error" in low or "redded" in low:
            tag = "err"
        elif "plaka:" in low or "yeni" in low:
            tag = "ok"
        elif "yok" in low or "beklen" in low or "digital planet" in low:
            tag = "warn"
        self.log.configure(state="normal")
        self.log.insert("end", raw + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_cam(self, cid: str, label: str, color: str) -> None:
        if cid in self.cam_state:
            self.cam_state[cid].configure(text=" " + label, fg=color)
            self.cam_dot[cid].configure(fg=color)

    def _photo_from_watch(self, cid: str, max_w: int, max_h: int = 540) -> tk.PhotoImage | None:
        path = OUT_DIR / f"watch_{cid}.jpg"
        if not path.is_file():
            return None
        try:
            import cv2

            im = cv2.imread(str(path))
            if im is None:
                return None
            h, w = im.shape[:2]
            scale = min(max_w / max(w, 1), max_h / max(h, 1), 1.0)
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            im = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_AREA)
            im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
            ppm = f"P6 {nw} {nh} 255\n".encode() + im.tobytes()
            return tk.PhotoImage(data=ppm)
        except Exception:
            return None

    def _tick_watch(self) -> None:
        for cid, lbl in self.cam_preview.items():
            photo = self._photo_from_watch(cid, max(lbl.winfo_width(), 1), max(lbl.winfo_height(), 1))
            if photo is None:
                lbl.configure(image="", text="Kamera görüntüsü bekleniyor")
                continue
            self._watch_photo[cid] = photo
            lbl.configure(image=photo, text="")
        for cid, win in list(self._watch_win.items()):
            if not win.winfo_exists():
                self._watch_win.pop(cid, None)
                continue
            photo = self._photo_from_watch(cid, 960)
            if photo is None:
                continue
            self._watch_photo[f"win-{cid}"] = photo
            try:
                win._img.configure(image=photo)  # type: ignore[attr-defined]
            except Exception:
                pass
        try:
            self.after(400, self._tick_watch)
        except Exception:
            pass

    def _open_watch(self, cid: str) -> None:
        old = self._watch_win.get(cid)
        if old is not None and old.winfo_exists():
            old.lift()
            return
        win = tk.Toplevel(self)
        win.title(f"Kamera {cid} — canlı")
        win.configure(bg="#000")
        win.geometry("980x580")
        img = tk.Label(win, bg="#000", text="Başlat ile kare gelir", fg=MUTED)
        img.pack(fill="both", expand=True)
        win._img = img  # type: ignore[attr-defined]
        self._watch_win[cid] = win
        photo = self._photo_from_watch(cid, 960)
        if photo is not None:
            self._watch_photo[f"win-{cid}"] = photo
            img.configure(image=photo, text="")

    def _start(self) -> None:
        if self.running:
            return
        try:
            _kill_stale_ocr()
            self.stop = threading.Event()
            self.running = True
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
            self.status_var.set("Başlatılıyor…")
            self.meta_var.set("YOLO plaka motoru hazırlanıyor")
            self._set_live("BAĞLANIYOR", YELLOW)
            for cid, *_ in CAMS:
                self._set_cam(cid, "Bağlanıyor…", YELLOW)
            self._append_log("— Okuma başladı —")
            env = os.environ.copy()
            env["PLAKA_GUI"] = "1"
            env["PLAKA_SURE"] = "0"
            env["TEMP"] = str(ROOT / ".tmp")
            env["TMP"] = str(ROOT / ".tmp")
            env["EASYOCR_MODULE_PATH"] = str(ROOT / ".EasyOCR")
            env["HF_HOME"] = str(ROOT / ".hf")
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.proc = subprocess.Popen(
                [str(_python_exe()), "-u", str(ROOT / "live_plate_ocr.py")],
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
            )

            def read_out() -> None:
                try:
                    assert self.proc is not None and self.proc.stdout is not None
                    for line in self.proc.stdout:
                        line = line.rstrip("\r\n")
                        if not line:
                            continue
                        if line.startswith("@@"):
                            try:
                                rec = json.loads(line[2:])
                                kind = rec.pop("kind", "log")
                                self.q.put((kind, rec))
                            except Exception:
                                self.q.put(("log", {"text": line}))
                        else:
                            self.q.put(("log", {"text": line}))
                except Exception as exc:
                    self.q.put(("error", {"text": str(exc)}))
                finally:
                    self.q.put(("stopped", {}))

            self.worker = threading.Thread(target=read_out, daemon=True)
            self.worker.start()
        except Exception as exc:
            err = traceback.format_exc()
            try:
                (ROOT / "app_error.log").write_text(err, encoding="utf-8")
            except Exception:
                pass
            self.running = False
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self.status_var.set("Başlatılamadı")
            self._set_live("HATA", RED)
            self._append_log(f"HATA: {exc}")

    def _stop_proc(self) -> None:
        proc = self.proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass
        self.proc = None

    def _stop(self) -> None:
        if not self.running:
            return
        self.stop.set()
        self._stop_proc()
        self.status_var.set("Durduruluyor…")
        self._append_log("Durdurma istendi…")

    def _on_close(self) -> None:
        self.stop.set()
        self._stop_proc()
        self.destroy()

    def _store(self):
        from visits import get_store

        return get_store()

    def _ask(self, text: str) -> bool:
        return bool(messagebox.askyesno("Onay", text, parent=self))

    def _selected_visit_id(self) -> str | None:
        sel = self.tree.selection()
        if sel:
            return str(sel[0])
        return self._active_visit_id

    def _selected_visit(self) -> dict | None:
        vid = self._selected_visit_id()
        if not vid:
            return None
        try:
            return self._store().get(vid)
        except Exception as exc:
            self._append_log(f"Visit okunamadı: {exc}")
            return None

    def _set_visit_filter(self, key: str) -> None:
        self._visit_filter = key
        for k, btn in self._filter_btns.items():
            on = k == key
            btn.configure(bg=CYAN if on else PANEL2, fg=HEADER if on else TEXT)
        self._refresh_visits()

    def _on_search(self) -> None:
        self._visit_query = (self.search_var.get() or "").strip()
        self._refresh_visits()

    def _refresh_visits_tick(self) -> None:
        self._refresh_visits()
        self._paint_active()
        self.after(2000, self._refresh_visits_tick)

    def _visit_passes_filter(self, visit: dict) -> bool:
        status = str(visit.get("status") or "")
        filt = self._visit_filter
        if filt == "facility" and status not in {
            "IN_FACILITY",
            "WAITING_ENTRY_WEIGHT",
            "WAITING_DELIVERY_NOTE",
            "WAITING_EXIT",
            "WAITING_EXIT_WEIGHT",
        }:
            return False
        if filt == "done" and status != "COMPLETED":
            return False
        if filt == "review" and status != "MANUAL_REVIEW":
            return False
        if filt == "irs":
            has_irs = bool(visit.get("irsaliye_no") or visit.get("irsaliye_id"))
            if has_irs or status in {"COMPLETED", "ERROR", "MANUAL_REVIEW"}:
                return False
        plate = str(visit.get("plate") or "").strip()
        unidentified = not visit.get("plate_key") or plate in {"", "—", "TANIMSIZ"}
        if unidentified and filt != "review" and status != "MANUAL_REVIEW":
            return False
        q = self._visit_query.replace(" ", "").upper()
        if q:
            from irsaliye import plate_key

            hay = plate_key(plate) + plate_key(str(visit.get("irsaliye_no") or visit.get("irsaliye_id") or ""))
            if q not in hay:
                return False
        return True

    def _row_values(self, visit: dict) -> tuple:
        irs = visit.get("irsaliye_no") or visit.get("irsaliye_id") or "—"
        status = str(visit.get("status") or "")
        durum = _status_tr(status)
        if status == "MANUAL_REVIEW":
            why = _reason_tr(visit.get("manual_review_reason") or visit.get("wait_reason"))
            if why:
                durum = why[:36]
        return (
            visit.get("plate") or "—",
            irs,
            durum,
            _fmt_kg(visit.get("full_weight")),
            _fmt_kg(visit.get("empty_weight")),
            _fmt_kg(visit.get("net_weight")),
            _fmt_clock(visit.get("entry_time")),
            _fmt_clock(visit.get("exit_time")),
        )

    def _row_tags(self, visit: dict, idx: int) -> tuple:
        status = str(visit.get("status") or "")
        parity = "even" if idx % 2 == 0 else "odd"
        if status == "MANUAL_REVIEW":
            return (parity, "review")
        if status == "COMPLETED":
            return (parity, "done")
        if status in {"WAITING_DELIVERY_NOTE", "WAITING_ENTRY_WEIGHT", "WAITING_EXIT_WEIGHT"}:
            return (parity, "wait")
        if visit.get("irsaliye_no") or visit.get("irsaliye_id"):
            return (parity, "hit")
        return (parity, "miss")

    def _refresh_visits(self) -> None:
        try:
            visits = list(self._store().all_visits())
        except Exception as exc:
            self._append_log(f"Eşlenenler okunamadı: {exc}")
            return
        visits.sort(key=lambda v: str(v.get("updated_at") or v.get("created_at") or ""), reverse=True)
        visible: list[dict] = [v for v in visits if self._visit_passes_filter(v)]
        want = {str(v.get("visit_id")) for v in visible if v.get("visit_id")}
        for iid in list(self.tree.get_children()):
            if iid not in want:
                self.tree.delete(iid)
        for i, visit in enumerate(visible):
            iid = str(visit.get("visit_id") or "")
            if not iid:
                continue
            vals = self._row_values(visit)
            tags = self._row_tags(visit, i)
            if self.tree.exists(iid):
                self.tree.item(iid, values=vals, tags=tags)
                self.tree.move(iid, "", i)
            else:
                self.tree.insert("", i, iid=iid, values=vals, tags=tags)
        n_match = sum(1 for v in visits if v.get("irsaliye_no") or v.get("irsaliye_id"))
        self.n_match = n_match
        self.stat_match.set(f"{n_match:02d}")
        if hasattr(self, "list_count"):
            self.list_count.set(f"{len(visible)} kayıt")

    def _paint_active(self, visit: dict | None = None, force: bool = False) -> None:
        if visit is None and self._active_visit_id:
            try:
                visit = self._store().get(self._active_visit_id)
            except Exception as exc:
                self._append_log(f"Aktif araç okunamadı: {exc}")
                visit = None
        if visit:
            live = str(
                self._scale.get("selected_plate")
                or self.plate_var.get()
                or ""
            )
            vp = str(visit.get("plate") or "")
            if (
                not force
                and not self._visit_browse
                and live
                and vp
                and live not in {"—  —  —", "—"}
                and _plates_same(live, vp) is False
            ):
                self._sync_irs_panel({"plate": live})
                self._paint_gate(None)
                return
            self.plate_var.set(vp or "—  —  —")
            cam = visit.get("exit_camera") or visit.get("entry_camera") or self._visit_cam
            if visit.get("exit_time") and visit.get("exit_camera"):
                yon = f"ÇIKIŞ  ·  Kamera {visit.get('exit_camera')}"
            else:
                yon = f"GİRİŞ  ·  Kamera {visit.get('entry_camera') or cam or '—'}"
            self.meta_var.set(yon)
            self.act_full.set(_fmt_kg(visit.get("full_weight")))
            self.act_empty.set(_fmt_kg(visit.get("empty_weight")))
            self.act_net.set(_fmt_kg(visit.get("net_weight")))
            irs = visit.get("irsaliye_no") or visit.get("irsaliye_id")
            self.act_irs.set(f"{irs} ✓" if irs else "BEKLENİYOR")
            self._sync_irs_panel(visit)
            self._paint_gate(visit)
        else:
            if not self.plate_var.get() or self.plate_var.get() == "—  —  —":
                self.act_full.set("—")
                self.act_empty.set("—")
                self.act_net.set("—")
                self.act_irs.set("—")
            self._paint_gate(None)
        self._paint_scale()

    def _paint_gate(self, visit: dict | None) -> None:
        operator_ui.refresh_steps(self, visit)
        auto = str(self._scale.get("auto_state") or "")
        if not self.running:
            self.gate_title.set("SİSTEM DURDURULDU")
            self.gate_sub.set("Canlı operasyon için izlemeyi başlatın.")
            self.gate_lbl.configure(fg=MUTED)
            return
        if (self._scale.get("ok") is False
                or self._scale.get("status") == "COMMUNICATION_ERROR"
                or auto == "COMM_ERROR"
                or (self._scale.get("ok") is not True and self._scale.get("kg") is None)):
            self.gate_title.set("KANTAR BAĞLANTISI BEKLENİYOR")
            self.gate_sub.set("Bağlantı doğrulanmadan geçiş hazır değildir.")
            self.gate_lbl.configure(fg=YELLOW)
            return
        kg_s = _fmt_kg(self._scale.get("kg"))
        plate = str(
            self._scale.get("selected_plate")
            or (visit or {}).get("plate")
            or self.plate_var.get()
            or ""
        )
        if plate in {"—  —  —", "—"}:
            plate = ""
        live_priority = {
            "PLATE_CANDIDATE",
            "VEHICLE_ENTERING",
            "WEIGHT_RISING",
            "WEIGHT_STABILIZING",
            "WEIGHT_STABLE",
            "WEIGHT_CAPTURED",
            "WAITING_SCALE_CLEAR",
            "VEHICLE_LEAVING",
            "COMM_ERROR",
            "RECOVERY",
            "MANUAL_REVIEW",
        }
        if auto in live_priority:
            title, sub, color = SCALE_STATE_UI.get(auto, ("🟡 BEKLEYİN", "", YELLOW))
            if auto == "PLATE_CANDIDATE" and plate:
                sub = plate
            elif auto in {"WEIGHT_STABILIZING", "WEIGHT_STABLE"}:
                sub = kg_s
            elif auto == "MANUAL_REVIEW":
                sub = _reason_tr(self._scale.get("reason")) or _reason_tr(
                    (visit or {}).get("wait_reason")
                )
            self.gate_title.set(title)
            self.gate_sub.set(sub)
            self.gate_lbl.configure(fg=color or YELLOW)
            return
        if visit and visit.get("gate") == "MANUAL_REVIEW":
            self.gate_title.set("🔴 OPERATÖR MÜDAHALESİ GEREKLİ")
            self.gate_sub.set(_reason_tr(visit.get("wait_reason")) or str(visit.get("wait_reason") or ""))
            self.gate_lbl.configure(fg=RED)
            return
        if visit and visit.get("gate") == "COMPLETED" and auto in {"", "SCALE_EMPTY"}:
            self.gate_title.set("🟢 TARTIM TAMAMLANDI")
            self.gate_sub.set(
                f"DOLU: {_fmt_kg(visit.get('full_weight'))}   "
                f"BOŞ: {_fmt_kg(visit.get('empty_weight'))}   "
                f"NET: {_fmt_kg(visit.get('net_weight'))}"
            )
            self.gate_lbl.configure(fg=GREEN)
            return
        if visit and visit.get("gate") == "READY_TO_PASS" and auto in {"", "SCALE_EMPTY"}:
            self.gate_title.set("🟢 İŞLEM TAMAM\nARAÇ GEÇEBİLİR")
            self.gate_sub.set("İrsaliye ve dolu tartım hazır")
            self.gate_lbl.configure(fg=GREEN)
            return
        if auto == "SCALE_EMPTY" or not visit:
            title, sub, color = SCALE_STATE_UI["SCALE_EMPTY"]
            self.gate_title.set(title)
            self.gate_sub.set(sub)
            self.gate_lbl.configure(fg=color)
            return
        reason = str(visit.get("wait_reason") or "")
        self.gate_title.set("🟡 BEKLEYİN")
        self.gate_sub.set(WAIT_TR.get(reason, _reason_tr(reason) or "Bekleniyor"))
        self.gate_lbl.configure(fg=YELLOW)

    def _paint_scale(self) -> None:
        st = str(self._scale.get("status") or "NO_WEIGHT")
        auto = str(self._scale.get("auto_state") or "")
        ok = self._scale.get("ok")
        kg = self._scale.get("kg")
        self.stat_kg.set(_fmt_kg(kg))
        if ok is False or st == "COMMUNICATION_ERROR" or auto == "COMM_ERROR":
            self.scale_link.set("BAĞLANTI  ·  🔴 KANTAR BAĞLANTISI YOK")
        elif ok is True or kg is not None:
            self.scale_link.set("BAĞLANTI  ·  🟢 BAĞLI")
        else:
            self.scale_link.set("BAĞLANTI  ·  —")
        self.scale_now.set(f"ANLIK  ·  {_fmt_kg(kg)}")
        ui = SCALE_STATE_UI.get(auto)
        if ui:
            self.scale_st.set(f"DURUM  ·  {ui[0]}")
        else:
            label = SCALE_TR.get(st, st or "—")
            if st == "UNSTABLE":
                self.scale_st.set(f"DURUM  ·  🟡 {label}")
            elif st == "STABLE":
                self.scale_st.set(f"DURUM  ·  🟢 {label}")
            elif st == "COMMUNICATION_ERROR":
                self.scale_st.set(f"DURUM  ·  🔴 {label}")
            else:
                self.scale_st.set(f"DURUM  ·  {label}")
        self._paint_scale_debug()

    def _paint_scale_debug(self) -> None:
        if not self._scale.get("debug"):
            if self.scale_dbg_lbl.winfo_manager():
                self.scale_dbg_lbl.pack_forget()
            self.scale_dbg.set("")
            return
        cands = self._scale.get("candidates") or []
        lines = [
            f"SCALE STATE: {self._scale.get('auto_state') or '—'}",
            f"CYCLE: {self._scale.get('cycle_id') or '—'}",
            f"WEIGHT: {self._scale.get('kg') if self._scale.get('kg') is not None else '—'}",
            "SAMPLES:",
        ]
        for kg in self._scale.get("samples") or []:
            lines.append(f"  {kg}")
        lines.append("PLATE CANDIDATES:")
        for rec in cands[:6]:
            if not isinstance(rec, dict):
                continue
            conf = rec.get("avg_conf")
            conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else "—"
            lines.append(
                f"  {rec.get('plate')} count={rec.get('count')} "
                f"conf={conf_s} cam={rec.get('camera')} score={rec.get('score')}"
            )
        sel = self._scale.get("selected_plate") or "—"
        lines.append(f"SELECTED: {sel}")
        if self._scale.get("reason"):
            lines.append(f"REASON: {self._scale.get('reason')}")
        self.scale_dbg.set("\n".join(lines))
        if not self.scale_dbg_lbl.winfo_manager():
            self.scale_dbg_lbl.pack(anchor="w", padx=16, pady=(6, 4), fill="x")

    def _play_scale_sound(self, kind: str | None) -> None:
        if not kind:
            return
        token = f"{self._scale.get('cycle_id')}:{kind}"
        if token == self._last_sound:
            return
        self._last_sound = token
        try:
            import winsound

            if kind in {"capture", "ready"}:
                winsound.MessageBeep(winsound.MB_OK)
            elif kind == "review":
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            elif kind == "comm":
                winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            try:
                self.bell()
            except Exception:
                pass

    def _on_visit_select(self, _evt=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        self._active_visit_id = str(sel[0])
        self._visit_browse = True
        self._irs_user_pick = False
        self._irs_pick_id = ""
        visit = self._selected_visit()
        if visit:
            self._paint_active(visit, force=True)

    def _visit_menu(self, evt) -> None:
        row = self.tree.identify_row(evt.y)
        if row:
            self.tree.selection_set(row)
        menu = tk.Menu(self, tearoff=0, bg=PANEL2, fg=TEXT, activebackground=CYAN)
        menu.add_command(label="Detay", command=self._show_detail)
        menu.add_command(label="Plaka Düzenle", command=self._edit_plate)
        menu.add_command(label="İrsaliye Getir", command=self._fetch_irs_for_row)
        visit = self._selected_visit()
        if visit and visit.get("status") == "MANUAL_REVIEW":
            menu.add_command(label="Sorunu Çöz", command=self._solve_review)
        try:
            menu.tk_popup(evt.x_root, evt.y_root)
        finally:
            menu.grab_release()

    def _show_detail(self, _evt=None) -> None:
        visit = self._selected_visit()
        if not visit:
            return
        win = tk.Toplevel(self)
        win.title("Araç detayı")
        win.configure(bg=PANEL)
        win.geometry("520x640")
        win.transient(self)
        body = tk.Frame(win, bg=PANEL)
        body.pack(fill="both", expand=True, padx=16, pady=16)
        rows = (
            ("Visit ID", visit.get("visit_id")),
            ("Plaka", visit.get("plate")),
            ("Durum", _status_tr(visit.get("status"))),
            ("Giriş", visit.get("entry_time")),
            ("Giriş kamera", visit.get("entry_camera")),
            ("Dolu", _fmt_kg(visit.get("full_weight"))),
            ("Çıkış", visit.get("exit_time")),
            ("Çıkış kamera", visit.get("exit_camera")),
            ("Boş", _fmt_kg(visit.get("empty_weight"))),
            ("Net", _fmt_kg(visit.get("net_weight"))),
            ("İrsaliye", visit.get("irsaliye_no") or visit.get("irsaliye_id") or "—"),
            ("Geçiş", visit.get("gate")),
        )
        for i, (k, v) in enumerate(rows):
            tk.Label(body, text=k, font=self.font_tiny, fg=MUTED, bg=PANEL, width=16, anchor="w").grid(
                row=i, column=0, sticky="nw", pady=2
            )
            tk.Label(
                body, text=str(v or "—"), font=self.font_small, fg=TEXT, bg=PANEL, wraplength=320, justify="left"
            ).grid(row=i, column=1, sticky="w", pady=2)
        tk.Label(body, text="OLAY GEÇMİŞİ", font=self.font_sec, fg=MUTED, bg=PANEL).grid(
            row=len(rows), column=0, columnspan=2, sticky="w", pady=(14, 6)
        )
        tl = tk.Text(body, height=12, bg=PANEL2, fg=TEXT, relief="flat", font=("Consolas", 9), wrap="word")
        tl.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="nsew")
        skip_ev = {
            "ANOMALY_DETECTED",
            "ANOMALY_ACKNOWLEDGED",
            "WEIGHT_RISING",
            "VEHICLE_ENTERING",
            "WEIGHT_STABILIZING",
            "SCALE_EMPTY",
            "WAITING_SCALE_CLEAR",
            "PLATE_CANDIDATE",
        }
        for ev in visit.get("events") or []:
            raw_act = str(ev.get("action") or "")
            if raw_act in skip_ev:
                continue
            ts = _fmt_clock(ev.get("ts"))
            action = EVENT_TR.get(raw_act, raw_act)
            detail = ev.get("detail") or {}
            extra = ""
            for key in ("kg", "net_weight", "full_weight", "empty_weight", "new_value", "plate", "reason"):
                if key in detail and detail[key] not in (None, ""):
                    val = detail[key]
                    if key.endswith("weight") or key == "kg":
                        val = _fmt_kg(val)
                    extra += f"  {val}"
                    break
            tl.insert("end", f"{ts}  {action}{extra}\n")
        tl.configure(state="disabled")
        btnf = tk.Frame(body, bg=PANEL)
        btnf.grid(row=len(rows) + 2, column=0, columnspan=2, sticky="w", pady=12)
        self._btn(btnf, "Kapat", win.destroy, "ghost")

    def _edit_plate(self) -> None:
        visit = self._selected_visit()
        if not visit:
            return
        win = tk.Toplevel(self)
        win.title("Plaka düzenle")
        win.configure(bg=PANEL)
        win.geometry("440x420")
        win.transient(self)
        win.grab_set()
        tk.Label(win, text="Mevcut plaka", font=self.font_tiny, fg=MUTED, bg=PANEL).pack(
            anchor="w", padx=18, pady=(16, 2)
        )
        tk.Label(win, text=str(visit.get("plate") or ""), font=("Segoe UI", 16, "bold"), fg=TEXT, bg=PANEL).pack(
            anchor="w", padx=18
        )
        cands = [c for c in (visit.get("pending_candidates") or []) if isinstance(c, dict) and c.get("plate")]
        if cands:
            tk.Label(win, text="Son okumalardan seç", font=self.font_tiny, fg=MUTED, bg=PANEL).pack(
                anchor="w", padx=18, pady=(10, 2)
            )
            rowc = tk.Frame(win, bg=PANEL)
            rowc.pack(fill="x", padx=18, pady=(0, 4))

            def pick(p: str) -> None:
                ent.delete(0, "end")
                ent.insert(0, p)

            for c in cands[:4]:
                self._btn(rowc, str(c.get("plate")), lambda p=str(c.get("plate")): pick(p), "ghost")
        tk.Label(win, text="Yeni plaka", font=self.font_tiny, fg=MUTED, bg=PANEL).pack(
            anchor="w", padx=18, pady=(12, 2)
        )
        ent = tk.Entry(win, bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat", font=self.font_small)
        ent.pack(fill="x", padx=18, ipady=8)
        current = str(visit.get("plate") or "")
        if current and current != "TANIMSIZ":
            ent.insert(0, current)
        ent.focus_set()
        tk.Label(win, text="Neden (zorunlu)", font=self.font_tiny, fg=MUTED, bg=PANEL).pack(
            anchor="w", padx=18, pady=(12, 2)
        )
        reason_ent = tk.Entry(win, bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat", font=self.font_small)
        reason_ent.pack(fill="x", padx=18, ipady=8)
        err = tk.StringVar(value="")
        tk.Label(win, textvariable=err, font=self.font_tiny, fg=RED, bg=PANEL).pack(anchor="w", padx=18, pady=4)

        def save() -> None:
            from irsaliye import format_plate
            from visits import is_valid_plate

            raw = (ent.get() or "").strip()
            why = (reason_ent.get() or "").strip()
            if not raw:
                err.set("Yeni plaka boş olamaz.")
                return
            if not is_valid_plate(raw):
                err.set("Plaka geçersiz. Örnek: 77 ADN 856")
                return
            if not why:
                err.set("Manuel değişiklik için neden yazılmalıdır.")
                return
            formatted = format_plate(raw)
            old = str(visit.get("plate") or "")
            if formatted == old:
                win.destroy()
                return
            if not self._ask(f"{old} plakası {formatted} olarak değiştirilecek. Devam etmek istiyor musunuz?"):
                return
            try:
                result = self._store().change_plate(
                    visit_id=str(visit.get("visit_id")),
                    new_plate=formatted,
                    source="OPERATOR",
                    reason=why,
                )
            except Exception as exc:
                err.set(str(exc))
                self._append_log(f"Plaka değişmedi: {exc}")
                return
            if not result.get("ok"):
                if result.get("error") == "PLATE_CONFLICT":
                    messagebox.showwarning(
                        "Plaka çakışması",
                        "Bu plakaya ait başka bir açık araç kaydı bulunuyor.",
                        parent=self,
                    )
                else:
                    messagebox.showwarning("Plaka", "Plaka kaydedilemedi.", parent=self)
                return
            self._append_log(f"Plaka değişti: {old} → {formatted} ({why})")
            self._refresh_visits()
            self._paint_active(result.get("visit"))
            win.destroy()

        row = tk.Frame(win, bg=PANEL)
        row.pack(fill="x", padx=18, pady=12)
        self._btn(row, "Kaydet", save, "primary")
        self._btn(row, "İptal", win.destroy, "ghost")
        ent.bind("<Return>", lambda _e: save())

    def _fetch_irs_for_row(self) -> None:
        visit = self._selected_visit()
        if not visit:
            return
        self._active_visit_id = str(visit.get("visit_id") or "")
        self._visit_cam = str(visit.get("entry_camera") or visit.get("exit_camera") or self._visit_cam)
        self.plate_var.set(str(visit.get("plate") or ""))
        self._paint_active(visit)
        plate = str(visit.get("plate") or "")
        if not plate or plate in {"—  —  —", "—", "TANIMSIZ"}:
            self._append_log("Getir: önce araç plakası gerekli.")
            return
        when = None
        try:
            raw = visit.get("exit_time") or visit.get("entry_time")
            if raw:
                when = datetime.fromisoformat(str(raw))
        except ValueError:
            when = None
        try:
            from eportal import load_config as load_eportal_config
            from eportal import sync as sync_eportal

            pcfg = load_eportal_config()
            if pcfg.get("dp_login") and pcfg.get("dp_password") and pcfg.get("dp_corporate"):
                self._append_log(f"Getir: {plate} için Digital Planet taranıyor…")
                sync_eportal(log=self._append_log, cfg=pcfg)
        except Exception as exc:
            self._append_log(f"Getir senkron: {exc}")
        try:
            from irsaliye import IrsaliyeIndex, plate_matches_doc

            hits = [
                h
                for h in IrsaliyeIndex().docs_for_when(plate, when)
                if plate_matches_doc(plate, h)
            ]
        except Exception as exc:
            self._append_log(f"İrsaliye arama: {exc}")
            hits = []
        self._set_irsaliye({}, False, hits)
        self._irs_pick_dialog(visit, hits)

    def _irs_pick_dialog(self, visit: dict, hits: list[dict]) -> None:
        win = tk.Toplevel(self)
        win.title("İrsaliye seç")
        win.configure(bg=PANEL)
        win.geometry("560x520")
        win.transient(self)
        tk.Label(win, text="İRSALİYE SEÇ", font=self.font_sec, fg=MUTED, bg=PANEL).pack(
            anchor="w", padx=16, pady=(14, 4)
        )
        tk.Label(
            win,
            text=f"Araç: {visit.get('plate')}    Zaman: {_fmt_clock(visit.get('entry_time') or visit.get('exit_time'))}",
            font=self.font_small,
            fg=TEXT,
            bg=PANEL,
        ).pack(anchor="w", padx=16, pady=(0, 8))
        box = tk.Frame(win, bg=PANEL)
        box.pack(fill="both", expand=True, padx=16)

        def take(rec: dict) -> None:
            win.destroy()
            self._confirm_irs(rec, "secim")

        def open_pdf(rec: dict) -> None:
            self._open_irs_doc(rec)

        if not hits:
            tk.Label(box, text="Bu plaka ve saate uygun irsaliye yok. Elle yazabilirsiniz.", fg=YELLOW, bg=PANEL).pack(
                anchor="w"
            )
        for rec in hits:
            row = tk.Frame(box, bg=PANEL2)
            row.pack(fill="x", pady=4)
            tk.Label(
                row,
                text=(
                    f"{rec.get('id') or '—'}   plaka {rec.get('plate') or '—'}   "
                    f"{rec.get('time') or ''}"
                ),
                font=self.font_small,
                fg=TEXT,
                bg=PANEL2,
            ).pack(side="left", padx=8, pady=8)
            self._btn(row, "Al", lambda r=rec: take(r), "primary")
            self._btn(row, "PDF", lambda r=rec: open_pdf(r), "ghost")
        man = tk.Frame(win, bg=PANEL)
        man.pack(fill="x", padx=16, pady=12)
        tk.Label(man, text="ELLE İRSALİYE NO", font=self.font_tiny, fg=MUTED, bg=PANEL).pack(anchor="w")
        ent = tk.Entry(man, bg=PANEL2, fg=TEXT, insertbackground=TEXT, relief="flat", font=self.font_small)
        ent.pack(fill="x", ipady=6, pady=(4, 8))

        def save_manual() -> None:
            num = (ent.get() or "").strip().upper().replace(" ", "")
            if not num:
                return
            rec = None
            try:
                from irsaliye import IrsaliyeIndex

                rec = IrsaliyeIndex().find_by_id(num)
            except Exception as exc:
                self._append_log(f"Elle irsaliye: {exc}")
            if rec is None:
                rec = {"id": num, "summary": "Elle kaydedildi"}
            win.destroy()
            self._confirm_irs(rec, "elle")

        self._btn(man, "Kaydet", save_manual, "ghost")
        self._btn(man, "Kapat", win.destroy, "ghost")

    def _solve_review(self) -> None:
        visit = self._selected_visit()
        if not visit or visit.get("status") != "MANUAL_REVIEW":
            return
        reason = str(visit.get("manual_review_reason") or "")
        if reason == "MULTIPLE_OPEN_VISITS":
            self._solve_multiple(visit)
        elif reason == "OPEN_VISIT_NOT_FOUND":
            self._solve_missing_entry(visit)
        else:
            self._edit_plate()

    def _solve_multiple(self, visit: dict) -> None:
        try:
            cands = self._store().review_siblings(str(visit.get("plate") or ""))
            if visit not in cands and visit.get("visit_id"):
                seen = {v.get("visit_id") for v in cands}
                if visit.get("visit_id") not in seen:
                    cands = [visit] + cands
        except Exception as exc:
            self._append_log(f"Aday kayıtlar: {exc}")
            return
        win = tk.Toplevel(self)
        win.title("Açık kayıt seç")
        win.configure(bg=PANEL)
        win.geometry("560x420")
        win.transient(self)
        tk.Label(
            win,
            text=f"{visit.get('plate')} için {len(cands)} açık kayıt bulundu. Rastgele seçim yapılmaz.",
            font=self.font_small,
            fg=TEXT,
            bg=PANEL,
            wraplength=520,
            justify="left",
        ).pack(anchor="w", padx=16, pady=12)

        def pick(vid: str) -> None:
            if not self._ask("Çıkış tartımı seçilen kayıtla eşleşecek. Devam?"):
                return
            try:
                updated = self._store().resolve_multiple_exit(chosen_id=vid, source="OPERATOR")
            except Exception as exc:
                messagebox.showerror("Eşleştirme", str(exc), parent=self)
                self._append_log(f"Manuel eşleştirme: {exc}")
                return
            self._append_log(f"Manuel eşleştirme: {vid}")
            self._active_visit_id = vid
            self._refresh_visits()
            self._paint_active(updated)
            win.destroy()

        for i, cand in enumerate(cands, 1):
            row = tk.Frame(win, bg=PANEL2)
            row.pack(fill="x", padx=16, pady=4)
            txt = (
                f"{i}.  Giriş {_fmt_clock(cand.get('entry_time'))}   "
                f"Dolu {_fmt_kg(cand.get('full_weight'))}   "
                f"İrsaliye {cand.get('irsaliye_no') or cand.get('irsaliye_id') or '—'}"
            )
            tk.Label(row, text=txt, font=self.font_small, fg=TEXT, bg=PANEL2).pack(
                side="left", padx=8, pady=8
            )
            self._btn(row, "Bu kayıtla eşleştir", lambda v=str(cand.get("visit_id")): pick(v), "primary")

    def _solve_missing_entry(self, visit: dict) -> None:
        win = tk.Toplevel(self)
        win.title("Giriş kaydı bulunamadı")
        win.configure(bg=PANEL)
        win.geometry("560x480")
        win.transient(self)
        tk.Label(win, text="🔴 GİRİŞ KAYDI BULUNAMADI", font=("Segoe UI", 14, "bold"), fg=RED, bg=PANEL).pack(
            anchor="w", padx=16, pady=(14, 4)
        )
        tk.Label(
            win,
            text=f"Plaka: {visit.get('plate')}    Çıkış: {_fmt_kg(visit.get('empty_weight'))}",
            font=self.font_small,
            fg=TEXT,
            bg=PANEL,
        ).pack(anchor="w", padx=16, pady=(0, 8))
        row = tk.Frame(win, bg=PANEL)
        row.pack(fill="x", padx=16, pady=4)
        self._btn(row, "Plakayı Düzenle", lambda: (win.destroy(), self._edit_plate()), "ghost")
        tk.Label(win, text="Açık kayıtlarda ara", font=self.font_tiny, fg=MUTED, bg=PANEL).pack(
            anchor="w", padx=16, pady=(10, 4)
        )
        try:
            opens = self._store().all_open()
        except Exception as exc:
            self._append_log(f"Açık kayıt listesi: {exc}")
            opens = []
        if not opens:
            tk.Label(win, text="Şu anda başka açık giriş kaydı yok.", fg=MUTED, bg=PANEL).pack(
                anchor="w", padx=16
            )
        for cand in opens:
            box = tk.Frame(win, bg=PANEL2)
            box.pack(fill="x", padx=16, pady=3)
            tk.Label(
                box,
                text=(
                    f"{cand.get('plate')}  {_fmt_clock(cand.get('entry_time'))}  "
                    f"Dolu {_fmt_kg(cand.get('full_weight'))}"
                ),
                font=self.font_small,
                fg=TEXT,
                bg=PANEL2,
            ).pack(side="left", padx=8, pady=6)

            def link(oid: str = str(cand.get("visit_id"))) -> None:
                if not self._ask("Çıkış tartımı bu açık kayıtla eşleşecek. Devam?"):
                    return
                try:
                    updated = self._store().attach_exit_to_open(
                        review_id=str(visit.get("visit_id")),
                        open_id=oid,
                        source="OPERATOR",
                    )
                except Exception as exc:
                    messagebox.showerror("Eşleştirme", str(exc), parent=self)
                    return
                self._active_visit_id = oid
                self._refresh_visits()
                self._paint_active(updated)
                win.destroy()

            self._btn(box, "Bu kayıtla eşleştir", link, "primary")
        self._btn(win, "Kapat", win.destroy, "ghost")

    def _push_reading(self, ts: str, plate: str, cid: str) -> None:
        line = f"{ts}  |  {plate}  |  {cid}"
        self._readings.insert(0, line)
        self._readings = self._readings[:20]
        self.read_list.delete(0, "end")
        for item in self._readings:
            self.read_list.insert("end", item)

    def _open_row(self, _evt=None) -> None:
        self._show_detail()

    def _open_irs_doc(self, irs: dict | None) -> None:
        if not irs:
            return
        try:
            from irsaliye import ensure_view

            path = ensure_view(irs)
            if path and path.exists():
                _open_path(path)
                return
            self._append_log("PDF açılamadı — irsaliye kaydı duruyor")
            messagebox.showwarning(
                "PDF",
                "Belge açılamadı. Seçilen irsaliye kaydı silinmedi.",
                parent=self,
            )
        except Exception as exc:
            self._append_log(f"PDF hata: {exc}")
            messagebox.showwarning(
                "PDF",
                f"Belge açılamadı.\n{exc}\nİrsaliye ve visit kaydı duruyor.",
                parent=self,
            )

    def _open_current_irs(self) -> None:
        self._open_irs_doc(self._irs_current)

    def _on_irs_pick(self, _evt=None) -> None:
        sel = self.irs_list.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self._irs_matches):
            rec = self._irs_matches[idx]
            self._irs_user_pick = True
            self._irs_pick_id = str(rec.get("id") or "")
            self._preview_irs(rec)
            taken = ""
            try:
                if self._active_visit_id:
                    vis = self._store().get(self._active_visit_id) or {}
                    taken = str(vis.get("irsaliye_no") or vis.get("irsaliye_id") or "")
            except Exception:
                taken = ""
            if taken and taken == self._irs_pick_id:
                self.irs_status.set("Alındı")
                self.irs_badge.configure(text="  ALINDI  ", bg=GREEN, fg=HEADER)
                self.irs_var.set("Başka belge için listeden seçip Al’a basın")
            else:
                self.irs_status.set("Seçildi — Al’a basın")
                self.irs_badge.configure(text="  SEÇİM  ", bg=ORANGE, fg=HEADER)
                self.irs_var.set(
                    "Al’a basınca bu belge kaydedilir"
                    if not taken
                    else f"{taken} yerine bu belge alınır"
                )

    def _irs_when(self, visit: dict | None) -> datetime | None:
        raw = (visit or {}).get("exit_time") or (visit or {}).get("entry_time")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            return None

    def _irs_hits_for_plate(self, plate: str, visit: dict | None = None) -> list[dict]:
        if not plate or plate in {"—", "—  —  —", "TANIMSIZ"}:
            return []
        from irsaliye import IrsaliyeIndex, plate_matches_doc

        hits = [
            h
            for h in IrsaliyeIndex().docs_for_when(plate, self._irs_when(visit))
            if plate_matches_doc(plate, h)
        ]
        seen = {str(h.get("id") or "") for h in hits}
        for m in self._irs_matches:
            mid = str(m.get("id") or "")
            if mid and mid not in seen and plate_matches_doc(plate, m):
                hits.append(m)
                seen.add(mid)
        return hits

    def _apply_irs_list(
        self,
        matches: list[dict],
        select_id: str | None = None,
        taken_id: str | None = None,
    ) -> None:
        uniq: list[dict] = []
        seen: set[str] = set()
        for rec in matches or []:
            if not rec:
                continue
            mid = str(rec.get("id") or "")
            if mid and mid in seen:
                continue
            if mid:
                seen.add(mid)
            uniq.append(rec)
        self._irs_matches = uniq
        self.irs_list.delete(0, "end")
        pick = 0
        for i, rec in enumerate(uniq):
            self.irs_list.insert("end", self._line_for_irs(rec))
            if select_id and str(rec.get("id") or "") == str(select_id):
                pick = i
        if not uniq:
            self._fill_irsaliye({}, False)
            return
        self.irs_list.selection_clear(0, "end")
        self.irs_list.selection_set(pick)
        self.irs_list.see(pick)
        chosen = uniq[pick]
        self._preview_irs(chosen)
        cid = str(chosen.get("id") or "")
        if taken_id and cid == str(taken_id):
            self.irs_status.set("Alındı")
            self.irs_badge.configure(text="  ALINDI  ", bg=GREEN, fg=HEADER)
            self.irs_var.set("Başka belge için listeden seçip Al’a basın")
        else:
            self.irs_status.set(f"{len(uniq)} belge — seçin ve Al’a basın")
            self.irs_badge.configure(text="  SEÇİM  ", bg=ORANGE, fg=HEADER)
            self.irs_var.set(
                f"{taken_id} yerine bu belge alınır"
                if taken_id
                else "Listeden doğru irsaliyeyi seçip Al’a basın"
            )

    def _sync_irs_panel(self, visit: dict) -> None:
        """Bu plakanın belgeleri kalsın; kullanıcı seçimini kantar boyaması ezmesin."""
        plate = str(visit.get("plate") or "")
        irs_id = str(visit.get("irsaliye_no") or visit.get("irsaliye_id") or "").strip()
        try:
            from irsaliye import IrsaliyeIndex, plate_matches_doc
        except Exception:
            if irs_id and str((self._irs_current or {}).get("id") or "") != irs_id:
                self._apply_irs_list([{"id": irs_id}], select_id=irs_id, taken_id=irs_id)
            return
        hits: list[dict] = []
        try:
            hits = self._irs_hits_for_plate(plate, visit)
        except Exception as exc:
            self._append_log(f"İrsaliye panel: {exc}")
        if irs_id and not any(str(h.get("id") or "") == irs_id for h in hits):
            rec = None
            try:
                rec = IrsaliyeIndex().find_by_id(irs_id)
            except Exception:
                rec = None
            rec = rec or {"id": irs_id, "plate": plate}
            if plate and (rec.get("official_plates") or rec.get("plate")):
                if not plate_matches_doc(plate, rec):
                    self.irs_var.set(
                        f"Bu irsaliye {rec.get('plate') or irs_id} plakasına ait, araç {plate}."
                    )
                else:
                    hits = [rec] + hits
            else:
                hits = [rec] + hits
        if plate:
            hits = [
                h
                for h in hits
                if plate_matches_doc(plate, h) or str(h.get("id") or "") == irs_id
            ]
        self._irs_plate = plate
        cur_id = str((self._irs_current or {}).get("id") or "")
        old_ids = [str(m.get("id") or "") for m in self._irs_matches]
        new_ids = [str(h.get("id") or "") for h in hits]
        if old_ids == new_ids and cur_id and (self._irs_user_pick or cur_id in new_ids):
            if irs_id and cur_id == irs_id:
                self.irs_status.set("Alındı")
                self.irs_badge.configure(text="  ALINDI  ", bg=GREEN, fg=HEADER)
            return
        if not hits:
            if self._irs_user_pick and cur_id:
                return
            if self._irs_current and plate and not plate_matches_doc(plate, self._irs_current):
                self._fill_irsaliye({}, False)
            elif not self._irs_matches:
                self._fill_irsaliye({}, False)
            return
        select = self._irs_pick_id if self._irs_user_pick else (irs_id or None)
        self._apply_irs_list(hits, select_id=select, taken_id=irs_id)

    def _preview_irs(self, irs: dict) -> None:
        self._irs_current = irs
        self.irs_id.set(str(irs.get("id") or "—"))
        self.irs_customer.set(str(irs.get("customer") or irs.get("supplier") or "—"))
        self.irs_driver.set(str(irs.get("driver") or "—"))
        date = " ".join(
            p for p in (str(irs.get("date") or ""), str(irs.get("time") or "")) if p
        ).strip()
        self.irs_date.set(date or "—")
        goods: list[str] = []
        for ln in irs.get("lines") or []:
            bit = " ".join(
                p
                for p in (
                    str(ln.get("name") or ln.get("sku") or ""),
                    str(ln.get("qty") or ""),
                    str(ln.get("unit") or ""),
                )
                if p and p != "—"
            ).strip()
            if bit:
                goods.append(bit)
        self.irs_goods.set("\n".join(goods) if goods else "—")
        notes = [str(n) for n in (irs.get("notes") or []) if n]
        self.irs_var.set("  ·  ".join(notes) if notes else "Al’a basınca bu belge kaydedilir")
        self.btn_irs_take.configure(state="normal")
        self.btn_irs_open.configure(state="normal")

    def _fill_irsaliye(self, irs: dict, matched: bool) -> None:
        if matched:
            self._preview_irs(irs)
            self.irs_status.set("Alındı")
            self.irs_badge.configure(text="  ALINDI  ", bg=GREEN, fg=HEADER)
        else:
            self._irs_current = None
            self.irs_status.set("Eşleşme yok")
            self.irs_id.set("—")
            self.irs_customer.set("—")
            self.irs_driver.set("—")
            self.irs_date.set("—")
            self.irs_goods.set("—")
            self.irs_var.set("Listede yoksa aşağıya irsaliye numarasını yazıp Kaydet’e basın")
            self.irs_badge.configure(text="  BEKLENİYOR  ", bg=YELLOW, fg=HEADER)
            self.btn_irs_take.configure(state="disabled")
            self.btn_irs_open.configure(state="disabled")

    def _line_for_irs(self, rec: dict) -> str:
        mal = ""
        lines = rec.get("lines") or []
        if lines:
            mal = str(lines[0].get("name") or "")[:28]
        t = str(rec.get("time") or "")[:8]
        who = str(rec.get("supplier") or rec.get("customer") or "")[:22]
        mark = ""
        if rec.get("time_match"):
            mark = "önce"
        elif rec.get("time_delta_sec") is not None:
            sec = int(rec["time_delta_sec"])
            if sec < 24 * 3600:
                mark = f"{sec // 60}dk"
        return "  ".join(
            p
            for p in (
                mark,
                str(rec.get("id") or "—"),
                rec.get("plate") or "",
                t,
                who,
                mal,
            )
            if p
        )

    def _set_irsaliye(self, irs: dict, matched: bool, matches: list | None = None) -> None:
        matches = [m for m in (matches or []) if m]
        if matched and irs and not matches:
            matches = [irs]
        if not matches:
            self._fill_irsaliye(irs or {}, False)
            return
        taken = str((irs or {}).get("id") or "") if matched else ""
        keep = self._irs_pick_id if self._irs_user_pick else (taken or None)
        self._apply_irs_list(matches, select_id=keep, taken_id=taken or None)

    def _update_visit_row(self, irs_id: str, rec: dict | None) -> None:
        self._refresh_visits()
        self._paint_active()
        if rec and self._active_visit_id:
            self.history_irs[self._active_visit_id] = rec

    def _confirm_irs(self, rec: dict, source: str) -> None:
        irs_id = str(rec.get("id") or "").strip()
        if not irs_id:
            return
        visit_id = self._active_visit_id
        plate = ""
        try:
            if visit_id:
                current = self._store().get(visit_id)
                plate = str((current or {}).get("plate") or "")
        except Exception as exc:
            self._append_log(f"Visit plaka: {exc}")
        if not plate or plate in {"—  —  —", "—"}:
            plate = str(self.plate_var.get() or "").strip()
        try:
            from irsaliye import plate_matches_doc

            if plate and (rec.get("official_plates") or rec.get("plate")):
                if not plate_matches_doc(plate, rec):
                    doc_p = rec.get("plate") or ",".join(rec.get("official_plates") or [])
                    if source == "elle":
                        if not self._ask(
                            f"Belge plakası {doc_p}, araç {plate}. Elle yine de bağlamak istiyor musunuz?"
                        ):
                            return
                    else:
                        messagebox.showerror(
                            "İrsaliye",
                            f"Bu belge {doc_p} plakasına ait. Araç {plate}. Bağlanmaz.",
                            parent=self,
                        )
                        return
        except Exception as exc:
            self._append_log(f"Plaka kontrol: {exc}")
        old_irs = ""
        try:
            if visit_id:
                current = self._store().get(visit_id)
                old_irs = str((current or {}).get("irsaliye_no") or (current or {}).get("irsaliye_id") or "")
        except Exception as exc:
            self._append_log(f"Visit irsaliye kontrol: {exc}")
        if old_irs and old_irs != irs_id:
            if not self._ask(f"{old_irs} irsaliyesi {irs_id} olarak değiştirilecek. Devam etmek istiyor musunuz?"):
                return
        try:
            from irsaliye import IrsaliyeIndex, save_choice

            save_choice(
                plate,
                irs_id,
                source,
                self._visit_cam,
                extra={"visit_id": visit_id or ""},
            )
            if source == "secim":
                IrsaliyeIndex()._mark_used(irs_id)
            if visit_id or plate:
                self._store().attach_irsaliye(
                    visit_id=visit_id,
                    plate=plate,
                    irsaliye_id=irs_id,
                    irsaliye_no=irs_id,
                    irsaliye_source="MANUAL" if source == "elle" else source,
                    source="OPERATOR",
                )
        except Exception as exc:
            self._append_log(f"İrsaliye kaydı: {exc}")
            messagebox.showerror("İrsaliye", f"Kayıt yazılamadı:\n{exc}", parent=self)
            return
        self._irs_user_pick = False
        self._irs_pick_id = irs_id
        self._irs_plate = plate
        self._fill_irsaliye(rec, True)
        self._refresh_visits()
        vis = None
        try:
            vis = self._store().get(visit_id) if visit_id else None
        except Exception:
            vis = None
        vis = vis or {"plate": plate, "irsaliye_id": irs_id, "irsaliye_no": irs_id}
        try:
            hits = self._irs_hits_for_plate(plate, vis)
        except Exception:
            hits = [rec]
        if not any(str(h.get("id") or "") == irs_id for h in hits):
            hits = [rec] + hits
        self._apply_irs_list(hits, select_id=irs_id, taken_id=irs_id)
        self._paint_active(vis, force=True)
        self._append_log(f"İrsaliye alındı: {irs_id}  {plate}")
        if rec.get("file") or rec.get("uuid"):
            self._open_irs_doc(rec)
        try:
            self.clipboard_clear()
            self.clipboard_append(irs_id)
        except Exception as exc:
            self._append_log(f"Pano: {exc}")

    def _take_irs(self) -> None:
        rec = self._irs_current
        if not rec:
            sel = self.irs_list.curselection()
            if sel and 0 <= int(sel[0]) < len(self._irs_matches):
                rec = self._irs_matches[int(sel[0])]
        if rec:
            self._confirm_irs(rec, "secim")

    def _save_manual_irs(self) -> None:
        num = (self.irs_manual.get() or "").strip().upper().replace(" ", "")
        if not num:
            return
        rec: dict | None = None
        try:
            from irsaliye import IrsaliyeIndex

            rec = IrsaliyeIndex().find_by_id(num)
        except Exception:
            rec = None
        if rec is None:
            rec = {"id": num, "summary": "Elle kaydedildi"}
        self._confirm_irs(rec, "elle")
        self.irs_manual.delete(0, "end")

    def _copy_row(self, _evt=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        if vals:
            try:
                self.clipboard_clear()
                self.clipboard_append(vals[0])
            except Exception as exc:
                self._append_log(f"Pano: {exc}")

    def _apply_status(self, parts: list[str]) -> None:
        for part in parts:
            if ":" not in part:
                continue
            cid, rest = part.split(":", 1)
            self._set_cam_from_state(cid, rest.lower(), rest)

    def _set_cam_from_state(self, cid: str, state: str, text: str = "") -> None:
        label = text or state
        color = MUTED
        st = (state or "").lower()
        if st in {"vehicle", "arac"} or "araç" in label.lower() or "arac" in st:
            color = ORANGE
            label = text or "Araç var"
        elif st in {"empty", "bos"} or label == "Boş":
            color = GREEN
            label = "Boş"
        elif st in {"warmup", "hazirlik"} or "zemin" in label.lower():
            color = ACCENT
            label = text or "Zemin öğreniliyor"
        elif st in {"down", "yok"}:
            color = RED
            label = text or "Görüntü yok"
        elif st in {"busy", "mesgul"}:
            color = YELLOW
            label = text or "Meşgul"
        elif "okuyor" in label.lower():
            color = ORANGE
        self._set_cam(cid, label, color)

    def _poll(self) -> None:
        try:
            while True:
                try:
                    kind, kw = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._on_proc_event(kind, kw)
                except Exception as exc:
                    self._append_log(f"Arayüz olay ({kind}): {exc}")
        except Exception as exc:
            try:
                self._append_log(f"Arayüz: {exc}")
            except Exception:
                pass
        finally:
            try:
                self.after(150, self._poll)
            except Exception:
                pass

    def _on_proc_event(self, kind: str, kw: dict) -> None:
        while True:
            if kind == "log":
                self._append_log(str(kw.get("text", "")))
            elif kind == "busy":
                self.status_var.set(str(kw.get("text", "Çalışıyor…")))
            elif kind == "cam":
                cid = str(kw.get("id", ""))
                ok = bool(kw.get("ok", False))
                mode = kw.get("mode")
                if ok:
                    label = "Bağlı" if mode == "hold" else "Hazır (kısa oturum)"
                    self._set_cam(cid, label, GREEN)
                    if cid:
                        self.cam_ok.add(cid)
                else:
                    self._set_cam(cid, "Hata", RED)
                    self.cam_ok.discard(cid)
                self._refresh_stats()
            elif kind == "status":
                cams = kw.get("cams") or []
                if cams:
                    for cam in cams:
                        self._set_cam_from_state(
                            str(cam.get("id", "")),
                            str(cam.get("state", "")),
                            str(cam.get("text", "")),
                        )
                    self.status_var.set(str(kw.get("summary") or "İzleniyor"))
                else:
                    parts = kw.get("parts") or []
                    self._apply_status(parts)
                    self.status_var.set(str(kw.get("summary") or "İzleniyor"))
            elif kind == "ocr":
                cid = str(kw.get("id", ""))
                if kw.get("running"):
                    self._set_cam(cid, "Plaka okunuyor…", ORANGE)
                    self.status_var.set(f"{kw.get('name') or cid}: plaka okunuyor…")
                    self._set_live("OKUMA", ORANGE)
                else:
                    self._set_cam(cid, "İzleniyor", GREEN)
            elif kind == "weight":
                kg = kw.get("kg")
                ts = str(kw.get("ts") or "")
                seated = bool(kw.get("seated"))
                empty = bool(kw.get("empty"))
                status = str(kw.get("status") or "")
                self._scale.update(
                    {
                        "id": str(kw.get("id") or self._scale.get("id") or ""),
                        "kg": kg,
                        "status": status
                        or (
                            "STABLE"
                            if seated
                            else "NO_WEIGHT"
                            if empty
                            else "UNSTABLE"
                        ),
                        "name": str(kw.get("name") or self._scale.get("name") or ""),
                        "seated": seated,
                        "empty": empty,
                        "ok": status != "COMMUNICATION_ERROR",
                        "scale_gate": str(kw.get("scale_gate") or self._scale.get("scale_gate") or ""),
                        "auto_state": str(
                            kw.get("scale_state") or self._scale.get("auto_state") or ""
                        ),
                    }
                )
                kg_s = _fmt_kg(kg)
                self.stat_kg.set(kg_s)
                kname = str(kw.get("name") or "Kantar").strip()
                if seated:
                    self.weight_var.set(f"{kname} · oturdu · {kg_s}  {ts}".strip())
                elif empty:
                    self.weight_var.set(f"{kname} · anlık · {kg_s}  (boş)")
                else:
                    self.weight_var.set(f"{kname} · anlık · {kg_s}  {ts}".strip())
                self._paint_scale()
                self._paint_active()
            elif kind == "scale":
                sel = kw.get("selected") or {}
                plate = ""
                if isinstance(sel, dict):
                    plate = str(sel.get("plate") or "")
                self._scale.update(
                    {
                        "id": str(kw.get("id") or self._scale.get("id") or ""),
                        "kg": kw.get("kg", self._scale.get("kg")),
                        "auto_state": str(kw.get("state") or ""),
                        "scale_gate": str(kw.get("state") or ""),
                        "selected_plate": plate,
                        "cycle_id": str(kw.get("cycle_id") or ""),
                        "debug": bool(kw.get("debug")),
                        "samples": list(kw.get("samples") or []),
                        "candidates": list(kw.get("candidates") or []),
                        "reason": str(kw.get("reason") or ""),
                        "captured": bool(kw.get("captured")),
                        "ok": str(kw.get("state") or "") != "COMM_ERROR",
                        "status": (
                            "COMMUNICATION_ERROR"
                            if str(kw.get("state") or "") == "COMM_ERROR"
                            else self._scale.get("status") or "UNSTABLE"
                        ),
                    }
                )
                if plate:
                    self.plate_var.set(plate)
                self._play_scale_sound(kw.get("sound"))
                self._paint_scale()
                self._paint_active()
            elif kind == "visit":
                visit = kw.get("visit") or {}
                vid = str(visit.get("visit_id") or "")
                if vid:
                    self._active_visit_id = vid
                if visit.get("gate") == "READY_TO_PASS":
                    self._play_scale_sound("ready")
                self._refresh_visits()
                self._paint_active(visit if visit else None)
            elif kind == "plate":
                plate = str(kw.get("plate", ""))
                name = str(kw.get("name", ""))
                cid = str(kw.get("id", ""))
                ts = str(kw.get("ts", ""))
                conf = kw.get("conf", 0)
                irs = kw.get("irsaliye") or {}
                matches = list(kw.get("irsaliyeler") or [])
                try:
                    from irsaliye import plate_matches_doc

                    matches = [m for m in matches if plate_matches_doc(plate, m)]
                    if irs and not plate_matches_doc(plate, irs):
                        irs = {}
                except Exception:
                    matches = []
                    irs = {}
                try:
                    opens = self._store().open_visits(plate)
                    if opens:
                        self._active_visit_id = str(opens[0].get("visit_id") or "")
                except Exception:
                    pass
                self.plate_var.set(plate)
                try:
                    cf = min(max(float(conf), 0.0), 1.0)
                    conf_s = f"{cf:.0%}"
                except (TypeError, ValueError):
                    conf_s = str(conf)
                self.meta_var.set(f"{name}  ·  {ts}  ·  güven {conf_s}")
                wkg = kw.get("weight")
                wts = str(kw.get("weight_ts") or "")
                if wkg is not None:
                    w_s = _fmt_kg(wkg)
                    seated = "oturdu" if kw.get("weight_seated") else "anlık"
                    kname = str(kw.get("weight_name") or "Kantar").strip()
                    self.weight_var.set(f"{kname} · {seated} · {w_s}  {wts}".strip())
                    self.stat_kg.set(w_s)
                self._visit_cam = cid
                vid = str(kw.get("visit_id") or "")
                if vid:
                    self._active_visit_id = vid
                    if kw.get("file"):
                        self.history_files[vid] = str(kw["file"])
                if not (
                    self._irs_user_pick
                    and self._irs_plate
                    and _plates_same(self._irs_plate, plate)
                ):
                    self._set_irsaliye(irs, False, matches)
                self.n_read += 1
                self._refresh_stats()
                self._push_reading(ts, plate, cid)
                try:
                    self.clipboard_clear()
                    self.clipboard_append(plate)
                except Exception as exc:
                    self._append_log(f"Pano: {exc}")
                self._set_live("CANLI", GREEN)
                self._refresh_visits()
                self._paint_active()
                if not matches:
                    try:
                        self.irs_manual.focus_set()
                    except Exception as exc:
                        self._append_log(f"Odak: {exc}")
            elif kind == "ready":
                self.status_var.set("İzleniyor — araç gelince okur")
                self.meta_var.set("Araç bekleniyor")
                self._set_live("CANLI", GREEN)
            elif kind == "error":
                self._append_log("HATA: " + str(kw.get("text", "")))
                self.status_var.set("Hata — ayrıntı app_error.log")
                self._set_live("HATA", RED)
            elif kind == "stopped":
                self.running = False
                self.btn_start.configure(state="normal")
                self.btn_stop.configure(state="disabled")
                self.status_var.set("Durdu")
                self._set_live("DURDU", MUTED)
                self.cam_ok.clear()
                self._refresh_stats()
                for cid, *_ in CAMS:
                    self._set_cam(cid, "Kapalı", MUTED)
            break


def main() -> int:
    _dpi_aware()
    if not _single_instance():
        return 0
    sys.excepthook = lambda t, v, tb: (ROOT / "app_error.log").write_text(
        "".join(traceback.format_exception(t, v, tb)), encoding="utf-8"
    )
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BursaCimento.AkilliKantar")
    except Exception:
        pass
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
