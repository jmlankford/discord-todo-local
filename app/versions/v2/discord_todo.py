"""
Discord To-Do Desktop Widget — v2
- Appearance: background color, font color, font size, bold/italic
- Timed minimize: 15m / 1h / 2h / custom (max 23:59) / until 8:35 PM ET
- Resizable window with responsive text wrapping
"""

import tkinter as tk
from tkinter import colorchooser
import requests
import json
import os
import sys
import threading
from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ── Paths ─────────────────────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(APP_DIR, "config.json")
DISCORD_API = "https://discord.com/api/v10"

DEFAULT_APPEARANCE = {
    "bg_color":   "#FFD500",
    "font_color": "#000000",
    "font_size":  10,
    "font_bold":  False,
    "font_italic": False,
}

MIN_W, MIN_H = 200, 200   # minimum widget dimensions


def darken(hex_color: str, factor: float = 0.82) -> str:
    h = hex_color.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"#{int(r*factor):02x}{int(g*factor):02x}{int(b*factor):02x}"


class DiscordTodoApp:
    def __init__(self):
        self.config        = {}
        self.tasks         = []
        self.scheduler     = None
        self.root          = None
        self.task_frame    = None
        self._canvas       = None
        self.sync_var      = None
        self._drag_x       = 0
        self._drag_y       = 0
        self._minimize_job = None

        # resize state
        self._rsz_x = 0
        self._rsz_y = 0
        self._rsz_w = 0
        self._rsz_h = 0

        # widget refs for live recoloring
        self._tbar        = None
        self._title_lbl   = None
        self._outer       = None
        self._bbar        = None
        self._sync_lbl    = None
        self._refresh_btn = None
        self._grip        = None

    # ── Appearance helpers ────────────────────────────────────────────────────

    @property
    def _ap(self) -> dict:
        return {**DEFAULT_APPEARANCE, **self.config.get('appearance', {})}

    def _task_font(self):
        ap = self._ap
        parts = []
        if ap['font_bold']:   parts.append('bold')
        if ap['font_italic']: parts.append('italic')
        return ('Georgia', ap['font_size'], ' '.join(parts) if parts else 'normal')

    def _wrap_width(self) -> int:
        """Calculate wraplength based on current canvas width."""
        if self._canvas:
            w = self._canvas.winfo_width()
            if w > 10:
                return max(80, w - 46)
        return 230

    # ── Config ────────────────────────────────────────────────────────────────

    def load_config(self) -> bool:
        try:
            with open(CONFIG_FILE, 'r') as f:
                self.config = json.load(f)
            return bool(self.config.get('token'))
        except Exception:
            return False

    def save_config(self):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=2)

    @property
    def _headers(self):
        return {"Authorization": f"Bot {self.config['token']}"}

    # ── Discord API ───────────────────────────────────────────────────────────

    def _find_thread(self):
        name       = self.config.get('thread_name', 'Josh').strip().lower()
        guild_id   = self.config.get('guild_id',   '').strip()
        channel_id = self.config.get('channel_id', '').strip()

        try:
            r = requests.get(f"{DISCORD_API}/guilds/{guild_id}/threads/active",
                             headers=self._headers, timeout=10)
            if r.status_code == 200:
                for t in r.json().get('threads', []):
                    if (str(t.get('parent_id')) == channel_id
                            and t['name'].strip().lower() == name):
                        return t['id'], None
        except requests.RequestException as e:
            return None, f"Network error: {e}"

        for suffix in ('archived/public', 'archived/private'):
            try:
                r = requests.get(
                    f"{DISCORD_API}/channels/{channel_id}/threads/{suffix}",
                    headers=self._headers, timeout=10)
                if r.status_code == 200:
                    for t in r.json().get('threads', []):
                        if t['name'].strip().lower() == name:
                            return t['id'], None
            except requests.RequestException:
                pass

        return None, f"Thread '{self.config.get('thread_name', 'Josh')}' not found."

    def fetch_messages(self):
        thread_id, err = self._find_thread()
        if err:
            return None, err

        filter_user  = self.config.get('filter_user', 'taskbot').strip().lower()
        all_messages = []
        last_id      = None

        try:
            while True:
                params = {'limit': 100}
                if last_id:
                    params['before'] = last_id
                r = requests.get(f"{DISCORD_API}/channels/{thread_id}/messages",
                                 headers=self._headers, params=params, timeout=10)
                if r.status_code != 200:
                    return None, f"API error {r.status_code}: {r.text[:120]}"
                batch = r.json()
                if not batch:
                    break
                all_messages.extend(batch)
                if len(batch) < 100:
                    break
                last_id = batch[-1]['id']
        except requests.RequestException as e:
            return None, f"Network error: {e}"

        filtered = [
            m for m in all_messages
            if m['author']['username'].strip().lower() != filter_user
            and m.get('content', '').strip()
        ]
        filtered.sort(key=lambda m: int(m['id']))
        return [
            {'content': m['content'].strip(),
             'author':  m['author']['username'],
             'id':      m['id']}
            for m in filtered
        ], None

    # ── Setup window ──────────────────────────────────────────────────────────

    def run_setup(self) -> bool:
        success = [False]
        win = tk.Tk()
        win.title("Discord To-Do — Setup")
        win.geometry("500x440")
        win.resizable(False, False)
        win.configure(bg='#12121e')
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"500x440+{(sw-500)//2}+{(sh-440)//2}")

        tk.Label(win, text="Discord To-Do", font=('Segoe UI', 20, 'bold'),
                 bg='#12121e', fg='#FFD500').pack(pady=(24, 4))
        tk.Label(win, text="Enter your Discord bot details to get started.",
                 font=('Segoe UI', 9), bg='#12121e', fg='#888888').pack(pady=(0, 20))

        form = tk.Frame(win, bg='#12121e')
        form.pack(padx=48, fill='x')

        field_defs = [
            ("Bot Token",   "token",       "",        True),
            ("Guild ID",    "guild_id",    "",        False),
            ("Channel ID",  "channel_id",  "",        False),
            ("Thread Name", "thread_name", "Josh",    False),
            ("Filter User", "filter_user", "taskbot", False),
        ]
        entries = {}
        for label, key, default, secret in field_defs:
            row = tk.Frame(form, bg='#12121e')
            row.pack(fill='x', pady=6)
            tk.Label(row, text=label, width=13, anchor='w',
                     font=('Segoe UI', 10), bg='#12121e', fg='#cccccc').pack(side='left')
            e = tk.Entry(row, font=('Segoe UI', 10), bg='#1e1e34', fg='#ffffff',
                         insertbackground='#FFD500', relief='flat', bd=6,
                         show='●' if secret else '')
            e.insert(0, self.config.get(key, default))
            e.pack(side='left', fill='x', expand=True, ipady=5)
            entries[key] = e

        status_var = tk.StringVar()
        status_lbl = tk.Label(win, textvariable=status_var, font=('Segoe UI', 9),
                               bg='#12121e', fg='#ff6b6b')
        status_lbl.pack(pady=(8, 2))

        def connect():
            for label, key, default, secret in field_defs:
                self.config[key] = entries[key].get().strip()
            if not self.config.get('token'):
                status_var.set("Bot token is required.")
                return
            btn.config(state='disabled', text='Connecting…')
            status_var.set("")
            win.update()

            def do_connect():
                tasks, err = self.fetch_messages()
                def on_done():
                    if err:
                        status_var.set(f"Error: {err}")
                        status_lbl.config(fg='#ff6b6b')
                        btn.config(state='normal', text='Connect & Launch')
                    else:
                        self.tasks = tasks
                        self.save_config()
                        success[0] = True
                        win.destroy()
                win.after(0, on_done)
            threading.Thread(target=do_connect, daemon=True).start()

        btn = tk.Button(win, text="Connect & Launch", command=connect,
                        font=('Segoe UI', 11, 'bold'), bg='#FFD500', fg='#000000',
                        relief='flat', padx=20, pady=9, cursor='hand2',
                        activebackground='#e6c200', activeforeground='#000000')
        btn.pack(pady=12)
        tk.Label(win, text="Tip: All settings can be changed later via ⚙",
                 font=('Segoe UI', 8), bg='#12121e', fg='#555555').pack()

        win.mainloop()
        return success[0]

    # ── Main widget ───────────────────────────────────────────────────────────

    def run_widget(self):
        self.root = tk.Tk()
        self.root.title("Discord To-Do")
        self.root.attributes('-topmost', True)
        self.root.overrideredirect(True)
        self.root.minsize(MIN_W, MIN_H)

        ap      = self._ap
        bg      = ap['bg_color']
        tbar_bg = darken(bg)
        bbar_bg = darken(bg, 0.75)

        self.root.configure(bg=bg)
        W, H = 300, 430
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{sw-W-18}+{sh-H-52}")

        # ── Title bar ────────────────────────────────────────────────────────
        self._tbar = tk.Frame(self.root, bg=tbar_bg, height=36)
        self._tbar.pack(fill='x')
        self._tbar.pack_propagate(False)

        thread_label    = self.config.get('thread_name', 'Josh') + "'s Tasks"
        self._title_lbl = tk.Label(
            self._tbar, text=f"📋  {thread_label}",
            font=('Segoe UI', 10, 'bold'), bg=tbar_bg, fg='#000000')
        self._title_lbl.pack(side='left', padx=10, pady=6)

        def _tbtn(text, cmd, fg='#333333'):
            return tk.Button(
                self._tbar, text=text, command=cmd,
                font=('Segoe UI', 10), bg=tbar_bg, fg=fg,
                relief='flat', bd=0, cursor='hand2', padx=5,
                activebackground=darken(tbar_bg), activeforeground=fg)

        _tbtn('✕', self.quit_app,       fg='#aa0000').pack(side='right', pady=6, padx=4)
        _tbtn('⚙', self.open_settings              ).pack(side='right', pady=6)
        _tbtn('–', self.prompt_minimize             ).pack(side='right', pady=6)

        # Drag (title bar)
        def drag_start(e):
            self._drag_x = e.x_root - self.root.winfo_x()
            self._drag_y = e.y_root - self.root.winfo_y()
        def drag_move(e):
            self.root.geometry(f"+{e.x_root-self._drag_x}+{e.y_root-self._drag_y}")
        for w in (self._tbar, self._title_lbl):
            w.bind('<Button-1>', drag_start)
            w.bind('<B1-Motion>', drag_move)

        # ── Scrollable task list ──────────────────────────────────────────────
        self._outer = tk.Frame(self.root, bg=bg)
        self._outer.pack(fill='both', expand=True, padx=8, pady=6)

        self._canvas = tk.Canvas(self._outer, bg=bg, highlightthickness=0)
        sb = tk.Scrollbar(self._outer, orient='vertical', command=self._canvas.yview)

        self.task_frame = tk.Frame(self._canvas, bg=bg)
        self.task_frame.bind(
            '<Configure>',
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox('all')))

        self._canvas.create_window((0, 0), window=self.task_frame, anchor='nw')
        self._canvas.configure(yscrollcommand=sb.set)
        self._canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        self.root.bind_all(
            '<MouseWheel>',
            lambda e: self._canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units'))

        # Reflow text when canvas width changes
        self._canvas.bind('<Configure>', self._on_canvas_resize)

        # ── Bottom bar ────────────────────────────────────────────────────────
        self._bbar = tk.Frame(self.root, bg=bbar_bg, height=28)
        self._bbar.pack(fill='x', side='bottom')
        self._bbar.pack_propagate(False)

        self.sync_var  = tk.StringVar(value="")
        self._sync_lbl = tk.Label(self._bbar, textvariable=self.sync_var,
                                   font=('Segoe UI', 7), bg=bbar_bg, fg='#555555')
        self._sync_lbl.pack(side='left', padx=7, pady=5)

        self._refresh_btn = tk.Button(
            self._bbar, text='↻ Refresh', command=self._manual_refresh,
            font=('Segoe UI', 8), bg=bbar_bg, fg='#111111',
            relief='flat', bd=0, cursor='hand2', activebackground=darken(bbar_bg))
        self._refresh_btn.pack(side='right', padx=(0, 2), pady=4)

        # Resize grip (bottom-right corner)
        self._grip = tk.Label(self._bbar, text='◢', font=('Arial', 9),
                               bg=bbar_bg, fg='#888888', cursor='size_nw_se')
        self._grip.pack(side='right', padx=(0, 2), pady=4)

        def rsz_start(e):
            self._rsz_x = e.x_root
            self._rsz_y = e.y_root
            self._rsz_w = self.root.winfo_width()
            self._rsz_h = self.root.winfo_height()
        def rsz_drag(e):
            nw = max(MIN_W, self._rsz_w + e.x_root - self._rsz_x)
            nh = max(MIN_H, self._rsz_h + e.y_root - self._rsz_y)
            self.root.geometry(f"{nw}x{nh}")

        self._grip.bind('<Button-1>',  rsz_start)
        self._grip.bind('<B1-Motion>', rsz_drag)

        self._draw_tasks()
        self._start_scheduler()
        self.root.mainloop()

    # ── Canvas resize → reflow text ───────────────────────────────────────────

    def _on_canvas_resize(self, event):
        """Called whenever the canvas is resized; redraws tasks with new wraplength."""
        self._draw_tasks()

    # ── Appearance: recolor live ──────────────────────────────────────────────

    def _apply_colors(self):
        if not self.root:
            return
        ap      = self._ap
        bg      = ap['bg_color']
        tbar_bg = darken(bg)
        bbar_bg = darken(bg, 0.75)

        self.root.configure(bg=bg)
        if self._tbar:
            self._tbar.configure(bg=tbar_bg)
            for w in self._tbar.winfo_children():
                w.configure(bg=tbar_bg, activebackground=darken(tbar_bg))
        if self._title_lbl:
            self._title_lbl.configure(bg=tbar_bg)
        if self._outer:
            self._outer.configure(bg=bg)
        if self._canvas:
            self._canvas.configure(bg=bg)
        if self.task_frame:
            self.task_frame.configure(bg=bg)
        if self._bbar:
            self._bbar.configure(bg=bbar_bg)
        if self._sync_lbl:
            self._sync_lbl.configure(bg=bbar_bg)
        if self._refresh_btn:
            self._refresh_btn.configure(bg=bbar_bg, activebackground=darken(bbar_bg))
        if self._grip:
            self._grip.configure(bg=bbar_bg)

        self._draw_tasks()

    # ── Task rendering ────────────────────────────────────────────────────────

    def _draw_tasks(self):
        if not self.task_frame:
            return
        ap   = self._ap
        bg   = ap['bg_color']
        fg   = ap['font_color']
        font = self._task_font()
        wrap = self._wrap_width()

        for w in self.task_frame.winfo_children():
            w.destroy()

        # Make task_frame fill canvas width so wrapping is correct
        if self._canvas:
            self._canvas.itemconfig('all', width=self._canvas.winfo_width())

        if not self.tasks:
            tk.Label(self.task_frame, text="No tasks in thread.",
                     font=font, bg=bg, fg=fg, wraplength=wrap
                     ).pack(anchor='w', pady=8, padx=6)
        else:
            for task in self.tasks:
                row = tk.Frame(self.task_frame, bg=bg)
                row.pack(fill='x', pady=2, padx=6)
                tk.Label(row, text='•',
                         font=('Georgia', ap['font_size'] + 4, 'bold'),
                         bg=bg, fg=fg).pack(side='left', padx=(0, 5), anchor='n')
                tk.Label(row, text=task['content'], font=font,
                         bg=bg, fg=fg, wraplength=wrap,
                         justify='left', anchor='nw'
                         ).pack(side='left', fill='x', expand=True)

        et  = pytz.timezone('America/New_York')
        now = datetime.now(et).strftime('%b %d, %I:%M %p ET')
        if self.sync_var:
            self.sync_var.set(f"Synced {now}")

    # ── Minimize with timer ───────────────────────────────────────────────────

    def prompt_minimize(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Minimize")
        dlg.configure(bg='#12121e')
        dlg.attributes('-topmost', True)
        dlg.resizable(False, False)
        dlg.update_idletasks()
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        dlg.geometry(f"295x365+{(sw-295)//2}+{(sh-365)//2}")

        tk.Label(dlg, text="Minimize for how long?",
                 font=('Segoe UI', 11, 'bold'), bg='#12121e', fg='#FFD500'
                 ).pack(pady=(16, 10))

        def do_minimize(minutes=None, until_refresh=False):
            dlg.destroy()
            ms = self._ms_until_refresh() if until_refresh else int(minutes * 60 * 1000)
            if self._minimize_job:
                try:
                    self.root.after_cancel(self._minimize_job)
                except Exception:
                    pass
            self.root.withdraw()
            self._minimize_job = self.root.after(ms, self._restore)

        btn_kw = dict(
            font=('Segoe UI', 10), bg='#1e1e34', fg='#ffffff',
            relief='flat', bd=0, cursor='hand2', padx=10, pady=8,
            activebackground='#2a2a50', activeforeground='#FFD500', width=27)

        tk.Button(dlg, text="⏱  15 minutes",
                  command=lambda: do_minimize(15),              **btn_kw).pack(pady=3)
        tk.Button(dlg, text="⏱  1 hour",
                  command=lambda: do_minimize(60),              **btn_kw).pack(pady=3)
        tk.Button(dlg, text="⏱  2 hours",
                  command=lambda: do_minimize(120),             **btn_kw).pack(pady=3)
        tk.Button(dlg, text="🔄  Until next refresh (8:35 PM ET)",
                  command=lambda: do_minimize(until_refresh=True), **btn_kw).pack(pady=3)

        # ── Custom time ───────────────────────────────────────────────────────
        tk.Frame(dlg, bg='#2a2a4a', height=1).pack(fill='x', padx=24, pady=(10, 6))
        tk.Label(dlg, text="Custom duration  (max 23 : 59)",
                 font=('Segoe UI', 8), bg='#12121e', fg='#666688').pack()

        trow   = tk.Frame(dlg, bg='#12121e')
        trow.pack(pady=6)
        hh_var = tk.StringVar(value="00")
        mm_var = tk.StringVar(value="30")

        tk.Spinbox(trow, from_=0, to=23, width=3, textvariable=hh_var,
                   font=('Segoe UI', 13), bg='#1e1e34', fg='#ffffff',
                   buttonbackground='#2a2a50', relief='flat',
                   justify='center', format="%02.0f").pack(side='left')
        tk.Label(trow, text=" : ", font=('Segoe UI', 13, 'bold'),
                 bg='#12121e', fg='#FFD500').pack(side='left')
        tk.Spinbox(trow, from_=0, to=59, width=3, textvariable=mm_var,
                   font=('Segoe UI', 13), bg='#1e1e34', fg='#ffffff',
                   buttonbackground='#2a2a50', relief='flat',
                   justify='center', format="%02.0f").pack(side='left')

        err_var = tk.StringVar()
        tk.Label(dlg, textvariable=err_var, font=('Segoe UI', 8),
                 bg='#12121e', fg='#ff6b6b').pack()

        def custom_minimize():
            err_var.set("")
            try:
                h = max(0, min(23, int(hh_var.get())))
                m = max(0, min(59, int(mm_var.get())))
            except ValueError:
                err_var.set("Please enter valid numbers.")
                return
            total = h * 60 + m
            if total <= 0:
                err_var.set("Duration must be at least 1 minute.")
                return
            if total >= 24 * 60:          # hard cap < 24 h
                err_var.set("Maximum is 23 hours 59 minutes.")
                return
            do_minimize(total)

        tk.Button(dlg, text="Minimize", command=custom_minimize,
                  font=('Segoe UI', 9, 'bold'), bg='#FFD500', fg='#000000',
                  relief='flat', padx=14, pady=5, cursor='hand2',
                  activebackground='#e6c200').pack(pady=(2, 10))

    def _ms_until_refresh(self) -> int:
        et     = pytz.timezone('America/New_York')
        now    = datetime.now(et)
        target = now.replace(hour=20, minute=35, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        return int((target - now).total_seconds() * 1000)

    def _restore(self):
        if self.root:
            self.root.deiconify()
            self.root.attributes('-topmost', True)
        self._minimize_job = None

    # ── Refresh ───────────────────────────────────────────────────────────────

    def _manual_refresh(self):
        if self.sync_var:
            self.sync_var.set("Refreshing…")
        threading.Thread(target=self._bg_refresh, daemon=True).start()

    def _bg_refresh(self):
        tasks, err = self.fetch_messages()
        if self.root:
            if err:
                self.root.after(0, lambda: self.sync_var.set(f"Error: {err[:55]}"))
            else:
                self.tasks = tasks
                self.root.after(0, self._draw_tasks)

    def _start_scheduler(self):
        self.scheduler = BackgroundScheduler()
        self.scheduler.add_job(
            self._bg_refresh,
            CronTrigger(hour=20, minute=35, timezone='America/New_York'))
        self.scheduler.start()

    # ── Settings window ───────────────────────────────────────────────────────

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("450x530")
        win.configure(bg='#12121e')
        win.attributes('-topmost', True)
        win.resizable(False, False)
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"450x530+{(sw-450)//2}+{(sh-530)//2}")

        tk.Label(win, text="Settings", font=('Segoe UI', 14, 'bold'),
                 bg='#12121e', fg='#FFD500').pack(pady=(16, 4))

        # ── Discord ───────────────────────────────────────────────────────────
        tk.Label(win, text="DISCORD", font=('Segoe UI', 8, 'bold'),
                 bg='#12121e', fg='#4444aa').pack(anchor='w', padx=32, pady=(10, 2))
        disc_frame = tk.Frame(win, bg='#12121e')
        disc_frame.pack(padx=32, fill='x')

        disc_fields = [
            ("Thread Name", "thread_name", "Josh"),
            ("Filter User", "filter_user", "taskbot"),
        ]
        disc_entries = {}
        for label, key, default in disc_fields:
            row = tk.Frame(disc_frame, bg='#12121e')
            row.pack(fill='x', pady=6)
            tk.Label(row, text=label, width=13, anchor='w',
                     font=('Segoe UI', 10), bg='#12121e', fg='#cccccc').pack(side='left')
            e = tk.Entry(row, font=('Segoe UI', 10), bg='#1e1e34', fg='#ffffff',
                         insertbackground='#FFD500', relief='flat', bd=6)
            e.insert(0, self.config.get(key, default))
            e.pack(side='left', fill='x', expand=True, ipady=4)
            disc_entries[key] = e

        tk.Frame(win, bg='#222244', height=1).pack(fill='x', padx=20, pady=12)

        # ── Appearance ────────────────────────────────────────────────────────
        tk.Label(win, text="APPEARANCE", font=('Segoe UI', 8, 'bold'),
                 bg='#12121e', fg='#4444aa').pack(anchor='w', padx=32, pady=(0, 2))
        ap_frame = tk.Frame(win, bg='#12121e')
        ap_frame.pack(padx=32, fill='x')

        ap              = self._ap
        current_bg      = [ap['bg_color']]
        current_fg_col  = [ap['font_color']]

        def color_row(parent, label, color_ref):
            row = tk.Frame(parent, bg='#12121e')
            row.pack(fill='x', pady=6)
            tk.Label(row, text=label, width=13, anchor='w',
                     font=('Segoe UI', 10), bg='#12121e', fg='#cccccc').pack(side='left')
            swatch = tk.Label(row, bg=color_ref[0], width=4,
                              relief='solid', bd=1, cursor='hand2')
            swatch.pack(side='left', padx=(0, 8), ipady=8)
            hex_var = tk.StringVar(value=color_ref[0])
            tk.Entry(row, textvariable=hex_var, width=9, font=('Segoe UI', 10),
                     bg='#1e1e34', fg='#ffffff', insertbackground='#FFD500',
                     relief='flat', bd=6).pack(side='left', ipady=4)

            def pick():
                res = colorchooser.askcolor(color=color_ref[0], title=f"Choose {label}")
                if res and res[1]:
                    color_ref[0] = res[1]
                    swatch.configure(bg=res[1])
                    hex_var.set(res[1])

            def on_hex(*_):
                val = hex_var.get().strip()
                if len(val) == 7 and val.startswith('#'):
                    try:
                        int(val[1:], 16)
                        color_ref[0] = val
                        swatch.configure(bg=val)
                    except ValueError:
                        pass

            swatch.bind('<Button-1>', lambda e: pick())
            hex_var.trace_add('write', on_hex)

        color_row(ap_frame, "Background", current_bg)
        color_row(ap_frame, "Font Color",  current_fg_col)

        # Font size
        sz_row = tk.Frame(ap_frame, bg='#12121e')
        sz_row.pack(fill='x', pady=6)
        tk.Label(sz_row, text="Font Size", width=13, anchor='w',
                 font=('Segoe UI', 10), bg='#12121e', fg='#cccccc').pack(side='left')
        size_var = tk.IntVar(value=ap['font_size'])
        tk.Spinbox(sz_row, from_=8, to=24, textvariable=size_var, width=5,
                   font=('Segoe UI', 10), bg='#1e1e34', fg='#ffffff',
                   buttonbackground='#2a2a50', relief='flat', justify='center'
                   ).pack(side='left', ipady=4)
        tk.Label(sz_row, text="pt", font=('Segoe UI', 9),
                 bg='#12121e', fg='#888888').pack(side='left', padx=4)

        # Font style
        st_row = tk.Frame(ap_frame, bg='#12121e')
        st_row.pack(fill='x', pady=6)
        tk.Label(st_row, text="Font Style", width=13, anchor='w',
                 font=('Segoe UI', 10), bg='#12121e', fg='#cccccc').pack(side='left')
        bold_var   = tk.BooleanVar(value=ap['font_bold'])
        italic_var = tk.BooleanVar(value=ap['font_italic'])
        chk = dict(bg='#12121e', selectcolor='#1e1e34',
                   activebackground='#12121e', activeforeground='#FFD500', fg='#cccccc')
        tk.Checkbutton(st_row, text="Bold",   variable=bold_var,
                       font=('Segoe UI', 10, 'bold'),   **chk).pack(side='left', padx=(0, 14))
        tk.Checkbutton(st_row, text="Italic", variable=italic_var,
                       font=('Segoe UI', 10, 'italic'), **chk).pack(side='left')

        # ── Save ─────────────────────────────────────────────────────────────
        def save():
            for label, key, default in disc_fields:
                self.config[key] = disc_entries[key].get().strip()
            self.config['appearance'] = {
                'bg_color':    current_bg[0],
                'font_color':  current_fg_col[0],
                'font_size':   size_var.get(),
                'font_bold':   bold_var.get(),
                'font_italic': italic_var.get(),
            }
            self.save_config()
            win.destroy()
            self._update_title()
            self._apply_colors()
            self._manual_refresh()

        tk.Button(win, text="Save & Apply", command=save,
                  font=('Segoe UI', 10, 'bold'), bg='#FFD500', fg='#000000',
                  relief='flat', padx=16, pady=8, cursor='hand2',
                  activebackground='#e6c200').pack(pady=14)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _update_title(self):
        if self._title_lbl:
            self._title_lbl.config(
                text=f"📋  {self.config.get('thread_name', 'Josh')}'s Tasks")

    def quit_app(self):
        if self.scheduler:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:
                pass
        if self.root:
            self.root.quit()
        sys.exit(0)

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self):
        config_ok = self.load_config()
        if config_ok:
            tasks, err = self.fetch_messages()
            if err:
                if not self.run_setup():
                    return
            else:
                self.tasks = tasks
        else:
            if not self.run_setup():
                return
        self.run_widget()


if __name__ == '__main__':
    app = DiscordTodoApp()
    app.run()
