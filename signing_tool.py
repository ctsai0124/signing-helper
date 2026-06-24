# -*- coding: utf-8 -*-
"""
公文簽核快速貼上工具 (signing_tool.py)

功能：
  1. 按全域快捷鍵（預設 Ctrl+Alt+S），在螢幕上拖曳框選一個範圍
  2. 用 Windows 內建 OCR 辨識框選範圍裡的文字（免安裝、離線、不需 AI）
  3. 依「關鍵字 → 自動貼出」規則比對，命中就把對應文字放進剪貼簿
  4. 你只要在要簽核的欄位按 Ctrl+V 貼上即可
  5. 設定視窗可自訂規則，全部存在 rules.json，可直接分享給朋友

需要的套件：
  pip install pillow pyperclip keyboard winocr
（winocr 會使用 Windows 內建 OCR，需先安裝「中文(繁體)」語言的 OCR 功能）
"""

import os
import re
import sys
import json
import ctypes
import asyncio
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# ---- 相依套件（缺少時給友善提示，不直接崩潰）----
try:
    import pyperclip
except Exception:
    pyperclip = None

try:
    import keyboard
except Exception:
    keyboard = None

try:
    from PIL import Image, ImageGrab, ImageOps, ImageFilter
except Exception:
    Image = None
    ImageGrab = None
    ImageOps = None
    ImageFilter = None

# Windows 內建 OCR
try:
    import winocr
    HAS_OCR = True
except Exception:
    winocr = None
    HAS_OCR = False

# 記錄最近一次 OCR 失敗的原因，方便診斷
LAST_OCR_ERROR = ""


# ============================================================
# 設定檔處理
# ============================================================
def set_dpi_aware():
    """讓座標與實際像素一致，避免高 DPI 縮放造成擷取位置偏移。"""
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def virtual_screen():
    """回傳整個虛擬桌面（含所有螢幕）的範圍 (left, top, width, height)。"""
    try:
        u = ctypes.windll.user32
        left = u.GetSystemMetrics(76)    # SM_XVIRTUALSCREEN
        top = u.GetSystemMetrics(77)     # SM_YVIRTUALSCREEN
        width = u.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        height = u.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        if width > 0 and height > 0:
            return left, top, width, height
    except Exception:
        pass
    return 0, 0, 0, 0


