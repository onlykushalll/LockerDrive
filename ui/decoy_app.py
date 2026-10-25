"""
decoy_app.py — Kushal Studios Desktop Arcade Hub (v3.0 Pastel & Indigo Production Engine)

Engineered for zero-lag switching & smooth UX conditioning:
  * DOMS Pastel Sky / Lavender / Mauve light palette + Deep Indigo dark mode.
  * All 4 navigation views (Arcade, AI Chat, Stats, Settings) are pre-mounted
    once and cached in memory (<1ms tab switching).
  * Live instant search/filter bar in the Arcade Library + keyboard shortcuts
    (Ctrl+1..4 for tabs, Ctrl+L to clear chat, Escape to clear search).
  * Debounced 60 FPS volume slider + live Pygame subprocess completion watcher.
"""

import customtkinter as ctk
import subprocess
import os
import sys
import time
from tkinter import messagebox

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="ignore")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from engine import ai_regex
except Exception:
    ai_regex = None

try:
    from ui.games import GAMES
except Exception:
    GAMES = []

try:
    from ui import appdata
except Exception:
    class _AppDataStub:
        def __init__(self):
            self._settings = {"theme": "light", "volume": 80, "window": "980x650"}
            self._stats = {"games": {}, "chat_msgs": 0}
        def load_settings(self): return dict(self._settings)
        def save_settings(self, s): self._settings = dict(s)
        def load_stats(self): return {"games": {k: dict(v) for k, v in self._stats["games"].items()},
                                      "chat_msgs": self._stats["chat_msgs"]}
        def save_stats(self, s): self._stats = {"games": dict(s.get("games", {})),
                                                "chat_msgs": s.get("chat_msgs", 0)}
        def record_play(self, name):
            e = self._stats["games"].setdefault(name, {"plays": 0, "high": 0, "best": 0,
                                                       "wins": 0, "losses": 0, "last": ""})
            e["plays"] += 1
        def record_score(self, name, score, lower_better=False):
            e = self._stats["games"].setdefault(name, {"plays": 0, "high": 0, "best": 0,
                                                       "wins": 0, "losses": 0, "last": ""})
            if lower_better:
                if e["best"] == 0 or score < e["best"]: e["best"] = score
            elif score > e["high"]:
                e["high"] = score
        def record_win(self, name, win):
            e = self._stats["games"].setdefault(name, {"plays": 0, "high": 0, "best": 0,
                                                       "wins": 0, "losses": 0, "last": ""})
            if win: e["wins"] += 1
            else: e["losses"] += 1
        def touch_chat(self): self._stats["chat_msgs"] += 1
        def reset_stats(self): self._stats = {"games": {}, "chat_msgs": 0}
    appdata = _AppDataStub()

APP_TITLE = "Kushal Studios v3.0"
GREETING = "Hello! I am Kushal's Studio AI. Ask me about any of the 9 arcade games, coding tips, or studio commands!"
MAX_CHAT = 150

# DOMS Pastel Sky / Lavender / Mauve palette (Light) + Deep Indigo (Dark)
_ACCENT_PRIMARY = ("#5B50E6", "#6366F1")
_ACCENT_HOVER = ("#473CC9", "#4F46E5")
_CARD_BG = ("#FFFFFF", "#151824")
_CARD_BORDER = ("#DDD6FE", "#272C42")
_SIDEBAR_BG = ("#F3EFFA", "#0E1019")
_CONTENT_BG = ("#F9F7FD", "#090B11")
_NAV_ACTIVE_BG = ("#E0E7FF", "#1E2338")
_NAV_ACTIVE_TEXT = ("#4338CA", "#A5B4FC")
_NAV_IDLE_TEXT = ("#4B4463", "#94A3B8")
_MUTED_TEXT = ("#6E6687", "#64748B")

self_settings = appdata.load_settings()
ctk.set_appearance_mode(self_settings.get("theme", "light"))
ctk.set_default_color_theme("blue")


class DecoyApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        try:
            _base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            _ico = os.path.join(_base, "assets", "kushal_studios_icon.ico")
            if not os.path.exists(_ico):
                _ico = os.path.join(os.path.dirname(sys.executable), "assets", "kushal_studios_icon.ico")
            if os.path.exists(_ico):
                self.iconbitmap(_ico)
        except Exception:
            pass
        self.minsize(880, 570)
        self.configure(fg_color=_CONTENT_BG)

        self.settings = appdata.load_settings()
        try:
            self.geometry(self.settings.get("window", "980x650"))
        except Exception:
            self.geometry("980x650")

        self.chat_messages = []
        self.greeted = False
        self._last_launch = (None, 0.0)
        self._active_game_proc = None
        self._active_game_name = None
        self._current_tab = None
        self._vol_save_after_id = None

        self._pages = {}
        self._arcade_cards = {}
        self._arcade_card_frames = []
        self._stats_summary_labels = {}
        self._stats_row_labels = {}

        # Status bar
        bar = ctk.CTkFrame(self, height=28, corner_radius=0, fg_color=("#EDE9FE", "#0B0D14"))
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        self.status_bar = ctk.CTkLabel(
            bar, text=self._base_status(),
            font=ctk.CTkFont(size=11, weight="bold"), text_color=_MUTED_TEXT
        )
        self.status_bar.pack(side="left", padx=14)
        self.status_clock = ctk.CTkLabel(
            bar, text="", font=ctk.CTkFont(family="Consolas", size=11), text_color=_MUTED_TEXT
        )
        self.status_clock.pack(side="right", padx=14)

        # Sidebar
        self.sidebar = ctk.CTkFrame(
            self, width=220, corner_radius=0, fg_color=_SIDEBAR_BG,
            border_width=1, border_color=_CARD_BORDER
        )
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        brand_box = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand_box.pack(fill="x", padx=18, pady=(26, 20))
        ctk.CTkLabel(
            brand_box, text="🎮 Kushal Studios",
            font=ctk.CTkFont(size=20, weight="bold"), anchor="w"
        ).pack(fill="x")
        ctk.CTkLabel(
            brand_box, text="PASTEL ARCADE & AI SUITE v3.0",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=_ACCENT_PRIMARY, anchor="w"
        ).pack(fill="x", pady=(2, 0))

        self.nav_buttons = {}
        for name, text, cmd in [
            ("arcade",   "🕹️   Arcade Hub",    self._show_arcade),
            ("chat",     "🤖   Studio AI",     self._show_chat),
            ("stats",    "📊   Player Stats",  self._show_stats),
            ("settings", "⚙️   Preferences",   self._show_settings),
        ]:
            btn = ctk.CTkButton(
                self.sidebar, text=text, fg_color="transparent",
                text_color=_NAV_IDLE_TEXT, hover_color=_NAV_ACTIVE_BG,
                anchor="w", height=42, corner_radius=10,
                font=ctk.CTkFont(size=13, weight="bold"), command=cmd
            )
            btn.pack(fill="x", padx=12, pady=3)
            self.nav_buttons[name] = btn

        # Sidebar footer badge
        foot = ctk.CTkFrame(self.sidebar, fg_color=_CARD_BG, corner_radius=10, border_width=1, border_color=_CARD_BORDER)
        foot.pack(side="bottom", fill="x", padx=12, pady=14)
        ctk.CTkLabel(
            foot, text="● 100% Offline Engine",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=("#059669", "#10B981")
        ).pack(anchor="w", padx=12, pady=(8, 1))
        ctk.CTkLabel(
            foot, text="Ctrl+1..4 Quick Nav Ready",
            font=ctk.CTkFont(size=10), text_color=_MUTED_TEXT
        ).pack(anchor="w", padx=12, pady=(0, 8))

        # Main content host
        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color=_CONTENT_BG)
        self.content.pack(side="right", fill="both", expand=True)

        self._build_arcade_page()
        self._build_chat_page()
        self._build_stats_page()
        self._build_settings_page()

        # Global hotkeys for fast navigation
        self.bind("<Control-Key-1>", lambda e: self._show_arcade())
        self.bind("<Control-Key-2>", lambda e: self._show_chat())
        self.bind("<Control-Key-3>", lambda e: self._show_stats())
        self.bind("<Control-Key-4>", lambda e: self._show_settings())

        self._tick_clock()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._show_arcade()

        # Lift window above File Explorer and clear topmost after focus
        try:
            self.lift()
            self.attributes("-topmost", True)
            self.after(300, lambda: self.attributes("-topmost", False))
        except Exception:
            pass

    # ---------- shared helpers ----------

    def _base_status(self):
        if self._active_game_name:
            return f"🟢 Playing {self._active_game_name} — scores sync automatically on exit"
        engine = "AI Ready" if ai_regex is not None else "AI Offline"
        return f"Kushal Studios v3.0  •  {len(GAMES)} Arcade Titles  •  {engine}"

    def _set_status(self, text=None):
        self.status_bar.configure(text=text if text else self._base_status())

    def _tick_clock(self):
        try:
            self.status_clock.configure(text=time.strftime("%a %d %b  •  %H:%M:%S"))
            self.after(1000, self._tick_clock)
        except Exception:
            pass

    def _switch_page(self, name):
        if self._current_tab == name:
            return
        if self._current_tab and self._current_tab in self._pages:
            self._pages[self._current_tab].pack_forget()
        self._pages[name].pack(fill="both", expand=True)
        self._current_tab = name

        for k, btn in self.nav_buttons.items():
            if k == name:
                btn.configure(fg_color=_NAV_ACTIVE_BG, text_color=_NAV_ACTIVE_TEXT)
            else:
                btn.configure(fg_color="transparent", text_color=_NAV_IDLE_TEXT)

    # ---------- Page 1: Arcade ----------

    def _format_card_meta(self, game, entry):
        plays = entry.get("plays", 0)
        high = entry.get("high", 0)
        best = entry.get("best", 0)
        wins = entry.get("wins", 0)
        losses = entry.get("losses", 0)
        if plays > 0:
            if game["name"] == "Memory Match" and best:
                return f"Played {plays}x  •  Best: {best} moves"
            if game["name"] == "Tic-Tac-Toe":
                return f"Record: {wins}W / {losses}L  ({plays}x)"
            if high:
                return f"Played {plays}x  •  High: {high}"
            return f"Played {plays}x"
        return game.get("desc", "Click to launch").capitalize()

    def _build_arcade_page(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        self._pages["arcade"] = page

        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.pack(fill="x", padx=32, pady=(20, 6))
        ctk.CTkLabel(
            hdr, text="🎮 Arcade Library",
            font=ctk.CTkFont(size=24, weight="bold")
        ).pack(side="left")

        self.search_entry = ctk.CTkEntry(
            hdr, placeholder_text="🔍 Filter games...", width=210, height=32,
            corner_radius=10, font=ctk.CTkFont(size=12)
        )
        self.search_entry.pack(side="right")
        self.search_entry.bind("<KeyRelease>", self._filter_arcade_grid)
        self.search_entry.bind("<Escape>", lambda e: (self.search_entry.delete(0, "end"), self._filter_arcade_grid()))

        if not GAMES:
            ctk.CTkLabel(
                page, text="⚠️ Games engine failed to load. Try restarting the application.",
                font=ctk.CTkFont(size=14), text_color=_MUTED_TEXT
            ).pack(pady=80)
            return

        self.arcade_grid = ctk.CTkFrame(page, fg_color="transparent")
        self.arcade_grid.pack(fill="both", expand=True, padx=26, pady=8)
        for c in range(3):
            self.arcade_grid.grid_columnconfigure(c, weight=1, uniform="arcade_col")
        for r in range(3):
            self.arcade_grid.grid_rowconfigure(r, weight=1, uniform="arcade_row")

        stats = appdata.load_stats()
        games_stats = stats.get("games", {})

        for i, game in enumerate(GAMES):
            r, c = divmod(i, 3)
            card = ctk.CTkFrame(
                self.arcade_grid, fg_color=_CARD_BG, corner_radius=14,
                border_width=1, border_color=_CARD_BORDER
            )
            card.grid(row=r, column=c, padx=7, pady=7, sticky="nsew")

            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=14, pady=(12, 2))
            ctk.CTkLabel(
                top, text=f"{game['icon']}  {game['name']}",
                font=ctk.CTkFont(size=15, weight="bold"), anchor="w"
            ).pack(side="left")

            entry = games_stats.get(game["name"], {})
            meta_lbl = ctk.CTkLabel(
                card, text=self._format_card_meta(game, entry),
                font=ctk.CTkFont(size=11), text_color=_MUTED_TEXT, anchor="w"
            )
            meta_lbl.pack(fill="x", padx=14, pady=(2, 8))
            self._arcade_cards[game["name"]] = (game, meta_lbl)
            self._arcade_card_frames.append((i, game, card))

            play_btn = ctk.CTkButton(
                card, text="Play Now ▶", height=32, corner_radius=9,
                fg_color=_ACCENT_PRIMARY, hover_color=_ACCENT_HOVER,
                font=ctk.CTkFont(size=12, weight="bold"),
                command=lambda idx=i: self._launch_game(idx)
            )
            play_btn.pack(fill="x", padx=14, pady=(2, 12), side="bottom")

    def _filter_arcade_grid(self, event=None):
        query = (self.search_entry.get() or "").strip().lower()
        visible_idx = 0
        for orig_i, game, card in self._arcade_card_frames:
            hay = f"{game['name']} {game.get('desc', '')}".lower()
            if not query or query in hay:
                r, c = divmod(visible_idx, 3)
                card.grid(row=r, column=c, padx=7, pady=7, sticky="nsew")
                visible_idx += 1
            else:
                card.grid_remove()

    def _refresh_arcade_cards(self):
        stats = appdata.load_stats()
        games_stats = stats.get("games", {})
        for gname, (game, lbl) in self._arcade_cards.items():
            lbl.configure(text=self._format_card_meta(game, games_stats.get(gname, {})))

    def _show_arcade(self):
        self._refresh_arcade_cards()
        self._switch_page("arcade")
        self._set_status()

    def _launch_game(self, idx):
        last_idx, last_t = self._last_launch
        now = time.monotonic()
        if last_idx == idx and now - last_t < 1.2:
            return
        self._last_launch = (idx, now)

        if self._active_game_proc is not None and self._active_game_proc.poll() is None:
            self._set_status(f"⚠️ {self._active_game_name} is already running!")
            return

        game = GAMES[idx]
        appdata.record_play(game["name"])
        self._refresh_arcade_cards()

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        launcher = os.path.join(project_root, "launch_game.py")
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--launch-game", str(idx)]
        elif os.path.isfile(launcher):
            cmd = [sys.executable, launcher, str(idx)]
        else:
            cmd = [sys.executable, os.path.join(project_root, "main.py"), "--launch-game", str(idx)]
        try:
            self._active_game_proc = subprocess.Popen(
                cmd,
                cwd=project_root,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            self._active_game_name = game["name"]
            self._set_status()
            self.after(500, self._poll_active_game)
        except Exception:
            self._active_game_proc = None
            self._active_game_name = None
            self._set_status("Could not launch the game :(")

    def _poll_active_game(self):
        if self._active_game_proc is None:
            return
        if self._active_game_proc.poll() is None:
            self.after(500, self._poll_active_game)
        else:
            self._active_game_proc = None
            self._active_game_name = None
            self._refresh_arcade_cards()
            self._refresh_stats_page()
            self._set_status()

    def _terminate_active_game(self):
        """Terminate running game subprocess if any, avoiding orphaned windows."""
        proc = self._active_game_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass
        self._active_game_proc = None
        self._active_game_name = None

    # ---------- Page 2: AI Chat ----------

    def _build_chat_page(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        self._pages["chat"] = page

        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.pack(fill="x", padx=32, pady=(22, 6))
        ctk.CTkLabel(
            hdr, text="🤖 Studio AI Assistant",
            font=ctk.CTkFont(size=24, weight="bold")
        ).pack(side="left")
        ctk.CTkLabel(
            hdr, text="Instant offline regex engine • 5 msgs/min rate limit",
            font=ctk.CTkFont(size=12), text_color=_MUTED_TEXT
        ).pack(side="right", pady=(6, 0))

        chips = ctk.CTkFrame(page, fg_color="transparent")
        chips.pack(fill="x", padx=32, pady=(2, 8))
        for label, prompt in [
            ("🎮 List Games", "list games"),
            ("🐍 Snake Guide", "how to play snake"),
            ("🧩 2048 Strategy", "how to play 2048"),
            ("🐍 Python Tips", "python tips"),
            ("😂 Tell a Joke", "tell me a joke"),
            ("🖥️ PC Specs", "system info"),
        ]:
            ctk.CTkButton(
                chips, text=label, height=28, width=100, corner_radius=14,
                fg_color=_CARD_BG, hover_color=_NAV_ACTIVE_BG,
                text_color=_NAV_IDLE_TEXT, border_width=1, border_color=_CARD_BORDER,
                font=ctk.CTkFont(size=11, weight="bold"),
                command=lambda p=prompt: self._quick_send(p)
            ).pack(side="left", padx=(0, 6))

        self.chat_display = ctk.CTkTextbox(
            page, font=ctk.CTkFont(size=13), state="disabled",
            fg_color=_CARD_BG, corner_radius=14, border_width=1, border_color=_CARD_BORDER,
            wrap="word"
        )
        self.chat_display.pack(pady=6, padx=32, fill="both", expand=True)

        input_frame = ctk.CTkFrame(page, fg_color="transparent")
        input_frame.pack(fill="x", padx=32, pady=(6, 20))
        self.chat_input = ctk.CTkEntry(
            input_frame, placeholder_text="Ask about games, coding, jokes, or type a command...",
            height=40, corner_radius=10, font=ctk.CTkFont(size=13)
        )
        self.chat_input.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.chat_input.bind("<Return>", self._send)

        ctk.CTkButton(
            input_frame, text="Send ↵", width=95, height=40, corner_radius=10,
            fg_color=_ACCENT_PRIMARY, hover_color=_ACCENT_HOVER,
            font=ctk.CTkFont(size=13, weight="bold"), command=self._send
        ).pack(side="left")
        ctk.CTkButton(
            input_frame, text="Clear", width=76, height=40, corner_radius=10,
            fg_color="transparent", border_width=1, border_color=_CARD_BORDER,
            text_color=_NAV_IDLE_TEXT, hover_color=_NAV_ACTIVE_BG,
            command=self._clear_chat
        ).pack(side="left", padx=(8, 0))

    def _show_chat(self):
        self._switch_page("chat")
        self._set_status()
        if not self.greeted:
            self._chat("AI Assistant", GREETING)
            self.greeted = True
        self.after(50, lambda: self.chat_input.focus_set())

    def _quick_send(self, prompt_text):
        self.chat_input.delete(0, "end")
        self.chat_input.insert(0, prompt_text)
        self._send()

    def _render_chat(self):
        self.chat_display.configure(state="normal")
        self.chat_display.delete("1.0", "end")
        for t, sender, msg in self.chat_messages:
            self.chat_display.insert("end", f"[{t}] {sender}: {msg}\n\n")
        self.chat_display.configure(state="disabled")
        self.chat_display.see("end")

    def _chat(self, sender, msg):
        self.chat_messages.append((time.strftime("%H:%M"), sender, msg))
        if len(self.chat_messages) > MAX_CHAT:
            self.chat_messages = self.chat_messages[-MAX_CHAT:]
            self._render_chat()
            return
        try:
            self.chat_display.configure(state="normal")
            self.chat_display.insert("end", f"[{self.chat_messages[-1][0]}] {sender}: {msg}\n\n")
            self.chat_display.configure(state="disabled")
            self.chat_display.see("end")
        except Exception:
            pass

    def _clear_chat(self):
        self.chat_messages = []
        self._render_chat()
        self._chat("AI Assistant", "Chat history cleared — ready for new questions!")

    def _send(self, event=None):
        text = self.chat_input.get().strip()
        if not text:
            return
        self._chat("You", text)
        self.chat_input.delete(0, "end")
        appdata.touch_chat()

        if ai_regex is None:
            self._chat("AI Assistant", "Sorry, my regex engine failed to load. Try restarting the app!")
            return

        response, trigger = ai_regex.get_response(text)
        if trigger:
            self._chat("AI Assistant", response)
            self.after(220, self._launch_vault)
            return

        self._set_status("🤖 Studio AI is typing...")
        delay = min(420, 120 + len(response) * 2)
        self.after(delay, lambda: self._bot_reply(response))

    def _bot_reply(self, response):
        self._set_status()
        self._chat("AI Assistant", response)

    def _launch_vault(self):
        """Close Kushal Studios and morph into LockerDrive Vault mode (reusing the upfront UAC daemon)."""
        self._terminate_active_game()
        try:
            self.settings["window"] = self.geometry()
            appdata.save_settings(self.settings)
        except Exception:
            pass
        if getattr(sys, "frozen", False):
            exe_dir = os.path.dirname(sys.executable)
            subprocess.Popen(
                [sys.executable, "--gui", "--reuse-daemon"],
                cwd=exe_dir,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
            self.destroy()
            return
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        main_script = os.path.join(project_root, "main.py")
        py_exe = sys.executable
        pyw = os.path.join(os.path.dirname(py_exe), "pythonw.exe")
        if os.path.basename(py_exe).lower() != "pythonw.exe" and os.path.isfile(pyw):
            py_exe = pyw
        target_script = main_script if os.path.isfile(main_script) else os.path.join(project_root, "ui", "app.py")
        args = [py_exe, target_script]
        if target_script == main_script:
            args.extend(["--gui", "--reuse-daemon"])
        subprocess.Popen(args, cwd=project_root)
        self.destroy()


    # ---------- Page 3: Stats ----------

    def _build_stats_page(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        self._pages["stats"] = page

        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.pack(fill="x", padx=32, pady=(22, 8))
        ctk.CTkLabel(
            hdr, text="📊 Player Telemetry & High Scores",
            font=ctk.CTkFont(size=24, weight="bold")
        ).pack(side="left")

        summary = ctk.CTkFrame(
            page, fg_color=_CARD_BG, corner_radius=14,
            border_width=1, border_color=_CARD_BORDER
        )
        summary.pack(fill="x", padx=32, pady=8)

        for key, label in [
            ("plays", "Games Played"),
            ("chat", "AI Chat Messages"),
            ("fav", "Most Played Title"),
        ]:
            box = ctk.CTkFrame(summary, fg_color="transparent")
            box.pack(side="left", expand=True, fill="both", padx=12, pady=12)
            val_lbl = ctk.CTkLabel(box, text="0", font=ctk.CTkFont(size=22, weight="bold"), text_color=_ACCENT_PRIMARY)
            val_lbl.pack()
            ctk.CTkLabel(box, text=label, font=ctk.CTkFont(size=11, weight="bold"), text_color=_MUTED_TEXT).pack()
            self._stats_summary_labels[key] = val_lbl

        table = ctk.CTkFrame(
            page, fg_color=_CARD_BG, corner_radius=14,
            border_width=1, border_color=_CARD_BORDER
        )
        table.pack(fill="both", expand=True, padx=32, pady=(6, 20))
        for col in range(5):
            table.grid_columnconfigure(col, weight=1)

        header_font = ctk.CTkFont(size=12, weight="bold")
        body_font = ctk.CTkFont(size=12)
        for col, text in enumerate(["Game", "Plays", "Best Score", "W / L Record", "Last Played"]):
            ctk.CTkLabel(
                table, text=text, font=header_font, text_color=_MUTED_TEXT
            ).grid(row=0, column=col, padx=14, pady=(12, 6), sticky="w")

        for row, game in enumerate(GAMES, start=1):
            ctk.CTkLabel(
                table, text=f"{game['icon']}  {game['name']}", font=ctk.CTkFont(size=12, weight="bold")
            ).grid(row=row, column=0, padx=14, pady=4, sticky="w")

            plays_lbl = ctk.CTkLabel(table, text="0", font=body_font)
            plays_lbl.grid(row=row, column=1, padx=14, pady=4, sticky="w")

            best_lbl = ctk.CTkLabel(table, text="—", font=body_font)
            best_lbl.grid(row=row, column=2, padx=14, pady=4, sticky="w")

            rec_lbl = ctk.CTkLabel(table, text="—", font=body_font)
            rec_lbl.grid(row=row, column=3, padx=14, pady=4, sticky="w")

            last_lbl = ctk.CTkLabel(table, text="—", font=body_font, text_color=_MUTED_TEXT)
            last_lbl.grid(row=row, column=4, padx=14, pady=4, sticky="w")

            self._stats_row_labels[game["name"]] = (plays_lbl, best_lbl, rec_lbl, last_lbl)

    def _refresh_stats_page(self):
        stats = appdata.load_stats()
        games_stats = stats.get("games", {})
        total_plays = sum(e.get("plays", 0) for e in games_stats.values())
        chat_msgs = stats.get("chat_msgs", 0)
        fav_item = max(games_stats.items(), key=lambda kv: kv[1].get("plays", 0)) if games_stats else None
        fav_name = fav_item[0] if (fav_item and fav_item[1].get("plays", 0) > 0) else "—"

        if "plays" in self._stats_summary_labels:
            self._stats_summary_labels["plays"].configure(text=str(total_plays))
            self._stats_summary_labels["chat"].configure(text=str(chat_msgs))
            self._stats_summary_labels["fav"].configure(text=fav_name)

        for game in GAMES:
            gname = game["name"]
            if gname not in self._stats_row_labels:
                continue
            plays_lbl, best_lbl, rec_lbl, last_lbl = self._stats_row_labels[gname]
            e = games_stats.get(gname, {})
            plays = e.get("plays", 0)
            high = e.get("high", 0)
            best = e.get("best", 0)
            wins = e.get("wins", 0)
            losses = e.get("losses", 0)
            last = e.get("last", "")

            if gname == "Memory Match":
                best_txt = f"{best} moves" if best else "—"
            elif gname == "Tic-Tac-Toe":
                best_txt = "—" if plays == 0 else str(high)
            else:
                best_txt = str(high) if high else "—"

            rec_txt = f"{wins}W / {losses}L" if gname == "Tic-Tac-Toe" else "—"

            plays_lbl.configure(text=str(plays))
            best_lbl.configure(text=best_txt)
            rec_lbl.configure(text=rec_txt)
            last_lbl.configure(text=last if plays else "—")

    def _show_stats(self):
        self._refresh_stats_page()
        self._switch_page("stats")
        self._set_status()

    # ---------- Page 4: Settings ----------

    def _build_settings_page(self):
        page = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        self._pages["settings"] = page

        ctk.CTkLabel(
            page, text="⚙️ Preferences & Studio Info",
            font=ctk.CTkFont(size=24, weight="bold")
        ).pack(anchor="w", padx=32, pady=(16, 12))

        frame = ctk.CTkFrame(
            page, fg_color=_CARD_BG, corner_radius=14,
            border_width=1, border_color=_CARD_BORDER
        )
        frame.pack(fill="x", padx=32, pady=8)
        ctk.CTkLabel(
            frame, text="🎨 Appearance & Audio",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=20, pady=(16, 6))

        ctk.CTkLabel(frame, text="Color Theme:", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=20, pady=(8, 2))
        self.theme_menu = ctk.CTkOptionMenu(
            frame, values=["light", "dark", "system"], width=220,
            fg_color=_ACCENT_PRIMARY, button_color=_ACCENT_HOVER,
            command=self._set_theme
        )
        self.theme_menu.set(self.settings.get("theme", "light"))
        self.theme_menu.pack(anchor="w", padx=20, pady=4)

        ctk.CTkLabel(frame, text="Game Audio FX Volume:", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=20, pady=(12, 2))
        vol_row = ctk.CTkFrame(frame, fg_color="transparent")
        vol_row.pack(fill="x", padx=20, pady=4)
        self.vol_slider = ctk.CTkSlider(
            vol_row, from_=0, to=100, width=280,
            button_color=_ACCENT_PRIMARY, progress_color=_ACCENT_PRIMARY,
            command=self._set_volume
        )
        self.vol_slider.set(self.settings.get("volume", 80))
        self.vol_slider.pack(side="left", padx=(0, 12))
        self._vol_lbl = ctk.CTkLabel(
            vol_row, text=f"{self.settings.get('volume', 80)}%",
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold")
        )
        self._vol_lbl.pack(side="left")
        ctk.CTkLabel(
            frame, text="Games load the saved volume level automatically when launched.",
            font=ctk.CTkFont(size=11), text_color=_MUTED_TEXT
        ).pack(anchor="w", padx=20, pady=(0, 14))

        danger = ctk.CTkFrame(
            page, fg_color=_CARD_BG, corner_radius=14,
            border_width=1, border_color=_CARD_BORDER
        )
        danger.pack(fill="x", padx=32, pady=8)
        ctk.CTkLabel(
            danger, text="🧹 Telemetry Reset",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=20, pady=(16, 4))
        ctk.CTkLabel(
            danger, text="Clears all play counts, win/loss records, and high scores while preserving your preferences.",
            font=ctk.CTkFont(size=12), text_color=_MUTED_TEXT
        ).pack(anchor="w", padx=20)
        ctk.CTkButton(
            danger, text="Reset All Stats", width=140, height=34, corner_radius=9,
            fg_color="#DC2626", hover_color="#B91C1C",
            font=ctk.CTkFont(size=12, weight="bold"), command=self._reset_stats
        ).pack(anchor="w", padx=20, pady=14)

        about = ctk.CTkFrame(
            page, fg_color=_CARD_BG, corner_radius=14,
            border_width=1, border_color=_CARD_BORDER
        )
        about.pack(fill="x", padx=32, pady=(8, 24))
        ctk.CTkLabel(
            about, text="ℹ️ About Kushal Studios v3.0",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=20, pady=(16, 6))
        ctk.CTkLabel(
            about,
            text=(
                "Kushal Studios v3.0 (Production Build 2026.10)\n"
                "Engineered by: Kushal  •  Stack: Python, CustomTkinter, Pygame-CE\n\n"
                "v3.0 Engineering Upgrades:\n"
                "• DOMS Pastel Sky & Lavender aesthetic with Dark Indigo mode\n"
                "• Instant (<1ms) pre-mounted view caching + live Arcade search filter\n"
                "• Mtime-aware in-memory state cache + debounced atomic JSON persistence\n"
                "• Live subprocess game watcher with automatic scoreboard refresh on exit"
            ),
            justify="left", font=ctk.CTkFont(size=12), text_color=_MUTED_TEXT
        ).pack(anchor="w", padx=20, pady=(0, 16))

    def _show_settings(self):
        self._switch_page("settings")
        self._set_status()

    def _set_theme(self, choice):
        ctk.set_appearance_mode(choice)
        self.settings["theme"] = choice
        appdata.save_settings(self.settings)
        self._set_status(f"Theme updated: {choice}")

    def _set_volume(self, value):
        vol = int(value)
        self.settings["volume"] = vol
        try:
            self._vol_lbl.configure(text=f"{vol}%")
        except Exception:
            pass
        if self._vol_save_after_id is not None:
            try:
                self.after_cancel(self._vol_save_after_id)
            except Exception:
                pass
        self._vol_save_after_id = self.after(180, lambda: appdata.save_settings(self.settings))

    def _reset_stats(self):
        if messagebox.askyesno("Reset stats", "Wipe ALL play counts and high scores?\n(This cannot be undone)"):
            appdata.reset_stats()
            self._refresh_arcade_cards()
            self._refresh_stats_page()
            self._set_status("Player telemetry wiped.")

    # ---------- close ----------

    def _on_close(self):
        """Cleanly terminates any running game, locks V:\\ if mounted, scrubs traces,
        and shuts down the background UAC daemon when Kushal Studios is closed."""
        self._terminate_active_game()
        try:
            self.settings["window"] = self.geometry()
            appdata.save_settings(self.settings)
        except Exception:
            pass
        try:
            import vault_mount
            if os.path.exists("V:\\"):
                vault_mount.lock()
            vault_mount.stop_elevated_daemon()
        except Exception:
            pass
        try:
            import anti_re
            anti_re.deep_forensic_scrub("V")
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    app = DecoyApp()
    app.mainloop()
