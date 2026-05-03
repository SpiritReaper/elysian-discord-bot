import os
import sys
import json
import time
import queue
import hashlib
import subprocess
import threading
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# ============================================================
# Environment / Config
# ============================================================

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_elysian_env():
    candidate_paths = [os.path.join(BASE_DIR, ".env")]
    parent_dir = os.path.dirname(BASE_DIR)
    candidate_paths.append(os.path.join(parent_dir, ".env"))

    preset_project_dir = os.environ.get("ELYSIAN_PROJECT_DIR")
    if preset_project_dir:
        candidate_paths.append(os.path.join(preset_project_dir, ".env"))

    seen = set()
    loaded = []
    for env_path in candidate_paths:
        env_path = os.path.abspath(env_path)
        if env_path in seen:
            continue
        seen.add(env_path)
        if os.path.exists(env_path):
            load_dotenv(env_path, override=False)
            loaded.append(env_path)
    return loaded


LOADED_ENV_PATHS = load_elysian_env()
ENV_PATH = LOADED_ENV_PATHS[0] if LOADED_ENV_PATHS else os.path.join(BASE_DIR, ".env")
PROJECT_DIR = os.getenv("ELYSIAN_PROJECT_DIR", BASE_DIR)
ELYSIAN_SOURCE_FILE = os.path.join(PROJECT_DIR, "elysian.py")
BOT_FILE = os.getenv("BOT_FILE", "bot.py")
API_BASE = os.getenv("ELYSIAN_API_BASE", "https://elysian-discord-bot-production.up.railway.app").rstrip("/")
STATS_URL = f"{API_BASE}/stats"
HEALTH_URL = f"{API_BASE}/health"
STATS_API_KEY = os.getenv("STATS_API_KEY", "")
SAGE_AI_FILE = os.getenv("ELYSIAN_SAGE_AI_FILE", os.path.join(BASE_DIR, "sage_ai_suggestions.jsonl"))
SAGE_CHAT_FILE = os.getenv("ELYSIAN_SAGE_CHAT_FILE", os.path.join(BASE_DIR, "sage_ai_chat.jsonl"))
SAGE_SCAN_SECONDS = int(os.getenv("ELYSIAN_SAGE_SCAN_SECONDS", "30"))
ALERT_ERROR_THRESHOLD = int(os.getenv("ELYSIAN_ALERT_ERROR_THRESHOLD", "1"))
SETTINGS_FILE = os.getenv("ELYSIAN_SETTINGS_FILE", os.path.join(BASE_DIR, "elysian_launcher_settings.json"))
APP_VERSION = "2.1.0-pyside6-copilot"


# ============================================================
# Theme System
# ============================================================

THEMES = {
    "Light": {
        "window": "#F6F7FB",
        "sidebar": "#FFFFFF",
        "header": "#FFFFFF",
        "card": "#FFFFFF",
        "surface": "#F1F3F9",
        "surface_alt": "#F8FAFC",
        "text": "#17142A",
        "muted": "#8B8EA3",
        "border": "#ECEEF6",
        "accent": "#7C3AED",
        "accent_hover": "#6D28D9",
        "accent_soft": "#F3E8FF",
        "cyan": "#4CC9F0",
        "success": "#22C55E",
        "warning": "#F59E0B",
        "danger": "#EF4444",
        "terminal_bg": "#10101F",
        "terminal_text": "#F8F7FF",
    },
    "Dark": {
        "window": "#0B0A1F",
        "sidebar": "#141229",
        "header": "#17142F",
        "card": "#18152F",
        "surface": "#100F24",
        "surface_alt": "#1E1A3D",
        "text": "#F8F7FF",
        "muted": "#8E8BAE",
        "border": "#292443",
        "accent": "#7C3AED",
        "accent_hover": "#9D4EDD",
        "accent_soft": "#22183D",
        "cyan": "#4CC9F0",
        "success": "#22C55E",
        "warning": "#F59E0B",
        "danger": "#FF4D6D",
        "terminal_bg": "#070716",
        "terminal_text": "#E8E6FF",
    },
}


# ============================================================
# Small UI Components
# ============================================================

class AnimatedButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(44)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 55))
        self.setGraphicsEffect(shadow)


