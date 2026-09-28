"""Live operator console. Display values are bound to the existing application state."""
import tkinter as tk
from tkinter import font as tkfont
import customtkinter as ctk

BG = "#091827"
HEADER = "#0C1E2E"
PANEL = "#0F2233"
EDGE = "#294255"
TEXT = "#EFF5FC"
MUTED = "#A1B6CC"
CYAN = "#21C8EA"
GREEN = "#53EE86"


def label(parent, text="", size=14, color=TEXT, bold=False, **kw):
    return tk.Label(parent, text=text, fg=color, bg=kw.pop("bg", PANEL),
                    font=("Segoe UI", -size, "bold" if bold else "normal"), **kw)


def card(parent, **pack):
    shell = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=10,
                         border_width=1, border_color=EDGE)
    inside = tk.Frame(shell, bg=PANEL)
    inside.pack(fill="both", expand=True, padx=8, pady=8)
    if pack:
        shell.pack(**pack)
    return inside


def button(parent, text, command, kind="ghost"):
    colors = {"primary": (CYAN, "#082033", "#62DCF3", CYAN),
              "ghost": (PANEL, TEXT, "#1B354B", EDGE),
              "danger": (HEADER, "#FF6480", "#3B2131", "#C3445F")}
    bg, fg, hover, border = colors[kind]
    b = ctk.CTkButton(parent, text=text, command=command, width=max(85, len(text)*8+24),
                      height=36, corner_radius=6, border_width=1,
                      border_color=border, fg_color=bg, text_color=fg,
                      text_color_disabled="#8294A5", hover_color=hover,
                      font=("Segoe UI", 14, "bold"))
    b.pack(side="left", padx=4)
    return b


def build(a):
    ctk.set_appearance_mode("dark")
    ctk.set_widget_scaling(1.0)
    a.configure(bg=BG)
    a.font_tiny = tkfont.Font(family="Segoe UI", size=-12)
    a.font_sec = tkfont.Font(family="Segoe UI", size=-14, weight="bold")
    a.font_small = tkfont.Font(family="Segoe UI", size=-14)
    a.font_kpi = tkfont.Font(family="Segoe UI", size=-28, weight="bold")
    a.font_plate = tkfont.Font(family="Bahnschrift", size=-48, weight="bold")
    a.weight_font = tkfont.Font(family="Segoe UI", size=-58, weight="bold")
    a._style_ttk()
    header(a)
    a._footer()
    a._body()
    a.records_panel.pack_configure(side="bottom", fill="x", expand=False)
    a.records_panel.configure(height=245)
    a.records_panel.pack_propagate(False)
    a.workspace = tk.Frame(a, bg=BG)
    a.workspace.pack(fill="both", expand=True, padx=18, pady=(10, 8))
    a.workspace.columnconfigure(0, weight=69, uniform="main")
    a.workspace.columnconfigure(1, weight=31, uniform="main")
    a.workspace.rowconfigure(0, weight=1)
    a.operation = tk.Frame(a.workspace, bg=BG)
    a.operation.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
    a.operation.columnconfigure(0, weight=1)
    a.operation.rowconfigure(0, weight=1)
    a.operation.rowconfigure(1, minsize=206)
    cameras(a)
    active_vehicle(a)
    a.side_panel = card(a.workspace)
    a.side_panel.master.grid(row=0, column=1, sticky="nsew")
    workflow(a)
    a.bind("<Configure>", lambda e: resize(a) if e.widget is a else None, add="+")
    a.after_idle(lambda: resize(a))
    a._paint_gate(None)


