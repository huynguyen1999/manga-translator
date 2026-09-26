# ===============================================================
# Main Application Window (PySide6 Version)
#
# Author: User & Gemini Collaboration
#
# Description: This module contains the main application class,
#              which is being rebuilt using PySide6 to create the UI.
# ===============================================================

from .main_window_shared import *
from .main_window_widgets import DynamicHeightListWidget, NoScrollComboBox
from .main_window_window_layout import WindowLayoutMixin
from .main_window_setting_widgets import SettingWidgetsMixin
from .main_window_translator_settings import TranslatorSettingsMixin
from .main_window_settings_state import SettingsStateMixin
from .main_window_queue import QueueMixin
from .main_window_visual import VisualTestMixin
from .main_window_pipeline import PipelineMixin
from .main_window_theme import ThemeMixin
from .main_window_api import ApiMixin


class TranslatorStudioApp(
    WindowLayoutMixin, SettingWidgetsMixin, TranslatorSettingsMixin, SettingsStateMixin, QueueMixin,
    VisualTestMixin, PipelineMixin, ThemeMixin, ApiMixin, QMainWindow,
):

    log_signal = Signal(str, str)
    pipeline_finished_signal = Signal()

    def __init__(self):
        super().__init__()
        self.project_base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        self.config_loader = ConfigLoader(self.project_base_dir)
        self._load_app_state()
        self._build_font_map()
        self.setting_widgets = {}
        self.task_widgets = {}
        self.task_settings = {}
        self.widget_references = {}
        self.current_settings = self.config_loader.get_factory_defaults()
        if sys.platform == "darwin":
            self.current_settings["processing_device"] = "Apple GPU (MPS)"
        self.job_queue = []
        self.history_queue = []
        self.selected_job_id = None
        self.is_running_pipeline = False
        self._stopped_by_user = False
        self.pipeline_process = None
        self.available_themes = {}

        # --- Variables for the Visual Compare Tab ---
        self.test_image_path = None
        self.original_pixmap_item = None
        self.translated_pixmap_item = None
        self.is_panning = False
        self.last_pan_pos = None
        self.temp_dir = os.path.join(self.project_base_dir, "MangaStudio_Data", "temp")
        self.detected_vram_gb = 0
        try:
            import torch
            if torch.cuda.is_available():
                # Get total memory in bytes and convert to gigabytes
                mem_bytes = torch.cuda.get_device_properties(0).total_memory
                self.detected_vram_gb = mem_bytes / (1024**3)
                print(f"[INFO] Detected {self.detected_vram_gb:.2f} GB of VRAM.")
        except Exception as e:
            print(f"[WARNING] Could not detect VRAM. Automatic mode will default to Safe. Error: {e}")

        # --- Pipeline for backend processing (CORRECTED LINE) ---
        temp_dir = os.path.join(self.project_base_dir, "MangaStudio_Data", "temp")
        self.pipeline = Pipeline(self, self.config_loader.python_executable, temp_dir)

        self._initialize_app()
        # Connect custom signals to their slots
        self.log_signal.connect(self._insert_log_text)
        self.pipeline_finished_signal.connect(self._on_pipeline_finished)

    def _initialize_app(self):
        """
        Sets up the main window, its properties, and creates the main layout.
        """
        print("[UI] Initializing PySide6 application window...")
        self.setWindowTitle("🎌 Manga Translation Studio - PySide")
        self.resize(1280, 720)
        self.setMinimumSize(QSize(960, 540))
        self._create_main_layout()
        print("[UI] Main layout and dynamic widgets created successfully.")
















































        # Future logic will go here
        # selected_items = self.queue_list_widget.selectedItems()
        # ... loop and remove from self.job_queue ...
        # self._update_job_list_ui()



















    def log(self, level: str, message: str):
        """
        Thread-safe method to log messages, with intelligent parsing for RAW backend output.
        It emits a signal that the main UI thread will catch.
        """
        # This logic mimics the original application's behavior for cleaner logs.
        if level.upper() == "RAW":
            # For RAW messages from the backend, we don't add our own prefix.
            # We pass the message through directly.
            raw_message = message.strip()
            msg_lower = raw_message.lower()

            # We can still re-classify the message type based on content for coloring.
            log_level_for_color = "INFO"  # Default for raw messages
            if msg_lower.startswith(('error:', 'validationerror:', 'exception:', 'traceback')):
                log_level_for_color = "ERROR"
            elif "out of memory" in msg_lower or "allocation failed" in msg_lower:
                log_level_for_color = "ERROR"

            color = LOG_COLORS.get(log_level_for_color, "white")
            # We emit the RAW message without any extra prefixes.
            self.log_signal.emit(color, raw_message)
        else:
            # For our own UI-generated logs (PIPELINE, SUCCESS, etc.), we add a prefix.
            color = LOG_COLORS.get(level.upper(), "white")
            self.log_signal.emit(color, f"[{level.upper()}] {message.strip()}")

    def _insert_log_text(self, color: str, message: str):
        """
        This is the slot that receives the log signal. It safely updates the
        QTextEdit widget from the main UI thread.
        """
        # Use simple HTML to color the text
        self.log_textbox.append(f'<span style="color:{color};">{message}</span>')

    def _clear_log(self):
        """Clears all text from the log box."""
        self.log_textbox.clear()




























    def _create_font_combobox(self, info: dict) -> QComboBox:
        """Creates a combobox populated with fonts from the project's /fonts folder."""
        combo_box = QComboBox()
        
        font_names = list(self.font_map.keys())
        
        if font_names:
            combo_box.addItems(font_names)
        else:
            combo_box.addItem("No fonts found in /fonts folder")
            combo_box.setEnabled(False)

        # Try to set the default font, otherwise select the first one
        default_font = info.get("default", "")
        if default_font in font_names:
            combo_box.setCurrentText(default_font)
        elif font_names:
            combo_box.setCurrentIndex(0)
            
        return combo_box
