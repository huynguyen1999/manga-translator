# ===============================================================
# Application Constants
#
# Description: This file holds all semi-static data for the
#              application, such as language lists, model groups,
#              and other constants. This makes it easy to update
#              this data without searching through all the code.
# ===============================================================

# A dictionary mapping user-friendly language names to their API codes.
LANGUAGES = {
    "Auto-Detect": "auto",
    "English": "ENG",
    "Turkish": "TRK",
    "Japanese": "JPN",
    "Korean": "KOR",
    "Simplified Chinese": "CHS",
    "Traditional Chinese": "CHT",
    "Spanish": "ESP",
    "French": "FRA",
    "German": "DEU",
    "Russian": "RUS",
    "Portuguese (Brazilian)": "PTB",
    "Italian": "ITA",
    "Polish": "POL",
    "Dutch": "NLD",
    "Czech": "CSY",
    "Hungarian": "HUN",
    "Romanian": "ROM",
    "Ukrainian": "UKR",
    "Vietnamese": "VIN",
    "Arabic": "ARA",
    "Serbian": "SRP",
    "Croatian": "HRV",
    "Thai": "THA",
    "Indonesian": "IND",
    "Filipino (Tagalog)": "FIL"
}

# A dictionary to group translators in the dropdown menu for better readability.
TRANSLATOR_GROUPS = {
    "--- OFFLINE MODELS (No API Key) ---": [
        "sugoi"
    ],
    "--- API-BASED (Requires Setup) ---": [
        "deepseek", "gemini", "openai", "groq", "openrouter",
        "custom_openai", "sakura", "deepl", "youdao", "baidu", "caiyun"
    ],
    "--- OTHER ACTIONS ---": [
        "original",
        "none"
    ]
}

# A dictionary that maps translators to their supported language pairs (source, target).
# This provides the data for both filtering the target language dropdown and
# displaying informative tooltips to the user.
#
# Format:
# 'translator_name': {
#     'source_language_code': ['list_of', 'supported', 'target_codes'],
#     '__any__': '__all__' // A special key indicating that this model can translate
#                          // from any supported language to any other.
# }
TRANSLATOR_CAPABILITIES = {
    # --- API-BASED (Generally versatile) ---
    # For major APIs, assuming they can handle any pair we have in our language list.
    "deepl": {'__any__': '__all__'},
    "gemini": {'__any__': '__all__'},
    "deepseek": {'__any__': '__all__'},
    "groq": {'__any__': '__all__'},
    "openrouter": {'__any__': '__all__'},
    "youdao": {'__any__': '__all__'},
    "baidu": {'__any__': '__all__'},
    "caiyun": {'__any__': '__all__'},
    "openai": {'__any__': '__all__'},
    "custom_openai": {'__any__': '__all__'},

    # --- SPECIALIZED APIs (Limited pairs) ---
    "sakura": {  # Specialized for CJK languages as per docs
        'JPN': ['CHS', 'CHT'],
        'CHS': ['JPN'],
        'CHT': ['JPN']
    },

    # --- SPECIALIZED OFFLINE MODELS (Often one-way) ---
    "sugoi": {  # Primarily Japanese to English
        'JPN': ['ENG']
    },

    # --- OTHER ACTIONS (No translation capabilities) ---
    "original": {},
    "none": {}
}

LOG_COLORS = {
    "ERROR": "#E74C3C",
    "SUCCESS": "#2ECC71",
    "PIPELINE": "#5DADE2",
    "WARNING": "#F39C12",
    "INFO": "white",
    "DEBUG": "gray",
    "RAW": "gray"
}