def header(a):
    bar = tk.Frame(a, bg=HEADER, height=68)
    bar.pack(fill="x")
    bar.pack_propagate(False)
    brand = tk.Frame(bar, bg=HEADER)
    brand.pack(side="left", padx=(24, 32))
    tk.Label(brand, text="BC", font=("Georgia", -40), bg=HEADER, fg="#C7A767").pack(side="left", padx=(0, 14))
    names = tk.Frame(brand, bg=HEADER)
    names.pack(side="left")
    label(names, "Bursa Çimento", 21, bold=True, bg=HEADER).pack(anchor="w")
    label(names, "Akıllı Kantar", 14, MUTED, bg=HEADER).pack(anchor="w")
    nav = tk.Frame(bar, bg=HEADER)
    nav.pack(side="left")
    active = button(nav, "Canlı operasyon", lambda: a.workspace.focus_set())
    active.configure(fg_color="#103248", text_color=CYAN, border_color=CYAN)
    button(nav, "Araç kayıtları", lambda: a.tree.focus_set())
    button(nav, "Raporlar", a._show_reports)
    controls = tk.Frame(bar, bg=HEADER)
    controls.pack(side="right", padx=16)
    a.btn_start = button(controls, "Sistemi başlat", a._start, "primary")
    a.btn_stop = button(controls, "Sistemi durdur", a._stop, "danger")
    a.btn_stop.configure(state="disabled")
    strip = tk.Frame(a, bg=HEADER, height=42, highlightthickness=1, highlightbackground=EDGE)
    strip.pack(fill="x")
    strip.pack_propagate(False)
    a.live_dot = label(strip, "●", 16, MUTED, bg=HEADER)
    a.live_dot.pack(side="left", padx=(24, 8))
    a.live_var = tk.StringVar(value="DURDURULDU")
    label(strip, textvariable=a.live_var, size=12, color=MUTED, bg=HEADER).pack(side="left", padx=(0, 24))
    a.stat_cam = tk.StringVar(value="0 / 2")
    label(strip, textvariable=a.stat_cam, color=MUTED, bg=HEADER).pack(side="left")
    label(strip, " kamera bağlı", color=MUTED, bg=HEADER).pack(side="left", padx=(0, 24))
    a.scale_link = tk.StringVar(value="Kantar bağlantısı bekleniyor")
    label(strip, textvariable=a.scale_link, size=13, color=MUTED, bg=HEADER).pack(side="left", padx=(0, 24))
    a.stat_read = tk.StringVar(value="00")
    a.stat_match = tk.StringVar(value="00")
    a.stat_kg = tk.StringVar(value="— kg")
    a.clock_var = tk.StringVar()
    label(strip, textvariable=a.clock_var, color=MUTED, bg=HEADER).pack(side="right", padx=20)


def cameras(a):
    row = tk.Frame(a.operation, bg=BG)
    row.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
    row.rowconfigure(0, weight=1)
    for i, (cid, lane) in enumerate((("153", "Giriş"), ("165", "Çıkış"))):
        row.columnconfigure(i, weight=1, uniform="camera")
        panel = card(row)
        panel.master.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
        top = tk.Frame(panel, bg=PANEL)
        top.pack(fill="x", pady=(0, 8))
        label(top, f"{lane} kamerası", 15, bold=True).pack(side="left", padx=(4, 8))
        label(top, f"CAM {cid}", 12, MUTED).pack(side="left")
        dot = label(top, "●", 12, MUTED)
        dot.pack(side="left", padx=(10, 3))
        status = label(top, "Bekliyor", 11, MUTED)
        status.pack(side="left")
        a.cam_dot[cid], a.cam_state[cid] = dot, status
        viewport = tk.Frame(panel, bg="#07131E")
        viewport.pack(fill="both", expand=True)
        viewport.pack_propagate(False)
        prev = label(viewport, "Görüntü bekleniyor\n\nCanlı izleme için sistemi başlatın", 15, MUTED, bg="#07131E")
        prev.pack(fill="both", expand=True)
        prev.bind("<Double-1>", lambda e, c=cid: a._open_watch(c))
        a.cam_preview[cid] = prev


