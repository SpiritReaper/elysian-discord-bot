import os
import sys
import json
import time
import queue
import subprocess
import threading
import requests
import customtkinter as ctk
from dotenv import load_dotenv

from datetime import timedelta, datetime
from collections import deque

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

BOT_FILE = os.getenv("BOT_FILE", "bot.py")
API_BASE = os.getenv("ELYSIAN_API_BASE", "https://elysian-discord-bot-production.up.railway.app").rstrip("/")
STATS_URL = f"{API_BASE}/stats"
HEALTH_URL = f"{API_BASE}/health"
STATS_API_KEY = os.getenv("STATS_API_KEY", "")
METRICS_FILE = os.getenv("ELYSIAN_METRICS_FILE", "elysian_metrics_history.jsonl")
ALERT_ERROR_THRESHOLD = int(os.getenv("ELYSIAN_ALERT_ERROR_THRESHOLD", "1"))

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class ElysianLauncher(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Elysian")
        self.geometry("1250x780")

        self.process = None
        self.start_time = None
        self.log_queue = queue.Queue()

        self.time_data = deque(maxlen=30)
        self.uptime_data = deque(maxlen=30)
        self.server_data = deque(maxlen=30)
        self.active_channel_data = deque(maxlen=30)
        self.total_channel_data = deque(maxlen=30)
        self.error_data = deque(maxlen=30)
        self.sample_count = 0
        self.last_error_count = 0
        self.last_online_state = None
        self.git_conflict_active = False
        self.metrics_history_loaded = False

        self.configure(fg_color="#11142d")

        self.sidebar = ctk.CTkFrame(self, width=230, fg_color="#252b5c")
        self.sidebar.pack(side="left", fill="y")

        self.main = ctk.CTkFrame(self, fg_color="#11142d")
        self.main.pack(side="right", expand=True, fill="both")

        self.build_sidebar()
        self.build_tabs()

        # Load saved metrics one time at startup only.
        self.load_metrics_history()

        self.after(1000, self.update_local_uptime)
        self.after(500, self.process_logs)
        self.after(5000, self.auto_update_remote_status)

        if not STATS_API_KEY:
            self.log("Warning: STATS_API_KEY is missing from .env. /stats may return Unauthorized.")

    def build_sidebar(self):
        ctk.CTkLabel(
            self.sidebar,
            text="ELYSIAN",
            font=("Segoe UI", 26, "bold")
        ).pack(pady=25)

        buttons = [
            ("Fetch Remote Data", self.fetch_data),
            ("Check Health", self.check_health),
            ("Start Local Bot", self.start_bot),
            ("Stop Local Bot", self.stop_bot),
            ("Restart Local Bot", self.restart_bot),
            ("Push Updates to GitHub", self.update_code),
            ("Git Status", self.git_status),
            ("Abort Git Conflict", self.abort_git_conflict),
            ("Update Packages", self.update_packages),
            ("Python Version", self.check_python),
            ("Clear Logs", self.clear_logs),
        ]

        self.sidebar_buttons = {}
        for text, cmd in buttons:
            button = ctk.CTkButton(
                self.sidebar,
                text=text,
                command=cmd
            )
            button.pack(fill="x", padx=20, pady=5)
            self.sidebar_buttons[text] = button

    def build_tabs(self):
        self.tabs = ctk.CTkTabview(self.main, fg_color="#11142d")
        self.tabs.pack(fill="both", expand=True, padx=15, pady=10)

        self.dashboard_tab = self.tabs.add("Dashboard")
        self.graphs_tab = self.tabs.add("Stats Graphs")
        self.about_tab = self.tabs.add("About / Help")

        self.build_dashboard()
        self.build_graphs_tab()
        self.build_about_tab()

    def build_dashboard(self):
        self.status_badge = ctk.CTkLabel(
            self.dashboard_tab,
            text="Remote Status: Unknown"
        )
        self.status_badge.pack(pady=10)

        self.log_box = ctk.CTkTextbox(self.dashboard_tab, height=260)
        self.log_box.pack(fill="both", expand=True, padx=20, pady=10)

        self.remote_info = ctk.CTkTextbox(self.dashboard_tab, height=210)
        self.remote_info.pack(fill="both", expand=True, padx=20, pady=10)

        self.bottom_status_frame = ctk.CTkFrame(self.dashboard_tab, fg_color="#11142d")
        self.bottom_status_frame.pack(pady=8)

        self.local_status_label = ctk.CTkLabel(
            self.bottom_status_frame,
            text="Local Status: Stopped"
        )
        self.local_status_label.grid(row=0, column=0, padx=18)

        self.local_uptime_label = ctk.CTkLabel(
            self.bottom_status_frame,
            text="Local Uptime: 00:00:00"
        )
        self.local_uptime_label.grid(row=0, column=1, padx=18)

        self.railway_status_label = ctk.CTkLabel(
            self.bottom_status_frame,
            text="Railway Status: Unknown"
        )
        self.railway_status_label.grid(row=0, column=2, padx=18)

        self.railway_uptime_label = ctk.CTkLabel(
            self.bottom_status_frame,
            text="Railway Runtime: Unknown"
        )
        self.railway_uptime_label.grid(row=0, column=3, padx=18)

    def build_graphs_tab(self):
        self.graph_status_label = ctk.CTkLabel(
            self.graphs_tab,
            text="Live bot graphs update automatically every 5 seconds",
            font=("Segoe UI", 15, "bold")
        )
        self.graph_status_label.pack(pady=10)

        self.graph_frame = ctk.CTkFrame(self.graphs_tab, fg_color="#11142d")
        self.graph_frame.pack(fill="both", expand=True, padx=20, pady=10)

        self.figure, self.axes = plt.subplots(2, 2, figsize=(9, 6))
        self.figure.patch.set_facecolor("#11142d")

        for ax in self.axes.flatten():
            ax.set_facecolor("#1b1b1b")
            ax.tick_params(colors="white")
            ax.title.set_color("white")
            ax.xaxis.label.set_color("white")
            ax.yaxis.label.set_color("white")

        self.canvas = FigureCanvasTkAgg(self.figure, master=self.graph_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def build_about_tab(self):
        about_box = ctk.CTkTextbox(
            self.about_tab,
            wrap="word",
            font=("Segoe UI", 14)
        )
        about_box.pack(fill="both", expand=True, padx=20, pady=20)

        about_text = """
ELYSIAN LAUNCHER HELP GUIDE

This launcher is a control panel for your Elysian Discord bot.

IMPORTANT:
The Railway bot is the live cloud-hosted bot.
The local bot is only the copy running on your PC for testing.

------------------------------------------------------------
REMOTE / RAILWAY CONTROLS
------------------------------------------------------------

Fetch Remote Data
Pulls live bot data from the Railway-hosted bot using the /stats endpoint.
This shows bot online status, bot name, uptime, server count, active temp channels, total temp channels created, recent errors, and timestamp.

Check Health
Checks the /health endpoint to confirm the Railway app is online and the Discord bot is ready.
If bot_ready is True, the live bot is connected properly.

Push Updates to GitHub
Stages your local file changes, commits them, pulls/rebases from GitHub, and pushes to the main branch.
Railway is connected to GitHub, so every successful push automatically redeploys the bot.

Git Status
Shows the current Git status and remote repository information.
Use this to check whether files are modified, staged, committed, or synced with GitHub.

Abort Git Conflict
Attempts to cancel an active Git rebase or merge conflict.
Use this only if Git gets stuck during a pull/rebase or push process.

------------------------------------------------------------
LOCAL BOT CONTROLS
------------------------------------------------------------

Start Local Bot
Starts bot.py on your local computer.
This is only for local testing and does not control the Railway bot.

Stop Local Bot
Stops the locally running bot.py process on your computer.
This does not stop the Railway bot.

Restart Local Bot
Stops and starts the local bot.py process.
This is useful for testing changes before pushing to GitHub.

------------------------------------------------------------
UTILITY BUTTONS
------------------------------------------------------------

Update Packages
Updates local Python packages used by the launcher and bot.
This affects your computer only, not Railway.

Python Version
Displays the Python version being used locally.

Clear Logs
Clears the launcher log box and remote info box.

------------------------------------------------------------
BOTTOM STATUS BAR
------------------------------------------------------------

Local Status
Shows whether the local bot process is running on your PC.

Local Uptime
Shows how long the local bot has been running.

Railway Status
Shows whether the live Railway bot is online or offline.

Railway Runtime
Shows how long the current Railway deployment has been running.
This resets when Railway redeploys, restarts, or the bot is updated.

------------------------------------------------------------
STATS GRAPHS TAB
------------------------------------------------------------

Railway Runtime Seconds
Tracks how long the current Railway deployment has been running.

Servers Connected
Shows how many Discord servers the bot is currently in.

Temp Channels
Tracks active temporary channels and total created temporary channels.

Recent Error Count
Shows how many recent errors the bot has recorded.

------------------------------------------------------------
COMMON NOTES
------------------------------------------------------------

If Railway Runtime resets, it usually means Railway redeployed or restarted.

If temporary voice channels stop working after a redeploy, run /setup again in Discord.

If Push Updates to GitHub fails, click Git Status and check the log message.

Never push your .env file to GitHub.
Your Discord token should stay in Railway Variables and your local .env only.
"""

        about_box.insert("1.0", about_text)
        about_box.configure(state="disabled")

    def log(self, msg):
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")

    def clear_logs(self):
        self.log_box.delete("1.0", "end")
        self.remote_info.delete("1.0", "end")

    def run_command(self, command, allow_fail=False):
        self.log(f"Running: {' '.join(command)}")

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        output = result.stdout.strip() if result.stdout else ""

        if output:
            self.log(output)

        if result.returncode != 0 and not allow_fail:
            raise Exception(f"Command failed: {' '.join(command)}")

        return result

    def record_metric(self, data):
        row = {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "uptime_seconds": data.get("uptime_seconds", 0),
            "servers": data.get("servers", 0),
            "active_temp_channels": data.get("active_temp_channels", 0),
            "total_temp_channels_created": data.get("total_temp_channels_created", 0),
            "recent_error_count": len(data.get("recent_errors", [])),
            "bot_online": data.get("bot_online"),
        }
        try:
            with open(METRICS_FILE, "a", encoding="utf-8") as file:
                file.write(json.dumps(row) + "\n")
        except Exception as e:
            self.log(f"Could not save metrics history: {e}")

    def load_metrics_history(self):
        if self.metrics_history_loaded:
            return

        self.metrics_history_loaded = True

        if not os.path.exists(METRICS_FILE):
            return

        try:
            rows = []
            with open(METRICS_FILE, "r", encoding="utf-8") as file:
                for line in file:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))

            # Load only the latest 30 historical samples into the graph.
            self.time_data.clear()
            self.uptime_data.clear()
            self.server_data.clear()
            self.active_channel_data.clear()
            self.total_channel_data.clear()
            self.error_data.clear()
            self.sample_count = 0

            loaded_rows = rows[-30:]
            for row in loaded_rows:
                self.sample_count += 1
                self.time_data.append(self.sample_count)
                self.uptime_data.append(row.get("uptime_seconds", 0))
                self.server_data.append(row.get("servers", 0))
                self.active_channel_data.append(row.get("active_temp_channels", 0))
                self.total_channel_data.append(row.get("total_temp_channels_created", 0))
                self.error_data.append(row.get("recent_error_count", 0))

            if loaded_rows:
                self.redraw_graphs()
                self.log(f"Loaded {len(loaded_rows)} saved metric samples.")
        except Exception as e:
            self.log(f"Could not load metrics history: {e}")

    def check_alerts(self, data):
        online = bool(data.get("bot_online"))
        error_count = len(data.get("recent_errors", []))

        if self.last_online_state is not None and online != self.last_online_state:
            state = "online" if online else "offline"
            self.log(f"ALERT: Railway bot changed status to {state}.")

        if error_count >= ALERT_ERROR_THRESHOLD and error_count > self.last_error_count:
            self.log(f"ALERT: Recent bot errors increased to {error_count}. Check recent_errors in remote data.")

        self.last_online_state = online
        self.last_error_count = error_count

    def redraw_graphs(self):
        axs = self.axes.flatten()

        axs[0].clear()
        axs[0].plot(list(self.time_data), list(self.uptime_data))
        axs[0].set_title("Railway Runtime Seconds")

        axs[1].clear()
        axs[1].plot(list(self.time_data), list(self.server_data))
        axs[1].set_title("Servers Connected")

        axs[2].clear()
        axs[2].plot(list(self.time_data), list(self.active_channel_data))
        axs[2].plot(list(self.time_data), list(self.total_channel_data))
        axs[2].set_title("Temp Channels")
        axs[2].legend(["Active", "Total"], fontsize=8)

        axs[3].clear()
        axs[3].plot(list(self.time_data), list(self.error_data))
        axs[3].set_title("Recent Error Count")

        for ax in axs:
            ax.set_facecolor("#1b1b1b")
            ax.tick_params(colors="white")
            ax.title.set_color("white")
            ax.xaxis.label.set_color("white")
            ax.yaxis.label.set_color("white")

        self.figure.tight_layout()
        self.canvas.draw()

    def update_graphs(self, data):
        self.sample_count += 1

        self.time_data.append(self.sample_count)
        self.uptime_data.append(data.get("uptime_seconds", 0))
        self.server_data.append(data.get("servers", 0))
        self.active_channel_data.append(data.get("active_temp_channels", 0))
        self.total_channel_data.append(data.get("total_temp_channels_created", 0))
        self.error_data.append(len(data.get("recent_errors", [])))

        self.record_metric(data)
        self.check_alerts(data)
        self.redraw_graphs()

    def detect_git_conflict(self):
        conflict_paths = [
            os.path.join(".git", "rebase-merge"),
            os.path.join(".git", "rebase-apply"),
            os.path.join(".git", "MERGE_HEAD"),
        ]
        self.git_conflict_active = any(os.path.exists(path) for path in conflict_paths)
        push_button = self.sidebar_buttons.get("Push Updates to GitHub")
        if push_button:
            push_button.configure(state="disabled" if self.git_conflict_active else "normal")
        return self.git_conflict_active

    def git_status(self):
        self.log("Checking Git status...")

        try:
            self.run_command(["git", "status"])
            self.run_command(["git", "remote", "-v"])
            if self.detect_git_conflict():
                self.log("Git conflict/rebase detected. Push is disabled until you abort or resolve it.")
            else:
                self.log("No active Git conflict/rebase detected.")
        except Exception as e:
            self.log(f"Git status failed: {e}")

    def abort_git_conflict(self):
        self.log("Attempting to abort active Git conflict/rebase...")

        self.run_command(["git", "rebase", "--abort"], allow_fail=True)
        self.run_command(["git", "merge", "--abort"], allow_fail=True)

        self.detect_git_conflict()
        self.log("Git conflict abort attempted. Run Git Status to confirm.")

    def ensure_gitignore_protection(self):
        gitignore_path = ".gitignore"

        existing = ""
        if os.path.exists(gitignore_path):
            with open(gitignore_path, "r", encoding="utf-8") as file:
                existing = file.read()

        needed_lines = [".env", "__pycache__/", "*.pyc"]
        changed = False

        for line in needed_lines:
            if line not in existing:
                existing += f"\n{line}"
                changed = True

        if changed:
            with open(gitignore_path, "w", encoding="utf-8") as file:
                file.write(existing.strip() + "\n")

            self.log(".gitignore updated with safe defaults.")

        self.run_command(["git", "rm", "--cached", ".env"], allow_fail=True)

    def update_code(self):
        if self.detect_git_conflict():
            self.log("Push blocked because a Git conflict/rebase is active. Use Abort Git Conflict or resolve it manually.")
            return

        self.log("Pushing updates to GitHub...")

        try:
            self.ensure_gitignore_protection()

            self.run_command(["git", "add", "."])

            commit_result = self.run_command(
                ["git", "commit", "-m", "Update bot"],
                allow_fail=True
            )

            if commit_result.returncode != 0:
                self.log("No new local commit was created. Continuing sync anyway.")

            pull_result = self.run_command(
                ["git", "pull", "--rebase", "origin", "main"],
                allow_fail=True
            )

            if pull_result.returncode != 0:
                self.log("")
                self.log("Git pull/rebase failed.")
                self.log("Click 'Abort Git Conflict' if you want to cancel the rebase.")
                self.detect_git_conflict()
                return

            push_result = self.run_command(
                ["git", "push", "origin", "main"],
                allow_fail=True
            )

            if push_result.returncode != 0:
                self.log("")
                self.log("Push failed.")
                self.log("Run Git Status, then try Push Updates again.")
                return

            self.detect_git_conflict()
            self.log("Push successful. Railway will automatically redeploy.")

        except Exception as e:
            self.log(f"Push failed: {e}")

    def start_bot(self):
        if self.process and self.process.poll() is None:
            self.log("Local bot is already running.")
            return

        if not os.path.exists(BOT_FILE):
            self.log("bot.py not found.")
            return

        self.process = subprocess.Popen(
            [sys.executable, BOT_FILE],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        self.start_time = time.time()
        self.local_status_label.configure(text="Local Status: Running")

        threading.Thread(target=self.read_output, daemon=True).start()
        self.log("Local bot started.")

    def stop_bot(self):
        if not self.process:
            self.log("No local bot process is running.")
            return

        self.process.terminate()
        self.process = None
        self.start_time = None

        self.local_status_label.configure(text="Local Status: Stopped")
        self.local_uptime_label.configure(text="Local Uptime: 00:00:00")

        self.log("Local bot stopped.")

    def restart_bot(self):
        self.log("Restarting local bot...")
        self.stop_bot()
        self.after(1000, self.start_bot)

    def read_output(self):
        try:
            for line in self.process.stdout:
                self.log_queue.put(line.strip())
        except Exception as e:
            self.log_queue.put(f"Log read error: {e}")

    def process_logs(self):
        while not self.log_queue.empty():
            self.log(self.log_queue.get())

        self.after(500, self.process_logs)

    def update_local_uptime(self):
        if self.start_time and self.process and self.process.poll() is None:
            elapsed = int(time.time() - self.start_time)
            self.local_uptime_label.configure(
                text=f"Local Uptime: {str(timedelta(seconds=elapsed))}"
            )
        elif self.process and self.process.poll() is not None:
            self.process = None
            self.start_time = None
            self.local_status_label.configure(text="Local Status: Stopped")
            self.local_uptime_label.configure(text="Local Uptime: 00:00:00")

        self.after(1000, self.update_local_uptime)

    def check_health(self):
        self.log("Checking remote health...")

        try:
            response = requests.get(HEALTH_URL, timeout=10)

            if response.status_code != 200:
                self.status_badge.configure(text="Remote Status: Offline")
                self.railway_status_label.configure(text="Railway Status: Offline")
                self.log(f"Health check failed with status code: {response.status_code}")
                self.log(response.text)
                return

            data = response.json()

            self.remote_info.delete("1.0", "end")

            for key, value in data.items():
                self.remote_info.insert("end", f"{key}: {value}\n")

            if data.get("bot_ready"):
                self.status_badge.configure(text="Remote Status: Online")
                self.railway_status_label.configure(text="Railway Status: Online")
            else:
                self.status_badge.configure(text="Remote Status: Starting / Not Ready")
                self.railway_status_label.configure(text="Railway Status: Starting")

            self.log("Health check successful.")

        except Exception as e:
            self.status_badge.configure(text="Remote Status: Offline")
            self.railway_status_label.configure(text="Railway Status: Offline")
            self.log(f"Health check failed: {e}")

    def fetch_data(self):
        self.log("Fetching remote bot data...")

        try:
            headers = {"Authorization": f"Bearer {STATS_API_KEY}"} if STATS_API_KEY else {}
            response = requests.get(STATS_URL, headers=headers, timeout=10)

            if response.status_code != 200:
                self.status_badge.configure(text="Remote Status: Offline")
                self.railway_status_label.configure(text="Railway Status: Offline")
                self.log(f"Fetch failed with status code: {response.status_code}")
                self.log(response.text)
                return

            data = response.json()

            if data.get("bot_online"):
                self.status_badge.configure(text="Remote Status: Online")
                self.railway_status_label.configure(text="Railway Status: Online")
            else:
                self.status_badge.configure(text="Remote Status: Offline")
                self.railway_status_label.configure(text="Railway Status: Offline")

            self.railway_uptime_label.configure(
                text=f"Railway Runtime: {data.get('uptime', 'Unknown')}"
            )

            self.remote_info.delete("1.0", "end")

            for key, value in data.items():
                self.remote_info.insert("end", f"{key}: {value}\n")

            self.update_graphs(data)

            self.log("Remote data fetched successfully.")

        except Exception as e:
            self.status_badge.configure(text="Remote Status: Offline")
            self.railway_status_label.configure(text="Railway Status: Offline")
            self.log(f"Fetch failed: {e}")

    def auto_update_remote_status(self):
        try:
            headers = {"Authorization": f"Bearer {STATS_API_KEY}"} if STATS_API_KEY else {}
            response = requests.get(STATS_URL, headers=headers, timeout=5)

            if response.status_code == 200:
                data = response.json()

                if data.get("bot_online"):
                    self.status_badge.configure(text="Remote Status: Online")
                    self.railway_status_label.configure(text="Railway Status: Online")
                else:
                    self.status_badge.configure(text="Remote Status: Offline")
                    self.railway_status_label.configure(text="Railway Status: Offline")

                self.railway_uptime_label.configure(
                    text=f"Railway Runtime: {data.get('uptime', 'Unknown')}"
                )

                self.update_graphs(data)
            else:
                self.railway_status_label.configure(text="Railway Status: Offline")

        except Exception:
            self.railway_status_label.configure(text="Railway Status: Offline")

        self.after(5000, self.auto_update_remote_status)

    def update_packages(self):
        self.log("Updating local Python packages...")

        subprocess.Popen([
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "discord.py",
            "python-dotenv",
            "aiohttp",
            "pytz",
            "requests",
            "customtkinter",
            "matplotlib"
        ])

    def check_python(self):
        self.log(sys.version)


if __name__ == "__main__":
    app = ElysianLauncher()
    app.mainloop()