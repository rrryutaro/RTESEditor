"""
Launcher — RTESEditor 管理ランチャー
  - サブフォルダを自動スキャンしてツールを検出
  - Python直実行 / EXE実行を1クリックで起動
  - 共通UI仕様: タイトルバー非表示, Alt+Tab表示, ドラッグ移動,
               リサイズグリップ, 右クリックメニュー, 位置保存
"""

import tkinter as tk
import json, os, sys, glob, subprocess, ctypes, ctypes.wintypes, re

# ─── ウィンドウスタイル定数 ──────────────────────────────────────────
GWL_EXSTYLE      = -20
WS_EX_APPWINDOW  = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080

MIN_W, MIN_H = 300, 140

# ─── バージョン定義箇所（ツール名 → (ファイル名, 行番号)）───────────────────
VERSION_HINTS: dict[str, tuple[str, int]] = {
    "RTESEditor": ("main.py", 1),
}

# ─── エントリポイントオーバーライド ─────────────────────────────────────
ENTRY_OVERRIDES: dict[str, tuple[str, ...]] = {}

# ─── パス設定 ────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "launcher_settings.json")

DEFAULT_CONFIG = {
    "x": 200, "y": 200,
    "width": 320, "height": 160,
    "topmost": True,
}

# ─── カラーパレット ──────────────────────────────────────────────────
BG    = "#0D1B2A"
BG2   = "#1C2E3F"
FG    = "#C9D1E0"
FG2   = "#7A8899"
BTN   = "#1E3A4A"
BTN_H = "#2E5068"
SEP   = "#1E3A4A"


# ─── ユーティリティ ──────────────────────────────────────────────────

def _is_on_any_monitor(x: int, y: int, w: int = 1, h: int = 1) -> bool:
    try:
        MONITOR_DEFAULTTONULL = 0
        pt = ctypes.wintypes.POINT(x + w // 2, y + h // 2)
        return ctypes.windll.user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONULL) != 0
    except Exception:
        return True


def _read_version(tool_dir: str, tool_name: str) -> str | None:
    hint = VERSION_HINTS.get(tool_name)
    if not hint:
        return None
    file_name, line_no = hint
    file_path = os.path.join(tool_dir, file_name)
    try:
        with open(file_path, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) >= line_no:
            m = re.match(r'\s*["\']version["\']\s*:\s*["\']([^"\']+)["\']', lines[line_no - 1])
            if m:
                return m.group(1)
            m = re.match(r'\s*VERSION\s*=\s*["\']([^"\']+)["\']', lines[line_no - 1])
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                merged = DEFAULT_CONFIG.copy()
                merged.update(json.load(f))
                return merged
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"設定保存エラー: {e}")


def discover_tools(base_dir: str) -> list[dict]:
    """*.spec があるサブフォルダをツールとして自動検出"""
    tools = []
    skip = {".", "Document", "build", "dist", "__pycache__", ".git", "i18n"}
    try:
        entries = sorted(os.scandir(base_dir), key=lambda e: e.name)
    except Exception:
        return tools

    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name in skip:
            continue

        folder = entry.path
        spec_files = glob.glob(os.path.join(folder, "*.spec"))
        if not spec_files:
            continue

        py_files = [f for f in glob.glob(os.path.join(folder, "*.py"))
                    if not os.path.basename(f).startswith("_")]
        if not py_files:
            continue

        spec_base = os.path.splitext(os.path.basename(spec_files[0]))[0].lower()
        main_py = py_files[0]
        for pf in py_files:
            if os.path.splitext(os.path.basename(pf))[0].lower() == spec_base:
                main_py = pf
                break

        override = ENTRY_OVERRIDES.get(entry.name)
        if override:
            override_path = os.path.join(folder, *override)
            if os.path.exists(override_path):
                main_py = override_path

        exe_files = glob.glob(os.path.join(folder, "dist", "*.exe"))
        main_exe = exe_files[0] if exe_files else None

        bat_path = os.path.join(folder, "build_exe.bat")
        build_bat = bat_path if os.path.exists(bat_path) else None

        version = _read_version(folder, entry.name)
        tools.append({
            "name": entry.name,
            "py":   main_py,
            "exe":  main_exe,
            "bat":  build_bat,
            "version": version,
        })

    return tools


# ─── ランチャー本体 ──────────────────────────────────────────────────