def active_vehicle(a):
    panel = card(a.operation)
    panel.master.grid(row=1, column=0, sticky="nsew")
    label(panel, "AKTİF ARAÇ", 15, bold=True).pack(anchor="w", padx=12, pady=(2, 5))
    identity = tk.Frame(panel, bg=PANEL)
    identity.pack(fill="x", padx=12)
    plate = ctk.CTkFrame(identity, fg_color="#F4F1E8", corner_radius=7, border_width=2, border_color="#8C9BAD")
    plate.pack(side="left")
    tk.Label(plate, text="TR", bg="#063FB7", fg="white", font=("Segoe UI", -16, "bold"), width=3, pady=18).pack(side="left", padx=(4, 0), pady=4)
    a.plate_var = tk.StringVar(value="—  —  —")
    a.plate_lbl = tk.Label(plate, textvariable=a.plate_var, font=a.font_plate, bg="#F4F1E8", fg="#111820", padx=12)
    a.plate_lbl.pack(side="left", padx=(0, 5), pady=4)
    a.meta_var = tk.StringVar(value="Araç bekleniyor")
    label(panel, textvariable=a.meta_var, size=12, color=MUTED).pack(anchor="w", padx=12, pady=(4, 2))
    a.weight_var = tk.StringVar(value="Anlık: —")
    weight = tk.Frame(identity, bg=PANEL)
    weight.pack(side="right", padx=(12, 2))
    a.weight_label = tk.Label(weight, textvariable=a.stat_kg, font=a.weight_font, bg=PANEL, fg=TEXT)
    a.weight_label.pack(side="left")
    a.stable_var = tk.StringVar(value="Tartım bekleniyor")
    a.stable_label = label(weight, textvariable=a.stable_var, size=12, color=MUTED, wraplength=80)
    a.stable_label.pack(side="left", padx=(10, 0))
    tk.Frame(panel, bg=EDGE, height=1).pack(fill="x", padx=12, pady=(2, 7))
    facts = tk.Frame(panel, bg=PANEL)
    facts.pack(fill="x", padx=12, pady=(0, 3))
    a.act_full, a.act_empty, a.act_net, a.act_irs = [tk.StringVar(value="—") for _ in range(4)]
    for i, (name, var) in enumerate((("Dolu", a.act_full), ("Boş", a.act_empty), ("Net", a.act_net))):
        facts.columnconfigure(i, weight=1, uniform="weight")
        cell = tk.Frame(facts, bg=PANEL)
        cell.grid(row=0, column=i, sticky="ew")
        label(cell, name, 13, MUTED).pack()
        label(cell, textvariable=var, size=24, bold=True).pack()


def workflow(a):
    panel = a.side_panel
    label(panel, "Geçiş kontrolü", 22, bold=True).pack(anchor="w", padx=10, pady=(2, 10))
    a.gate_box = ctk.CTkFrame(panel, fg_color="#152E3E", corner_radius=8, border_width=1, border_color=EDGE)
    a.gate_box.pack(fill="x", padx=8)
    a.gate_title = tk.StringVar(value="Sistem durduruldu")
    a.gate_sub = tk.StringVar(value="İzlemeyi başlatın")
    a.gate_lbl = label(a.gate_box, textvariable=a.gate_title, size=20, bold=True, bg="#152E3E", anchor="w", justify="left", wraplength=350)
    a.gate_lbl.pack(fill="x", padx=16, pady=(12, 2))
    a.gate_sub_lbl = label(a.gate_box, textvariable=a.gate_sub, size=13, color=MUTED, bg="#152E3E", anchor="w", justify="left", wraplength=350)
    a.gate_sub_lbl.pack(fill="x", padx=16, pady=(0, 12))
    a.step_labels = []
    steps = tk.Frame(panel, bg=PANEL)
    steps.pack(fill="x", padx=12, pady=(10, 6))
    for i, name in enumerate(("Plaka bekleniyor", "Tartım bekleniyor", "İrsaliye bekleniyor"), 1):
        line = tk.Frame(steps, bg=PANEL)
        line.pack(fill="x", pady=5)
        label(line, f"{i:02d}", 13, MUTED).pack(side="left", padx=(0, 18))
        status = label(line, "○  " + name, 15, MUTED)
        status.pack(side="left")
        a.step_labels.append(status)
    a.scale_now, a.scale_st, a.scale_dbg = [tk.StringVar() for _ in range(3)]
    a.scale_dbg_lbl = label(a.diagnostics, textvariable=a.scale_dbg, size=12, color=MUTED, justify="left", anchor="w")
    tk.Frame(panel, bg=EDGE, height=1).pack(fill="x", padx=10, pady=(0, 10))
    a.irs_status, a.irs_id, a.irs_customer, a.irs_driver, a.irs_date, a.irs_goods, a.irs_var = [tk.StringVar(value="—") for _ in range(7)]
    head = tk.Frame(panel, bg=PANEL)
    head.pack(fill="x", padx=10, pady=(0, 8))
    label(head, "E-İRSALİYE", 13, MUTED, True).pack(side="left")
    a.irs_badge = label(head, "BEKLİYOR", 11, MUTED)
    a.irs_badge.pack(side="right")
    a.irs_labels = []
    details = tk.Frame(panel, bg=PANEL)
    details.pack(fill="x", padx=10)
    details.columnconfigure(1, weight=1)
    for row, (name, var) in enumerate((("Belge", a.irs_id), ("Firma", a.irs_customer), ("Sürücü", a.irs_driver), ("Malzeme", a.irs_goods), ("Tarih", a.irs_date))):
        label(details, name + ":", 13, MUTED).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=2)
        value = label(details, textvariable=var, size=14, anchor="w", width=1)
        value.grid(row=row, column=1, sticky="ew", pady=2)
        a.irs_labels.append(value)
    actions = tk.Frame(panel, bg=PANEL)
    actions.pack(side="bottom", fill="x", padx=10, pady=(6, 2))
    a.btn_irs_take = button(actions, "İrsaliyeyi eşleştir", a._take_irs, "primary")
    a.btn_irs_take.pack_configure(side="top", fill="x", padx=0, pady=(4, 7))
    a.btn_irs_take.configure(height=42, state="disabled")
    secondary = tk.Frame(actions, bg=PANEL)
    secondary.pack(fill="x")
    a.btn_irs_open = button(secondary, "Belgeyi görüntüle", a._open_current_irs)
    a.btn_irs_open.configure(state="disabled")
    button(secondary, "Manuel düzeltme", a._show_manual_irs)
    a.irs_list = tk.Listbox(panel, height=1, bg=PANEL, fg=TEXT, selectbackground="#1B4158", relief="flat", highlightthickness=0, font=("Segoe UI", -12), exportselection=False)
    a.irs_list.pack(side="bottom", fill="x", padx=10, pady=(6, 0))
    a.irs_list.bind("<<ListboxSelect>>", a._on_irs_pick)
    a.irs_list.bind("<Double-1>", lambda e: a._take_irs())
    a.manual_window = tk.Toplevel(a)
    a.manual_window.title("Elle irsaliye eşleştir")
    a.manual_window.geometry("560x220")
    a.manual_window.configure(bg=PANEL)
    a.manual_window.protocol("WM_DELETE_WINDOW", a.manual_window.withdraw)
    a.manual_window.withdraw()
    label(a.manual_window, "İrsaliye numarası", 16, bold=True).pack(anchor="w", padx=20, pady=(18, 8))
    a.irs_manual = tk.Entry(a.manual_window, bg=HEADER, fg=TEXT, insertbackground=TEXT, font=("Segoe UI", -16), relief="flat")
    a.irs_manual.pack(fill="x", padx=20, ipady=8)
    a.irs_manual.bind("<Return>", lambda e: a._save_manual_irs())
    a.btn_irs_save = button(a.manual_window, "Kaydet", a._save_manual_irs, "primary")
    a.btn_irs_save.pack_configure(side="top", anchor="e", padx=20, pady=10)
    label(a.manual_window, textvariable=a.irs_var, size=12, color=MUTED, wraplength=500).pack(fill="x", padx=20)


