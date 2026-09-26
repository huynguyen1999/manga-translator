"""Existing main-window behavior grouped by feature."""

from .main_window_shared import *
from .main_window_widgets import DynamicHeightListWidget, NoScrollComboBox


class WindowLayoutMixin:
    def _create_main_layout(self):
        """Creates the main QWidget and layouts to structure the window."""
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        top_area_widget = QWidget()
        top_layout = QHBoxLayout(top_area_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)

        left_panel = self._create_left_panel()
        right_panel = self._create_right_panel()

        top_layout.addWidget(left_panel, stretch=1)
        top_layout.addWidget(right_panel, stretch=3)  # Give right panel more space

        bottom_panel = self._create_bottom_panel()

        main_layout.addWidget(top_area_widget)
        main_layout.addWidget(bottom_panel)

    def _create_left_panel(self) -> QWidget:
        """Creates the main left panel, divided into a 'Queue' and 'History' section."""
        # Main container for the entire left side
        left_panel_container = QFrame()
        left_panel_container.setObjectName("LeftPanel")
        left_panel_layout = QVBoxLayout(left_panel_container)

        # --- 1. Top Section: Queue ---
        queue_frame = QWidget()
        queue_layout = QVBoxLayout(queue_frame)
        queue_layout.setContentsMargins(0, 0, 0, 0)

        queue_title = QLabel("Queue (Next Up)")
        font = queue_title.font()
        font.setPointSize(12)
        font.setBold(True)
        queue_title.setFont(font)
        queue_layout.addWidget(queue_title)

        self.queue_list_widget = QListWidget()
        self.queue_list_widget.setToolTip("Add folders by clicking 'Add Job' or by dragging and dropping them here.")
        self.queue_list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)  # Enable drag-drop reordering
        self.queue_list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)  # Allow multi-select with Ctrl/Shift
        self.queue_list_widget.itemSelectionChanged.connect(self._on_job_selection_changed)

        self.queue_list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue_list_widget.customContextMenuRequested.connect(self._show_queue_context_menu)
        queue_layout.addWidget(self.queue_list_widget)

        # --- Job Control Buttons for the Queue ---
        job_controls_container = QWidget()
        job_controls_layout = QHBoxLayout(job_controls_container)
        job_controls_layout.setContentsMargins(0, 0, 0, 0)

        # We will replace this with a dropdown button later
        add_btn = QPushButton("➕ Add Job")
        add_btn.clicked.connect(self._add_job)

        remove_btn = QPushButton("🗑️ Remove Selected")
        # Connect this button to the working function that removes jobs
        remove_btn.clicked.connect(self._remove_selected_jobs_from_queue)

        clear_btn = QPushButton("🧹 Clear Queue")
        clear_btn.clicked.connect(self._clear_queue)

        # Add all buttons to the horizontal layout
        job_controls_layout.addWidget(add_btn)
        job_controls_layout.addWidget(remove_btn)
        job_controls_layout.addWidget(clear_btn)

        # Add the button container to the main vertical layout for the queue
        queue_layout.addWidget(job_controls_container)

        # --- 2. Bottom Section: History ---
        history_frame = QWidget()
        history_layout = QVBoxLayout(history_frame)
        history_layout.setContentsMargins(0, 0, 0, 0)

        history_title = QLabel("History (Completed Jobs)")
        history_title.setFont(font)  # Reuse the same font
        history_layout.addWidget(history_title)

        self.history_list_widget = QListWidget()
        self.history_list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)

        self.history_list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_list_widget.customContextMenuRequested.connect(self._show_history_context_menu)

        history_layout.addWidget(self.history_list_widget)

        # --- History Control Buttons ---
        history_controls_container = QWidget()
        history_controls_layout = QHBoxLayout(history_controls_container)
        history_controls_layout.setContentsMargins(0, 0, 0, 0)
        history_controls_layout.addStretch()  # Push button to the right

        clear_history_btn = QPushButton("Clear History")
        clear_history_btn.clicked.connect(self._clear_history)
        history_controls_layout.addWidget(clear_history_btn)
        history_layout.addWidget(history_controls_container)

        # --- Add both sections to the main panel layout ---
        left_panel_layout.addWidget(queue_frame, stretch=3)  # Give more space to the queue
        left_panel_layout.addWidget(history_frame, stretch=2)  # Give less space to history

        self.left_panel_widget = left_panel_container
        return left_panel_container

    def _create_right_panel(self) -> QWidget:
        """Creates the right panel widget containing the main tabs."""
        self.main_tabs = QTabWidget()

        # --- CHANGED: The configuration tab is now built dynamically ---
        tab_config = self._create_settings_tab_container()
        tab_compare = self._create_visual_compare_tab()
        tab_log = self._create_log_tab()

        # Add a simple label to the other tabs for now
        compare_layout = QVBoxLayout(tab_compare)
        compare_layout.addWidget(QLabel("Visual comparison tools will be built here."))

        log_layout = QVBoxLayout(tab_log)
        log_layout.addWidget(QLabel("The live log will be displayed here."))

        self.main_tabs.addTab(tab_config, "Configuration ⚙️")
        # --- DISABLED FEATURE: The Visual Compare tab is hidden until its core bug is fixed. ---
        # TODO: The 'translated_view' panel does not correctly display the output image after
        #       the single-image pipeline finishes. The underlying logic in _run_visual_test
        #       and _display_test_result needs to be debugged.
        # self.main_tabs.addTab(tab_compare, "Visual Compare 👁️") 
        self.main_tabs.addTab(tab_log, "Live Log 📊")

        return self.main_tabs

    def _create_settings_tab_container(self) -> QWidget:
        """
        Creates the content for the 'Configuration' tab, which itself is another
        set of tabs read dynamically from the config loader.
        """
        container_widget = QWidget()
        container_layout = QVBoxLayout(container_widget)
        container_layout.setContentsMargins(5, 5, 5, 5)
        container_layout.setSpacing(10)

        self.settings_tab_view = QTabWidget()
        container_layout.addWidget(self.settings_tab_view)

        # --- Build the regular settings tabs from ui_map.json ---
        config_data = self.config_loader.full_config_data
        tab_order = self.config_loader.get_tab_order()
        grouped_settings = {tab_name: [] for tab_name in tab_order}
        for key, info in config_data.items():
            group = info.get("group", "Other")
            if group in grouped_settings:
                grouped_settings[group].append(info)

        for tab_name in tab_order:
            settings_list = sorted(grouped_settings.get(tab_name, []), key=lambda x: x.get('order', 999))
            tab_content_widget = self._build_dynamic_tab_content(tab_name, settings_list)
            self.settings_tab_view.addTab(tab_content_widget, tab_name)

        # --- NEW: Build the 'Tasks' tab ---
        tasks_tab_content = self._build_tasks_tab_content()
        self.settings_tab_view.addTab(tasks_tab_content, "Tasks 🛠️")

        return container_widget

    def _create_visual_compare_tab(self) -> QWidget:
        """Creates the entire UI for the Visual Compare tab and connects its signals."""
        container = QWidget()
        layout = QVBoxLayout(container)

        # 1. Top Controls Panel
        controls_frame = QFrame()
        controls_layout = QHBoxLayout(controls_frame)

        load_button = QPushButton("Load Test Image...")
        load_button.clicked.connect(self._load_test_image)  # Connect signal

        self.fast_preview_check = QCheckBox("Fast Preview")
        self.fast_preview_check.setChecked(True)

        self.run_test_button = QPushButton("Run Test")
        self.run_test_button.setEnabled(False)
        self.run_test_button.clicked.connect(self._run_visual_test_thread)

        reset_button = QPushButton("Reset View")
        reset_button.clicked.connect(self._fit_image_to_view)  # Connect signal

        self.zoom_label = QLabel("Zoom: 100%")

        self.limit_zoom_check = QCheckBox("Limit Zoom")
        self.limit_zoom_check.setChecked(True)  # Enabled by default
        self.limit_zoom_check.setToolTip("When checked, zoom is limited between 5% and 800%.")

        controls_layout.addWidget(load_button)
        controls_layout.addWidget(self.fast_preview_check)
        controls_layout.addStretch()
        controls_layout.addWidget(self.zoom_label)
        controls_layout.addWidget(reset_button)
        controls_layout.addWidget(self.run_test_button)

        # 2. Image Display Area
        image_area_frame = QFrame()
        image_area_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.image_area_layout = QHBoxLayout(image_area_frame)

        self.original_view = QGraphicsView()
        self.original_scene = QGraphicsScene()
        self.original_view.setScene(self.original_scene)
        self.original_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.original_view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)  # Enable panning!

        self.translated_view = QGraphicsView()
        self.translated_scene = QGraphicsScene()
        self.translated_view.setScene(self.translated_scene)
        self.translated_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.translated_view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

        # --- Connect events for zooming and synchronized panning ---
        self.original_view.wheelEvent = self._wheel_event_zoom
        self.translated_view.wheelEvent = self._wheel_event_zoom
        self.original_view.horizontalScrollBar().valueChanged.connect(self.translated_view.horizontalScrollBar().setValue)
        self.original_view.verticalScrollBar().valueChanged.connect(self.translated_view.verticalScrollBar().setValue)
        self.translated_view.horizontalScrollBar().valueChanged.connect(self.original_view.horizontalScrollBar().setValue)
        self.translated_view.verticalScrollBar().valueChanged.connect(self.original_view.verticalScrollBar().setValue)

        original_container = QWidget()
        original_layout = QVBoxLayout(original_container)
        original_layout.addWidget(QLabel("Original (Ctrl+Scroll=Zoom, Drag=Pan)"))
        original_layout.addWidget(self.original_view)

        translated_container = QWidget()
        translated_layout = QVBoxLayout(translated_container)
        translated_layout.addWidget(QLabel("Output"))
        translated_layout.addWidget(self.translated_view)

        self.image_area_layout.addWidget(original_container)
        self.image_area_layout.addWidget(translated_container)

        layout.addWidget(controls_frame)
        layout.addWidget(image_area_frame, stretch=1)

        return container
