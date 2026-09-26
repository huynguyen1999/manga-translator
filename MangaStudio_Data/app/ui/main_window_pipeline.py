"""Existing main-window behavior grouped by feature."""

from .main_window_shared import *
from .main_window_widgets import DynamicHeightListWidget, NoScrollComboBox


class PipelineMixin:
    def _start_pipeline_thread(self):
        """Starts the main job processing pipeline in a separate thread."""
        if self.is_running_pipeline:
            return
        if not self.job_queue:
            QMessageBox.information(self, "Information", "Please add one or more jobs to the queue first.")
            return

        self._stopped_by_user = False

        self._toggle_ui_state(True)
        thread = threading.Thread(target=self._run_pipeline, daemon=True)
        thread.start()

    def _update_colorize_restore_ui_state(self):
        """Enables or disables the upscale factor widget based on the checkbox."""
        restore_checkbox = self.setting_widgets.get('restore_size_after_colorize')
        factor_widget = self.setting_widgets.get('colorize_upscale_factor')

        if not all([restore_checkbox, factor_widget]):
            return

        is_enabled = restore_checkbox.isChecked()
        factor_widget.setEnabled(is_enabled)

    def _build_final_config_for_job(self, job: dict) -> dict:
        """
        Builds the correct, nested config dictionary for a specific job
        by ONLY using the settings stored within that job object.
        """
        job_type = job.get('job_type')
        settings = job.get('settings', {})
        
        final_config = {}
        all_props = self.config_loader.full_config_data

        for key, prop_info in all_props.items():
            if key not in settings: continue
            
            value = settings.get(key)
            if value == "" or value is None: continue

            # Skip font_family, it's handled manually below
            if key == 'font_family':
                continue

            group = prop_info.get("group", "")
            if "General & Translator" in group:
                target_dict = final_config.setdefault("translator", {})
            elif "Detector & OCR" in group:
                if key in ["ocr", "use_mocr_merge", "min_text_length", "ignore_bubble", "prob"]:
                    target_dict = final_config.setdefault("ocr", {})
                else:
                    target_dict = final_config.setdefault("detector", {})
            elif "Image & Inpainter" in group:
                if key in ["upscaler", "revert_upscaling", "upscale_ratio"]:
                    target_dict = final_config.setdefault("upscale", {})
                elif key in ["colorizer", "colorization_size", "denoise_sigma", "color_threshold", "restore_size"]:
                    target_dict = final_config.setdefault("colorizer", {})
                else:
                    target_dict = final_config.setdefault("inpainter", {})
            elif "Render & Output" in group:
                target_dict = final_config.setdefault("render", {})
            else:
                final_config[key] = value
                continue
            
            target_dict[key] = value

        # --- NEW SIMPLIFIED FONT LOGIC ---
        selected_font_name = settings.get('font_family')
        if selected_font_name and selected_font_name in self.font_map:
            # Always get the path from our map and add it for the CLI argument
            final_config['font_path'] = self.font_map[selected_font_name]
            # Also add it to the render config for GIMP, just in case
            final_config.setdefault('render', {})['gimp_font'] = selected_font_name
        # --- END OF FONT LOGIC ---

        if settings.get('translator_chain'):
            final_config.get("translator", {}).pop('translator', None)
        
        final_config['processing_device'] = settings.get('processing_device', 'CPU')

        if job_type in ['R', 'U', 'C']:
            task_key_map = {'R': 'raw_output', 'U': 'upscale', 'C': 'colorize'}
            task_info = self.config_loader.tasks_config.get(task_key_map.get(job_type), {})
            backend_overrides = task_info.get("backend_config", {})
            for category, overrides in backend_overrides.items():
                final_config.setdefault(category, {}).update(overrides)

        return final_config

    def _run_pipeline(self):
        """
        Processes all 'Ready' jobs in the queue sequentially.
        This version includes "resume" functionality and smart folder naming
        to avoid conflicts, based on user settings.
        """
        try:
            while self.is_running_pipeline:
                job_to_process = next((job for job in self.job_queue if job.get('status') == 'Ready'), None)
                if not job_to_process:
                    self.log("PIPELINE", "No more 'Ready' jobs in the queue. Finishing run.")
                    break

                job = job_to_process
                self.currently_processing_job_id = job['id']
                job['status'] = 'Processing'
                self._update_job_list_ui()
                self._toggle_ui_state(True, job['id'])

                settings = job.get('settings', {})
                selected_mode = settings.get('processing_mode', 'Automatic')
                output_format = settings.get('output_format', 'png')

                mode_to_use = 'High VRAM'
                if selected_mode == 'Low VRAM' or (selected_mode == 'Automatic' and self.detected_vram_gb > 0 and self.detected_vram_gb <= 6):
                    mode_to_use = 'Low VRAM'

                # --- NEW FOLDER NAMING LOGIC ---
                source_path = job['source_path']
                job_type_tag = f"TASK-{job.get('job_type')}" if job.get('job_type') != 'T' else settings.get('target_lang', 'ENG')
                base_output_folder_name = f"{os.path.basename(source_path)}-{job_type_tag}"
                output_dir = os.path.dirname(source_path)
                
                final_output_folder_name = base_output_folder_name

                # Check the user's preference for avoiding conflicts
                if settings.get('avoid_conflicts', True):
                    counter = 1
                    # Append (1), (2), etc., until a unique name is found
                    while os.path.exists(os.path.join(output_dir, final_output_folder_name)):
                        final_output_folder_name = f"{base_output_folder_name} ({counter})"
                        counter += 1
                
                final_output_path = os.path.join(output_dir, final_output_folder_name)
                # --- END OF FOLDER NAMING LOGIC ---

                os.makedirs(final_output_path, exist_ok=True)
                all_source_files = sorted([f for f in os.listdir(source_path) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.bmp'))])

                try:
                    processed_files = {os.path.splitext(f)[0] for f in os.listdir(final_output_path) if f.lower().endswith(f".{output_format}")}
                    files_to_process = [f for f in all_source_files if os.path.splitext(f)[0] not in processed_files]
                except FileNotFoundError:
                    files_to_process = all_source_files

                if not files_to_process:
                    self.log("INFO", f"All files for job '{job['name']}' seem to be processed already. Skipping to avoid errors.")
                    job['status'] = "Completed"
                    self.job_queue.remove(job)
                    self.history_queue.append(job)
                    self._update_job_list_ui()
                    self._update_history_list_ui()
                    # A small delay to let the UI update before the pipeline finishes
                    QApplication.processEvents() 
                    continue # Move to the next job in the queue
                
                # (The rest of the function remains exactly the same as before)
                self.log("INFO", f"Found {len(files_to_process)} unprocessed image(s) for job '{job['name']}'.")

                success = True
                if mode_to_use == 'Low VRAM':
                    try:
                        batch_size = int(settings.get('batch_size', 5))
                        if batch_size <= 0: batch_size = 1
                    except ValueError:
                        batch_size = 5

                    self.log("PIPELINE", f"Resuming job '{job['name']}' in Low VRAM Mode (Batch Size: {batch_size}).")
                    num_batches = (len(files_to_process) + batch_size - 1) // batch_size

                    for i in range(num_batches):
                        if not self.is_running_pipeline:
                            success = False
                            break

                        batch_files = files_to_process[i * batch_size: (i + 1) * batch_size]
                        self.log("INFO", f"Processing batch {i + 1}/{num_batches} ({len(batch_files)} images)...")

                        temp_batch_dir = os.path.join(self.temp_dir, f"batch_{job['id']}")
                        if os.path.exists(temp_batch_dir): shutil.rmtree(temp_batch_dir)
                        os.makedirs(temp_batch_dir)
                        for f in batch_files:
                            shutil.copy(os.path.join(source_path, f), temp_batch_dir)

                        job_for_batch = copy.deepcopy(job)
                        job_for_batch['source_path'] = temp_batch_dir

                        final_config = self._build_final_config_for_job(job_for_batch)
                        is_verbose = settings.get("enable_verbose_output", False)
                        
                        batch_success = self.pipeline.run(job_for_batch, final_output_path, final_config, self.log, is_verbose, output_format)
                        shutil.rmtree(temp_batch_dir)

                        if not batch_success:
                            success = False
                            break
                else:  # High VRAM Mode
                    self.log("PIPELINE", f"Resuming job '{job['name']}' in High VRAM Mode.")

                    temp_source_dir = os.path.join(self.temp_dir, "high_vram_processing")
                    if os.path.exists(temp_source_dir): shutil.rmtree(temp_source_dir)
                    os.makedirs(temp_source_dir)
                    for f in files_to_process:
                        shutil.copy(os.path.join(source_path, f), temp_source_dir)

                    job_for_run = copy.deepcopy(job)
                    job_for_run['source_path'] = temp_source_dir

                    final_config = self._build_final_config_for_job(job_for_run)
                    is_verbose = settings.get("enable_verbose_output", False)
                    success = self.pipeline.run(job_for_run, final_output_path, final_config, self.log, is_verbose, output_format)
                    shutil.rmtree(temp_source_dir)

                job['status'] = "Completed" if success else ("Stopped" if self._stopped_by_user else "Failed")

                if not success and not self._stopped_by_user:
                    QTimer.singleShot(0, lambda j=job: QMessageBox.critical(self, "Job Failed", f"The job '{j['name']}' failed due to a critical error.\n\nCheck the Live Log for details."))

                self.job_queue.remove(job)
                self.history_queue.append(job)
                self.currently_processing_job_id = None
                self._update_job_list_ui()
                self._update_history_list_ui()

                if self._stopped_by_user:
                    self.log("PIPELINE", "Pipeline stopped by user command.")
                    break
        finally:
            self.pipeline_finished_signal.emit()

    def _stop_pipeline(self):
        """Stops the running pipeline process immediately and updates the UI."""
        if not self.is_running_pipeline:
            return

        self.log("PIPELINE", "Stop command received. Terminating backend process...")

        # --- GÜNCELLEME: Set the flag BEFORE stopping the process ---
        self._stopped_by_user = True

        # The pipeline object will handle the actual process killing.
        self.pipeline.stop(self.log)

    def _toggle_ui_state(self, is_running: bool, running_job_id: str = None):
        """
        Locks ONLY the essential UI elements during processing.
        - Toggles Start/Stop buttons.
        - Disables the specific list item being processed.
        - The rest of the UI remains interactive.
        """
        self.is_running_pipeline = is_running

        # 1. Toggle Start/Stop buttons
        self.start_button.setEnabled(not is_running)
        self.stop_button.setEnabled(is_running)

        # 2. Find and visually lock/unlock the specific job item in the queue
        for i in range(self.queue_list_widget.count()):
            item = self.queue_list_widget.item(i)
            # If a job is running and its ID matches this item's ID
            if is_running and item.data(Qt.ItemDataRole.UserRole) == running_job_id:
                # Disable interaction (can't be selected, moved, or right-clicked)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            else:
                # Ensure all other items are fully enabled
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled)

    def _set_settings_panel_enabled(self, is_enabled: bool):
        """Helper function to enable or disable all widgets in the settings panel."""
        interactive_widget_types = (QPushButton, QComboBox, QCheckBox, QSlider, QLineEdit)

        if hasattr(self, 'settings_tab_view'):
            # CORRECTED LOGIC: Loop through each type and call findChildren separately.
            for widget_type in interactive_widget_types:
                # Find all widgets of a specific type within the settings area
                for widget in self.settings_tab_view.findChildren(widget_type):
                    widget.setEnabled(is_enabled)

    def _update_progress(self, percent: float, text: str):
        """Thread-safe method to update the progress bar and label."""
        self.progress_bar.setValue(int(percent * 100))
        self.progress_label.setText(text)

    def _reset_task_settings(self, task_key: str):
        """Resets the settings of a specific task to its defaults from tasks.json."""
        if task_key not in self.task_settings:
            return

        task_info = self.config_loader.tasks_config.get(task_key, {})
        defaults = task_info.get("defaults", {})

        # Update the settings dictionary
        self.task_settings[task_key] = defaults.copy()

        # Update the widgets on the UI
        for setting_key, default_value in defaults.items():
            widget = self.task_widgets.get(task_key, {}).get(setting_key)
            if widget:
                # We must block signals here as well to prevent loops
                widget.blockSignals(True)
                self._set_widget_value(setting_key, default_value, widget)
                widget.blockSignals(False)

        self.log("INFO", f"Settings for task '{task_info.get('label')}' have been reset.")

    def _assign_task_to_selection(self, task_key: str):
        """Applies a special task's configuration and type to all selected jobs."""
        selected_items = self.queue_list_widget.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "No Job Selected", "Please select one or more jobs from the queue to assign this task.")
            return

        task_info = self.config_loader.tasks_config.get(task_key, {})
        task_settings_from_ui = self.task_settings.get(task_key, {})

        job_type_map = {'raw_output': 'R', 'upscale': 'U', 'colorize': 'C'}
        job_type = job_type_map.get(task_key, '?')

        for item in selected_items:
            job_id = item.data(Qt.ItemDataRole.UserRole)
            job_data = next((job for job in self.job_queue if job['id'] == job_id), None)
            if job_data:
                current_job_settings = task_settings_from_ui.copy()

                if task_key == 'upscale':
                    # Get value from our special grid widget
                    upscale_value_str = current_job_settings.pop('task_upscale_grid', '2x')
                    # Save it under the key the backend expects ('upscale_ratio')
                    current_job_settings['upscale_ratio'] = (
                        None if upscale_value_str == 'Disabled'
                        else int(upscale_value_str.replace('x', ''))
                    )

                if task_key == 'colorize' and current_job_settings.get('restore_size_after_colorize'):
                    try:
                        source_dir = job_data['source_path']
                        first_image_name = next((f for f in os.listdir(source_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))), None)

                        if first_image_name:
                            image_path = os.path.join(source_dir, first_image_name)
                            with Image.open(image_path) as img:
                                width, height = img.size

                            original_long_side = max(width, height)
                            target_colorize_size = int(current_job_settings.get('colorization_size', 576))

                            if target_colorize_size > 0 and original_long_side > target_colorize_size:
                                division_ratio = original_long_side / target_colorize_size
                                calculated_upscale_ratio = max(2, round(division_ratio))

                                current_job_settings['upscale_ratio'] = calculated_upscale_ratio
                                current_job_settings['revert_upscaling'] = False
                                self.log("INFO", f"Job '{job_data['name']}': Auto-calculated upscale ratio: {calculated_upscale_ratio}x")
                        else:
                            self.log("WARNING", f"Job '{job_data['name']}': Could not find an image to calculate upscale ratio. Skipping auto-upscale.")

                    except Exception as e:
                        self.log("ERROR", f"Failed to auto-calculate upscale ratio for '{job_data['name']}': {e}")

                device_widget = self.tasks_processing_device_widget
                button_group = device_widget.findChild(QButtonGroup)
                selected_device = "CPU"
                if button_group and button_group.checkedButton():
                    selected_device = button_group.checkedButton().text()
                
                current_job_settings['processing_device'] = selected_device

                current_job_settings['processing_mode'] = self._get_value_from_widget('processing_mode', self.setting_widgets.get('processing_mode'))
                current_job_settings['batch_size'] = self._get_value_from_widget('batch_size', self.setting_widgets.get('batch_size'))

                job_data['settings'] = current_job_settings
                job_data['job_type'] = job_type
                job_data['status'] = 'Ready'

        self.log("INFO", f"Assigned task '{task_info.get('label')}' to {len(selected_items)} job(s).")
        self._update_job_list_ui()

    def _on_pipeline_finished(self):
        """
        A dedicated, thread-safe function to call when the pipeline finishes.
        This centralizes the UI reset logic.
        """
        self.is_running_pipeline = False
        self.currently_processing_job_id = None
        self._update_progress(1.0, "Finished!")
        # A brief delay before unlocking allows the progress bar to show "Finished!"
        QTimer.singleShot(100, lambda: self._toggle_ui_state(False))
        QTimer.singleShot(2000, lambda: self._update_progress(0, "Ready"))

    def closeEvent(self, event):
        """Handles the window close event to save the application state."""
        if self.is_running_pipeline:
            reply = QMessageBox.question(self, "Confirm Exit",
                                         "A process is still running. Are you sure you want to stop it and exit?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self._stop_pipeline()
            else:
                event.ignore()
                return

        self._save_app_state()
        event.accept()

    def _load_app_state(self):
        """Loads application state (window geometry, last directory) from a config file."""
        self.app_settings_path = os.path.join(self.project_base_dir, "MangaStudio_Data", "studio_config.json")
        try:
            if os.path.exists(self.app_settings_path):
                with open(self.app_settings_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)

                # Restore window geometry
                geometry_hex = settings.get("window_geometry")
                if geometry_hex:
                    self.restoreGeometry(QByteArray.fromHex(geometry_hex.encode('utf-8')))

                # Restore last used directory
                self.last_selected_directory = settings.get("last_directory")
                print("[INFO] Application state loaded.")
        except Exception as e:
            print(f"[WARNING] Could not load app settings: {e}")

    def _save_app_state(self):
        """Saves the current application state to a config file."""
        if not hasattr(self, 'app_settings_path'):
            self.app_settings_path = os.path.join(self.project_base_dir, "MangaStudio_Data", "studio_config.json")

        settings = {
            # Convert QByteArray to a JSON-compatible hex string
            "window_geometry": self.saveGeometry().toHex().data().decode('utf-8'),
            "last_directory": getattr(self, 'last_selected_directory', None)
        }
        try:
            with open(self.app_settings_path, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=4)
            print("[INFO] Application state saved.")
        except Exception as e:
            print(f"[ERROR] Could not save app settings: {e}")