class LauncherApp:
    _IDX_TOPMOST = 1

    def __init__(self):
        self._closing = False
        self.cfg = load_config()
        self.root = tk.Tk()
        self._setup_window()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(10, self._set_appwindow)
        self.root.mainloop()

    # ─── ウィンドウセットアップ ──────────────────────────────────
    def _setup_window(self):
        self.root.title("RTESEditor Launcher")
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", self.cfg["topmost"])
        x, y = self.cfg["x"], self.cfg["y"]
        w, h = self.cfg["width"], self.cfg["height"]
        if not _is_on_any_monitor(x, y, w, h):
            x, y = 200, 200
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(MIN_W, MIN_H)

    # ─── UI構築 ──────────────────────────────────────────────────
    def _build_ui(self):
        # ヘッダー（タイトル兼ドラッグ領域）
        header = tk.Frame(self.root, bg=BG2, height=32)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="⚔  RTESEditor", bg=BG2, fg=FG,
            font=("メイリオ", 11, "bold"), anchor="w"
        ).pack(side="left", padx=10)
        header.bind("<ButtonPress-1>", self._start_drag)
        header.bind("<B1-Motion>",     self._on_drag)
        header.bind("<Button-3>",      self._show_menu)

        # ツール一覧エリア
        self.tools_frame = tk.Frame(self.root, bg=BG)
        self.tools_frame.pack(fill="both", expand=True, padx=8, pady=6)

        self.tools = discover_tools(BASE_DIR)
        self._render_tools()

        # 右クリックメニュー・リサイズグリップ
        self._build_menu()
        self._build_resize_grip()
        self.root.bind("<Button-3>", self._show_menu)

    def _render_tools(self):
        for w in self.tools_frame.winfo_children():
            w.destroy()

        if not self.tools:
            tk.Label(
                self.tools_frame, text="ツールが見つかりません",
                bg=BG, fg=FG2, font=("メイリオ", 10)
            ).pack(pady=20)
            return

        for i, tool in enumerate(self.tools):
            row = tk.Frame(self.tools_frame, bg=BG)
            row.pack(fill="x", pady=2)
            row.bind("<Button-3>", self._show_menu)

            # ツール名・バージョン
            name_cell = tk.Frame(row, bg=BG)
            name_cell.pack(side="left")
            tk.Label(
                name_cell, text=tool["name"], bg=BG, fg=FG,
                font=("メイリオ", 10), anchor="w", width=16
            ).pack(anchor="w", pady=(1, 0))
            ver = tool.get("version")
            if ver:
                tk.Label(
                    name_cell, text=f"v{ver}", bg=BG, fg=FG2,
                    font=("メイリオ", 7), anchor="w", width=16
                ).pack(anchor="w", pady=(0, 1))

            # ▶ Python
            tk.Button(
                row, text="▶ Python", bg=BTN, fg=FG,
                font=("メイリオ", 9), relief="flat", cursor="hand2",
                activebackground=BTN_H, activeforeground=FG, padx=6, pady=3,
                command=lambda t=tool: self._run_python(t)
            ).pack(side="left", padx=(4, 2))

            # ▶ EXE
            has_exe = bool(tool["exe"] and os.path.exists(tool["exe"]))
            tk.Button(
                row, text="▶ EXE",
                bg=BTN if has_exe else BG2,
                fg=FG if has_exe else FG2,
                font=("メイリオ", 9), relief="flat",
                cursor="hand2" if has_exe else "arrow",
                activebackground=BTN_H, activeforeground=FG,
                padx=6, pady=3,
                state="normal" if has_exe else "disabled",
                command=lambda t=tool: self._run_exe(t)
            ).pack(side="left", padx=2)

            # 🔨 Build
            has_bat = bool(tool.get("bat"))
            tk.Button(
                row, text="🔨 Build",
                bg=BTN if has_bat else BG2,
                fg=FG if has_bat else FG2,
                font=("メイリオ", 9), relief="flat",
                cursor="hand2" if has_bat else "arrow",
                activebackground=BTN_H, activeforeground=FG,
                padx=6, pady=3,
                state="normal" if has_bat else "disabled",
                command=lambda t=tool: self._run_build(t)
            ).pack(side="left", padx=2)

            if i < len(self.tools) - 1:
                tk.Frame(self.tools_frame, bg=SEP, height=1).pack(fill="x", pady=1)

    # ─── 起動 ────────────────────────────────────────────────────
    def _run_python(self, tool: dict):
        try:
            subprocess.Popen(
                [sys.executable, tool["py"]],
                cwd=os.path.dirname(tool["py"])
            )
        except Exception as e:
            print(f"Python起動エラー [{tool['name']}]: {e}")

    def _run_exe(self, tool: dict):
        if not tool["exe"] or not os.path.exists(tool["exe"]):
            return
        try:
            subprocess.Popen([tool["exe"]], cwd=os.path.dirname(tool["exe"]))
        except Exception as e:
            print(f"EXE起動エラー [{tool['name']}]: {e}")

    def _run_build(self, tool: dict):
        bat = tool.get("bat")
        if not bat or not os.path.exists(bat):
            return
        try:
            proc = subprocess.Popen(
                ["cmd", "/c", bat],
                cwd=os.path.dirname(bat),
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            self._poll_build(proc)
        except Exception as e:
            print(f"ビルドエラー [{tool['name']}]: {e}")

    def _poll_build(self, proc):
        if proc.poll() is None:
            self.root.after(1000, lambda: self._poll_build(proc))
        else:
            self._rescan()

    # ─── 右クリックメニュー ──────────────────────────────────────
    def _build_menu(self):
        M = "#1C1C2E"; FG_M = "#C9D1E0"; HL = "#2E2E46"
        self.menu = tk.Menu(
            self.root, tearoff=0,
            bg=M, fg=FG_M, activebackground=HL,
            activeforeground=FG_M, font=("メイリオ", 10), bd=0
        )
        self.menu.add_command(label="🔄  ツール再スキャン", command=self._rescan)
        self.menu.add_command(label=self._topmost_label(), command=self._toggle_topmost)
        self.menu.add_separator()
        self.menu.add_command(label="─  最小化", command=self._minimize)
        self.menu.add_command(label="✖  終了",   command=self._on_close)

    def _topmost_label(self) -> str:
        return "📌  常に最前面" + ("  ✔" if self.cfg["topmost"] else "")

    def _show_menu(self, event):
        if self._closing:
            return
        try:
            self.root.focus_force()
            self.menu.tk_popup(event.x_root, event.y_root)
        except Exception:
            pass

    def _toggle_topmost(self):
        self.cfg["topmost"] = not self.cfg["topmost"]
        self.root.attributes("-topmost", self.cfg["topmost"])
        self.menu.entryconfig(self._IDX_TOPMOST, label=self._topmost_label())
        save_config(self.cfg)

    def _rescan(self):
        self.tools = discover_tools(BASE_DIR)
        self._render_tools()

    # ─── ドラッグ移動 ────────────────────────────────────────────
    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    # ─── リサイズグリップ ─────────────────────────────────────────
    def _build_resize_grip(self):
        grip = tk.Canvas(
            self.root, width=16, height=16,
            bg=BG, highlightthickness=0, cursor="size_nw_se"
        )
        grip.place(relx=1.0, rely=1.0, anchor="se")
        for i in range(3):
            offset = 4 + i * 4
            grip.create_line(16, offset, offset, 16, fill="#334455", width=1)
        grip.bind("<ButtonPress-1>", self._resize_start)
        grip.bind("<B1-Motion>",     self._resize_move)

    def _resize_start(self, event):
        self._resize_x = event.x_root
        self._resize_y = event.y_root
        self._resize_w = self.root.winfo_width()
        self._resize_h = self.root.winfo_height()

    def _resize_move(self, event):
        dw = event.x_root - self._resize_x
        dh = event.y_root - self._resize_y
        new_w = max(MIN_W, self._resize_w + dw)
        new_h = max(MIN_H, self._resize_h + dh)
        self.cfg["width"]  = new_w
        self.cfg["height"] = new_h
        self.root.geometry(f"{new_w}x{new_h}")

    # ─── Alt+Tab 表示 ────────────────────────────────────────────
    def _set_appwindow(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    # ─── 最小化・終了 ────────────────────────────────────────────
    def _minimize(self):
        self.root.overrideredirect(False)
        self.root.iconify()

        def on_restore(event):
            self.root.overrideredirect(True)
            self.root.after(10, self._set_appwindow)
            self.root.unbind("<Map>")

        self.root.bind("<Map>", on_restore)

    def _on_close(self):
        self._closing = True
        self.cfg["x"] = self.root.winfo_x()
        self.cfg["y"] = self.root.winfo_y()
        self.cfg["width"]  = self.root.winfo_width()
        self.cfg["height"] = self.root.winfo_height()
        save_config(self.cfg)
        try:
            self.menu.unpost()
        except Exception:
            pass
        self.root.destroy()


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    LauncherApp()
