"""Existing main-window behavior grouped by feature."""

from .main_window_shared import *
from .main_window_widgets import DynamicHeightListWidget, NoScrollComboBox


class QueueMixin:
    def _add_job(self):
        """Opens a dialog to select a folder and adds it as a job."""
        # Use the last selected directory, or the project base directory as a fallback.
        initial_dir = getattr(self, 'last_selected_directory', self.project_base_dir)

        folder_path = QFileDialog.getExistingDirectory(self, "Select Manga/Image Folder", initial_dir)

        if folder_path:
            # Store the newly selected directory to be saved on exit.
            self.last_selected_directory = folder_path
            self._add_job_from_path(folder_path)

    def _add_job_from_path(self, path):
        """
        Adds a job with a default 'Awaiting Config' status to the queue
        and selects it in the UI.
        """
        import time

        job_id = f"job_{int(time.time() * 1000)}_{len(self.job_queue)}"
        job_data = {
            "id": job_id,
            "source_path": path,
            "name": os.path.basename(path),
            # A new job starts with a fresh copy of factory defaults
            "settings": self.config_loader.get_factory_defaults().copy(),
            # A new job is awaiting configuration by the user
            "status": "Awaiting Config",  # Status: ⚪
            # A new job has no assigned type until a configuration is applied
            "job_type": None
        }
        self.job_queue.append(job_data)

        # Refresh the entire queue UI to show the new job
        self._update_job_list_ui()

        # --- CRITICAL FIX: Select the newly added item in the correct list widget ---
        # Find the item we just added by its unique job_id and set it as the current row.
        for i in range(self.queue_list_widget.count()):
            item = self.queue_list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == job_id:
                # Setting the current row will automatically trigger the
                # _on_job_selection_changed signal, which is what we want.
                self.queue_list_widget.setCurrentRow(i)
                break

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if os.path.isdir(path):
                    self._add_job_from_path(path)
                else:
                    self.log("WARNING", f"Dropped item is not a directory: {path}")
            event.acceptProposedAction()
        else:
            event.ignore()

    def _remove_selected_jobs_from_queue(self):
        """Placeholder for removing selected jobs. To be implemented with context menu."""
        print("Action: Remove Selected Jobs (Not yet implemented)")

    def _duplicate_selected_jobs(self):
        """
        Creates a new, clean job using the same source path as the selected job(s).
        The new job will have factory default settings and no assigned job type.
        """
        selected_items = self.queue_list_widget.selectedItems()
        if not selected_items:
            return

        jobs_to_add = []
        for item in selected_items:
            original_job_id = item.data(Qt.ItemDataRole.UserRole)
            original_job = next((job for job in self.job_queue if job['id'] == original_job_id), None)

            if original_job:
                # Create a completely new job dictionary, only reusing the source path and name.
                # This is similar to adding a brand new job.
                new_job = {
                    "id": f"job_{int(time.time() * 1000)}_{len(self.job_queue) + len(jobs_to_add)}",
                    "source_path": original_job['source_path'],
                    "name": original_job['name'],
                    # The new job gets fresh factory defaults, not copied ones.
                    "settings": self.config_loader.get_factory_defaults().copy(),
                    # The new job starts as a blank slate, awaiting configuration.
                    "status": "Awaiting Config",
                    "job_type": None
                }
                jobs_to_add.append(new_job)

        self.job_queue.extend(jobs_to_add)

        self._update_job_list_ui()
        self.log("INFO", f"Duplicated {len(jobs_to_add)} job(s) as new, unconfigured tasks.")

    def _clear_queue(self):
        """Removes all jobs from the queue after confirmation."""
        if not self.job_queue:
            return

        reply = QMessageBox.question(self, "Confirm Clear Queue",
                                     "Are you sure you want to remove ALL jobs from the queue?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            self.job_queue.clear()
            self.log("INFO", "All jobs have been cleared from the queue.")
            self._update_job_list_ui()

    def _clear_history(self):
        """Removes all jobs from the history list after confirmation."""
        if not self.history_queue:
            return

        reply = QMessageBox.question(self, "Confirm Clear History",
                                     "Are you sure you want to remove ALL jobs from the history?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            self.history_queue.clear()
            self.log("INFO", "History has been cleared.")
            self._update_history_list_ui()

    def _move_job(self, direction: str):
        """Moves the selected job up or down in the queue."""
        # This function is now superseded by drag-and-drop, but we keep it for now.
        if not self.selected_job_id or len(self.job_queue) < 2:
            return

        index = self._get_selected_job_index()
        if index is None:
            return

        if direction == "up" and index > 0:
            new_index = index - 1
        elif direction == "down" and index < len(self.job_queue) - 1:
            new_index = index + 1
        else:
            return

        self.job_queue.insert(new_index, self.job_queue.pop(index))
        self._update_job_list_ui()

        # Keep the moved item selected in the new list widget
        self.queue_list_widget.setCurrentRow(new_index)

    def _update_job_list_ui(self):
        """
        Refreshes both the queue and history list widgets based on the current state
        of self.job_queue and self.history_queue.
        """
        # Block signals to prevent selection changes from firing events during redraw
        self.queue_list_widget.blockSignals(True)
        self.queue_list_widget.clear()

        # Populate the queue list
        for i, job in enumerate(self.job_queue, 1):  # Use enumerate to get numbers starting from 1
            status_icon = "⚪"
            if job.get('status') == "Ready":
                status_icon = "🟢"
            elif job.get('status') == "Processing":
                status_icon = "🟡"

            job_type = job.get('job_type')
            job_type_tag = f"[{job_type}]" if job_type else ""

            # Prepend the number to the display text
            display_text = f"{i}. {job_type_tag} {status_icon} {job['name']}"
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, job['id'])  # Store ID for reference
            self.queue_list_widget.addItem(item)

        self.queue_list_widget.blockSignals(False)

    def _update_history_list_ui(self):
        """Refreshes the history list widget based on the self.history_queue."""
        self.history_list_widget.clear()

        # Populate the history list from the end (most recent first)
        for i, job in enumerate(reversed(self.history_queue), 1):  # Use enumerate here as well
            status = job.get('status', 'Unknown')

            if status == "Completed":
                status_icon = "✅"
            elif status == "Failed":
                status_icon = "❌"
            elif status == "Stopped":
                status_icon = "⏹️"
            else:
                status_icon = "❔"

            job_type = job.get('job_type')
            job_type_tag = f"[{job_type}]" if job_type else ""

            # Prepend the number
            display_text = f"{i}. {job_type_tag} {status_icon} {job['name']}"

            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, job['id'])

            # Color the item based on status
            if status == "Failed" or status == "Stopped":
                item.setForeground(Qt.GlobalColor.red)
            elif status == "Completed":
                item.setForeground(Qt.GlobalColor.green)

            self.history_list_widget.addItem(item)

    def _on_job_selection_changed(self):
        """
        Handles the logic when a different job is selected in the queue list.
        This now ONLY updates the internal reference to the selected job ID
        and no longer automatically loads its settings into the panel.
        """
        selected_items = self.queue_list_widget.selectedItems()
        if not selected_items:
            self.selected_job_id = None
        else:
            # We still need to know which job is selected for context menu actions.
            self.selected_job_id = selected_items[0].data(Qt.ItemDataRole.UserRole)

        print(f"[Jobs] Selection changed to job ID: {self.selected_job_id}. Panel state is not affected.")

    def _populate_settings_panel(self):
        """
        Updates all setting widgets to reflect the settings of the currently selected job
        OR the application's default settings if no job is selected.
        This function is now smart enough to handle special compound widgets.
        """
        # Determine the source of settings
        job_index = self._get_selected_job_index()
        if job_index is not None:
            settings_source = self.job_queue[job_index]['settings']
        else:
            # If no job is selected, show factory defaults
            settings_source = self.config_loader.get_factory_defaults()

        # Update the main settings dictionary to reflect what's being shown
        self.current_settings = copy.deepcopy(settings_source)

        # Block signals on all widgets to prevent infinite loops during programmatic changes
        for widget in self.setting_widgets.values():
            if widget:
                widget.blockSignals(True)
                if isinstance(widget, QWidget) and widget.findChild(QSlider):
                    widget.findChild(QSlider).blockSignals(True)

        # Update all widgets with the new values from the determined source
        for key, value in self.current_settings.items():
            widget = self.setting_widgets.get(key)
            if widget:
                # SPECIAL HANDLING for the translator chain
                if key == 'translator_chain':
                    # The value is a string like 'sugoi:ENG'. Rebuild the UI from it.
                    if hasattr(self, '_rebuild_chain_from_string'):
                        self._rebuild_chain_from_string(value or "")
                    # Also update the 'enable' checkbox state
                    enable_checkbox = self.setting_widgets.get('enable_translator_chain')
                    if enable_checkbox:
                        # If the chain string is not empty, the checkbox should be checked.
                        is_chain_enabled = bool(value)
                        enable_checkbox.setChecked(is_chain_enabled)
                        # Trigger the UI state update to enable/disable the correct panels
                        self._update_chain_ui_state()
                else:
                    # Standard handling for all other widgets
                    self._set_widget_value(key, value, widget)

        # Unblock signals to restore normal user interaction
        for widget in self.setting_widgets.values():
            if widget:
                widget.blockSignals(False)
                if isinstance(widget, QWidget) and widget.findChild(QSlider):
                    widget.findChild(QSlider).blockSignals(False)

    def _get_selected_job_index(self) -> int | None:
        """Finds the index in job_queue for the currently selected job_id."""
        if not self.selected_job_id:
            return None
        for i, job in enumerate(self.job_queue):
            if job['id'] == self.selected_job_id:
                return i
        return None

    def _show_queue_context_menu(self, position):
        """Creates and shows the context menu for the queue list with Checkpoint logic."""
        selected_items = self.queue_list_widget.selectedItems()
        if not selected_items:
            return

        menu = QMenu()

        # Action 1: Save settings TO the job
        save_action = menu.addAction("✅ Save Settings to Job (Checkpoint)")
        save_action.triggered.connect(self._save_settings_to_job)

        # Action 2: Load settings FROM the job
        load_action = menu.addAction("✏️ Load Job Settings to Panel")
        if len(selected_items) != 1:
            load_action.setDisabled(True)
            load_action.setToolTip("Select only one job to load its settings.")
        load_action.triggered.connect(self._load_settings_from_job)

        menu.addSeparator()

        # Action 3: Duplicate Job
        duplicate_action = menu.addAction("➕ Duplicate Job (as new task)")
        duplicate_action.triggered.connect(self._duplicate_selected_jobs)

        # Action 4: Remove
        remove_action = menu.addAction("🗑️ Remove from Queue")
        remove_action.triggered.connect(self._remove_selected_jobs_from_queue)

        menu.exec(self.queue_list_widget.mapToGlobal(position))

    def _apply_settings_to_selection(self):
        """Applies the main configuration from the 'Configuration' tabs to the selected jobs."""
        selected_items = self.queue_list_widget.selectedItems()
        if not selected_items:
            return

        for item in selected_items:
            job_id = item.data(Qt.ItemDataRole.UserRole)
            job_data = next((job for job in self.job_queue if job['id'] == job_id), None)
            if job_data:
                # Assign settings from the main config tabs
                job_data['settings'] = self.current_settings.copy()
                job_data['status'] = 'Ready'
                job_data['job_type'] = 'T'

        self.log("INFO", f"Applied 'Translate [T]' settings to {len(selected_items)} job(s).")
        self._update_job_list_ui()

    def _remove_selected_jobs_from_queue(self):
        """Removes all selected jobs from the queue."""
        selected_items = self.queue_list_widget.selectedItems()
        if not selected_items:
            return

        ids_to_remove = {item.data(Qt.ItemDataRole.UserRole) for item in selected_items}

        # Rebuild the job_queue, excluding the jobs to be removed
        self.job_queue = [job for job in self.job_queue if job['id'] not in ids_to_remove]

        self.log("INFO", f"Removed {len(ids_to_remove)} job(s) from the queue.")
        # If the currently selected job was removed, clear the selection
        if self.selected_job_id in ids_to_remove:
            self.selected_job_id = None
            self._populate_settings_panel()

        self._update_job_list_ui()

    def _save_settings_to_job(self):
        """Saves the current panel settings to the selected job(s) (Checkpoint)."""
        selected_items = self.queue_list_widget.selectedItems()
        if not selected_items:
            return
        
        processing_mode_value = self._get_value_from_widget('processing_mode', self.setting_widgets.get('processing_mode'))
        batch_size_value = self._get_value_from_widget('batch_size', self.setting_widgets.get('batch_size'))
        
        for item in selected_items:
            job_id = item.data(Qt.ItemDataRole.UserRole)
            job = next((j for j in self.job_queue if j['id'] == job_id), None)
            if job:
                job['settings'] = copy.deepcopy(self.current_settings)
                job['status'] = 'Ready'
                # If the job has no type, default it to 'Translate'
                if not job.get('job_type'):
                    job['job_type'] = 'T'

        self._update_job_list_ui()
        self.log("SUCCESS", f"Checkpoint created. Saved settings to {len(selected_items)} job(s).")

    def _load_settings_from_job(self):
        """Loads a selected job's settings back into the main panel for editing."""
        selected_items = self.queue_list_widget.selectedItems()
        # This action should only work when a single job is selected
        if len(selected_items) != 1:
            return

        job_id = selected_items[0].data(Qt.ItemDataRole.UserRole)
        job = next((j for j in self.job_queue if j['id'] == job_id), None)

        if job:
            # Load the job's settings into the main panel
            self.current_settings = copy.deepcopy(job['settings'])
            self._populate_settings_panel()
            self.log("INFO", f"Loaded settings from '{job['name']}' into the panel for editing.")

    def _requeue_job(self):
        """Moves the selected job(s) from the history back to the queue for another run."""
        selected_items = self.history_list_widget.selectedItems()
        if not selected_items:
            return

        # Process the list in reverse to avoid index issues with multiple selections
        for item in reversed(selected_items):
            job_id_to_requeue = item.data(Qt.ItemDataRole.UserRole)
            job_to_move = next((job for job in self.history_queue if job['id'] == job_id_to_requeue), None)

            if job_to_move:
                # Remove from history
                self.history_queue.remove(job_to_move)

                # IMPORTANT: Reset status to 'Ready' so it can be processed again,
                # but keep its settings and job type intact.
                job_to_move['status'] = 'Ready'

                # Add to the end of the job queue
                self.job_queue.append(job_to_move)

        # Update both UI lists to reflect the change
        self._update_history_list_ui()
        self._update_job_list_ui()

        self.log("INFO", f"Re-queued {len(selected_items)} job(s) from history.")

    def _show_history_context_menu(self, position):
        """Creates and shows the context menu for the history list."""
        selected_items = self.history_list_widget.selectedItems()
        if not selected_items:
            return

        menu = QMenu()

        # Action to re-queue the job
        requeue_action = menu.addAction("↪️ Re-queue Job")
        requeue_action.setToolTip("Moves the selected job(s) back to the queue with their last used settings.")
        requeue_action.triggered.connect(self._requeue_job)

        menu.exec(self.history_list_widget.mapToGlobal(position))