def app_dir():
    """回傳程式所在資料夾（打包成 exe 後也正確），rules.json 會放這裡。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


RULES_PATH = os.path.join(app_dir(), "rules.json")

DEFAULT_CONFIG = {
    "hotkey": "ctrl+alt+q",
    "phrase_hotkey": "ctrl+alt+w",
    "auto_paste": False,
    "paste_delay": 500,
    "ocr_lang": "zh-Hant",
    "rules": [
        {"keywords": ["出席", "參加", "請假"], "output": "奉核後予以公假登記"},
        {"keywords": ["知悉"], "output": "知悉"},
        {"keywords": ["擬辦"], "output": "如擬"},
        {"keywords": ["請核示"], "output": "可"},
        {"keywords": ["請查照"], "output": "閱"},
    ],
}


def load_config():
    if os.path.exists(RULES_PATH):
        try:
            with open(RULES_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            cfg.setdefault("hotkey", "ctrl+alt+q")
            cfg.setdefault("phrase_hotkey", "ctrl+alt+w")
            cfg.setdefault("auto_paste", False)
            cfg.setdefault("paste_delay", 500)
            cfg.setdefault("ocr_lang", "zh-Hant")
            cfg.setdefault("rules", [])
            # 兼容：若 keywords 寫成字串，轉成清單
            for r in cfg["rules"]:
                if isinstance(r.get("keywords"), str):
                    r["keywords"] = [r["keywords"]]
            return cfg
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷貝預設值


def save_config(cfg):
    with open(RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ============================================================
# 詞庫（常用擬辦用語）處理
# ============================================================
PHRASES_PATH = os.path.join(app_dir(), "phrases.json")

DEFAULT_PHRASES = [
    "陳閱後文存。",
    "依來文辦理。",
    "陳閱後公告本校網站周知。",
    "陳閱後公告本校網站周知，有意願參加研習同仁，奉核後公假登記課務自理。",
    "陳閱後於晨會宣導。",
    "陳閱後於晨會宣導，並公告校網周知。",
    "奉核後請以公假登記前往。",
    "奉核後請以公假登記線上研習。",
    "奉核後請以公(差)假登記前往。",
    "文會幹事、校護及主計知照。",
    "奉核後提會辦理敘獎事宜。",
    "提會辦理敘獎事宜。",
    "本校無缺額，文存。",
    "奉核後請以公假登記前往與會。",
    "請依高雄市立各級學校教師擔任運動競賽裁判及工作人員公假核給要點，"
    "一學期不逾20日為限，奉核後以公假登記前往。",
]


def load_phrases():
    if os.path.exists(PHRASES_PATH):
        try:
            with open(PHRASES_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [str(p) for p in data if str(p).strip()]
        except Exception:
            pass
    save_phrases(DEFAULT_PHRASES)
    return list(DEFAULT_PHRASES)


def save_phrases(phrases):
    try:
        with open(PHRASES_PATH, "w", encoding="utf-8") as f:
            json.dump(phrases, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ============================================================
# 自動學習記憶（記住常見的 OCR 誤讀 → 正確批示）
# ============================================================
LEARNED_PATH = os.path.join(app_dir(), "learned.json")


def load_learned():
    if os.path.exists(LEARNED_PATH):
        try:
            with open(LEARNED_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass
    return {}


def save_learned(d):
    try:
        with open(LEARNED_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def parse_keywords(s):
    """把使用者輸入的多個關鍵字（用 、 , ， / 空白 分隔）拆成清單。"""
    parts = re.split(r"[、,，/\s]+", s.strip())
    return [p for p in parts if p]


# ============================================================
# OCR 與規則比對
# ============================================================
def _resample():
    try:
        return Image.Resampling.LANCZOS
    except AttributeError:
        return Image.LANCZOS


def make_variants(pil_img):
    """產生多種前處理版本（不同放大倍率／灰階），提升辨識命中率。"""
    if pil_img is None:
        return []
    if Image is None:
        return [pil_img]
    variants = []
    w, h = pil_img.size
    longest = max(w, h) or 1
    rs = _resample()
    for target in (2400, 3400):
        ratio = min(target / longest, 6.0) if longest < target else 1.0
        base = pil_img
        if ratio > 1.0:
            base = pil_img.resize(
                (max(1, int(w * ratio)), max(1, int(h * ratio))), rs)
        if ImageOps is not None:
            try:
                g = ImageOps.autocontrast(ImageOps.grayscale(base))
                if ImageFilter is not None:
                    g = g.filter(ImageFilter.SHARPEN)
                variants.append(g)
            except Exception:
                variants.append(base)
        else:
            variants.append(base)
    # 再加一版：大倍率的原彩圖（彩底文字有時較好）
    ratio = min(3400 / longest, 6.0) if longest < 3400 else 1.0
    if ratio > 1.0:
        variants.append(pil_img.resize(
            (max(1, int(w * ratio)), max(1, int(h * ratio))), rs))
    return variants or [pil_img]


def ocr_one(img, lang="zh-Hant"):
    """對單一已處理圖片做 Windows OCR，回傳純文字。"""
    global LAST_OCR_ERROR
    if not HAS_OCR or img is None:
        LAST_OCR_ERROR = "winocr 未安裝或圖片為空"
        return ""

    async def _recognize(im, lg):
        if lg is None:
            return await winocr.recognize_pil(im)
        return await winocr.recognize_pil(im, lg)

    candidates = ["zh-TW", "zh-Hant", "zh-Hant-TW", lang]
    seen = []
    errors = []
    for lg in candidates:
        if lg in seen:
            continue
        seen.append(lg)
        try:
            result = asyncio.run(_recognize(img, lg))
            txt = (result.text or "").strip()
            if txt:
                return txt
        except Exception as e:
            errors.append(f"{lg}: {type(e).__name__}: {e}")
            continue
    LAST_OCR_ERROR = " | ".join(errors) if errors else "OCR 回傳空白（沒讀到字）"
    return ""


def _edit_le1(a, b):
    """兩字串編輯距離是否 <= 1。"""
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if a == b:
        return True
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = j = diff = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            diff += 1
            if diff > 1:
                return False
            if la == lb:
                i += 1
                j += 1
            else:
                j += 1
    diff += (lb - j) + (la - i)
    return diff <= 1


def _fuzzy_find(text, keyword):
    """在 text 中找出與 keyword 差一字以內的片段，回傳該片段或 None。"""
    L = len(keyword)
    if L < 2:
        return None
    for wlen in (L, L - 1, L + 1):
        if wlen < 1 or wlen > len(text):
            continue
        for i in range(0, len(text) - wlen + 1):
            seg = text[i:i + wlen]
            if _edit_le1(seg, keyword):
                return seg
    return None


def match_output(text, rules, learned=None):
    """
    比對順序：學過的別名 → 規則關鍵字（完全） → 模糊比對（差一字）。
    回傳 (要貼出的文字, 命中關鍵字, 要學起來的別名或None)。
    """
    cleaned = "".join(text.split())
    if not cleaned:
        return None, None, None
    # 1) 學過的誤讀別名（完全包含）
    if learned:
        for alias, out in learned.items():
            if alias and alias in cleaned:
                return out, alias, None
    # 2) 規則關鍵字（完全包含）
    for rule in rules:
        for kw in rule.get("keywords", []):
            kw = kw.strip()
            if kw and kw in cleaned:
                return rule.get("output", ""), kw, None
    # 3) 模糊比對（差一字），命中就回傳並標記要學的別名
    for rule in rules:
        out = rule.get("output", "")
        for kw in rule.get("keywords", []):
            kw = kw.strip()
            if not kw:
                continue
            seg = _fuzzy_find(cleaned, kw)
            if seg:
                learn = None if seg == kw else (seg, out)
                return out, kw, learn
    return None, None, None


# ============================================================
# 螢幕框選的半透明覆蓋層
# ============================================================
class RegionSelector:
    def __init__(self, root, on_done):
        self.on_done = on_done
        self.vx, self.vy, vw, vh = virtual_screen()
        self.top = tk.Toplevel(root)
        if vw > 0 and vh > 0:
            # 覆蓋整個虛擬桌面（橫跨所有螢幕）
            self.top.overrideredirect(True)
            self.top.geometry(f"{vw}x{vh}+{self.vx}+{self.vy}")
        else:
            # 取不到虛擬桌面資訊時，退回單螢幕全螢幕
            self.top.attributes("-fullscreen", True)
        self.top.attributes("-alpha", 0.30)
        self.top.attributes("-topmost", True)
        self.top.configure(bg="black")
        self.canvas = tk.Canvas(self.top, cursor="cross", bg="black",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.start_x = self.start_y = 0
        self.rect = None
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.top.bind("<Escape>", lambda e: self.finish(None))
        self.top.focus_force()

    def on_press(self, e):
        self.start_x, self.start_y = e.x, e.y
        self.rect = self.canvas.create_rectangle(
            e.x, e.y, e.x, e.y, outline="#37a", width=2)

    def on_drag(self, e):
        if self.rect is not None:
            self.canvas.coords(self.rect, self.start_x, self.start_y, e.x, e.y)

    def on_release(self, e):
        x1, y1 = min(self.start_x, e.x), min(self.start_y, e.y)
        x2, y2 = max(self.start_x, e.x), max(self.start_y, e.y)
        if x2 - x1 < 5 or y2 - y1 < 5:
            self.finish(None)  # 點一下沒拖曳，視為取消
        else:
            # 換算成整個虛擬桌面的絕對座標（支援雙螢幕）
            bbox = (self.vx + x1, self.vy + y1, self.vx + x2, self.vy + y2)
            self.finish(bbox)

    def finish(self, bbox):
        try:
            self.top.destroy()
        except Exception:
            pass
        # 等覆蓋層真的關閉後再擷取，避免拍到半透明黑幕
        self.on_done.__self__.root.after(120, lambda: self.on_done(bbox))


# ============================================================
# 詞庫面板（美化版，Ctrl+Alt+W 叫出）
# ============================================================
class PhrasePanel:
    # 配色（與說明文件一致的公文風格）
    SEAL = "#b5402f"
    INK = "#1d1b18"
    SUB = "#8a8275"
    WHITE = "#ffffff"
    LINE = "#ece7dd"
    HOVER = "#f6efe9"
    FOOT = "#faf8f3"
    FONT = "Microsoft JhengHei"

    def __init__(self, app):
        self.app = app
        self.phrases = load_phrases()

        self.win = tk.Toplevel(app.root)
        self.win.title("公文擬辦詞庫")
        self.win.configure(bg=self.WHITE)
        self.win.geometry("470x560")
        self.win.minsize(380, 420)
        self.win.attributes("-topmost", True)
        self._center()

        self._build_header()
        self._build_search()
        self._build_list()
        self._build_footer()

        self.win.bind("<Escape>", lambda e: self.close())
        self.win.bind("<Return>", lambda e: self._select_first())
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self._wheel_bind()
        self._render()
        self.search_entry.focus_set()

    # ---------- 版面 ----------
    def _center(self):
        self.win.update_idletasks()
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        w, h = 470, 560
        self.win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")

    def _build_header(self):
        head = tk.Frame(self.win, bg=self.SEAL, height=52)
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(head, text="公文擬辦詞庫", bg=self.SEAL, fg="#fff",
                 font=(self.FONT, 14, "bold")).pack(side="left", padx=18)
        x = tk.Label(head, text="✕", bg=self.SEAL, fg="#fff",
                     font=(self.FONT, 13), cursor="hand2")
        x.pack(side="right", padx=16)
        x.bind("<Button-1>", lambda e: self.close())

    def _build_search(self):
        bar = tk.Frame(self.win, bg=self.WHITE)
        bar.pack(fill="x", padx=16, pady=(14, 6))
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            bar, textvariable=self.search_var, font=(self.FONT, 12),
            relief="solid", bd=1, highlightthickness=1,
            highlightcolor=self.SEAL, highlightbackground=self.LINE)
        self.search_entry.pack(fill="x", ipady=6, ipadx=4)
        self.search_var.trace_add("write", lambda *a: self._render())
        tk.Label(self.win, text="輸入關鍵字即時篩選（例：敘獎、晨會、裁判）",
                 bg=self.WHITE, fg=self.SUB, font=(self.FONT, 10),
                 anchor="w").pack(fill="x", padx=18)

    def _build_list(self):
        body = tk.Frame(self.win, bg=self.LINE)
        body.pack(fill="both", expand=True, padx=16, pady=10)
        self.canvas = tk.Canvas(body, bg=self.LINE, highlightthickness=0)
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=self.LINE)
        self._inner_id = self.canvas.create_window(
            (0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._inner_id, width=e.width))

    def _build_footer(self):
        foot = tk.Frame(self.win, bg=self.FOOT)
        foot.pack(fill="x", side="bottom")
        inner = tk.Frame(foot, bg=self.FOOT)
        inner.pack(fill="x", padx=14, pady=10)

        ttk.Checkbutton(inner, text="選取後自動貼上 (Ctrl+V)",
                        variable=self.app.autopaste_var,
                        command=self.app._toggle_autopaste).pack(side="left")
        ttk.Button(inner, text="匯入 .txt…",
                   command=self._import_txt).pack(side="right")

        addrow = tk.Frame(foot, bg=self.FOOT)
        addrow.pack(fill="x", padx=14, pady=(0, 10))
        self.add_var = tk.StringVar()
        tk.Entry(addrow, textvariable=self.add_var, font=(self.FONT, 11),
                 relief="solid", bd=1).pack(side="left", fill="x",
                                            expand=True, ipady=3)
        ttk.Button(addrow, text="＋ 新增片語",
                   command=self._add_phrase).pack(side="left", padx=(8, 0))

    # ---------- 清單渲染 ----------
    def _filtered(self):
        terms = self.search_var.get().split()
        if not terms:
            return list(self.phrases)
        return [p for p in self.phrases if all(t in p for t in terms)]

    def _render(self):
        for w in self.inner.winfo_children():
            w.destroy()
        items = self._filtered()
        if not items:
            tk.Label(self.inner, text="找不到符合的片語", bg=self.WHITE,
                     fg=self.SUB, font=(self.FONT, 11), pady=16).pack(
                fill="x")
            return
        for phrase in items:
            self._make_row(phrase)

    def _make_row(self, phrase):
        row = tk.Frame(self.inner, bg=self.WHITE, cursor="hand2")
        row.pack(fill="x", pady=(0, 1))
        lbl = tk.Label(row, text=phrase, bg=self.WHITE, fg=self.INK,
                       font=(self.FONT, 11), justify="left", anchor="w",
                       wraplength=360, padx=14, pady=9)
        lbl.pack(side="left", fill="x", expand=True)
        xb = tk.Label(row, text="✕", bg=self.WHITE, fg="#cfc7b8",
                      font=(self.FONT, 10), cursor="hand2", padx=12)
        xb.pack(side="right")

        def on_enter(_):
            row.config(bg=self.HOVER)
            lbl.config(bg=self.HOVER)
            xb.config(bg=self.HOVER)

        def on_leave(_):
            row.config(bg=self.WHITE)
            lbl.config(bg=self.WHITE)
            xb.config(bg=self.WHITE)

        for w in (row, lbl):
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
            w.bind("<Button-1>", lambda e, p=phrase: self._select(p))
        xb.bind("<Enter>", on_enter)
        xb.bind("<Leave>", on_leave)
        xb.bind("<Button-1>", lambda e, p=phrase: self._delete(p))

    # ---------- 動作 ----------
    def _select(self, phrase):
        if pyperclip:
            pyperclip.copy(phrase)
        self.close()
        preview = phrase if len(phrase) <= 16 else phrase[:16] + "…"
        self.app.status.set(f"已複製：{preview}")
        self.app._toast(f"已複製：{preview}")
        self.app._schedule_paste()

    def _select_first(self):
        items = self._filtered()
        if items:
            self._select(items[0])

    def _add_phrase(self):
        text = self.add_var.get().strip()
        if not text:
            return
        if text not in self.phrases:
            self.phrases.append(text)
            save_phrases(self.phrases)
        self.add_var.set("")
        self._render()

    def _delete(self, phrase):
        if phrase in self.phrases:
            self.phrases.remove(phrase)
            save_phrases(self.phrases)
            self._render()

    def _import_txt(self):
        path = filedialog.askopenfilename(
            parent=self.win, title="選擇詞庫文字檔",
            filetypes=[("文字檔", "*.txt"), ("所有檔案", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
        except Exception:
            try:
                with open(path, encoding="big5") as f:
                    lines = [ln.strip() for ln in f if ln.strip()]
            except Exception as e:
                messagebox.showerror("匯入失敗", str(e), parent=self.win)
                return
        added = 0
        for ln in lines:
            if ln not in self.phrases:
                self.phrases.append(ln)
                added += 1
        save_phrases(self.phrases)
        self._render()
        messagebox.showinfo("匯入完成", f"新增了 {added} 筆片語。", parent=self.win)

    # ---------- 捲動與關閉 ----------
    def _wheel_bind(self):
        def on_wheel(e):
            self.canvas.yview_scroll(int(-e.delta / 120), "units")
        self._on_wheel = on_wheel
        self.win.bind_all("<MouseWheel>", on_wheel)

    def close(self):
        try:
            self.win.unbind_all("<MouseWheel>")
        except Exception:
            pass
        self.app._phrase_panel = None
        try:
            self.win.destroy()
        except Exception:
            pass


# ============================================================
# 主程式（設定視窗 + 背景監聽）
# ============================================================
class App:
    def __init__(self):
        self.cfg = load_config()
        self.learned = load_learned()
        self._capturing = False
        self._phrase_panel = None

        self.root = tk.Tk()
        self.root.title("公文簽核快速貼上工具")
        self.root.geometry("580x560")
        self.root.minsize(540, 500)
        self.autopaste_var = tk.BooleanVar(value=bool(self.cfg.get("auto_paste", False)))

        self._build_ui()
        self._refresh_tree()
        self._register_hotkey()
        self._check_env()

    # ---------- 介面 ----------
    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="圈選辨識快捷鍵：").grid(row=0, column=0, sticky="w", pady=2)
        self.hotkey_var = tk.StringVar(value=self.cfg["hotkey"])
        ttk.Entry(top, textvariable=self.hotkey_var, width=15).grid(row=0, column=1, padx=6)
        ttk.Label(top, text="詞庫快捷鍵：").grid(row=1, column=0, sticky="w", pady=2)
        self.phrase_hotkey_var = tk.StringVar(
            value=self.cfg.get("phrase_hotkey", "ctrl+alt+w"))
        ttk.Entry(top, textvariable=self.phrase_hotkey_var, width=15).grid(
            row=1, column=1, padx=6)
        ttk.Button(top, text="套用快捷鍵", command=self._apply_hotkey).grid(
            row=0, column=2, rowspan=2, padx=6)
        ttk.Button(top, text="開啟詞庫", command=self._open_phrases).grid(
            row=0, column=3, rowspan=2, padx=2)
        ttk.Checkbutton(top, text="選取後自動貼上", variable=self.autopaste_var,
                        command=self._toggle_autopaste).grid(
            row=0, column=4, padx=10, sticky="w")
        ttk.Label(top, text="貼上時機：").grid(row=1, column=4, sticky="e", pady=2)
        self.delay_label_to_ms = {"馬上貼": 0, "延遲0.5秒": 500, "延遲2秒": 2000}
        cur_ms = int(self.cfg.get("paste_delay", 500))
        cur_label = next((k for k, v in self.delay_label_to_ms.items()
                          if v == cur_ms), "延遲0.5秒")
        self.delay_var = tk.StringVar(value=cur_label)
        delay_box = ttk.Combobox(top, textvariable=self.delay_var, width=10,
                                 state="readonly",
                                 values=list(self.delay_label_to_ms.keys()))
        delay_box.grid(row=1, column=5, padx=6, sticky="w")
        delay_box.bind("<<ComboboxSelected>>", lambda e: self._apply_delay())

        # 規則清單
        mid = ttk.Frame(self.root)
        mid.pack(fill="both", expand=True, **pad)
        cols = ("kw", "out")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", height=10)
        self.tree.heading("kw", text="辨識到的關鍵字（任一即觸發）")
        self.tree.heading("out", text="自動貼出")
        self.tree.column("kw", width=300)
        self.tree.column("out", width=200)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda e: self._edit_selected())

        # 規則操作按鈕
        btns = ttk.Frame(self.root)
        btns.pack(fill="x", **pad)
        ttk.Button(btns, text="編輯選取", command=self._edit_selected).pack(side="left")
        ttk.Button(btns, text="刪除選取", command=self._delete_selected).pack(side="left", padx=4)
        ttk.Button(btns, text="上移", command=lambda: self._move(-1)).pack(side="left")
        ttk.Button(btns, text="下移", command=lambda: self._move(1)).pack(side="left", padx=4)
        ttk.Label(btns, text="（雙擊規則也可編輯）",
                  foreground="#999").pack(side="left", padx=8)

        # 新增／編輯規則（同一區，不再彈出新視窗）
        self._edit_index = None
        self.add_frame = ttk.LabelFrame(self.root, text="新增規則")
        add = self.add_frame
        add.pack(fill="x", **pad)
        ttk.Label(add, text="關鍵字").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.new_kw = ttk.Entry(add, width=30)
        self.new_kw.grid(row=0, column=1, padx=6, pady=6)
        ttk.Label(add, text="（多個用、分隔）").grid(row=0, column=2, sticky="w")
        ttk.Label(add, text="自動貼出").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        self.new_out = ttk.Entry(add, width=30)
        self.new_out.grid(row=1, column=1, padx=6, pady=6)
        self.save_btn = ttk.Button(add, text="新增", command=self._save_rule)
        self.save_btn.grid(row=1, column=2, padx=6)
        self.cancel_btn = ttk.Button(add, text="取消編輯", command=self._cancel_edit)
        self.cancel_btn.grid(row=1, column=3, padx=4)
        self.cancel_btn.grid_remove()  # 編輯模式才顯示

        # 狀態列
        self.status = tk.StringVar(value="就緒")
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", side="bottom")
        ttk.Label(bar, textvariable=self.status, foreground="#555").pack(
            side="left", padx=12, pady=6)
        ttk.Label(bar, text="把視窗縮到最小即可，快捷鍵在背景仍有效",
                  foreground="#999").pack(side="right", padx=12)

    def _refresh_tree(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for r in self.cfg["rules"]:
            kw = "、".join(r.get("keywords", []))
            self.tree.insert("", "end", values=(kw, r.get("output", "")))

    # ---------- 規則編輯（同視窗） ----------
    def _save_rule(self):
        kws = parse_keywords(self.new_kw.get())
        out = self.new_out.get().strip()
        if not kws or not out:
            messagebox.showwarning("提醒", "關鍵字與自動貼出都要填。")
            return
        editing = self._edit_index
        if editing is None:
            self.cfg["rules"].append({"keywords": kws, "output": out})
            msg = "已新增規則並儲存"
        else:
            self.cfg["rules"][editing] = {"keywords": kws, "output": out}
            msg = "已更新規則並儲存"
        save_config(self.cfg)
        self._exit_edit_mode()
        self._refresh_tree()
        if editing is not None:
            children = self.tree.get_children()
            if editing < len(children):
                self.tree.selection_set(children[editing])
        self.status.set(msg)

    def _enter_edit_mode(self, idx):
        rule = self.cfg["rules"][idx]
        self._edit_index = idx
        self.new_kw.delete(0, "end")
        self.new_kw.insert(0, "、".join(rule.get("keywords", [])))
        self.new_out.delete(0, "end")
        self.new_out.insert(0, rule.get("output", ""))
        self.add_frame.configure(text="編輯規則（改完按更新）")
        self.save_btn.configure(text="更新")
        self.cancel_btn.grid()
        self.new_kw.focus_set()
        self.status.set("編輯中：改完按「更新」儲存")

    def _exit_edit_mode(self):
        self._edit_index = None
        self.new_kw.delete(0, "end")
        self.new_out.delete(0, "end")
        self.add_frame.configure(text="新增規則")
        self.save_btn.configure(text="新增")
        self.cancel_btn.grid_remove()

    def _cancel_edit(self):
        self._exit_edit_mode()
        self.status.set("已取消編輯")

    def _selected_index(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self.tree.index(sel[0])

    def _edit_selected(self):
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo("提醒", "請先點選一條規則再編輯。")
            return
        self._enter_edit_mode(idx)

    def _delete_selected(self):
        idx = self._selected_index()
        if idx is None:
            return
        del self.cfg["rules"][idx]
        save_config(self.cfg)
        self._refresh_tree()
        self.status.set("已刪除規則並儲存")

    def _move(self, delta):
        idx = self._selected_index()
        if idx is None:
            return
        new = idx + delta
        if new < 0 or new >= len(self.cfg["rules"]):
            return
        rules = self.cfg["rules"]
        rules[idx], rules[new] = rules[new], rules[idx]
        save_config(self.cfg)
        self._refresh_tree()
        item = self.tree.get_children()[new]
        self.tree.selection_set(item)

    # ---------- 快捷鍵 ----------
    def _apply_hotkey(self):
        self.cfg["hotkey"] = self.hotkey_var.get().strip() or "ctrl+alt+s"
        self.cfg["phrase_hotkey"] = (
            self.phrase_hotkey_var.get().strip() or "ctrl+alt+w")
        save_config(self.cfg)
        self._register_hotkey()

    def _register_hotkey(self):
        if keyboard is None:
            self.status.set("找不到 keyboard 套件，無法註冊快捷鍵")
            return
        try:
            keyboard.remove_all_hotkeys()
        except Exception:
            pass
        enabled = []
        try:
            keyboard.add_hotkey(self.cfg["hotkey"], self._trigger_capture)
            enabled.append(self.cfg["hotkey"])
        except Exception as e:
            self.status.set(f"圈選快捷鍵註冊失敗：{e}")
            return
        ph = self.cfg.get("phrase_hotkey", "ctrl+alt+w")
        try:
            keyboard.add_hotkey(ph, self._trigger_phrases)
            enabled.append(ph)
        except Exception:
            pass
        self.status.set("快捷鍵已啟用：" + " ／ ".join(enabled))

    # ---------- 詞庫面板 ----------
    def _trigger_phrases(self):
        self.root.after(0, self._open_phrases)

    def _open_phrases(self):
        if self._phrase_panel is not None:
            try:
                self._phrase_panel.win.lift()
                self._phrase_panel.win.attributes("-topmost", True)
                self._phrase_panel.search_entry.focus_set()
                return
            except Exception:
                self._phrase_panel = None
        self._phrase_panel = PhrasePanel(self)

    def _toggle_autopaste(self):
        self.cfg["auto_paste"] = bool(self.autopaste_var.get())
        save_config(self.cfg)

    def _apply_delay(self):
        ms = self.delay_label_to_ms.get(self.delay_var.get(), 500)
        self.cfg["paste_delay"] = ms
        save_config(self.cfg)

    def _schedule_paste(self):
        """依設定的延遲自動貼上（兩種快捷鍵共用）。"""
        if not self.cfg.get("auto_paste"):
            return
        delay = int(self.cfg.get("paste_delay", 500))
        self.root.after(max(0, delay), self._do_paste)

    def _do_paste(self):
        if keyboard is not None:
            try:
                keyboard.send("ctrl+v")
            except Exception:
                pass

    def _trigger_capture(self):
        # keyboard 的回呼在別的執行緒，要丟回主執行緒處理 tkinter
        self.root.after(0, self._start_capture)

    def _start_capture(self):
        if self._capturing:
            return
        if ImageGrab is None:
            self.status.set("找不到 Pillow，無法擷取螢幕")
            return
        self._capturing = True
        self.status.set("框選範圍中…（Esc 取消）")
        RegionSelector(self.root, self._handle_region)

    def _handle_region(self, bbox):
        if bbox is None:
            self._capturing = False
            self.status.set("已取消")
            return
        # 擷取與 OCR 放到背景執行緒，避免畫面卡住
        threading.Thread(target=self._process, args=(bbox,), daemon=True).start()

    def _process(self, bbox):
        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
            # 除錯：把框到的畫面存檔，方便診斷
            try:
                img.save(os.path.join(app_dir(), "last_capture.png"))
            except Exception:
                pass

            lang = self.cfg.get("ocr_lang", "zh-Hant")
            best_text = ""
            out = kw = learn = None
            # 多版本辨識：任一版命中規則就採用，否則保留最完整的辨識文字
            for variant in make_variants(img):
                txt = ocr_one(variant, lang)
                if not txt:
                    continue
                if len(txt) > len(best_text):
                    best_text = txt
                o, k, lr = match_output(txt, self.cfg["rules"], self.learned)
                if o:
                    out, kw, learn = o, k, lr
                    best_text = txt
                    break

            try:
                with open(os.path.join(app_dir(), "last_ocr.txt"),
                          "w", encoding="utf-8") as f:
                    f.write(best_text if best_text
                            else ("（沒讀到字）\nOCR 錯誤：" + LAST_OCR_ERROR))
            except Exception:
                pass

            # 自動學習：把這次靠模糊比對救回來的誤讀記起來
            learned_now = False
            if learn:
                alias, alias_out = learn
                if alias and alias not in self.learned:
                    self.learned[alias] = alias_out
                    save_learned(self.learned)
                    learned_now = True

            self.root.after(
                0, lambda: self._finish_process(out, kw, best_text, learned_now))
        except Exception as e:
            self.root.after(0, lambda: self._fail_process(str(e)))

    def _finish_process(self, out, kw, text, learned=False):
        self._capturing = False
        if out:
            if pyperclip:
                pyperclip.copy(out)
            note = "（已學起來）" if learned else ""
            tip = "（自動貼上中）" if self.cfg.get("auto_paste") else "（按 Ctrl+V 貼上）"
            self.status.set(f"命中「{kw}」→ 已複製：{out}{note}{tip}")
            self._toast(f"已複製：{out}")
            self._schedule_paste()
        elif text:
            preview = (text[:20] + "…") if len(text) > 20 else text
            self.status.set(f"沒有命中規則。辨識內容：{preview}")
            self._toast("沒有命中任何規則")
        else:
            self.status.set(f"OCR 沒讀到字：{LAST_OCR_ERROR}")
            self._toast("OCR 沒讀到字")

    def _fail_process(self, msg):
        self._capturing = False
        self.status.set(f"處理失敗：{msg}")

    # ---------- 提示小視窗 ----------
    def _toast(self, msg):
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg="#222")
        lbl = tk.Label(t, text=msg, bg="#222", fg="white",
                       font=("Microsoft JhengHei", 11), padx=16, pady=8)
        lbl.pack()
        t.update_idletasks()
        sw = t.winfo_screenwidth()
        sh = t.winfo_screenheight()
        w = t.winfo_width()
        h = t.winfo_height()
        t.geometry(f"+{sw - w - 30}+{sh - h - 60}")
        t.after(1800, t.destroy)

    # ---------- 環境檢查 ----------
    def _check_env(self):
        missing = []
        if pyperclip is None:
            missing.append("pyperclip")
        if keyboard is None:
            missing.append("keyboard")
        if ImageGrab is None:
            missing.append("pillow")
        if not HAS_OCR:
            missing.append("winocr（Windows 內建 OCR）")
        if missing:
            messagebox.showwarning(
                "缺少套件",
                "以下套件未安裝，部分功能無法使用：\n\n"
                + "、".join(missing)
                + "\n\n請執行：pip install pillow pyperclip keyboard winocr",
            )

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    set_dpi_aware()
    App().run()
