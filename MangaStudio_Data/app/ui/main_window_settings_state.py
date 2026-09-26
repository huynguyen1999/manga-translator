"""Existing main-window behavior grouped by feature."""

from .main_window_shared import *
from .main_window_widgets import DynamicHeightListWidget, NoScrollComboBox


class SettingsStateMixin:
    def _handle_widget_button_click(self, key: str, associated_widget: QWidget):
        """Handles clicks for buttons that are part of a widget row."""
        if key == "font_color":
            current_color = associated_widget.text()
            if not current_color: current_color = "000000"
            color = QColorDialog.getColor(initial=f"#{current_color}", title="Choose Font Color")
            if color.isValid():
                new_color_hex = color.name()[1:]
                associated_widget.setText(new_color_hex)
                self._on_setting_changed(key)
        
        elif key == "gpt_config":
            configs_dir = os.path.join(self.project_base_dir, "MangaStudio_Data", "gpt_configs")
            os.makedirs(configs_dir, exist_ok=True)
            config_path, _ = QFileDialog.getOpenFileName(self, "Select GPT Config File", configs_dir, "YAML Files (*.yaml *.yml);;All Files (*)")
            if config_path:
                file_name = os.path.basename(config_path)
                associated_widget.setText(file_name)
                self._on_setting_changed(key)
        
        elif key in ["pre_dict_path", "post_dict_path"]:
            # --- NEW DICTIONARY LOGIC ---
            dicts_dir = os.path.join(self.project_base_dir, "MangaStudio_Data", "dicts")
            os.makedirs(dicts_dir, exist_ok=True)
            
            file_path, _ = QFileDialog.getOpenFileName(
                self, 
                "Select Dictionary File", 
                dicts_dir, 
                "Text Files (*.txt);;All Files (*)"
            )
            
            if file_path:
                # We only want the relative path from the project base directory
                relative_path = os.path.relpath(file_path, self.project_base_dir)
                associated_widget.setText(relative_path.replace("\\", "/")) # Use forward slashes for consistency
                self._on_setting_changed(key)

    def _refresh_profile_list(self):
        """Reloads the list of profiles from the directory and updates the combobox."""
        profiles_dir = os.path.join(self.project_base_dir, "MangaStudio_Data", "profiles")
        os.makedirs(profiles_dir, exist_ok=True)
        try:
            profiles = sorted([f.replace(".json", "") for f in os.listdir(profiles_dir) if f.endswith(".json")])
            self.profile_combobox.clear()
            if profiles:
                self.profile_combobox.addItems(profiles)
            else:
                self.profile_combobox.addItem("No profiles found")
        except Exception as e:
            print(f"[ERROR] Failed to refresh profiles: {e}")

    def _save_profile(self):
        """Saves the current settings dictionary as a profile."""
        name = self.profile_name_entry.text().strip()
        if not name:
            QMessageBox.warning(self, "Warning", "Please enter a profile name.")
            return

        profiles_dir = os.path.join(self.project_base_dir, "MangaStudio_Data", "profiles")
        path = os.path.join(profiles_dir, f"{name}.json")

        if os.path.exists(path):
            reply = QMessageBox.question(self, "Confirm Overwrite", f"Profile '{name}' already exists. Overwrite it?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.No:
                return

        try:
            with open(path, 'w', encoding='utf-8') as f:
                # Save the current_settings dictionary to the JSON file
                import json
                json.dump(self.current_settings, f, indent=4)

            self._refresh_profile_list()
            self.profile_combobox.setCurrentText(name)
            print(f"Profile '{name}' saved successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save profile: {e}")

    def _load_profile(self):
        """Loads a profile and applies its settings, ensuring the UI remains enabled."""
        name = self.profile_combobox.currentText()
        if not name or name == "No profiles found":
            return

        path = os.path.join(self.project_base_dir, "MangaStudio_Data", "profiles", f"{name}.json")
        if not os.path.exists(path):
            QMessageBox.critical(self, "Error", f"Profile file not found: {name}.json")
            self._refresh_profile_list()
            return

        try:
            with open(path, 'r', encoding='utf-8') as f:
                import json
                loaded_settings = json.load(f)

            # Update the settings for the currently selected job (if any)
            job_index = self._get_selected_job_index()
            if job_index is not None:
                self.job_queue[job_index]['settings'].update(loaded_settings)
            else:
                # If no job is selected, update the global defaults instead
                self.current_settings.update(loaded_settings)

            # Repopulate the panel with the new settings
            self._populate_settings_panel()

            if 'translator_chain' in loaded_settings:
                self._rebuild_chain_from_string(loaded_settings['translator_chain'])

            # Manually trigger the UI state update for the chain builder
            self._update_chain_ui_state()

            # --- CRITICAL FIX ---
            # After populating, explicitly ensure the settings panel is enabled,
            # as long as a job is selected. This prevents it from getting stuck in a disabled state.
            self._set_settings_panel_enabled(job_index is not None)

            self.log("SUCCESS", f"Profile '{name}' loaded and applied.")
            print(f"Profile '{name}' loaded successfully.")

        except Exception as e:
            error_message = f"An unexpected error occurred while loading profile '{name}'.\n\nDetails: {e}"
            print(f"[ERROR] {error_message}")
            QMessageBox.critical(self, "Profile Load Error", error_message)
        self._set_settings_panel_enabled(True)

    def _delete_profile(self):
        """Deletes the selected profile."""
        name = self.profile_combobox.currentText()
        if not name or name == "No profiles found":
            return

        reply = QMessageBox.question(self, "Confirm Delete", f"Are you sure you want to delete profile '{name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No:
            return

        profiles_dir = os.path.join(self.project_base_dir, "MangaStudio_Data", "profiles")
        path = os.path.join(profiles_dir, f"{name}.json")
        try:
            if os.path.exists(path):
                os.remove(path)
                print(f"Profile '{name}' deleted.")
                self._refresh_profile_list()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to delete profile: {e}")
            print(f"[ERROR] Failed to delete profile '{name}': {e}")

    def _on_font_scale_changed(self, text: str):
        """
        Applies a global font size by RE-APPLYING the currently selected theme,
        which will automatically use the new font scale.
        """
        # Get the name of the currently selected theme from the combobox
        current_theme_name = self.theme_combobox.currentText()
        
        # Re-apply the theme. This function is smart enough to read the new font
        # scale from the combobox and include it in the full stylesheet.
        self._apply_theme(current_theme_name)

    def _connect_widget_signal(self, key: str, widget: QWidget, context_key: str = None):
        """
        Connects the appropriate signal of a widget to the setting change handler.
        This version correctly passes the context_key to the handler.
        """
        info = self.config_loader.full_config_data.get(key, {})
        widget_type = info.get("widget")

        # Create a handler function that "captures" the current key and context_key.
        # The lambda function is perfect for this.
        handler = lambda *args, k=key, ctx=context_key: self._on_setting_changed(k, ctx)

        if isinstance(widget, QComboBox):
            # For QComboBox, currentIndexChanged sends an integer index, which we can ignore with *args
            widget.currentIndexChanged.connect(handler)
            # If this is the main translator dropdown, connect our special handlers
            if key == 'translator':
                # currentTextChanged sends the string name, which is what we need
                widget.currentTextChanged.connect(self._on_translator_changed)
                widget.currentTextChanged.connect(self._update_translator_tooltip)
                # Call it once at the beginning to set the initial tooltip
                self._update_translator_tooltip(widget.currentText())
        elif isinstance(widget, QCheckBox):
            # For QCheckBox, stateChanged sends the state, which we can ignore with *args
            widget.stateChanged.connect(handler)
            if key == 'enable_translator_chain':
                widget.stateChanged.connect(self._update_chain_ui_state)
            if key == 'restore_size_after_colorize':
                widget.stateChanged.connect(self._update_colorize_restore_ui_state)
        elif isinstance(widget, QLineEdit):
            # editingFinished has no arguments, so it works perfectly.
            widget.editingFinished.connect(handler)
        elif widget_type in ["segmented_button", "grid_segmented_button"]:
            button_group = widget.findChild(QButtonGroup)
            if button_group:
                button_group.buttonClicked.connect(handler)
        elif widget_type == "language_checkbox_grid":
            checkbox_dict = self.widget_references.get(key, {})
            if checkbox_dict:
                for checkbox in checkbox_dict.values():
                    # stateChanged sends the state, which we can ignore with *args
                    checkbox.stateChanged.connect(handler)
        elif widget_type == "slider":
            slider = widget.findChild(QSlider)
            if slider:
                # valueChanged sends the new value, which we can ignore with *args
                slider.valueChanged.connect(handler)
        elif widget_type == "entry_with_button":
            entry = widget.findChild(QLineEdit)
            if entry:
                # editingFinished has no arguments.
                entry.editingFinished.connect(handler)

    def _on_setting_changed(self, key: str, context_key: str = None):
        """
        A generic handler called whenever a setting widget's value changes.
        """
        if context_key:  # It's a setting for a special task
            widget = self.task_widgets[context_key].get(key)
            new_value = self._get_value_from_widget(key, widget)  # Pass widget directly
            self.task_settings[context_key][key] = new_value
            print(f"[Task Settings] Updated '{context_key}.{key}' to: {new_value}")
        else:  # It's a global setting for the main pipeline
            widget = self.setting_widgets.get(key)
            if key == 'translator_chain':
                new_value = self._get_translator_chain_string()
            else:
                new_value = self._get_value_from_widget(key, widget)
            self.current_settings[key] = new_value
            print(f"[Settings] Updated '{key}' to: {new_value}")

    def _on_translator_changed(self, translator_name: str):
        """Handles changes in the main translator selection."""
        # Filter the main target language dropdown
        lang_combo = self.setting_widgets.get('target_lang')
        self._filter_language_dropdown(translator_name, lang_combo)

        # Update the tooltip
        self._update_translator_tooltip(translator_name)

    def _filter_language_dropdown(self, translator_name: str, lang_combo: QComboBox):
        """
        A centralized function to filter a given language QComboBox based on
        the capabilities of the selected translator.
        """
        if not lang_combo:
            return

        capabilities = TRANSLATOR_CAPABILITIES.get(translator_name, {})
        supported_codes = set()

        if capabilities.get('__any__') == '__all__':
            all_langs = list(LANGUAGES.values())
            if "auto" in all_langs:
                all_langs.remove("auto")
            supported_codes = set(all_langs)
        else:
            for source_lang, target_langs in capabilities.items():
                supported_codes.update(target_langs)

        supported_display_names = [name for name, code in LANGUAGES.items() if code in supported_codes]

        current_selection = lang_combo.currentText()

        lang_combo.blockSignals(True)
        lang_combo.clear()
        if not supported_display_names:
            lang_combo.addItem("No Supported Targets")
            lang_combo.setEnabled(False)
        else:
            lang_combo.addItems(sorted(supported_display_names))
            lang_combo.setEnabled(True)
        lang_combo.blockSignals(False)

        if current_selection in supported_display_names:
            lang_combo.setCurrentText(current_selection)
        elif "English" in supported_display_names:
            lang_combo.setCurrentText("English")

    def _get_value_from_widget(self, key: str, widget: QWidget) -> any:
        """Retrieves the current value from a given widget by its key."""
        # The widget is now passed directly, no need for lookup
        if not widget:
            return None

        info = self.config_loader.full_config_data.get(key, {})
        widget_type = info.get("widget")

        if isinstance(widget, QComboBox):
            if widget_type == "optionmenu_languages":
                return LANGUAGES.get(widget.currentText(), "auto")
            return widget.currentText()
        elif isinstance(widget, QCheckBox):
            return widget.isChecked()
        elif isinstance(widget, QLineEdit):
            return widget.text()
        elif widget_type in ["segmented_button", "grid_segmented_button"]:
            button_group = widget.findChild(QButtonGroup)
            if button_group and button_group.checkedButton():
                value = button_group.checkedButton().text()
                if key == "upscale_ratio":
                    if value == "Disabled":
                        return None  # Return None instead of the string "Disabled"
                    else:
                        return int(value.replace("x", ""))
                return value
            return None  # Return None if no button is checked
        elif key == "language_checkbox_grid":
            checkbox_dict = self.widget_references.get(key, {})
            selected = [code for code, cb in checkbox_dict.items() if cb.isChecked()]
            return ",".join(sorted(selected))
        elif widget_type == "slider":
            slider = widget.findChild(QSlider)
            if slider:
                precision = 100
                multiplier = info.get("value_multiplier", 1)
                actual_value = (slider.value() / precision) * multiplier

                # Get the format string to decide if we need an int or a float
                value_format = info.get("value_format", "{:.0f}")

                # If the format string specifies an integer (like "{:.0f}")
                if value_format.endswith("0f}"):
                    return int(round(actual_value))
                else:
                    # Otherwise, it's a float. Return it rounded for cleanliness.
                    return round(actual_value, 4)
            return None
        elif widget_type == "entry_with_button":
            entry = widget.findChild(QLineEdit)
            if entry:
                return entry.text()
            return None

        return None

    def _set_widget_value(self, key: str, value: any, widget: QWidget):
        """Sets the value of a given widget by its key."""
        if not widget or value is None:
            return

        info = self.config_loader.full_config_data.get(key, {})
        widget_type = info.get("widget")

        if isinstance(widget, QComboBox):
            if widget_type == "optionmenu_languages":
                display_name = next((k for k, v in LANGUAGES.items() if v == value), None)
                if display_name:
                    widget.setCurrentText(display_name)
            else:
                widget.setCurrentText(str(value))
        elif isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QLineEdit):
            widget.setText(str(value))
        elif widget_type == "segmented_button":
            button_group = widget.findChild(QButtonGroup)
            if button_group:
                value_to_check = str(value)
                if key == "upscale_ratio":
                    if value is None:
                        value_to_check = "Disabled"
                    else:
                        value_to_check = f"{value}x"

                for button in button_group.buttons():
                    if button.text() == value_to_check:
                        button.setChecked(True)
                        break
        elif widget_type == "grid_segmented_button":
            button_group = widget.findChild(QButtonGroup)
            if button_group:
                value_to_check = str(value)
                for button in button_group.buttons():
                    if button.text() == value_to_check:
                        button.setChecked(True)
                        break
        elif key == "language_checkbox_grid":
            checkbox_dict = self.widget_references.get(key, {})
            selected_langs = set(str(value).split(','))
            for code, cb in checkbox_dict.items():
                cb.setChecked(code in selected_langs)
        elif widget_type == "slider":
            slider = widget.findChild(QSlider)
            if slider and value is not None:
                precision = 100
                multiplier = info.get("value_multiplier", 1)
                slider_value = int((float(value) / multiplier) * precision) if multiplier != 0 else 0
                slider.setValue(slider_value)

                update_func = getattr(slider, 'update_label_func', None)
                if update_func:
                    update_func(slider_value)
        elif widget_type == "entry_with_button":
            entry = widget.findChild(QLineEdit)
            if entry:
                entry.setText(str(value))
