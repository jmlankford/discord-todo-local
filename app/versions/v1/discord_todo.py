"""
Discord To-Do Desktop Widget
Reads messages from a Discord thread and displays them as a post-it note on your desktop.
Auto-refreshes daily at 8:35 PM ET.
"""

import tkinter as tk
import requests
import json
import os
import sys
import threading
from datetime import datetime
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ── Config file lives next to the exe (or script during dev) ──────────────────
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(APP_DIR, "config.json")
DISCORD_API = "https://discord.com/api/v10"


class DiscordTodoApp:
    def __init__(self):
        self.config = {}
        self.tasks = []
        self.scheduler = None
        self.root = None
        self.task_frame = None
        self._canvas = None
        self.sync_var = None
        self._drag_x = 0
        self._drag_y = 0

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
        """Locate the named thread inside the configured channel.
        Returns (thread_id, error_string). One will always be None."""
        name = self.config.get('thread_name', 'Josh').strip().lower()
        guild_id = self.config.get('guild_id', '').strip()
        channel_id = self.config.get('channel_id', '').strip()

        # 1. Active threads across the guild
        try:
            r = requests.get(
                f"{DISCORD_API}/guilds/{guild_id}/threads/active",
                headers=self._headers, timeout=10
            )
            if r.status_code == 200:
                for t in r.json().get('threads', []):
                    if (str(t.get('parent_id')) == channel_id
                            and t['name'].strip().lower() == name):
                        return t['id'], None
        except requests.RequestException as e:
            return None, f"Network error: {e}"

        # 2. Archived public threads in the channel
        try:
            r = requests.get(
                f"{DISCORD_API}/channels/{channel_id}/threads/archived/public",
                headers=self._headers, timeout=10
            )
            if r.status_code == 200:
                for t in r.json().get('threads', []):
                    if t['name'].strip().lower() == name:
                        return t['id'], None
        except requests.RequestException:
            pass

        # 3. Archived private threads in the channel
        try:
            r = requests.get(
                f"{DISCORD_API}/channels/{channel_id}/threads/archived/private",
                headers=self._headers, timeout=10
            )
            if r.status_code == 200:
                for t in r.json().get('threads', []):
                    if t['name'].strip().lower() == name:
                        return t['id'], None
        except requests.RequestException:
            pass

        return None, (
            f"Thread '{self.config.get('thread_name', 'Josh')}' not found. "
            "Check that the thread exists and the bot has access."
        )

    def fetch_messages(self):
        """Fetch all messages from the thread, filter out the ignored user.
        Returns (task_list, error_string). One will always be None."""
        thread_id, err = self._find_thread()
        if err:
            return None, err

        filter_user = self.config.get('filter_user', 'taskbot').strip().lower()
        all_messages = []
        last_id = None

        try:
            while True:
                params = {'limit': 100}
                if last_id:
                    params['before'] = last_id

                r = requests.get(
                    f"{DISCORD_API}/channels/{thread_id}/messages",
                    headers=self._headers, params=params, timeout=10
                )
                if r.status_code != 200:
                    return None, f"Discord API error {r.status_code}: {r.text[:120]}"

                batch = r.json()
                if not batch:
                    break

                all_messages.extend(batch)

                if len(batch) < 100:
                    break
                last_id = batch[-1]['id']

        except requests.RequestException as e:
            return None, f"Network error: {e}"

        # Filter: exclude the configured user and blank messages
        filtered = [
            m for m in all_messages
            if m['author']['username'].strip().lower() != filter_user
            and m.get('content', '').strip()
        ]

        # Sort oldest-first (Discord snowflake IDs are chronological)
        filtered.sort(key=lambda m: int(m['id']))

        tasks = [
            {
                'content': m['content'].strip(),
                'author': m['author']['username'],
                'id': m['id'],
            }
            for m in filtered
        ]
        return tasks, None

    # ── Setup Window ──────────────────────────────────────────────────────────

    def run_setup(self) -> bool:
        """Show the first-run setup window. Returns True on success."""
        success = [False]
        result = {'tasks': None}

        win = tk.Tk()
        win.title("Discord To-Do — Setup")
        win.geometry("500x440")
        win.resizable(False, False)
        win.configure(bg='#12121e')
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"500x440+{(sw - 500) // 2}+{(sh - 440) // 2}")

        tk.Label(win, text="Discord To-Do", font=('Segoe UI', 20, 'bold'),
                 bg='#12121e', fg='#FFD500').pack(pady=(24, 4))
        tk.Label(win, text="Enter your Discord bot details to get started.",
                 font=('Segoe UI', 9), bg='#12121e', fg='#888888').pack(pady=(0, 20))

        form = tk.Frame(win, bg='#12121e')
        form.pack(padx=48, fill='x')

        # (display label, config key, default value, hide input?)
        field_defs = [
            ("Bot Token",   "token",       "",          True),
            ("Guild ID",    "guild_id",    "",          False),
            ("Channel ID",  "channel_id",  "",          False),
            ("Thread Name", "thread_name", "Josh",      False),
            ("Filter User", "filter_user", "taskbot",   False),
        ]

        entries = {}
        for label, key, default, secret in field_defs:
            row = tk.Frame(form, bg='#12121e')
            row.pack(fill='x', pady=6)
            tk.Label(row, text=label, width=13, anchor='w',
                     font=('Segoe UI', 10), bg='#12121e',
                     fg='#cccccc').pack(side='left')
            e = tk.Entry(
                row, font=('Segoe UI', 10), bg='#1e1e34', fg='#ffffff',
                insertbackground='#FFD500', relief='flat', bd=6,
                show='●' if secret else ''
            )
            e.insert(0, self.config.get(key, default))
            e.pack(side='left', fill='x', expand=True, ipady=5)
            entries[key] = e

        status_var = tk.StringVar()
        status_lbl = tk.Label(win, textvariable=status_var,
                               font=('Segoe UI', 9), bg='#12121e', fg='#ff6b6b')
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
                result['tasks'] = tasks

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

        btn = tk.Button(
            win, text="Connect & Launch", command=connect,
            font=('Segoe UI', 11, 'bold'), bg='#FFD500', fg='#000000',
            relief='flat', padx=20, pady=9, cursor='hand2',
            activebackground='#e6c200', activeforeground='#000000'
        )
        btn.pack(pady=12)

        tk.Label(win,
                 text="Tip: Thread Name and Filter User can be changed later in ⚙ Settings.",
                 font=('Segoe UI', 8), bg='#12121e', fg='#555555').pack()

        win.mainloop()
        return success[0]

    # ── Main Widget ───────────────────────────────────────────────────────────

    def run_widget(self):
        self.root = tk.Tk()
        self.root.title("Discord To-Do")
        self.root.configure(bg='#FFD500')
        self.root.attributes('-topmost', True)
        self.root.overrideredirect(True)   # no OS chrome

        W, H = 300, 430
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{sw - W - 18}+{sh - H - 52}")

        # ── Title bar ────────────────────────────────────────────────────────
        tbar = tk.Frame(self.root, bg='#d4ab00', height=36)
        tbar.pack(fill='x')
        tbar.pack_propagate(False)

        thread_label = self.config.get('thread_name', 'Josh') + "'s Tasks"
        title_lbl = tk.Label(
            tbar, text=f"📋  {thread_label}",
            font=('Segoe UI', 10, 'bold'), bg='#d4ab00', fg='#000000'
        )
        title_lbl.pack(side='left', padx=10, pady=6)

        def _btn(parent, text, cmd, fg='#333333'):
            return tk.Button(
                parent, text=text, command=cmd,
                font=('Segoe UI', 10), bg='#d4ab00', fg=fg,
                relief='flat', bd=0, cursor='hand2', padx=5,
                activebackground='#b89200', activeforeground=fg
            )

        _btn(tbar, '✕', self.quit_app, fg='#aa0000').pack(side='right', pady=6, padx=4)
        _btn(tbar, '⚙', self.open_settings).pack(side='right', pady=6)

        # Drag support
        def drag_start(e):
            self._drag_x = e.x_root - self.root.winfo_x()
            self._drag_y = e.y_root - self.root.winfo_y()

        def drag_move(e):
            self.root.geometry(
                f"+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}"
            )

        for w in [tbar, title_lbl]:
            w.bind('<Button-1>', drag_start)
            w.bind('<B1-Motion>', drag_move)

        # ── Scrollable task list ──────────────────────────────────────────────
        outer = tk.Frame(self.root, bg='#FFD500')
        outer.pack(fill='both', expand=True, padx=8, pady=6)

        self._canvas = tk.Canvas(outer, bg='#FFD500', highlightthickness=0)
        sb = tk.Scrollbar(outer, orient='vertical', command=self._canvas.yview)

        self.task_frame = tk.Frame(self._canvas, bg='#FFD500')
        self.task_frame.bind(
            '<Configure>',
            lambda e: self._canvas.configure(
                scrollregion=self._canvas.bbox('all')
            )
        )

        self._canvas.create_window((0, 0), window=self.task_frame, anchor='nw')
        self._canvas.configure(yscrollcommand=sb.set)
        self._canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        self.root.bind_all(
            '<MouseWheel>',
            lambda e: self._canvas.yview_scroll(
                -1 if e.delta > 0 else 1, 'units'
            )
        )

        # ── Bottom bar ────────────────────────────────────────────────────────
        bbar = tk.Frame(self.root, bg='#c4a000', height=28)
        bbar.pack(fill='x', side='bottom')
        bbar.pack_propagate(False)

        self.sync_var = tk.StringVar(value="")
        tk.Label(
            bbar, textvariable=self.sync_var,
            font=('Segoe UI', 7), bg='#c4a000', fg='#555555'
        ).pack(side='left', padx=7, pady=5)

        tk.Button(
            bbar, text='↻ Refresh', command=self._manual_refresh,
            font=('Segoe UI', 8), bg='#c4a000', fg='#111111',
            relief='flat', bd=0, cursor='hand2',
            activebackground='#b89200'
        ).pack(side='right', padx=8, pady=4)

        # ── First draw + scheduler ────────────────────────────────────────────
        self._draw_tasks()
        self._start_scheduler()
        self.root.mainloop()

    def _draw_tasks(self):
        """Render the current task list. Must be called from the main thread."""
        if not self.task_frame:
            return

        for w in self.task_frame.winfo_children():
            w.destroy()

        if not self.tasks:
            tk.Label(
                self.task_frame,
                text="No tasks in thread.",
                font=('Georgia', 10, 'italic'),
                bg='#FFD500', fg='#777777', wraplength=260
            ).pack(anchor='w', pady=8, padx=6)
        else:
            for task in self.tasks:
                row = tk.Frame(self.task_frame, bg='#FFD500')
                row.pack(fill='x', pady=2, padx=6)

                tk.Label(
                    row, text='•', font=('Georgia', 15, 'bold'),
                    bg='#FFD500', fg='#000000'
                ).pack(side='left', padx=(0, 5), anchor='n')

                tk.Label(
                    row, text=task['content'],
                    font=('Georgia', 10), bg='#FFD500', fg='#000000',
                    wraplength=228, justify='left', anchor='nw'
                ).pack(side='left', fill='x', expand=True)

        et = pytz.timezone('America/New_York')
        now = datetime.now(et).strftime('%b %d, %I:%M %p ET')
        if self.sync_var:
            self.sync_var.set(f"Synced {now}")

    def _update_title(self):
        """Update the title bar label after a thread name change."""
        if not self.root:
            return
        thread_label = self.config.get('thread_name', 'Josh') + "'s Tasks"
        # Find the title label in the title bar and update it
        tbar = self.root.winfo_children()[0]  # first child is the tbar frame
        for w in tbar.winfo_children():
            if isinstance(w, tk.Label):
                w.config(text=f"📋  {thread_label}")
                break

    def _manual_refresh(self):
        if self.sync_var:
            self.sync_var.set("Refreshing…")
        threading.Thread(target=self._bg_refresh, daemon=True).start()

    def _bg_refresh(self):
        """Fetch from Discord in a background thread, then update the UI."""
        tasks, err = self.fetch_messages()
        if self.root:
            if err:
                self.root.after(
                    0,
                    lambda: self.sync_var.set(f"Error: {err[:55]}")
                )
            else:
                self.tasks = tasks
                self.root.after(0, self._draw_tasks)

    def _start_scheduler(self):
        self.scheduler = BackgroundScheduler()
        self.scheduler.add_job(
            self._bg_refresh,
            CronTrigger(hour=20, minute=35, timezone='America/New_York')
        )
        self.scheduler.start()

    # ── Settings Window ───────────────────────────────────────────────────────

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("400x300")
        win.configure(bg='#12121e')
        win.attributes('-topmost', True)
        win.resizable(False, False)
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"400x300+{(sw - 400) // 2}+{(sh - 300) // 2}")

        tk.Label(win, text="Settings", font=('Segoe UI', 14, 'bold'),
                 bg='#12121e', fg='#FFD500').pack(pady=(16, 12))

        form = tk.Frame(win, bg='#12121e')
        form.pack(padx=34, fill='x')

        # These fields are editable post-setup
        editable = [
            ("Thread Name", "thread_name", "Josh"),
            ("Filter User", "filter_user", "taskbot"),
        ]

        entries = {}
        for label, key, default in editable:
            row = tk.Frame(form, bg='#12121e')
            row.pack(fill='x', pady=8)
            tk.Label(row, text=label, width=13, anchor='w',
                     font=('Segoe UI', 10), bg='#12121e',
                     fg='#cccccc').pack(side='left')
            e = tk.Entry(
                row, font=('Segoe UI', 10), bg='#1e1e34', fg='#ffffff',
                insertbackground='#FFD500', relief='flat', bd=6
            )
            e.insert(0, self.config.get(key, default))
            e.pack(side='left', fill='x', expand=True, ipady=5)
            entries[key] = e

        tk.Label(
            form,
            text="Changing Thread Name will search for a thread with that name.\n"
                 "Filter User is the username whose messages are hidden.",
            font=('Segoe UI', 8), bg='#12121e', fg='#555555',
            justify='left'
        ).pack(anchor='w', pady=(8, 0))

        def save():
            for label, key, default in editable:
                self.config[key] = entries[key].get().strip()
            self.save_config()
            win.destroy()
            self._update_title()
            self._manual_refresh()

        tk.Button(
            win, text="Save & Refresh", command=save,
            font=('Segoe UI', 10, 'bold'), bg='#FFD500', fg='#000000',
            relief='flat', padx=16, pady=8, cursor='hand2',
            activebackground='#e6c200'
        ).pack(pady=16)

    # ── Quit ──────────────────────────────────────────────────────────────────

    def quit_app(self):
        if self.scheduler:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:
                pass
        if self.root:
            self.root.quit()
        sys.exit(0)

    # ── Entry Point ───────────────────────────────────────────────────────────

    def run(self):
        config_ok = self.load_config()

        if config_ok:
            # Try connecting with saved config
            tasks, err = self.fetch_messages()
            if err:
                # Something's wrong — let user fix it in setup
                if not self.run_setup():
                    return
            else:
                self.tasks = tasks
        else:
            # No config — first run
            if not self.run_setup():
                return

        self.run_widget()


if __name__ == '__main__':
    app = DiscordTodoApp()
    app.run()
