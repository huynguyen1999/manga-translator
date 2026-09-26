"""Existing main-window behavior grouped by feature."""

from .main_window_shared import *
from .main_window_widgets import DynamicHeightListWidget, NoScrollComboBox


class SettingWidgetsMixin:
    def _build_dynamic_tab_content(self, tab_name: str, settings_list: list) -> QWidget:
        """
        Creates a scrollable area and populates it with settings widgets,
        now with a dedicated, collapsible section for advanced settings.
        """
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # --- NEW LOGIC: Split settings into standard and advanced groups first ---
        standard_settings = []
        advanced_settings = []
        for info in settings_list:
            if info.get("section") == "advanced":
                advanced_settings.append(info)
            else:
                standard_settings.append(info)

        # --- 1. Render all standard settings ---
        for info in standard_settings:
            widget_row = self._create_setting_row(info)
            layout.addWidget(widget_row)

        # --- 2. Render the 'Advanced Settings' separator and section ---
        # Only add the separator if there are actually advanced settings for this tab
        if advanced_settings:
            # Add some vertical space before the separator
            layout.addSpacing(15)

            # Create the separator widget (Label + Line)
            separator_container = QWidget()
            separator_layout = QVBoxLayout(separator_container)
            separator_layout.setContentsMargins(0, 5, 0, 5)
            separator_layout.setSpacing(5)

            label = QLabel("<b>ADVANCED SETTINGS</b>")

            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setFrameShadow(QFrame.Shadow.Sunken)

            separator_layout.addWidget(label)
            separator_layout.addWidget(line)

            layout.addWidget(separator_container)

            # --- 3. Render all advanced settings ---
            for info in advanced_settings:
                widget_row = self._create_setting_row(info)
                layout.addWidget(widget_row)

        # Special handling for Extra Settings tab (Theme manager, etc.)
        if tab_name == "Extra Settings":
            vram_info_label = QLabel()
            vram_info_label.setWordWrap(True)
            vram_info_label.setStyleSheet("font-size: 9pt; color: #999;")

            if self.detected_vram_gb > 0:
                vram_text = f"Detected {self.detected_vram_gb:.2f} GB of VRAM. "
                if self.detected_vram_gb <= 6:
                    vram_text += "<b>Recommendation: 'Low VRAM' mode.</b>"
                else:
                    vram_text += "<b>Recommendation: 'High VRAM' mode.</b>"
            else:
                vram_text = "Could not detect GPU VRAM. 'Low VRAM' mode is recommended for safety."

            vram_info_label.setText(vram_text)
            layout.addWidget(vram_info_label)

            separator = QFrame()
            separator.setFrameShape(QFrame.Shape.HLine)
            separator.setFrameShadow(QFrame.Shadow.Sunken)
            layout.addWidget(separator)

            font_scale_widget = self._create_font_scale_widget()
            theme_manager_widget = self._create_theme_manager_widget()
            layout.addWidget(font_scale_widget)
            layout.addWidget(theme_manager_widget)

        layout.addStretch()  # Pushes all widgets to the top
        scroll_area.setWidget(content_widget)
        return scroll_area

    def _build_tasks_tab_content(self) -> QWidget:
        """Creates the content for the 'Tasks' tab with improved layout."""
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)

        # This widget is created manually and not from ui_map.json
        device_widget_container = QWidget()
        device_layout = QHBoxLayout(device_widget_container)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.addWidget(QLabel("Task Processing Device:"))

        # We reuse the segmented button logic for consistency
        seg_button_container = QWidget()
        seg_button_layout = QHBoxLayout(seg_button_container)
        seg_button_layout.setContentsMargins(0, 0, 0, 0)
        seg_button_layout.setSpacing(0)

        button_group = QButtonGroup(seg_button_container)
        button_group.setExclusive(True)

        default_device = "Apple GPU (MPS)" if sys.platform == "darwin" else "CPU"
        for val in ["CPU", "NVIDIA GPU", "Apple GPU (MPS)"]:
            button = QPushButton(val)
            button.setCheckable(True)
            seg_button_layout.addWidget(button)
            button_group.addButton(button)
            if val == default_device:
                button.setChecked(True)

        seg_button_container.setLayout(seg_button_layout)
        device_layout.addWidget(seg_button_container, stretch=1)

        # Store a reference to this new widget so we can read its value later
        self.tasks_processing_device_widget = seg_button_container

        layout.addWidget(device_widget_container)

        # Add a separator line
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator)

        tasks_config = self.config_loader.tasks_config
        if not tasks_config:
            layout.addWidget(QLabel("Could not load tasks.json or it is empty."))
            content_widget.setLayout(layout)
            scroll_area.setWidget(content_widget)
            return scroll_area

        if not hasattr(self, 'task_settings'):
            self.task_settings = {}
            self.task_widgets = {}

        for task_key, task_info in tasks_config.items():
            task_frame = QFrame()
            task_frame.setObjectName("StyledPanel")
            task_frame.setFrameShape(QFrame.Shape.StyledPanel)
            task_layout = QVBoxLayout(task_frame)

            # --- Top Section: Title and Description ---
            title_label = QLabel(task_info.get("label", "Unnamed Task"))
            font = title_label.font()
            font.setPointSize(12)
            font.setBold(True)
            title_label.setFont(font)
            task_layout.addWidget(title_label)

            description_label = QLabel(task_info.get("description", ""))
            description_label.setWordWrap(True)
            task_layout.addWidget(description_label)

            separator = QFrame()
            separator.setFrameShape(QFrame.Shape.HLine)
            separator.setFrameShadow(QFrame.Shadow.Sunken)
            task_layout.addWidget(separator)

            # --- Middle Section: Dynamically created settings ---
            self.task_settings.setdefault(task_key, task_info.get("defaults", {}).copy())
            self.task_widgets.setdefault(task_key, {})

            settings_keys = task_info.get("settings_keys", [])
            for setting_key in settings_keys:
                widget_info = self.config_loader.full_config_data.get(setting_key)
                if widget_info:
                    widget_row = self._create_setting_row(widget_info, task_key)
                    task_layout.addWidget(widget_row)
                else:
                    task_layout.addWidget(QLabel(f"Warning: Definition for '{setting_key}' not found."))

            task_layout.addStretch(1)  # Add stretch to push buttons to the bottom

            # --- Bottom Section: Action Buttons ---
            button_container = QWidget()
            button_layout = QHBoxLayout(button_container)
            button_layout.setContentsMargins(0, 0, 0, 0)

            reset_button = QPushButton("Reset to Defaults")
            reset_button.clicked.connect(lambda checked, tk=task_key: self._reset_task_settings(tk))

            # CORRECT DEFINITION ORDER
            # Define the button first, then set its text and connect the signal.
            run_button = QPushButton()
            run_button.setText(f"Assign {task_info.get('label', 'Task')}")
            run_button.clicked.connect(lambda checked, tk=task_key: self._assign_task_to_selection(tk))

            button_layout.addWidget(reset_button, alignment=Qt.AlignmentFlag.AlignLeft)
            button_layout.addStretch()  # Pushes the two buttons apart
            button_layout.addWidget(run_button, alignment=Qt.AlignmentFlag.AlignRight)

            task_layout.addWidget(button_container)
            layout.addWidget(task_frame)

        layout.addStretch(1)  # Pushes all task frames to the top
        content_widget.setLayout(layout)
        scroll_area.setWidget(content_widget)
        return scroll_area

    def _create_setting_row(self, info: dict, context_key: str = None) -> QWidget:
        """
        Creates a single row (Label + Tooltip Icon + Widget) for a setting.
        This function now handles the special 'translator_chain_builder' case separately
        to prevent duplicate labels.
        """
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(5)

        widget_type = info.get("widget")

        # --- SPECIAL CASE: Non-interactive Label ---
        # This widget type is for display only and doesn't follow the standard flow.
        if widget_type == "label":
            widget = QLabel(info.get("label", ""))
            if "style" in info:
                widget.setStyleSheet(info["style"])
            row_layout.addWidget(widget)
            # Store a reference so we can update it later
            if not context_key:
                self.setting_widgets[info['key']] = widget
            return row_widget # Return immediately, do not process further.

        # --- PATH 1: For the special self-contained widget ---
        if widget_type == "translator_chain_builder":
            widget = self._create_translator_chain_builder(info)
            row_layout.addWidget(widget)

            if context_key:
                self.task_widgets[context_key][info['key']] = widget
            else:
                self.setting_widgets[info['key']] = widget
                if not hasattr(self, 'widget_references'): self.widget_references = {}
                self.widget_references[info['key']] = widget

            self._connect_widget_signal(info['key'], widget, context_key)

        # --- PATH 2: For all other standard widgets ---
        else:
            label_container = QWidget()
            label_layout = QHBoxLayout(label_container)
            label_layout.setContentsMargins(0, 0, 0, 0)
            label_layout.setSpacing(5)

            label_text = info.get("label", info.get("key", "N/A"))
            main_label = QLabel(label_text)
            label_layout.addWidget(main_label)

            tooltip_text = info.get("tooltip")
            if tooltip_text:
                tooltip_icon = QLabel("(?)")
                tooltip_icon.setStyleSheet("color: #40E0D0;")
                tooltip_icon.setCursor(Qt.CursorShape.PointingHandCursor)
                default_val = info.get('default', 'N/A')
                full_tooltip = f"<b>{label_text}</b><hr>{tooltip_text}<br><i>(Default: {default_val})</i>"
                tooltip_icon.setToolTip(full_tooltip)
                label_layout.addWidget(tooltip_icon)

            label_layout.addStretch()
            row_layout.addWidget(label_container, stretch=1)

            if widget_type == "segmented_button":
                widget = self._create_segmented_button(info)
            elif widget_type in ["optionmenu", "optionmenu_languages", "optionmenu_separators"]:
                widget = self._create_combobox(info)
            elif widget_type == "checkbox":
                widget = self._create_checkbox(info)
            elif widget_type == "slider":
                widget = self._create_slider(info)
            elif widget_type == "entry":
                widget = self._create_entry(info)
            elif widget_type == "language_checkbox_grid":
                widget = self._create_language_checkbox_grid(info)
            elif widget_type == "combobox_fonts":
                widget = self._create_font_combobox(info)
            elif widget_type == "entry_with_button":
                widget = self._create_entry_with_button(info)
            elif widget_type == "api_key_manager":
                widget = self._create_api_manager_widget(info)
            elif widget_type == "grid_segmented_button":
                widget = self._create_grid_segmented_button(info)
            elif widget_type == "preset_manager":
                widget = self._create_preset_manager(info)
            else:
                widget = QLabel(f"TODO: '{widget_type}'")
                widget.setStyleSheet("color: yellow;")

            row_layout.addWidget(widget, stretch=2)

            if context_key:
                self.task_widgets[context_key][info['key']] = widget
                initial_value = self.task_settings[context_key].get(info['key'])
            else:
                self.setting_widgets[info['key']] = widget
                initial_value = self.current_settings.get(info['key'])

            self._set_widget_value(info['key'], initial_value, widget)
            self._connect_widget_signal(info['key'], widget, context_key)

        return row_widget

    def _create_segmented_button(self, info: dict) -> QWidget:
        """Creates a group of toggleable buttons."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        button_group = QButtonGroup(container)
        button_group.setExclusive(True)  # Only one can be checked at a time

        values = info.get("values", [])
        for val in values:
            button = QPushButton(val)
            button.setCheckable(True)
            if val == info.get("default"):
                button.setChecked(True)
            layout.addWidget(button)
            button_group.addButton(button)

        return container

    def _create_combobox(self, info: dict) -> QComboBox:
        """Creates a dropdown (ComboBox) widget."""
        combo_box = QComboBox()
        values = info.get("values", [])

        if info.get("widget") == "optionmenu_separators":
            for group_name, translators in TRANSLATOR_GROUPS.items():
                # Add the group name as a non-selectable header
                item_index = combo_box.count()
                combo_box.addItem(group_name)
                combo_box.model().item(item_index).setEnabled(False)
                # Add the translators under that group
                combo_box.addItems(translators)
            # Set default value
            combo_box.setCurrentText(str(info.get("default")))
        else:
            combo_box.addItems(values)
            combo_box.setCurrentText(str(info.get("default")))

        return combo_box

    def _create_checkbox(self, info: dict) -> QCheckBox:
        """Creates a checkbox widget."""
        # In PySide, the label is part of the checkbox, so we pass an empty string
        check_box = QCheckBox("")
        if info.get("default") is True:
            check_box.setChecked(True)
        return check_box

    def _create_slider(self, info: dict) -> QWidget:
        """Creates a container with a QSlider and a QLabel to display its value."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        slider = QSlider(Qt.Orientation.Horizontal)

        # --- FIX: Prevent focus and wheel events ---
        # This makes the slider only controllable by dragging the handle.
        slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        value_label = QLabel()
        value_label.setMinimumWidth(45)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight)

        options = info.get("options", {})
        from_val = options.get("from_", 0)
        to_val = options.get("to", 100)

        # --- FIX: Ensure integer steps for integer-based sliders ---
        # The 'number_of_steps' key in ui_map.json can be used to control this,
        # but for now, we'll ensure the final value is rounded correctly.

        multiplier = info.get("value_multiplier", 1)
        # Use a high precision for smooth dragging
        internal_precision = 100

        slider.setMinimum(int(from_val * internal_precision))
        slider.setMaximum(int(to_val * internal_precision))

        def update_label(value):
            actual_value = (value / internal_precision) * multiplier
            value_format = info.get("value_format", "{:.0f}")

            if 'f' in value_format:
                display_value = float(actual_value)
            else:
                display_value = int(round(actual_value))

            if value_label:
                value_label.setText(value_format.format(display_value))

        slider.update_label_func = update_label
        slider.valueChanged.connect(update_label)

        default_value = info.get("default")
        initial_slider_value = 0
        if default_value is not None:
            try:
                initial_slider_value = int((float(default_value) / multiplier) * internal_precision)
            except (ValueError, TypeError):
                initial_slider_value = 0

        slider.setValue(initial_slider_value)
        update_label(initial_slider_value)

        layout.addWidget(slider)
        layout.addWidget(value_label)
        return container

    def _create_entry(self, info: dict) -> QLineEdit:
        """Creates a text input (QLineEdit) widget."""
        entry = QLineEdit()
        default_text = info.get("default", "")
        if default_text is not None:
            entry.setText(str(default_text))

        placeholder = info.get("placeholder", "")
        if placeholder:
            entry.setPlaceholderText(placeholder)

        return entry

    def _create_language_checkbox_grid(self, info: dict) -> QWidget:
        """Creates a scrollable grid of checkboxes for language selection."""
        # This widget is more complex, so it gets its own container and layout
        container = QFrame()
        container.setFrameShape(QFrame.Shape.StyledPanel)
        container_layout = QVBoxLayout(container)

        # We don't need a scroll area if the container itself can be a fixed size
        # for simplicity for now. A QScrollArea can be added later if needed.
        grid_widget = QWidget()
        grid_layout = QGridLayout(grid_widget)
        grid_layout.setSpacing(5)

        checkboxes = {}
        lang_items = [lang for lang in LANGUAGES.items() if lang[1] != 'auto']

        # Create a 2-column grid of checkboxes
        for i, (name, code) in enumerate(lang_items):
            row, col = divmod(i, 2)
            check_box = QCheckBox(name)
            grid_layout.addWidget(check_box, row, col)
            checkboxes[code] = check_box

        container_layout.addWidget(grid_widget)

        # We need to store these checkboxes somewhere to get their values later.
        # Let's create a storage for them if it doesn't exist.
        if not hasattr(self, 'widget_references'):
            self.widget_references = {}
        self.widget_references[info['key']] = checkboxes

        return container

    def _create_font_combobox(self, info: dict) -> QComboBox:
        """Creates a combobox populated with system and project fonts."""
        combo_box = QComboBox()
        font_list = self._get_font_list()
        combo_box.addItems(font_list)

        # Make the header items non-selectable for better UX
        for i, item_text in enumerate(font_list):
            if item_text.startswith("---"):
                combo_box.model().item(i).setEnabled(False)

        default_font = info.get("default", "Sans-serif")
        combo_box.setCurrentText(default_font)
        return combo_box