def resize(a):
    compact = a.winfo_width() < 1450
    a.font_plate.configure(size=-34 if compact else -48)
    a.weight_font.configure(size=-40 if compact else -58)
    if hasattr(a, "gate_lbl"):
        wrap = max(240, a.side_panel.winfo_width()-50)
        a.gate_lbl.configure(wraplength=wrap, font=("Segoe UI", -18 if compact else -20, "bold"))
        a.gate_sub_lbl.configure(wraplength=wrap)


def refresh_steps(a, visit=None):
    if not hasattr(a, "step_labels"):
        return
    v = visit or {}
    plate = str(v.get("plate") or a._scale.get("selected_plate") or a.plate_var.get() or "")
    detected = plate not in {"", "—", "—  —  —", "TANIMSIZ"}
    weighed = v.get("full_weight") is not None or bool(a._scale.get("seated"))
    matched = bool(v.get("irsaliye_no") or v.get("irsaliye_id"))
    for widget, done, yes, no in zip(a.step_labels, (detected, weighed, matched),
                                    ("Plaka okundu", "Tartım tamamlandı", "İrsaliye eşleşti"),
                                    ("Plaka bekleniyor", "Tartım bekleniyor", "İrsaliye bekleniyor")):
        widget.configure(text=("✓  " + yes if done else "○  " + no), fg=GREEN if done else MUTED)
    stable = a.running and a._scale.get("ok") is not False and a._scale.get("seated")
    a.stable_var.set("✓ Tartım stabil" if stable else "Tartım bekleniyor")
    a.stable_label.configure(fg=GREEN if stable else MUTED)


def gate_style(a, *args):
    if not hasattr(a, "gate_box"):
        return
    title = a.gate_title.get()
    good = "GEÇEBİLİR" in title or "TAMAMLANDI" in title
    bg = "#10372F" if good else "#152E3E"
    a.gate_box.configure(fg_color=bg, border_color="#287B55" if good else EDGE)
    a.gate_lbl.configure(bg=bg)
    a.gate_sub_lbl.configure(bg=bg)
