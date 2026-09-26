"""Existing main-window behavior grouped by feature."""

from .main_window_shared import *
from .main_window_widgets import DynamicHeightListWidget, NoScrollComboBox


class ThemeMixin:
    def _create_theme_manager_widget(self) -> QWidget:
        """Creates the UI component for theme selection."""
        theme_frame = QFrame()
        theme_layout = QVBoxLayout(theme_frame)
        theme_layout.setContentsMargins(0, 10, 0, 0)

        label = QLabel("Appearance & Theme")
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        theme_layout.addWidget(label)

        # A sub-frame for the actual controls
        controls_frame = QWidget()
        controls_layout = QHBoxLayout(controls_frame)
        controls_layout.setContentsMargins(0, 0, 0, 0)

        label = QLabel("Select Theme ⚠⚠⚠")
        label.setToolTip(
            "Note:\n"
            "Selected button colors\n"
            "might not be styled correctly\n"
            "when using themes.\n"
            "Default: Default Qt"
        )
        controls_layout.addWidget(label)

        self.theme_combobox = QComboBox()
        self.theme_combobox.setToolTip("Changes the visual appearance of the application. Default: Default Qt")
        self._load_themes()  # Populate the combobox
        self.theme_combobox.setCurrentText("Default Qt")  # Set Default Qt as initial theme
        self.theme_combobox.currentTextChanged.connect(self._apply_theme)

        controls_layout.addWidget(self.theme_combobox, stretch=1)
        theme_layout.addWidget(controls_frame)

        return theme_frame

    def _load_themes(self):
        """Scans the themes directory and populates the theme combobox."""
        self.available_themes.clear()
        themes_dir = os.path.join(self.project_base_dir, "MangaStudio_Data", "themes")

        # Add a special option to revert to the default style
        self.available_themes["Default Qt"] = {"name": "Default Qt", "style": {}}

        if not os.path.isdir(themes_dir):
            # Even if folder not found, still allow reverting to default
            self.theme_combobox.addItems(sorted(self.available_themes.keys()))
            return

        for filename in os.listdir(themes_dir):
            if filename.endswith(".json"):
                try:
                    filepath = os.path.join(themes_dir, filename)
                    with open(filepath, 'r', encoding='utf-8') as f:
                        theme_data = json.load(f)
                        theme_name = theme_data.get("name", filename)
                        self.available_themes[theme_name] = theme_data
                except Exception as e:
                    print(f"Warning: Could not load theme file {filename}. Error: {e}")

        self.theme_combobox.addItems(sorted(self.available_themes.keys()))

    def _apply_theme(self, theme_name: str):
        """
        Applies the selected theme's stylesheet to the entire application,
        with detailed styling for interactive widget states.
        """
        font_size_text = self.font_scale_combobox.currentText()
        percentage = int(font_size_text.split('%')[0])
        base_font_size = 10
        font_size = f"{base_font_size * (percentage / 100.0)}pt"

        if theme_name == "Default Qt":
            minimal_style = f"QWidget {{ font-size: {font_size}; }}"
            self.setStyleSheet(minimal_style)
            self.log("INFO", "Reverted to default Qt theme.")
            return

        theme_data = self.available_themes.get(theme_name)
        if not theme_data or "style" not in theme_data:
            return

        colors = theme_data["style"].get("colors", {})
        # Get all colors with fallbacks
        bg_main = colors.get("background_main", "#2d2d2d")
        bg_frame = colors.get("background_frame", "#2d2d2d")
        btn_primary = colors.get("primary_button", "#3a7ebf")
        btn_hover = colors.get("primary_button_hover", "#56a9e8")
        slider_groove = colors.get("slider_groove", "#242424")
        slider_handle = colors.get("slider_handle", "#3a7ebf")
        txt_main = colors.get("text_main", "#dce4ee")
        border = colors.get("border", "#555555")
        accent = colors.get("accent", "#4a9fcf")
        indicator = colors.get("checkbox_indicator", "#dce4ee")

        style_sheet = f"""
            /* --- GLOBAL --- */
            QWidget {{
                font-size: {font_size};
                background-color: {bg_main};
                color: {txt_main};
            }}
            /* --- FRAMES & PANELS --- */
            QFrame#StyledPanel, QFrame#LeftPanel {{
                background-color: {bg_frame};
                border: 1px solid {border};
                border-radius: 5px;
            }}
            /* --- SEGMENTED BUTTONS --- */
            QPushButton:checkable {{
                background-color: {btn_primary};
                color: {txt_main};
                border: 1px solid {border};
                padding: 5px;
                border-radius: 3px;
            }}
            QPushButton:checkable:hover {{
                background-color: {btn_hover};
            }}
            QPushButton:checkable:checked {{
                background-color: {accent} !important;
                color: white !important;
                border: 2px solid {accent} !important;
            }}
            QPushButton:checkable:checked:hover {{
                background-color: {btn_hover} !important;
                border: 2px solid {btn_hover} !important;
                color: white !important;
            }}
            
            /* --- NORMAL PUSH BUTTONS --- */
            QPushButton:!checkable {{
                background-color: {btn_primary};
                color: {txt_main};
                border: 1px solid {border};
                padding: 5px;
                border-radius: 3px;
            }}
            QPushButton:!checkable:hover {{ background-color: {btn_hover}; }}
            QPushButton:disabled {{ background-color: #555555; color: #888888; }}
            
            /* --- COMBOBOX & LINEEDIT --- */
            QComboBox, QLineEdit {{
                background-color: {bg_main};
                border: 1px solid {border};
                padding: 2px;
            }}
            QComboBox::drop-down {{ border: none; }}
            /* QComboBox::down-arrow {{ image: url(./path/to/your/arrow.png); }} */

            /* --- CHECKBOX --- */
            QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid {border};
            border-radius: 3px;
            }}
            QCheckBox::indicator:unchecked {{ 
                background-color: {bg_main}; 
            }}
            QCheckBox::indicator:checked {{
                background-color: {accent};
                border-color: {accent};
            }}

            /* --- SLIDERS --- */
            QSlider::groove:horizontal {{
                border: 1px solid {border};
                background: {slider_groove};
                height: 4px;
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {slider_handle};
                border: 1px solid {slider_handle};
                width: 14px;
                margin: -6px 0; 
                border-radius: 7px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {btn_hover};
                border: 1px solid {btn_hover};
            }}
            /* --- LIST WIDGET & TEXT EDIT --- */
            QListWidget, QTextEdit {{
                background-color: {bg_main};
                color: {txt_main};
                border: 1px solid {border};
                border-radius: 3px;
            }}
            /* --- TAB WIDGET --- */
            QTabWidget::pane {{
                background-color: {bg_main};
                border: 1px solid {border};
                border-radius: 5px;
            }}
            QTabBar::tab {{
                background: {bg_main};
                padding: 6px;
                border: 1px solid {border};
                border-bottom: none;
            }}
            QTabBar::tab:selected {{
                background: {bg_frame};
                color: {txt_main};
                border-bottom: 1px solid {bg_frame}; /* Hide bottom border */
            }}
            QTabBar::tab:!selected {{
                background: {bg_main};
                color: {txt_main};
            }}
            QTabBar::tab:!selected:hover {{
                background: {bg_frame};
            }}
            /* --- TOOLTIP --- */
            QToolTip {{
                color: {txt_main};
                background-color: {bg_frame};
                border: 1px solid {border};
            }}
        """

        self.setStyleSheet(style_sheet)
        self.log("INFO", f"Theme '{theme_name}' applied successfully.")
