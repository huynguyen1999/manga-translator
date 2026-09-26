"""Existing main-window behavior grouped by feature."""

from .main_window_shared import *
from .main_window_widgets import DynamicHeightListWidget, NoScrollComboBox


class TranslatorSettingsMixin:
    def _create_entry_with_button(self, info: dict) -> QWidget:
        """Creates a QLineEdit with a QPushButton next to it."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        entry = QLineEdit()
        entry.setText(str(info.get("default", "")))
        layout.addWidget(entry)

        button = QPushButton(info.get("button_text", "..."))
        button.setFixedWidth(40)
        # Pass both the widget key and the associated entry field to the handler
        button.clicked.connect(lambda: self._handle_widget_button_click(info['key'], entry))
        layout.addWidget(button)

        return container

    def _create_translator_chain_builder(self, info: dict) -> QWidget:
        """
        Creates a self-contained component for the translator chain,
        including its own header with a label and control buttons.
        """
        # Main container for the whole builder
        container = QFrame()
        container.setObjectName("ChainBuilderFrame")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(5)

        # --- Header Row ---
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)

        # The label is now created INSIDE the component
        label = QLabel(info.get("label", "Translation Steps:"))
        header_layout.addWidget(label)
        header_layout.addStretch()  # This pushes the buttons to the right

        # Control buttons for the list
        add_btn = QPushButton("➕ Add Step")
        remove_btn = QPushButton("➖ Remove Selected")
        header_layout.addWidget(add_btn)
        header_layout.addWidget(remove_btn)

        # Add the completed header to the main vertical layout
        container_layout.addWidget(header_widget)

        # --- List Widget ---
        self.chain_list_widget = DynamicHeightListWidget()
        self.chain_list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        container_layout.addWidget(self.chain_list_widget)

        # --- Connect signals ---
        add_btn.clicked.connect(self._add_chain_step)
        remove_btn.clicked.connect(self._remove_chain_step)

        # Store a reference for enabling/disabling the whole container
        self.widget_references[info['key']] = container

        # Initialize UI state
        QTimer.singleShot(0, self._update_chain_ui_state)

        return container

    def _create_chain_step_widget(self) -> QWidget:
        """Creates the widget for a single row/step in the translator chain."""
        step_widget = QWidget()
        layout = QHBoxLayout(step_widget)
        layout.setContentsMargins(5, 5, 5, 5)

        translator_combo = NoScrollComboBox()
        # Populate with translators from TRANSLATOR_GROUPS constant
        for group_name, translators in TRANSLATOR_GROUPS.items():
            item_index = translator_combo.count()
            translator_combo.addItem(group_name)
            translator_combo.model().item(item_index).setEnabled(False)
            translator_combo.addItems(translators)

        lang_combo = NoScrollComboBox()
        # Populate with languages from LANGUAGES constant
        lang_combo.addItems(list(LANGUAGES.keys()))

        layout.addWidget(QLabel("Translate with:"))
        layout.addWidget(translator_combo)
        layout.addWidget(QLabel("to"))
        layout.addWidget(lang_combo)

        # Store combo boxes in the widget's properties for later access
        step_widget.translator_combo = translator_combo
        step_widget.lang_combo = lang_combo

        translator_combo.currentTextChanged.connect(
            lambda text, lc=lang_combo: self._filter_language_dropdown(text, lc)
        )
        # Trigger it once to set the initial state
        self._filter_language_dropdown(translator_combo.currentText(), lang_combo)

        handler = lambda: self._on_setting_changed('translator_chain')
        translator_combo.currentTextChanged.connect(handler)
        lang_combo.currentTextChanged.connect(handler)

        return step_widget

    def _add_chain_step(self):
        """Adds a new, empty step to the translator chain list."""
        step_widget = self._create_chain_step_widget()

        list_item = QListWidgetItem(self.chain_list_widget)
        list_item.setSizeHint(step_widget.sizeHint())

        self.chain_list_widget.addItem(list_item)
        self.chain_list_widget.setItemWidget(list_item, step_widget)
        self._on_setting_changed('translator_chain')  # Notify that settings have changed
        self.chain_list_widget.updateGeometry()

    def _remove_chain_step(self):
        """Removes the currently selected step from the chain list."""
        selected_items = self.chain_list_widget.selectedItems()
        if not selected_items:
            return
        for item in selected_items:
            row = self.chain_list_widget.row(item)
            self.chain_list_widget.takeItem(row)
        self._on_setting_changed('translator_chain')
        self.chain_list_widget.updateGeometry()

    def _get_translator_chain_string(self) -> str:
        """
        Reads all steps from the chain_list_widget and builds the
        backend-compatible string (e.g., 'sugoi:ENG;deepl:TRK').
        """
        if not hasattr(self, 'chain_list_widget'):
            return ""

        steps = []
        for i in range(self.chain_list_widget.count()):
            item = self.chain_list_widget.item(i)
            widget = self.chain_list_widget.itemWidget(item)

            if widget and hasattr(widget, 'translator_combo') and hasattr(widget, 'lang_combo'):
                translator_name = widget.translator_combo.currentText()
                lang_name = widget.lang_combo.currentText()

                # Make sure the selected item is not a separator/header
                if translator_name not in TRANSLATOR_GROUPS:
                    lang_code = LANGUAGES.get(lang_name, '')
                    if lang_code:
                        steps.append(f"{translator_name}:{lang_code}")

        return ";".join(steps)

    def _rebuild_chain_from_string(self, chain_string: str):
        """Clears and rebuilds the translator chain UI from a saved string."""
        self.chain_list_widget.clear()
        if not chain_string:
            return

        steps = chain_string.split(';')
        code_to_lang_name = {v: k for k, v in LANGUAGES.items()}

        for step in steps:
            parts = step.split(':')
            if len(parts) == 2:
                translator_name, lang_code = parts

                # Add a new visual step to the list
                step_widget = self._create_chain_step_widget()
                list_item = QListWidgetItem(self.chain_list_widget)
                list_item.setSizeHint(step_widget.sizeHint())
                self.chain_list_widget.addItem(list_item)
                self.chain_list_widget.setItemWidget(list_item, step_widget)

                # Set the combobox values based on the loaded data
                step_widget.translator_combo.setCurrentText(translator_name)
                lang_name = code_to_lang_name.get(lang_code, "")
                if lang_name:
                    step_widget.lang_combo.setCurrentText(lang_name)

        self.chain_list_widget.updateGeometry()

    def _create_grid_segmented_button(self, info: dict) -> QWidget:
        """Creates a grid of toggleable buttons that can wrap to multiple lines."""
        container = QWidget()
        layout = QGridLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        button_group = QButtonGroup(container)
        button_group.setExclusive(True)

        values = info.get("values", [])
        columns = info.get("options", {}).get("columns", 4)  # Default to 4 columns

        row, col = 0, 0
        for val in values:
            button = QPushButton(val)
            button.setCheckable(True)
            if val == info.get("default"):
                button.setChecked(True)

            layout.addWidget(button, row, col)
            button_group.addButton(button)

            col += 1
            if col >= columns:
                col = 0
                row += 1

        return container

    def _create_preset_manager(self, info: dict) -> QWidget:
        """Creates the preset management compound widget."""
        preset_frame = QFrame()
        preset_frame.setObjectName("StyledPanel")
        preset_frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(preset_frame)

        self.profile_combobox = QComboBox()
        layout.addWidget(self.profile_combobox)

        self.profile_name_entry = QLineEdit()
        self.profile_name_entry.setPlaceholderText("Enter new preset name")
        layout.addWidget(self.profile_name_entry)

        # When a profile is selected from the dropdown, copy its name to the entry field
        self.profile_combobox.currentTextChanged.connect(self.profile_name_entry.setText)

        button_container = QWidget()
        button_layout = QHBoxLayout(button_container)
        button_layout.setContentsMargins(0, 0, 0, 0)

        save_btn = QPushButton("Save")
        load_btn = QPushButton("Load")
        delete_btn = QPushButton("Delete")

        button_layout.addWidget(save_btn)
        button_layout.addWidget(load_btn)
        button_layout.addWidget(delete_btn)
        layout.addWidget(button_container)

        # Connect buttons to their handler methods
        save_btn.clicked.connect(self._save_profile)
        load_btn.clicked.connect(self._load_profile)
        delete_btn.clicked.connect(self._delete_profile)

        self._refresh_profile_list()  # Initial population of the combobox
        return preset_frame

    def _get_font_list(self) -> list:
        """Gets a combined list of project and system fonts."""
        project_fonts = []
        fonts_dir = os.path.join(self.project_base_dir, "fonts")
        if os.path.isdir(fonts_dir):
            project_fonts = sorted([f for f in os.listdir(fonts_dir) if f.lower().endswith(('.ttf', '.otf'))])

        # Use QFontDatabase for a reliable way to get system fonts
        system_fonts = sorted(QFontDatabase().families())

        final_list = []
        if project_fonts:
            final_list.extend(project_fonts)
        final_list.extend(system_fonts)
        return final_list

    def _update_chain_ui_state(self):
        """
        Enables or disables the translator chain builder and the main translator dropdowns
        based on the 'Enable Translator Chain' checkbox.
        """
        # Find the necessary widgets
        enable_checkbox = self.setting_widgets.get('enable_translator_chain')
        chain_container = self.widget_references.get('translator_chain')
        main_translator_combo = self.setting_widgets.get('translator')
        main_language_combo = self.setting_widgets.get('target_lang')

        if not all([enable_checkbox, chain_container, main_translator_combo, main_language_combo]):
            return

        is_chain_enabled = enable_checkbox.isChecked()

        # Enable/disable the builder and its contents
        chain_container.setEnabled(is_chain_enabled)

        # Enable/disable the main dropdowns (opposite logic)
        main_translator_combo.setEnabled(not is_chain_enabled)
        main_language_combo.setEnabled(not is_chain_enabled)

        # If the chain is disabled, clear its contents
        if not is_chain_enabled:
            self.chain_list_widget.clear()
            self.chain_list_widget.updateGeometry()

        self._on_setting_changed('translator_chain')

    def _update_chain_list_height(self):
        """Calculates and sets the minimum height of the chain list widget to fit all its items."""
        if not hasattr(self, 'chain_list_widget'):
            return

        # Calculate the total height of all item widgets
        content_height = 0
        for i in range(self.chain_list_widget.count()):
            # Using sizeHintForRow is more accurate than getting the widget's hint directly
            content_height += self.chain_list_widget.sizeHintForRow(i)

        # Add spacing between items to the calculation
        if self.chain_list_widget.count() > 1:
            content_height += self.chain_list_widget.spacing() * (self.chain_list_widget.count() - 1)

        # Ensure the widget doesn't collapse completely when empty
        if content_height == 0:
            content_height = 40  # A sensible default height for an empty list

        # Force the layout to give the list at least this much vertical space
        self.chain_list_widget.setMinimumHeight(content_height)

    def _update_translator_tooltip(self, translator_name: str):
        """Updates the tooltip of the translator combobox to show its capabilities."""
        translator_combo = self.setting_widgets.get('translator')
        if not translator_combo:
            return

        capabilities = TRANSLATOR_CAPABILITIES.get(translator_name, {})

        # Reverse the LANGUAGES dict for easy code-to-name lookup
        code_to_name = {v: k for k, v in LANGUAGES.items()}

        tooltip_html = f"<b>{translator_name} Capabilities:</b><hr>"

        if not capabilities:
            tooltip_html += "No translation is performed."
        elif capabilities.get('__any__') == '__all__':
            tooltip_html += "Supports translation between most languages."
        else:
            lines = []
            for source_code, target_codes in capabilities.items():
                source_name = code_to_name.get(source_code, source_code)
                target_names = [code_to_name.get(tc, tc) for tc in target_codes]
                lines.append(f"<b>From {source_name}:</b><br>  → {', '.join(target_names)}")
            tooltip_html += "<br>".join(lines)

        translator_combo.setToolTip(tooltip_html)

    def _create_bottom_panel(self) -> QWidget:
        """Creates the bottom panel with progress bar and control buttons."""
        bottom_frame = QFrame()
        bottom_frame.setFrameShape(QFrame.Shape.NoFrame)
        layout = QHBoxLayout(bottom_frame)
        layout.setContentsMargins(0, 0, 0, 0)

        progress_widget = QWidget()
        progress_layout = QVBoxLayout(progress_widget)
        progress_layout.setSpacing(2)
        progress_layout.setContentsMargins(0, 0, 0, 0)

        self.progress_label = QLabel("Ready")
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)

        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)

        self.start_button = QPushButton("▶️ START PIPELINE")
        self.start_button.clicked.connect(self._start_pipeline_thread)
        self.start_button.setFixedHeight(40)
        font = self.start_button.font()
        font.setBold(True)
        self.start_button.setFont(font)

        self.stop_button = QPushButton("⏹️ STOP")
        self.stop_button.clicked.connect(self._stop_pipeline)
        self.stop_button.setEnabled(False)
        self.stop_button.setFixedHeight(40)

        layout.addWidget(progress_widget, stretch=1)
        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)

        return bottom_frame

    def _create_font_scale_widget(self) -> QWidget:
        """Creates a special row for the UI font scaling option."""
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(5)

        label = QLabel("UI Font Scale:")
        label.setToolTip("Changes the font size for the entire application UI.")
        row_layout.addWidget(label, stretch=1)

        self.font_scale_combobox = QComboBox()
        self.font_scale_combobox.addItems(["75%", "85%", "100% (Default)", "115%", "125%", "150%"])
        self.font_scale_combobox.setCurrentText("100% (Default)")

        # Connect the combobox signal to the handler function
        self.font_scale_combobox.currentTextChanged.connect(self._on_font_scale_changed)

        row_layout.addWidget(self.font_scale_combobox, stretch=2)
        return row_widget

