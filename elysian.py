import os
import sys
import time
import queue
import subprocess
import threading
import requests
import customtkinter as ctk
from datetime import timedelta

BOT_FILE = "bot.py"

API_BASE = "https://elysian-discord-bot-production.up.railway.app"
STATS_URL = f"{API_BASE}/stats"
HEALTH_URL = f"{API_BASE}/health"

STATS_API_KEY = "ElysianBotSecure_1919523"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class ElysianLauncher(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Elysian")
        self.geometry("1100x700")

        self.process = None
        self.start_time = None
        self.log_queue = queue.Queue()

        self.configure(fg_color="#11142d")

        self.sidebar = ctk.CTkFrame(self, width=230, fg_color="#252b5c")
        self.sidebar.pack(side="left", fill="y")

        self.main = ctk.CTkFrame(self, fg_color="#11142d")
        self.main.pack(side="right", expand=True, fill="both")

        self.build_sidebar()
        self.build_dashboard()

        self.after(1000, self.update_local_uptime)
        self.after(500, self.process_logs)
        self.after(5000, self.auto_update_remote_status)

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

        for text, cmd in buttons:
            ctk.CTkButton(
                self.sidebar,
                text=text,
                command=cmd
            ).pack(fill="x", padx=20, pady=5)

    def build_dashboard(self):
        self.status_badge = ctk.CTkLabel(
            self.main,
            text="Remote Status: Unknown"
        )
        self.status_badge.pack(pady=10)

        self.log_box = ctk.CTkTextbox(self.main, height=250)
        self.log_box.pack(fill="both", expand=True, padx=20, pady=10)

        self.remote_info = ctk.CTkTextbox(self.main, height=200)
        self.remote_info.pack(fill="both", expand=True, padx=20, pady=10)

        self.bottom_status_frame = ctk.CTkFrame(self.main, fg_color="#11142d")
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

    def git_status(self):
        self.log("Checking Git status...")

        try:
            self.run_command(["git", "status"])
            self.run_command(["git", "remote", "-v"])
        except Exception as e:
            self.log(f"Git status failed: {e}")

    def abort_git_conflict(self):
        self.log("Attempting to abort active Git conflict/rebase...")

        self.run_command(["git", "rebase", "--abort"], allow_fail=True)
        self.run_command(["git", "merge", "--abort"], allow_fail=True)

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
        self.log("Pushing updates to GitHub...")

        try:
            self.ensure_gitignore_protection()

            status_result = self.run_command(
                ["git", "status", "--porcelain"],
                allow_fail=True
            )

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
                self.log("This usually means there is a conflict that needs manual review.")
                self.log("Click 'Abort Git Conflict' if you want to cancel the rebase.")
                self.log("Then manually resolve bot.py if needed before pushing again.")
                return

            push_result = self.run_command(
                ["git", "push", "origin", "main"],
                allow_fail=True
            )

            if push_result.returncode != 0:
                self.log("")
                self.log("Push failed.")
                self.log("Most common reason: GitHub has newer changes or secret scanning blocked the push.")
                self.log("Run Git Status, then try Push Updates again.")
                return

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
            headers = {"Authorization": f"Bearer {STATS_API_KEY}"}
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

            self.log("Remote data fetched successfully.")

        except Exception as e:
            self.status_badge.configure(text="Remote Status: Offline")
            self.railway_status_label.configure(text="Railway Status: Offline")
            self.log(f"Fetch failed: {e}")

    def auto_update_remote_status(self):
        try:
            headers = {"Authorization": f"Bearer {STATS_API_KEY}"}
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
            "customtkinter"
        ])

    def check_python(self):
        self.log(sys.version)


if __name__ == "__main__":
    app = ElysianLauncher()
    app.mainloop()