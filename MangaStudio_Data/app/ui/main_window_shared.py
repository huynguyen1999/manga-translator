"""Shared PySide and application imports for the MangaStudio window."""

import os
import sys
import shutil
import threading
import copy
import time
import json
import subprocess

from PySide6.QtWidgets import (
    QMainWindow, QLabel, QWidget, QVBoxLayout, QHBoxLayout,
    QFrame, QPushButton, QProgressBar, QTabWidget, QScrollArea,
    QComboBox, QCheckBox, QButtonGroup, QSlider, QLineEdit, QGridLayout,
    QColorDialog, QMessageBox, QListWidget, QListWidgetItem, QFileDialog,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QTextEdit,
    QApplication, QMenu, QSizePolicy
)
from PySide6.QtCore import Qt, QSize, QTimer, Signal, QByteArray, QEvent
from PySide6.QtGui import QFont, QCursor, QStandardItemModel, QFontDatabase, QPixmap, QPainter
from PIL import Image

from app.core.pipeline import Pipeline
from app.core.config_loader import ConfigLoader
from app.core.constants import LANGUAGES, TRANSLATOR_GROUPS, TRANSLATOR_CAPABILITIES, LOG_COLORS

__all__ = [
    "os", "sys", "shutil", "threading", "copy", "time", "json", "subprocess",
    "QMainWindow", "QLabel", "QWidget", "QVBoxLayout", "QHBoxLayout",
    "QFrame", "QPushButton", "QProgressBar", "QTabWidget", "QScrollArea",
    "QComboBox", "QCheckBox", "QButtonGroup", "QSlider", "QLineEdit", "QGridLayout",
    "QColorDialog", "QMessageBox", "QListWidget", "QListWidgetItem", "QFileDialog",
    "QGraphicsView", "QGraphicsScene", "QGraphicsPixmapItem", "QTextEdit",
    "QApplication", "QMenu", "QSizePolicy", "Qt", "QSize", "QTimer", "Signal",
    "QByteArray", "QEvent", "QFont", "QCursor", "QStandardItemModel",
    "QFontDatabase", "QPixmap", "QPainter", "Image", "Pipeline", "ConfigLoader",
    "LANGUAGES", "TRANSLATOR_GROUPS", "TRANSLATOR_CAPABILITIES", "LOG_COLORS",
]
