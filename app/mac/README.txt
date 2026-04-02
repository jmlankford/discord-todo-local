Discord To-Do Widget — Mac
==========================

QUICK START (no build required)
--------------------------------
1. Open Terminal
2. cd into this folder
3. Run:  bash run_mac.sh

That's it. It installs Python dependencies and launches the app.
On first run you'll see a setup screen to enter your bot details.


TO BUILD A STANDALONE EXE
--------------------------
If you want a distributable binary (no Python needed to run):

1. Open Terminal
2. cd into this folder
3. Run:  bash build_mac.sh
4. Your binary will be at:  dist/DiscordTodo

Give someone else the dist/ folder (DiscordTodo + config.json).


REQUIREMENTS
------------
- Python 3.8+  →  https://www.python.org  (or: brew install python)
- tkinter       →  brew install python-tk  (if not already included)
- Internet connection (Discord API calls)


TROUBLESHOOTING
---------------
"tkinter not found":
    brew install python-tk
    or reinstall Python from python.org (bundles tkinter)

"permission denied" on .sh file:
    chmod +x run_mac.sh build_mac.sh

App doesn't appear on screen (Mac Sonoma+):
    Go to System Settings → Privacy & Security → Accessibility
    and allow the app if prompted.
