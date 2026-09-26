"""Existing main-window behavior grouped by feature."""

from .main_window_shared import *
from .main_window_widgets import DynamicHeightListWidget, NoScrollComboBox


class ApiMixin:
    def _create_api_manager_widget(self, info: dict) -> QWidget:
        """Creates a self-contained widget for the API key manager button."""
        container = QFrame()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 10, 0, 0)

        label = QLabel(info.get("label", "API Keys:"))
        layout.addWidget(label)
        layout.addStretch()

        button = QPushButton("Create / Open .env File")
        button.setToolTip(info.get("tooltip", "Click to manage your API keys."))
        button.clicked.connect(self._handle_create_env_file)
        layout.addWidget(button)

        return container

    def _handle_create_env_file(self):
        """Checks for, creates, and opens the .env file in the project root."""
        env_path = os.path.join(self.project_base_dir, ".env")
        self.log("INFO", f"Managing .env file at: {env_path}")

        # The list of API keys and related settings for the template
        API_KEY_TEMPLATE = [
            "# --- Baidu Translate ---",
            "BAIDU_APP_ID=",
            "BAIDU_SECRET_KEY=",
            "\n# --- Youdao Translate ---",
            "YOUDAO_APP_KEY=",
            "YOUDAO_SECRET_KEY=",
            "\n# --- DeepL Translate ---",
            "DEEPL_AUTH_KEY=",
            "\n# --- Caiyun Translate ---",
            "CAIYUN_TOKEN=",
            "\n# --- OpenAI ---",
            "OPENAI_API_KEY=",
            "OPENAI_MODEL=gpt-4o",
            "OPENAI_API_BASE=https://api.openai.com/v1",
            "OPENAI_HTTP_PROXY=",
            "OPENAI_GLOSSARY_PATH=./dict/mit_glossary.txt",
            "\n# --- Groq ---",
            "GROQ_API_KEY=",
            "GROQ_MODEL=mixtral-8x7b-32768",
            "\n# --- Gemini ---",
            "GEMINI_API_KEY=",
            "GEMINI_MODEL=gemini-1.5-flash",
            "\n# --- DeepSeek ---",
            "DEEPSEEK_API_KEY=",
            "DEEPSEEK_MODEL=deepseek-chat",
            "DEEPSEEK_API_BASE=https://api.deepseek.com",
            "\n# --- Sakura Translator ---",
            "SAKURA_API_BASE=http://127.0.0.1:8080/v1",
            "SAKURA_DICT_PATH=./dict/sakura_dict.txt",
            "\n# --- Custom OpenAI (Ollama, etc.) ---",
            "CUSTOM_OPENAI_API_KEY=ollama",
            "CUSTOM_OPENAI_MODEL=",
            "CUSTOM_OPENAI_API_BASE=http://localhost:11434/v1",
        ]

        try:
            if not os.path.exists(env_path):
                self.log("INFO", ".env file not found. Creating a new template...")
                with open(env_path, 'w', encoding='utf-8') as f:
                    f.write("# This file stores your secret API keys.\n")
                    f.write("# Do NOT share this file with anyone.\n\n")
                    f.write("\n".join(API_KEY_TEMPLATE))

            # Open the file with the default system application
            if sys.platform == "win32":
                os.startfile(env_path)
            elif sys.platform == "darwin":  # macOS
                subprocess.run(["open", env_path])
            else:  # linux
                subprocess.run(["xdg-open", env_path])

        except Exception as e:
            error_msg = f"Could not open the .env file. Please open it manually.\nPath: {env_path}\nError: {e}"
            self.log("ERROR", error_msg)
            QMessageBox.warning(self, "Could Not Open File", error_msg)

    def _build_font_map(self):
        """Scans the project's /fonts folder to create a name-to-filepath map."""
        self.font_map = {}
        fonts_dir = os.path.join(self.project_base_dir, "fonts")
        
        if not os.path.isdir(fonts_dir):
            print(f"[WARNING] Fonts directory not found at: {fonts_dir}")
            return

        for font_file in sorted(os.listdir(fonts_dir)):
            if font_file.lower().endswith(('.ttf', '.otf')):
                font_path = os.path.join(fonts_dir, font_file)
                # Use the filename as the key
                self.font_map[font_file] = font_path
