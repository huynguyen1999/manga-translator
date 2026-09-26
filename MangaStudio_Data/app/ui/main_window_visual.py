"""Existing main-window behavior grouped by feature."""

from .main_window_shared import *
from .main_window_widgets import DynamicHeightListWidget, NoScrollComboBox


class VisualTestMixin:
    def _load_test_image(self):
        """Opens a file dialog to load a test image and displays it."""
        file_path, _ = QFileDialog.getOpenFileName(self, "Select a Test Image", "", "Image Files (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not file_path:
            return

        self.test_image_path = file_path
        print(f"[Visual Test] Loaded test image: {os.path.basename(file_path)}")

        try:
            pixmap = QPixmap(file_path)
            if pixmap.isNull():
                raise ValueError("Pixmap is null. The image file may be corrupt or in an unsupported format.")

            # Clear previous images
            self.original_scene.clear()
            self.translated_scene.clear()

            # Display the new image in the 'Original' view
            self.original_pixmap_item = self.original_scene.addPixmap(pixmap)

            # Also create a placeholder in the 'Translated' view to maintain sync
            self.translated_pixmap_item = self.translated_scene.addPixmap(QPixmap())  # Empty pixmap

            # Fit the image to the view and enable the run button
            self.run_test_button.setEnabled(True)
            QTimer.singleShot(50, self._fit_image_to_view)

        except Exception as e:
            print(f"[ERROR] Failed to load image file: {e}")
            QMessageBox.critical(self, "Error", f"Could not load the image:\n{e}")

    def _fit_image_to_view(self):
        """Resets the view to fit the entire image within the visible area."""
        if not self.original_pixmap_item or self.original_pixmap_item.pixmap().isNull():
            return
        # Use the bounding rectangle of the pixmap item to fit it perfectly
        self.original_view.fitInView(self.original_pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self.translated_view.fitInView(self.original_pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)  # Use original's rect for sync
        self._update_zoom_label()

    def _wheel_event_zoom(self, event):
        """Handles zooming with Ctrl+MouseWheel, respecting the zoom limit checkbox."""
        if not self.original_pixmap_item or event.modifiers() != Qt.KeyboardModifier.ControlModifier:
            QGraphicsView.wheelEvent(self.original_view, event)
            QGraphicsView.wheelEvent(self.translated_view, event)
            return

        # Define zoom factors and limits
        zoom_in_factor = 1.15
        zoom_out_factor = 1 / zoom_in_factor

        # Get the current zoom level before making changes
        current_zoom = self.original_view.transform().m11()

        # Determine the zoom direction
        if event.angleDelta().y() > 0:
            zoom_factor = zoom_in_factor
        else:
            zoom_factor = zoom_out_factor

        # Check if zoom limiting is enabled
        if self.limit_zoom_check.isChecked():
            # Define standard limits (e.g., 5% to 800%)
            min_zoom, max_zoom = 0.05, 8.0
            # Only apply the zoom if the new level will be within the allowed range
            if (current_zoom * zoom_factor > min_zoom
                    and current_zoom * zoom_factor < max_zoom):
                self.original_view.scale(zoom_factor, zoom_factor)
                self.translated_view.scale(zoom_factor, zoom_factor)
        else:
            # If limiting is off, apply more generous limits to prevent freezing
            min_zoom, max_zoom = 0.01, 100.0
            if (current_zoom * zoom_factor > min_zoom
                    and current_zoom * zoom_factor < max_zoom):
                self.original_view.scale(zoom_factor, zoom_factor)
                self.translated_view.scale(zoom_factor, zoom_factor)

        self._update_zoom_label()

    def _update_zoom_label(self):
        """Updates the zoom level display label."""
        # The zoom level is the square root of the determinant of the view's matrix
        zoom = self.original_view.transform().m11()
        self.zoom_label.setText(f"Zoom: {zoom * 100:.0f}%")

    def _run_visual_test_thread(self):
        """Starts the visual test pipeline in a separate thread to avoid freezing the UI."""
        if not self.test_image_path:
            QMessageBox.warning(self, "No Image", "Please load a test image first.")
            return

        # Disable the button to prevent multiple clicks
        self.run_test_button.setEnabled(False)
        self.run_test_button.setText("Testing...")

        # Run the _run_visual_test method in a new thread
        thread = threading.Thread(target=self._run_visual_test, daemon=True)
        thread.start()

    def _run_visual_test(self):
        """Prepares and runs the pipeline on the single loaded test image."""
        self.log("PIPELINE", "Starting visual test pipeline...")

        # Create a temporary 'job' dictionary to hold the settings for the test.
        # This job is a 'Translate' type job for the purpose of config building.
        test_job = {
            "id": "visual_test_job",
            "job_type": "T",  # Treat it as a standard translate job
            "settings": copy.deepcopy(self.current_settings)
        }

        if self.fast_preview_check.isChecked():
            self.log("INFO", "Fast Preview enabled. Overriding settings for speed.")
            test_job['settings'].update({'detection_size': 1024, 'inpainting_size': 1024})
            if test_job['settings'].get('processing_device') == 'NVIDIA GPU':
                test_job['settings']['inpainting_precision'] = 'bf16'

        # Build the final configuration using our new centralized function
        final_config = self._build_final_config_for_job(test_job)

        # Define temporary and final paths for the output
        source_dir = os.path.dirname(self.test_image_path)
        source_name = os.path.splitext(os.path.basename(self.test_image_path))[0]
        # Use a more descriptive name for the final output
        final_output_dir = os.path.join(source_dir, f"{source_name}_translated_test")

        # Clean up old results before starting
        if os.path.exists(final_output_dir):
            shutil.rmtree(final_output_dir)

        # The pipeline now directly creates the final folder, so we don't need a temp output dir
        is_verbose = test_job['settings'].get("enable_verbose_output", False)

        # Call the updated pipeline function with the ready-made config
        success = self.pipeline.run_single_image_test(
            self.test_image_path,
            final_output_dir,
            final_config,
            self.log,
            is_verbose
        )

        if success:
            self.log("SUCCESS", "Visual test backend process completed.")
            result_files = os.listdir(final_output_dir)
            if result_files:
                # Find the resulting image (it should have the same name as the original)
                original_filename = os.path.basename(self.test_image_path)
                result_path = os.path.join(final_output_dir, original_filename)
                if os.path.exists(result_path):
                    # Use QTimer to ensure UI updates happen on the main thread
                    QTimer.singleShot(0, lambda: self._display_test_result(result_path))
                else:
                    self.log("ERROR", "Could not find the translated image in the output folder.")
        else:
            self.log("ERROR", "Visual test failed or was stopped.")
            # Clean up the potentially empty output folder on failure
            if os.path.exists(final_output_dir) and not os.listdir(final_output_dir):
                shutil.rmtree(final_output_dir)

        # Use QTimer to ensure the button is re-enabled on the main thread
        QTimer.singleShot(0, self._on_visual_test_finished)

    def _display_test_result(self, image_path: str):
        """Loads the result image and displays it in the 'Output' view."""
        print(f"[Visual Test] Displaying result from: {image_path}")
        try:
            pixmap = QPixmap(image_path)
            if pixmap.isNull():
                raise ValueError("Result pixmap is null.")

            # Clear the old placeholder and display the new result
            self.translated_scene.clear()
            self.translated_pixmap_item = self.translated_scene.addPixmap(pixmap)

            # Ensure the view is still synchronized
            self._fit_image_to_view()

        except Exception as e:
            print(f"[ERROR] Failed to load result image: {e}")
            QMessageBox.critical(self, "Error", f"Could not load the result image:\n{e}")

    def _on_visual_test_finished(self):
        """Resets the 'Run Test' button to its normal state."""
        self.run_test_button.setEnabled(True)
        self.run_test_button.setText("Run Test")

    def _create_log_tab(self) -> QWidget:
        """Creates the content for the 'Live Log' tab."""
        container = QWidget()
        layout = QVBoxLayout(container)

        header_frame = QFrame()
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(0, 0, 0, 0)

        header_layout.addStretch()  # Push button to the right
        clear_button = QPushButton("Clear Log")
        clear_button.clicked.connect(self._clear_log)
        header_layout.addWidget(clear_button)

        # The main text widget for logging, set to read-only
        self.log_textbox = QTextEdit()
        self.log_textbox.setReadOnly(True)
        self.log_textbox.setFont(QFont("Consolas", 10))  # Use a monospaced font

        layout.addWidget(header_frame)
        layout.addWidget(self.log_textbox, stretch=1)
        return container