class StatCard(Card):
    def __init__(self, title, value="Unknown", hint="", parent=None):
        super().__init__(parent)
        self.title_label = QLabel(title)
        self.value_label = QLabel(value)
        self.hint_label = QLabel(hint)
        self.value_label.setObjectName("StatValue")
        self.title_label.setObjectName("MutedLabel")
        self.hint_label.setObjectName("MutedLabel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(5)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.hint_label)

    def set_value(self, value, hint=None):
        self.value_label.setText(str(value))
        if hint is not None:
            self.hint_label.setText(str(hint))


# ============================================================
# Main Launcher
# ============================================================

class ElysianLauncher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Elysian")
        self.resize(1320, 820)
        self.setMinimumSize(1120, 740)

        self.is_closing = False
        self.process = None
        self.start_time = None
        self.log_queue = queue.Queue()
        self.git_conflict_active = False
        self.last_error_signature = None
        self.last_uptime_seconds = None
        self.last_sage_scan_time = 0
        self.sage_suggestions = []
        self.sage_chat_history = []
        self.last_remote_data = {}
        self.last_health_data = {}
        self.last_command_output = ""

        self.settings = self.load_launcher_settings()
        self.current_theme_name = self.settings.get("theme", "Dark")
        if self.current_theme_name not in THEMES:
            self.current_theme_name = "Dark"

        self.central = QWidget()
        self.setCentralWidget(self.central)
        self.root_layout = QHBoxLayout(self.central)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(260)
        self.root_layout.addWidget(self.sidebar)

        self.main = QFrame()
        self.main.setObjectName("Main")
        self.root_layout.addWidget(self.main, 1)

        self.main_layout = QVBoxLayout(self.main)
        self.main_layout.setContentsMargins(22, 20, 22, 20)
        self.main_layout.setSpacing(18)

        self.build_sidebar()
        self.build_topbar()
        self.build_pages()
        self.apply_theme(self.current_theme_name)

        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self.process_logs)
        self.log_timer.start(500)

        self.uptime_timer = QTimer(self)
        self.uptime_timer.timeout.connect(self.update_local_uptime)
        self.uptime_timer.start(1000)

        self.remote_timer = QTimer(self)
        self.remote_timer.timeout.connect(self.auto_update_remote_status)
        self.remote_timer.start(5000)

        if not STATS_API_KEY:
            self.log("Warning: STATS_API_KEY is missing from .env. /stats may return Unauthorized.")

    # ---------------- UI Build ----------------

    def build_sidebar(self):
        layout = QVBoxLayout(self.sidebar)
        layout.setContentsMargins(20, 28, 20, 18)
        layout.setSpacing(10)

        self.logo = QLabel("◆ Elysian")
        self.logo.setObjectName("Logo")
        self.subtitle = QLabel("Bot control center")
        self.subtitle.setObjectName("MutedLabel")
        layout.addWidget(self.logo)
        layout.addWidget(self.subtitle)
        layout.addSpacing(18)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons = {}

        nav_items = [
            ("Dashboard", "☰", 0),
            ("Utility", "⚙", 1),
            ("Sage AI", "✦", 2),
            ("Console", "⌁", 3),
            ("Analytics", "◈", 4),
            ("Settings", "◐", 5),
            ("Help", "?", 6),
        ]
        for name, icon, index in nav_items:
            button = AnimatedButton(f"{icon}  {name}")
            button.setCheckable(True)
            button.setObjectName("NavButton")
            button.clicked.connect(lambda checked=False, i=index: self.switch_page(i))
            self.nav_group.addButton(button, index)
            self.nav_buttons[name] = button
            layout.addWidget(button)

        self.nav_buttons["Dashboard"].setChecked(True)
        layout.addStretch()
        self.version_label = QLabel(f"Version {APP_VERSION}")
        self.version_label.setObjectName("MutedLabel")
        layout.addWidget(self.version_label)

    def build_topbar(self):
        self.topbar = QFrame()
        self.topbar.setObjectName("Topbar")
        top_layout = QHBoxLayout(self.topbar)
        top_layout.setContentsMargins(18, 12, 18, 12)
        top_layout.setSpacing(12)

        self.page_title = QLabel("Dashboard")
        self.page_title.setObjectName("PageTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search here")
        self.search.setObjectName("SearchBox")
        self.search.setMaximumWidth(360)

        self.theme_toggle = AnimatedButton("☾ Dark")
        self.theme_toggle.setObjectName("PrimaryButton")
        self.theme_toggle.setFixedWidth(110)
        self.theme_toggle.clicked.connect(self.toggle_theme)

        self.update_button = AnimatedButton("Check for Updates")
        self.update_button.setObjectName("PrimaryButton")
        self.update_button.setFixedWidth(170)
        self.update_button.clicked.connect(self.check_for_elysian_updates)

        top_layout.addWidget(self.page_title)
        top_layout.addStretch()
        top_layout.addWidget(self.search)
        top_layout.addWidget(self.theme_toggle)
        top_layout.addWidget(self.update_button)
        self.main_layout.addWidget(self.topbar)

    def build_pages(self):
        self.stack = QStackedWidget()
        self.main_layout.addWidget(self.stack, 1)

        self.dashboard_page = self.build_dashboard_page()
        self.utility_page = self.build_utility_page()
        self.sage_page = self.build_sage_page()
        self.console_page = self.build_console_page()
        self.analytics_page = self.build_analytics_page()
        self.settings_page = self.build_settings_page()
        self.help_page = self.build_help_page()

        for page in [
            self.dashboard_page,
            self.utility_page,
            self.sage_page,
            self.console_page,
            self.analytics_page,
            self.settings_page,
            self.help_page,
        ]:
            self.stack.addWidget(page)

    def build_dashboard_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        grid = QGridLayout()
        grid.setSpacing(16)
        self.remote_status_card = StatCard("Remote Status", "Unknown", "Railway bot")
        self.local_status_card = StatCard("Local Status", "Stopped", "Local bot.py")
        self.local_uptime_card = StatCard("Local Uptime", "00:00:00", "Current session")
        self.railway_runtime_card = StatCard("Railway Runtime", "Unknown", "Live deployment")
        for i, card in enumerate([self.remote_status_card, self.local_status_card, self.local_uptime_card, self.railway_runtime_card]):
            grid.addWidget(card, 0, i)
            grid.setColumnStretch(i, 1)
        layout.addLayout(grid)

        action_grid = QGridLayout()
        action_grid.setSpacing(16)
        actions = [
            ("Utility", "Start stop restart update Git packages and Python tools", 1),
            ("Sage AI", "Chat with SageAI and analyze bot health risk errors and suggested fixes", 2),
            ("Console", "View clean command logs and runtime output", 3),
            ("Analytics", "Review health stats temp channels errors and runtime", 4),
        ]
        for i, (title, desc, page_index) in enumerate(actions):
            card = Card()
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(22, 18, 22, 18)
            title_label = QLabel(title)
            title_label.setObjectName("CardTitle")
            desc_label = QLabel(desc)
            desc_label.setObjectName("MutedLabel")
            desc_label.setWordWrap(True)
            button = AnimatedButton("Open")
            button.setObjectName("PrimaryButton")
            button.clicked.connect(lambda checked=False, idx=page_index: self.switch_page(idx))
            card_layout.addWidget(title_label)
            card_layout.addWidget(desc_label)
            card_layout.addStretch()
            card_layout.addWidget(button)
            action_grid.addWidget(card, i // 2, i % 2)
            action_grid.setColumnStretch(i % 2, 1)
        layout.addLayout(action_grid, 1)

        self.remote_info = QTextEdit()
        self.remote_info.setObjectName("TextPanel")
        self.remote_info.setReadOnly(True)
        layout.addWidget(self.remote_info, 1)
        return page

    def build_utility_page(self):
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        items = [
            ("Fetch Remote Data", "☁", self.fetch_data),
            ("Check Health", "♥", self.check_health),
            ("Start Local Bot", "▶", self.start_bot),
            ("Stop Local Bot", "■", self.stop_bot),
            ("Restart Local Bot", "↻", self.restart_bot),
            ("Push Updates to GitHub", "⇧", self.update_code),
            ("Git Status", "⌁", self.git_status),
            ("Abort Git Conflict", "⚠", self.abort_git_conflict),
            ("Update Packages", "▣", self.update_packages),
            ("Python Version", "🐍", self.check_python),
            ("Clear Logs", "✕", self.clear_logs),
        ]

        self.utility_buttons = {}
        for i, (title, icon, command) in enumerate(items):
            card = Card()
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(20, 18, 20, 18)
            label = QLabel(f"{icon}  {title}")
            label.setObjectName("CardTitle")
            desc = QLabel("Run this launcher action")
            desc.setObjectName("MutedLabel")
            button = AnimatedButton("Run")
            button.setObjectName("PrimaryButton")
            button.clicked.connect(command)
            card_layout.addWidget(label)
            card_layout.addWidget(desc)
            card_layout.addStretch()
            card_layout.addWidget(button)
            self.utility_buttons[title] = button
            layout.addWidget(card, i // 3, i % 3)

        for column in range(3):
            layout.setColumnStretch(column, 1)
        return page

    def build_sage_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        grid = QGridLayout()
        grid.setSpacing(16)
        self.sage_online_card = StatCard("Bot Online", "Unknown", "Live status")
        self.sage_error_card = StatCard("Recent Errors", "0", "Latest scan")
        self.sage_channel_card = StatCard("Temp Channels", "0 active", "Voice system")
        self.sage_risk_card = StatCard("Risk Level", "Unknown", "Sage AI")
        for i, card in enumerate([self.sage_online_card, self.sage_error_card, self.sage_channel_card, self.sage_risk_card]):
            grid.addWidget(card, 0, i)
            grid.setColumnStretch(i, 1)
        layout.addLayout(grid)

        actions = QHBoxLayout()
        self.sage_scan_button = AnimatedButton("Analyze Now")
        self.sage_scan_button.setObjectName("PrimaryButton")
        self.sage_scan_button.clicked.connect(self.sage_manual_scan)
        self.sage_copy_button = AnimatedButton("Copy Suggestion")
        self.sage_copy_button.setObjectName("PrimaryButton")
        self.sage_copy_button.clicked.connect(self.copy_latest_sage_suggestion)
        self.sage_apply_button = AnimatedButton("Apply Suggested Action")
        self.sage_apply_button.setObjectName("PrimaryButton")
        self.sage_apply_button.clicked.connect(self.apply_latest_sage_suggestion)
        self.sage_ignore_button = AnimatedButton("Ignore Latest")
        self.sage_ignore_button.setObjectName("GhostButton")
        self.sage_ignore_button.clicked.connect(self.ignore_latest_sage_suggestion)
        for button in [self.sage_scan_button, self.sage_copy_button, self.sage_apply_button, self.sage_ignore_button]:
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)

        sage_body = QHBoxLayout()
        sage_body.setSpacing(16)

        self.sage_output = QTextEdit()
        self.sage_output.setObjectName("TextPanel")
        self.sage_output.setReadOnly(True)
        sage_body.addWidget(self.sage_output, 2)

        copilot_card = Card()
        copilot_card.setMinimumWidth(400)
        copilot_layout = QVBoxLayout(copilot_card)
        copilot_layout.setContentsMargins(18, 16, 18, 16)
        copilot_layout.setSpacing(10)

        copilot_title = QLabel("SageAI CoPilot")
        copilot_title.setObjectName("CardTitle")
        copilot_hint = QLabel("Talk with Sage about errors health Git conflicts packages temp channels uptime resets and next steps")
        copilot_hint.setObjectName("MutedLabel")
        copilot_hint.setWordWrap(True)

        self.sage_chat_box = QTextEdit()
        self.sage_chat_box.setObjectName("Terminal")
        self.sage_chat_box.setReadOnly(True)

        quick_row = QHBoxLayout()
        self.quick_explain_error = AnimatedButton("Explain Error")
        self.quick_explain_error.setObjectName("GhostButton")
        self.quick_explain_error.clicked.connect(lambda: self.ask_sage_preset("Explain the latest error and give me the safest next step"))
        self.quick_next_step = AnimatedButton("Next Step")
        self.quick_next_step.setObjectName("GhostButton")
        self.quick_next_step.clicked.connect(lambda: self.ask_sage_preset("What should I do next based on the current bot health"))
        quick_row.addWidget(self.quick_explain_error)
        quick_row.addWidget(self.quick_next_step)

        input_row = QHBoxLayout()
        self.sage_chat_input = QLineEdit()
        self.sage_chat_input.setObjectName("SearchBox")
        self.sage_chat_input.setPlaceholderText("Ask SageAI what should I check next")
        self.sage_chat_input.returnPressed.connect(self.handle_sage_chat)
        self.sage_chat_send = AnimatedButton("Send")
        self.sage_chat_send.setObjectName("PrimaryButton")
        self.sage_chat_send.setFixedWidth(90)
        self.sage_chat_send.clicked.connect(self.handle_sage_chat)
        input_row.addWidget(self.sage_chat_input, 1)
        input_row.addWidget(self.sage_chat_send)

        copilot_layout.addWidget(copilot_title)
        copilot_layout.addWidget(copilot_hint)
        copilot_layout.addWidget(self.sage_chat_box, 1)
        copilot_layout.addLayout(quick_row)
        copilot_layout.addLayout(input_row)
        sage_body.addWidget(copilot_card, 1)

        layout.addLayout(sage_body, 1)
        self.insert_text(self.sage_output, "Sage AI is ready. It will monitor remote bot health recent errors reconnect events temp channels and uptime resets.", "success")
        self.add_sage_chat_message("SageAI", "CoPilot online. Ask me about bot errors health checks Git issues uptime resets temp channels or what to do next.", "success")
        return page

    def build_console_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header = QFrame()
        header.setObjectName("Card")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)
        title = QLabel("Elysian Console")
        title.setObjectName("CardTitle")
        hint = QLabel("INFO / SUCCESS / WARN / CRITICAL")
        hint.setObjectName("MutedLabel")
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(hint)
        layout.addWidget(header)

        self.log_box = QTextEdit()
        self.log_box.setObjectName("Terminal")
        self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box, 1)
        self.console_log("Console initialized", "success")
        return page

    def build_analytics_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        grid = QGridLayout()
        grid.setSpacing(16)
        self.analytics_servers = StatCard("Servers", "Unknown", "From /stats")
        self.analytics_active_temp = StatCard("Active Temp Channels", "Unknown", "Live channels")
        self.analytics_total_temp = StatCard("Total Temp Channels", "Unknown", "Created lifetime")
        self.analytics_errors = StatCard("Errors", "0", "Recent errors")
        for i, card in enumerate([self.analytics_servers, self.analytics_active_temp, self.analytics_total_temp, self.analytics_errors]):
            grid.addWidget(card, 0, i)
            grid.setColumnStretch(i, 1)
        layout.addLayout(grid)

        self.analytics_panel = QTextEdit()
        self.analytics_panel.setObjectName("TextPanel")
        self.analytics_panel.setReadOnly(True)
        self.analytics_panel.setText("Fetch remote data to populate analytics.")
        layout.addWidget(self.analytics_panel, 1)
        return page

    def build_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        card = Card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 18, 22, 18)
        title = QLabel("Appearance")
        title.setObjectName("CardTitle")
        body = QLabel("Elysian uses one clean visual system with only Light and Dark mode rounded cards soft motion violet actions and cyan highlights.")
        body.setObjectName("MutedLabel")
        body.setWordWrap(True)
        row = QHBoxLayout()
        light = AnimatedButton("Light Mode")
        dark = AnimatedButton("Dark Mode")
        light.setObjectName("PrimaryButton")
        dark.setObjectName("PrimaryButton")
        light.clicked.connect(lambda: self.change_theme("Light"))
        dark.clicked.connect(lambda: self.change_theme("Dark"))
        row.addWidget(light)
        row.addWidget(dark)
        row.addStretch()
        card_layout.addWidget(title)
        card_layout.addWidget(body)
        card_layout.addLayout(row)
        layout.addWidget(card)
        layout.addStretch()
        return page

    def build_help_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.about_box = QTextEdit()
        self.about_box.setObjectName("TextPanel")
        self.about_box.setReadOnly(True)
        self.about_box.setText(
            "ELYSIAN LAUNCHER HELP GUIDE\n\n"
            "This launcher is a control panel for your Elysian Discord bot.\n\n"
            "Railway is the live cloud-hosted bot. Local bot controls only affect the copy running on your PC for testing.\n\n"
            "Utility includes remote data health checks local bot controls Git actions package updates Python version and log cleanup.\n\n"
            "Sage AI has two parts. The monitor checks online status recent errors temporary channels uptime resets and risk level. The CoPilot lets you ask questions and receive contextual next-step guidance based on the latest stats logs Git state and Sage scan.\n\n"
            "Never push your .env file to GitHub. Keep Discord tokens in Railway Variables and your local .env only."
        )
        layout.addWidget(self.about_box)
        return page

    # ---------------- Theme / Navigation ----------------

    def switch_page(self, index):
        names = ["Dashboard", "Utility", "Sage AI", "Console", "Analytics", "Settings", "Help"]
        self.stack.setCurrentIndex(index)
        self.page_title.setText(names[index])
        for name, button in self.nav_buttons.items():
            button.setChecked(name == names[index])

        anim = QPropertyAnimation(self.stack.currentWidget(), b"windowOpacity", self)
        anim.setDuration(180)
        anim.setStartValue(0.6)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        self._last_anim = anim

    def toggle_theme(self):
        self.change_theme("Light" if self.current_theme_name == "Dark" else "Dark")

    def change_theme(self, theme_name):
        self.current_theme_name = theme_name
        self.settings["theme"] = theme_name
        self.save_launcher_settings()
        self.apply_theme(theme_name)
        self.log(f"Launcher theme changed to {theme_name} mode.")

    def apply_theme(self, theme_name):
        t = THEMES[theme_name]
        self.theme_toggle.setText("☀ Light" if theme_name == "Light" else "☾ Dark")
        self.setStyleSheet(f"""
            * {{
                font-family: 'Segoe UI';
                font-size: 13px;
            }}
            QMainWindow, #Main {{
                background: {t['window']};
            }}
            #Sidebar {{
                background: {t['sidebar']};
                border-right: 1px solid {t['border']};
            }}
            #Topbar, #Card, Card, #Card {{
                background: {t['card']};
                border: 1px solid {t['border']};
                border-radius: 22px;
            }}
            #Logo {{
                color: {t['text']};
                font-size: 27px;
                font-weight: 800;
            }}
            #PageTitle {{
                color: {t['text']};
                font-size: 26px;
                font-weight: 800;
            }}
            #CardTitle {{
                color: {t['text']};
                font-size: 18px;
                font-weight: 800;
            }}
            #StatValue {{
                color: {t['text']};
                font-size: 28px;
                font-weight: 900;
            }}
            #MutedLabel {{
                color: {t['muted']};
            }}
            #NavButton {{
                background: transparent;
                color: {t['text']};
                text-align: left;
                padding: 10px 14px;
                border-radius: 16px;
                border: none;
                font-weight: 700;
            }}
            #NavButton:hover {{
                background: {t['accent_soft']};
            }}
            #NavButton:checked {{
                background: {t['accent']};
                color: white;
            }}
            #PrimaryButton {{
                background: {t['accent']};
                color: white;
                border: none;
                border-radius: 18px;
                padding: 10px 16px;
                font-weight: 800;
            }}
            #PrimaryButton:hover {{
                background: {t['accent_hover']};
            }}
            #GhostButton {{
                background: {t['surface_alt']};
                color: {t['text']};
                border: 1px solid {t['border']};
                border-radius: 18px;
                padding: 10px 16px;
                font-weight: 800;
            }}
            #GhostButton:hover {{
                background: {t['accent_soft']};
            }}
            #SearchBox {{
                background: {t['surface_alt']};
                color: {t['text']};
                border: 1px solid {t['border']};
                border-radius: 18px;
                padding: 10px 14px;
            }}
            #TextPanel {{
                background: {t['card']};
                color: {t['text']};
                border: 1px solid {t['border']};
                border-radius: 22px;
                padding: 12px;
            }}
            #Terminal {{
                background: {t['terminal_bg']};
                color: {t['terminal_text']};
                border: 1px solid {t['border']};
                border-radius: 22px;
                padding: 12px;
                font-family: Consolas;
                font-size: 12px;
            }}
            QTextEdit {{
                selection-background-color: {t['accent']};
            }}
        """)

    # ---------------- Settings ----------------

    def load_launcher_settings(self):
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
                    return json.load(file)
        except Exception:
            pass
        return {}

    def save_launcher_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
                json.dump(self.settings, file, indent=4)
        except Exception as e:
            self.log(f"Could not save launcher settings: {e}")

    # ---------------- Logging ----------------

    def classify_log_level(self, msg):
        text = str(msg).lower()
        if text.startswith("running:"):
            return "cmd"
        if any(word in text for word in ["critical", "fatal", "traceback", "exception", "failed", "unauthorized", "crash", "denied"]):
            return "critical"
        if any(word in text for word in ["warning", "offline", "not ready", "conflict", "missing", "invalid", "skipped", "could not"]):
            return "warning"
        if any(word in text for word in ["online", "active", "successful", "success", "started", "healthy"]):
            return "success"
        return "normal"

    def insert_text(self, target, msg, level="normal"):
        color = THEMES[self.current_theme_name].get("text")
        if level == "success":
            color = THEMES[self.current_theme_name]["success"]
        elif level == "warning":
            color = THEMES[self.current_theme_name]["warning"]
        elif level == "critical":
            color = THEMES[self.current_theme_name]["danger"]
        elif level == "cmd":
            color = THEMES[self.current_theme_name]["cyan"]
        target.moveCursor(QTextCursor.End)
        target.setTextColor(QColor(color))
        target.insertPlainText(str(msg) + "\n")
        target.moveCursor(QTextCursor.End)

    def console_log(self, message, level=None):
        if not hasattr(self, "log_box"):
            return
        level = level or self.classify_log_level(message)
        label = {
            "normal": "INFO",
            "success": "SUCCESS",
            "warning": "WARN",
            "critical": "CRITICAL",
            "cmd": "CMD",
        }.get(level, "INFO")
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.insert_text(self.log_box, f"[{timestamp}] {label:<8}: {message}", level)

    def log(self, msg):
        level = self.classify_log_level(msg)
        self.console_log(msg, level)

    def clear_logs(self):
        self.log_box.clear()
        self.remote_info.clear()
        self.analytics_panel.clear()
        self.console_log("Console cleared", "success")

    # ---------------- Bot / Process ----------------

    def start_bot(self):
        if self.process and self.process.poll() is None:
            self.log("Local bot is already running.")
            return
        if not os.path.exists(BOT_FILE):
            self.log("bot.py not found.")
            return
        self.process = subprocess.Popen([sys.executable, BOT_FILE], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.start_time = time.time()
        self.local_status_card.set_value("Running", "Local bot.py")
        threading.Thread(target=self.read_output, daemon=True).start()
        self.log("Local bot started.")

    def stop_bot(self):
        if not self.process:
            self.log("No local bot process is running.")
            return
        self.shutdown_local_processes(log_message=False)
        self.local_status_card.set_value("Stopped", "Local bot.py")
        self.local_uptime_card.set_value("00:00:00", "Current session")
        self.log("Local bot stopped.")

    def restart_bot(self):
        self.log("Restarting local bot.")
        self.stop_bot()
        QTimer.singleShot(1000, self.start_bot)

    def shutdown_local_processes(self, log_message=True):
        proc = self.process
        if proc and proc.poll() is None:
            if log_message:
                self.log("Stopping local bot process before closing launcher.")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except Exception:
                    pass
        self.process = None
        self.start_time = None

    def read_output(self):
        try:
            for line in self.process.stdout:
                self.log_queue.put(line.strip())
        except Exception as e:
            self.log_queue.put(f"Log read error: {e}")

    def process_logs(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get()
            self.last_command_output = msg
            self.log(msg)

    def update_local_uptime(self):
        if self.start_time and self.process and self.process.poll() is None:
            elapsed = int(time.time() - self.start_time)
            self.local_uptime_card.set_value(str(timedelta(seconds=elapsed)), "Current session")
        elif self.process and self.process.poll() is not None:
            self.process = None
            self.start_time = None
            self.local_status_card.set_value("Stopped", "Local bot.py")
            self.local_uptime_card.set_value("00:00:00", "Current session")

    # ---------------- Remote / Stats ----------------

    def check_health(self):
        self.log("Checking remote health.")
        try:
            response = requests.get(HEALTH_URL, timeout=10)
            if response.status_code != 200:
                self.remote_status_card.set_value("Offline", f"HTTP {response.status_code}")
                self.log(f"Health check failed with status code: {response.status_code}")
                self.log(response.text)
                return
            data = response.json()
            self.last_health_data = data
            self.remote_info.clear()
            for key, value in data.items():
                self.insert_text(self.remote_info, f"{key}: {value}")
            self.remote_status_card.set_value("Online" if data.get("bot_ready") else "Starting", "Health endpoint")
            self.log("Health check successful.")
        except Exception as e:
            self.remote_status_card.set_value("Offline", "Health endpoint")
            self.log(f"Health check failed: {e}")

    def fetch_data(self):
        self.log("Fetching remote bot data.")
        try:
            headers = {"Authorization": f"Bearer {STATS_API_KEY}"} if STATS_API_KEY else {}
            response = requests.get(STATS_URL, headers=headers, timeout=10)
            if response.status_code != 200:
                self.remote_status_card.set_value("Offline", f"HTTP {response.status_code}")
                self.log(f"Fetch failed with status code: {response.status_code}")
                self.log(response.text)
                return
            data = response.json()
            self.last_remote_data = data
            self.refresh_remote_visuals(data)
            self.remote_info.clear()
            for key, value in data.items():
                self.insert_text(self.remote_info, f"{key}: {value}")
            self.analytics_panel.clear()
            self.insert_text(self.analytics_panel, json.dumps(data, indent=2))
            self.update_sage_monitor(data, force=True)
            self.log("Remote data fetched successfully.")
        except Exception as e:
            self.remote_status_card.set_value("Offline", "Stats endpoint")
            self.log(f"Fetch failed: {e}")

    def auto_update_remote_status(self):
        try:
            headers = {"Authorization": f"Bearer {STATS_API_KEY}"} if STATS_API_KEY else {}
            response = requests.get(STATS_URL, headers=headers, timeout=5)
            if response.status_code == 200:
                data = response.json()
                self.last_remote_data = data
                self.refresh_remote_visuals(data)
                self.update_sage_monitor(data)
            else:
                self.remote_status_card.set_value("Offline", f"HTTP {response.status_code}")
        except Exception:
            self.remote_status_card.set_value("Offline", "Stats endpoint")

    def refresh_remote_visuals(self, data):
        online = bool(data.get("bot_online"))
        self.remote_status_card.set_value("Online" if online else "Offline", "Railway bot")
        self.railway_runtime_card.set_value(data.get("uptime", "Unknown"), "Live deployment")
        self.analytics_servers.set_value(data.get("server_count", data.get("guild_count", "Unknown")), "From /stats")
        self.analytics_active_temp.set_value(data.get("active_temp_channels", "Unknown"), "Live channels")
        self.analytics_total_temp.set_value(data.get("total_temp_channels_created", "Unknown"), "Created lifetime")
        self.analytics_errors.set_value(len(data.get("recent_errors", []) or []), "Recent errors")

    # ---------------- SageAI CoPilot Chat ----------------

    def add_sage_chat_message(self, speaker, message, level="normal"):
        if not hasattr(self, "sage_chat_box"):
            return
        color = THEMES[self.current_theme_name].get("text")
        if level == "success":
            color = THEMES[self.current_theme_name]["success"]
        elif level == "warning":
            color = THEMES[self.current_theme_name]["warning"]
        elif level == "critical":
            color = THEMES[self.current_theme_name]["danger"]
        elif level == "cmd":
            color = THEMES[self.current_theme_name]["cyan"]
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.sage_chat_box.moveCursor(QTextCursor.End)
        self.sage_chat_box.setTextColor(QColor(color))
        self.sage_chat_box.insertPlainText(f"[{timestamp}] {speaker}: {message}\n\n")
        self.sage_chat_box.moveCursor(QTextCursor.End)

        chat_entry = {"time": timestamp, "speaker": speaker, "message": message}
        self.sage_chat_history.append(chat_entry)
        if len(self.sage_chat_history) > 80:
            self.sage_chat_history.pop(0)
        self.write_sage_chat(chat_entry)

    def write_sage_chat(self, entry):
        try:
            with open(SAGE_CHAT_FILE, "a", encoding="utf-8") as file:
                file.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def ask_sage_preset(self, prompt):
        if hasattr(self, "sage_chat_input"):
            self.sage_chat_input.setText(prompt)
            self.handle_sage_chat()

    def handle_sage_chat(self):
        question = self.sage_chat_input.text().strip()
        if not question:
            return
        self.sage_chat_input.clear()
        self.add_sage_chat_message("You", question, "cmd")
        answer, level = self.generate_sage_response(question)
        self.add_sage_chat_message("SageAI", answer, level)

    def generate_sage_response(self, question):
        q = question.lower().strip()
        data = self.last_remote_data or {}
        latest = self.sage_suggestions[-1] if self.sage_suggestions else None
        errors = data.get("recent_errors", []) or []
        latest_error = errors[-1] if errors else None
        online = data.get("bot_online")
        uptime = data.get("uptime", "Unknown")
        uptime_seconds = data.get("uptime_seconds", "Unknown")
        active_temp = data.get("active_temp_channels", "Unknown")
        total_temp = data.get("total_temp_channels_created", "Unknown")
        guild_count = data.get("server_count", data.get("guild_count", "Unknown"))

        wants_status = any(word in q for word in ["status", "health", "online", "ready", "uptime", "server", "stats"])
        wants_error = any(word in q for word in ["error", "exception", "failed", "failure", "problem", "issue", "traceback"])
        wants_git = any(word in q for word in ["git", "push", "rebase", "merge", "conflict", "github"])
        wants_temp = any(word in q for word in ["temp", "channel", "voice", "category", "setup"])
        wants_next = any(word in q for word in ["next", "recommend", "should", "fix", "solution", "do now"])

        if not data and (wants_status or wants_error or wants_temp or wants_next):
            return "I do not have fresh remote stats yet. Click Analyze Now or Fetch Remote Data first so I can inspect the latest /stats response.", "warning"

        if wants_error:
            if latest_error:
                issue = latest_error.get("error", latest_error) if isinstance(latest_error, dict) else latest_error
                if latest:
                    return (
                        f"Latest detected issue: {issue}\n"
                        f"Likely cause: {latest.get('cause')}\n"
                        f"Recommended fix: {latest.get('suggested_fix')}\n"
                        f"Risk level: {latest.get('risk')}"
                    ), "warning" if latest.get("risk") != "Critical" else "critical"
                return f"Latest detected issue: {issue}. I recommend checking Console and Railway logs for the matching traceback before pushing another update.", "warning"
            return "I do not see recent errors in the current stats snapshot. The safest next step is to run Check Health and then Fetch Remote Data again if behavior still looks wrong.", "success"

        if wants_status:
            state = "online" if online else "offline or not ready"
            return (
                f"Current remote bot status appears {state}.\n"
                f"Railway runtime: {uptime}\n"
                f"Uptime seconds: {uptime_seconds}\n"
                f"Servers: {guild_count}\n"
                f"Active temp channels: {active_temp}\n"
                f"Total temp channels created: {total_temp}"
            ), "success" if online else "critical"

        if wants_temp:
            return (
                f"Temp channel snapshot: {active_temp} active and {total_temp} total created. "
                "If temp channels stop working after a redeploy run /setup again in Discord and confirm the saved category and New Party channel still exist. "
                "Also confirm the bot role has Manage Channels Move Members View Channel and Connect permissions."
            ), "normal"

        if wants_git:
            if self.git_conflict_active or self.detect_git_conflict():
                return "A Git conflict or rebase appears active. Push is blocked. Run Git Status first then use Abort Git Conflict only if you want to cancel the active rebase or merge.", "warning"
            return "No active Git conflict is detected right now. Run Git Status to verify before pushing updates to GitHub. If Git Status shows modified files review them before Push Updates.", "success"

        if any(word in q for word in ["package", "pip", "python version", "dependency", "module", "import"]):
            return "For dependency issues run Python Version first then Update Packages. If a module import error appears copy the exact error into SageAI and I will identify the likely missing package.", "normal"

        if any(word in q for word in ["restart", "redeploy", "railway reset", "uptime reset"]):
            return "If uptime reset recently Railway likely restarted or redeployed. Confirm bot_online is true then test temp channels. If temp channels act wrong run /setup again and review restore_temporary_channels related logs.", "warning"

        if wants_next:
            if latest:
                level = "success" if latest.get("risk") == "Healthy" else "warning"
                if latest.get("risk") == "Critical":
                    level = "critical"
                return (
                    f"My recommended next step: {latest.get('suggested_fix')}\n"
                    f"Reason: {latest.get('cause')}\n"
                    f"Risk level: {latest.get('risk')}"
                ), level
            return "I recommend clicking Analyze Now first. After I inspect /stats I can give you a more specific next step.", "normal"

        if any(word in q for word in ["what can you do", "help", "commands"]):
            return "I can explain the latest bot error check current health summarize temp channel status guide Git conflict recovery suggest package fixes and tell you the safest next action based on the latest Sage scan.", "normal"

        return "I can help with bot health errors Git conflicts package issues uptime resets temp channel setup and next-step troubleshooting. Ask me about current status latest error Git conflict or what you should do next.", "normal"

    # ---------------- Sage AI Monitor ----------------

    def sage_signature(self, data):
        errors = data.get("recent_errors", []) or []
        if errors:
            latest = errors[-1]
            if isinstance(latest, dict):
                return f"error:{latest.get('time')}:{latest.get('error')}"
            return f"error:{latest}"
        return f"state:{data.get('bot_online')}:{data.get('uptime_seconds')}:{len(errors)}"

    def sage_manual_scan(self):
        self.log("Sage AI manual scan requested.")
        self.fetch_data()

    def update_sage_monitor(self, data, force=False):
        now = time.time()
        signature = self.sage_signature(data)
        should_scan = force or signature != self.last_error_signature or now - self.last_sage_scan_time >= SAGE_SCAN_SECONDS
        self.refresh_sage_visuals(data)
        if should_scan:
            self.last_error_signature = signature
            self.last_sage_scan_time = now
            suggestion = self.analyze_bot_health(data)
            self.show_sage_suggestion(suggestion)
            if suggestion.get("risk") != "Healthy":
                self.add_sage_chat_message(
                    "SageAI",
                    f"I detected an issue. Risk: {suggestion.get('risk')}. Suggested fix: {suggestion.get('suggested_fix')}",
                    "warning" if suggestion.get("risk") != "Critical" else "critical",
                )

    def refresh_sage_visuals(self, data):
        online = bool(data.get("bot_online"))
        recent_errors = data.get("recent_errors", []) or []
        active_temp = data.get("active_temp_channels", 0)
        total_temp = data.get("total_temp_channels_created", 0)
        risk = self.calculate_sage_risk(data)
        self.sage_online_card.set_value("Online" if online else "Offline", "Live status")
        self.sage_error_card.set_value(str(len(recent_errors)), "Latest scan")
        self.sage_channel_card.set_value(f"{active_temp} active", f"{total_temp} total")
        self.sage_risk_card.set_value(risk, "Sage AI")

    def calculate_sage_risk(self, data):
        if not data.get("bot_online"):
            return "Critical"
        errors = data.get("recent_errors", []) or []
        if len(errors) >= ALERT_ERROR_THRESHOLD:
            return "Needs Review"
        if self.last_uptime_seconds is not None and data.get("uptime_seconds", 0) < self.last_uptime_seconds:
            return "Restarted"
        return "Healthy"

    def analyze_bot_health(self, data):
        errors = data.get("recent_errors", []) or []
        events = data.get("recent_events", []) or []
        online = bool(data.get("bot_online"))
        uptime_seconds = data.get("uptime_seconds", 0)
        active_temp = data.get("active_temp_channels", 0)
        total_temp = data.get("total_temp_channels_created", 0)
        risk = self.calculate_sage_risk(data)

        issue = "No active issue detected."
        cause = "The bot is online and the latest stats look normal."
        fix = "No fix needed right now. Keep monitoring."
        action_type = "none"
        action_payload = ""

        latest_error = errors[-1] if errors else None
        if isinstance(latest_error, dict):
            latest_error_text = str(latest_error.get("error", ""))
        else:
            latest_error_text = str(latest_error or "")
        error_lower = latest_error_text.lower()

        if not online:
            issue = "Railway bot appears offline."
            cause = "The /stats endpoint responded but bot_online is false. Discord may not be ready or the bot restarted."
            fix = "Check Railway logs verify DISCORD_TOKEN then wait for reconnect. If it stays offline redeploy from Railway or push a known-good commit."
            action_type = "open_console"
        elif latest_error:
            issue = latest_error_text
            if "unauthorized" in error_lower:
                cause = "Elysian may be missing the correct STATS_API_KEY or Railway variable does not match local .env."
                fix = "Confirm STATS_API_KEY in local .env matches Railway Variables then restart Elysian."
                action_type = "copy_text"
                action_payload = fix
            elif "missing permission" in error_lower or "forbidden" in error_lower or "denied" in error_lower:
                cause = "Discord rejected a channel action because the bot role likely lacks permissions or is too low in the role list."
                fix = "Move the bot role above managed roles and confirm Manage Channels Move Members View Channel and Connect permissions."
                action_type = "copy_text"
                action_payload = fix
            elif "category" in error_lower and "not found" in error_lower:
                cause = "The saved category_id no longer points to a real Discord category."
                fix = "Run /setup again in Discord so bot.py saves the current category and New Party channel."
                action_type = "copy_text"
                action_payload = fix
            elif "disconnect" in error_lower or "gateway" in error_lower:
                cause = "Discord gateway connection likely dropped during network instability or a Railway restart."
                fix = "Watch for a reconnect or resumed event. If it does not recover check Railway logs and redeploy."
                action_type = "open_console"
            else:
                cause = "A new bot error was reported by /stats but Sage does not have a specific rule for it yet."
                fix = "Review Console and Railway logs. Copy this error and inspect the matching bot.py function."
                action_type = "open_console"
        elif self.last_uptime_seconds is not None and uptime_seconds < self.last_uptime_seconds:
            issue = "Railway runtime appears to have reset."
            cause = "uptime_seconds is lower than the previous sample which usually means Railway restarted or redeployed."
            fix = "Confirm the bot recovered temp channels. If temp channels act wrong run /setup or check restore events."
            action_type = "copy_text"
            action_payload = fix

        self.last_uptime_seconds = uptime_seconds
        suggestion = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "risk": risk,
            "issue": issue,
            "cause": cause,
            "suggested_fix": fix,
            "action_type": action_type,
            "action_payload": action_payload,
            "bot_online": online,
            "uptime_seconds": uptime_seconds,
            "active_temp_channels": active_temp,
            "total_temp_channels_created": total_temp,
            "recent_error_count": len(errors),
            "latest_event": events[-1] if events else None,
        }
        self.sage_suggestions.append(suggestion)
        if len(self.sage_suggestions) > 50:
            self.sage_suggestions.pop(0)
        self.write_sage_suggestion(suggestion)
        return suggestion

    def write_sage_suggestion(self, suggestion):
        try:
            with open(SAGE_AI_FILE, "a", encoding="utf-8") as file:
                file.write(json.dumps(suggestion) + "\n")
        except Exception as e:
            self.log(f"Could not save Sage AI suggestion: {e}")

    def show_sage_suggestion(self, suggestion):
        level = "success" if suggestion["risk"] == "Healthy" else ("critical" if suggestion["risk"] == "Critical" else "warning")
        text = (
            "\n==================== SAGE AI SCAN ====================\n"
            f"Time: {suggestion['time']}\n"
            f"Risk: {suggestion['risk']}\n"
            f"Issue: {suggestion['issue']}\n"
            f"Likely cause: {suggestion['cause']}\n"
            f"Suggested fix: {suggestion['suggested_fix']}\n"
            f"Action available: {suggestion['action_type']}\n"
            "======================================================"
        )
        self.insert_text(self.sage_output, text, level)
        self.console_log(f"Sage AI scan complete. Risk: {suggestion['risk']}", level)

    def latest_sage_suggestion(self):
        if not self.sage_suggestions:
            QMessageBox.information(self, "Sage AI", "No Sage AI suggestions are available yet.")
            return None
        return self.sage_suggestions[-1]

    def copy_latest_sage_suggestion(self):
        suggestion = self.latest_sage_suggestion()
        if not suggestion:
            return
        text = (
            f"Sage AI Suggestion\nRisk: {suggestion['risk']}\nIssue: {suggestion['issue']}\n"
            f"Likely cause: {suggestion['cause']}\nSuggested fix: {suggestion['suggested_fix']}\n"
        )
        QApplication.clipboard().setText(text)
        self.log("Copied latest Sage AI suggestion to clipboard.")

    def apply_latest_sage_suggestion(self):
        suggestion = self.latest_sage_suggestion()
        if not suggestion:
            return
        action_type = suggestion.get("action_type")
        payload = suggestion.get("action_payload", "")
        if action_type == "copy_text" and payload:
            QApplication.clipboard().setText(payload)
            self.log("Sage AI copied the suggested manual fix to your clipboard.")
            QMessageBox.information(self, "Sage AI", "Suggested manual fix copied. Review and execute it from your end.")
        elif action_type == "open_console":
            self.switch_page(3)
            self.log("Sage AI opened the Console for manual review.")
        else:
            QMessageBox.information(self, "Sage AI", "No manual action is needed for the latest scan.")

    def ignore_latest_sage_suggestion(self):
        suggestion = self.latest_sage_suggestion()
        if suggestion:
            self.log(f"Ignored Sage AI suggestion: {suggestion.get('issue')}")
            self.insert_text(self.sage_output, "Latest Sage AI suggestion marked as ignored.", "warning")

    # ---------------- Git / Update ----------------

    def run_command(self, command, allow_fail=False):
        self.log(f"Running: {' '.join(command)}")
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=PROJECT_DIR)
        output = result.stdout.strip() if result.stdout else ""
        self.last_command_output = output
        if output:
            self.log(output)
        if result.returncode != 0 and not allow_fail:
            raise Exception(f"Command failed: {' '.join(command)}")
        return result

    def detect_git_conflict(self):
        conflict_paths = [
            os.path.join(PROJECT_DIR, ".git", "rebase-merge"),
            os.path.join(PROJECT_DIR, ".git", "rebase-apply"),
            os.path.join(PROJECT_DIR, ".git", "MERGE_HEAD"),
        ]
        self.git_conflict_active = any(os.path.exists(path) for path in conflict_paths)
        button = self.utility_buttons.get("Push Updates to GitHub")
        if button:
            button.setEnabled(not self.git_conflict_active)
        return self.git_conflict_active

    def git_status(self):
        self.log("Checking Git status.")
        try:
            self.run_command(["git", "status"], allow_fail=True)
            self.run_command(["git", "remote", "-v"], allow_fail=True)
            conflict = self.detect_git_conflict()
            self.log("Git conflict/rebase detected." if conflict else "No active Git conflict/rebase detected.")
            if conflict:
                self.add_sage_chat_message("SageAI", "Git conflict detected. Push is blocked until the rebase or merge is resolved or aborted.", "warning")
        except Exception as e:
            self.log(f"Git status failed: {e}")

    def abort_git_conflict(self):
        self.log("Attempting to abort active Git conflict/rebase.")
        self.run_command(["git", "rebase", "--abort"], allow_fail=True)
        self.run_command(["git", "merge", "--abort"], allow_fail=True)
        self.detect_git_conflict()
        self.log("Git conflict abort attempted. Run Git Status to confirm.")

    def ensure_gitignore_protection(self):
        gitignore_path = os.path.join(PROJECT_DIR, ".gitignore")
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
        self.log("Pushing updates to GitHub.")
        try:
            self.ensure_gitignore_protection()
            self.run_command(["git", "add", "."])
            commit_result = self.run_command(["git", "commit", "-m", "Update bot"], allow_fail=True)
            if commit_result.returncode != 0:
                self.log("No new local commit was created. Continuing sync anyway.")
            pull_result = self.run_command(["git", "pull", "--rebase", "origin", "main"], allow_fail=True)
            if pull_result.returncode != 0:
                self.log("Git pull/rebase failed. Click Abort Git Conflict if you want to cancel the rebase.")
                self.detect_git_conflict()
                return
            push_result = self.run_command(["git", "push", "origin", "main"], allow_fail=True)
            if push_result.returncode != 0:
                self.log("Push failed. Run Git Status then try Push Updates again.")
                return
            self.detect_git_conflict()
            self.log("Push successful. Railway will automatically redeploy.")
        except Exception as e:
            self.log(f"Push failed: {e}")

    def update_packages(self):
        self.log("Updating local Python packages.")
        subprocess.Popen([sys.executable, "-m", "pip", "install", "--upgrade", "discord.py", "python-dotenv", "aiohttp", "pytz", "requests", "PySide6"])

    def check_python(self):
        self.log(sys.version)

    def file_hash(self, path):
        try:
            with open(path, "rb") as file:
                return hashlib.sha256(file.read()).hexdigest()
        except Exception as e:
            self.log(f"Could not read file for update check: {e}")
            return None

    def check_for_elysian_updates(self):
        self.log("Checking Elysian launcher source for updates.")
        current_file = os.path.abspath(sys.argv[0])
        source_file = os.path.abspath(ELYSIAN_SOURCE_FILE)
        if not os.path.exists(source_file):
            self.log(f"Update check failed. Source file not found: {source_file}")
            QMessageBox.critical(self, "Update Check Failed", f"elysian.py was not found:\n\n{source_file}")
            return
        current_hash = self.file_hash(current_file)
        source_hash = self.file_hash(source_file)
        if not current_hash or not source_hash:
            QMessageBox.critical(self, "Update Check Failed", "Could not read one of the files needed for the update check.")
            return
        if current_hash == source_hash:
            self.log("No Elysian update available.")
            QMessageBox.information(self, "No Updates", "No Elysian launcher update is available.")
            return
        restart = QMessageBox.question(self, "Update Available", "An updated Elysian launcher file was detected.\n\nRestart now to apply it?")
        if restart == QMessageBox.Yes:
            self.rebuild_and_restart_elysian()
        else:
            self.log("Update detected but restart was cancelled.")

    def rebuild_and_restart_elysian(self):
        QMessageBox.information(self, "Rebuild", "The PySide6 launcher is ready for rebuild integration. Keep your existing batch rebuild flow here if you want the EXE updater preserved exactly.")
        self.log("Rebuild flow placeholder reached. Existing PyInstaller batch logic can be pasted here unchanged with PySide6 dependency added.")

    # ---------------- Close ----------------

    def closeEvent(self, event):
        self.is_closing = True
        self.shutdown_local_processes(log_message=False)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Elysian")
    app.setFont(QFont("Segoe UI", 10))
    window = ElysianLauncher()
    window.show()
    sys.exit(app.exec())
