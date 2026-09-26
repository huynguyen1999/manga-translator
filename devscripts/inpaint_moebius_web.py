#!/usr/bin/env python3
"""Interactive Web UI for Moebius Inpainting.

Provides a browser-based canvas editor with:
- Drag & drop / file upload / URL load for images
- Free-draw brush & Rectangle selection masking tools with adjustable brush sizes & undo
- Optional text prompt input and CFG slider
- Live inpainting using hustvl/Moebius in backend and side-by-side / slider comparison view
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
import sys
import time
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from PIL import Image
import uvicorn

# Reuse pipeline runner logic from inpaint_moebius
from inpaint_moebius import ensure_moebius_repo_and_weights, run_moebius_cli, parse_page_reference, fetch_server_page_assets, download_or_read_bytes, pad_to_square

app = FastAPI(title="Moebius Inpainting Web UI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MOEBIUS_REPO = Path("./Moebius").resolve()
OUTPUTS_DIR = Path("./outputs/web_inpaint").resolve()
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Moebius Inpainting Studio</title>
<style>
  :root {
    --bg-primary: #0f172a;
    --bg-secondary: #1e293b;
    --bg-tertiary: #334155;
    --text-primary: #f8fafc;
    --text-secondary: #94a3b8;
    --accent: #6366f1;
    --accent-hover: #4f46e5;
    --accent-active: #4338ca;
    --border: #334155;
    --success: #10b981;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background-color: var(--bg-primary);
    color: var(--text-primary);
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
  }
  header {
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  header h1 { font-size: 1.15rem; font-weight: 600; display: flex; align-items: center; gap: 8px; }
  header .badge {
    font-size: 0.75rem;
    background: rgba(99, 102, 241, 0.2);
    color: #a5b4fc;
    padding: 2px 8px;
    border-radius: 999px;
  }
  .main-container {
    display: flex;
    flex: 1;
    height: calc(100vh - 60px);
    overflow: hidden;
  }
  .sidebar {
    width: 320px;
    background: var(--bg-secondary);
    border-right: 1px solid var(--border);
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    overflow-y: auto;
  }
  .panel-section {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .panel-section label {
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-secondary);
  }
  input[type="text"], select, textarea {
    width: 100%;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--text-primary);
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 0.9rem;
    outline: none;
  }
  input[type="text"]:focus, select:focus, textarea:focus {
    border-color: var(--accent);
  }
  .btn-group { display: flex; gap: 6px; }
  .tool-btn {
    flex: 1;
    padding: 8px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--text-primary);
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.85rem;
    font-weight: 500;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    transition: all 0.15s ease;
  }
  .tool-btn:hover { background: #475569; }
  .tool-btn.active {
    background: var(--accent);
    border-color: var(--accent-hover);
    color: #fff;
  }
  .range-container {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .range-container input[type="range"] {
    flex: 1;
    accent-color: var(--accent);
  }
  .range-val { font-size: 0.85rem; width: 32px; color: var(--text-secondary); }
  .btn-action {
    background: var(--accent);
    color: white;
    border: none;
    padding: 10px 16px;
    border-radius: 6px;
    font-size: 0.95rem;
    font-weight: 600;
    cursor: pointer;
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 8px;
    transition: background 0.15s ease;
  }
  .btn-action:hover { background: var(--accent-hover); }
  .btn-action:disabled { background: #475569; cursor: not-allowed; opacity: 0.6; }
  .btn-secondary {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-primary);
    padding: 6px 12px;
    border-radius: 6px;
    font-size: 0.85rem;
    cursor: pointer;
  }
  .btn-secondary:hover { background: var(--bg-tertiary); }
  .workspace {
    flex: 1;
    display: block;
    background: var(--bg-primary);
    position: relative;
    overflow: hidden;
  }
  .canvas-wrapper {
    position: absolute;
    top: 0;
    left: 0;
    box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5);
    background: #000;
    border-radius: 4px;
    user-select: none;
  }
  #bgCanvas, #maskCanvas, #drawCanvas {
    position: absolute;
    top: 0;
    left: 0;
  }
  #drawCanvas { cursor: crosshair; }
  .drop-zone {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    border: 2px dashed var(--border);
    border-radius: 8px;
    padding: 40px 20px;
    text-align: center;
    color: var(--text-secondary);
    cursor: pointer;
    background: rgba(30, 41, 59, 0.4);
    transition: all 0.2s ease;
    width: 480px;
  }
  .drop-zone:hover { border-color: var(--accent); color: var(--text-primary); }
  .status-toast {
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-left: 4px solid var(--accent);
    padding: 12px 20px;
    border-radius: 6px;
    box-shadow: 0 10px 15px -3px rgba(0,0,0,0.4);
    display: flex;
    align-items: center;
    gap: 10px;
    z-index: 1000;
    transition: opacity 0.3s ease;
  }
  .view-tabs {
    display: flex;
    gap: 8px;
    margin-bottom: 12px;
  }
  .spinner {
    width: 16px;
    height: 16px;
    border: 2px solid rgba(255,255,255,0.3);
    border-top-color: white;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<header>
  <h1><span>🪄</span> Moebius Inpainting Studio <span class="badge" id="modelStatusBadge" style="background: rgba(234, 179, 8, 0.2); color: #fde047;">Loading Model...</span></h1>
  <div style="display: flex; gap: 10px; align-items: center;">
    <button class="btn-secondary" onclick="resetZoomAndPan()">Reset View (100%)</button>
    <button class="btn-secondary" onclick="document.getElementById('fileInput').click()">Upload Image</button>
    <input type="file" id="fileInput" accept="image/*" style="display:none" onchange="handleFileSelect(event)">
  </div>
</header>

<div class="main-container">
  <div class="sidebar">
    <div class="panel-section">
      <label>Load Image / Server URL</label>
      <div style="display: flex; gap: 6px;">
        <input type="text" id="imgUrlInput" placeholder="http://localhost:6868/gallery/pages/..." />
        <button class="btn-secondary" onclick="loadFromUrl()">Load</button>
      </div>
    </div>

    <div class="panel-section">
      <label>Tools (Hold Spacebar or Middle-Click to Pan)</label>
      <div class="btn-group">
        <button class="tool-btn active" id="toolBrush" onclick="setTool('brush')">✏️ Brush</button>
        <button class="tool-btn" id="toolRect" onclick="setTool('rect')">⬜ Rect</button>
        <button class="tool-btn" id="toolEraser" onclick="setTool('eraser')">🧹 Eraser</button>
        <button class="tool-btn" id="toolPan" onclick="setTool('pan')">✋ Pan</button>
      </div>
    </div>

    <div class="panel-section" id="brushSizeSection">
      <label>Brush Size</label>
      <div class="range-container">
        <input type="range" id="brushSize" min="5" max="150" value="30" oninput="updateBrushSize(this.value)">
        <span class="range-val" id="brushSizeVal">30</span>
      </div>
    </div>

    <div class="panel-section">
      <label>Mask Controls</label>
      <div class="btn-group">
        <button class="tool-btn" onclick="undoMask()">↩️ Undo</button>
        <button class="tool-btn" onclick="clearMask()">🗑️ Clear</button>
      </div>
    </div>

    <div class="panel-section">
      <label>Optional Prompt</label>
      <textarea id="promptInput" rows="2" placeholder="Leave empty for natural background reconstruction"></textarea>
    </div>

    <div class="panel-section">
      <label>Model Weights</label>
      <select id="weightSelect">
        <option value="pretrained">General / Pretrained</option>
        <option value="ft_celebahq">CelebA-HQ (Face Specialist)</option>
        <option value="ft_ffhq">FFHQ (Portrait Specialist)</option>
        <option value="ft_places2">Places2 (Scene Specialist)</option>
      </select>
    </div>

    <div class="panel-section">
      <label>Mask Dilation / Expand (px)</label>
      <div class="range-container">
        <input type="range" id="dilation" min="0" max="40" step="1" value="8" oninput="document.getElementById('dilationVal').innerText=this.value">
        <span class="range-val" id="dilationVal">8</span>
      </div>
      <span style="font-size: 0.72rem; color: var(--text-secondary);">Expands mask outwards to merge strokes & prevent boundary seams</span>
    </div>

    <div class="panel-section">
      <label>Denoising Steps</label>
      <div class="range-container">
        <input type="range" id="stepsInput" min="10" max="50" step="5" value="20" oninput="document.getElementById('stepsVal').innerText=this.value">
        <span class="range-val" id="stepsVal">20</span>
      </div>
    </div>

    <div class="panel-section">
      <label>Guidance Scale (CFG)</label>
      <div class="range-container">
        <input type="range" id="cfgScale" min="1.0" max="7.0" step="0.5" value="2.5" oninput="document.getElementById('cfgVal').innerText=this.value">
        <span class="range-val" id="cfgVal">2.5</span>
      </div>
    </div>

    <div class="panel-section">
      <label>Seed (-1 for Random)</label>
      <div style="display: flex; gap: 6px;">
        <input type="text" id="seedInput" value="-1" placeholder="-1">
        <button class="btn-secondary" onclick="document.getElementById('seedInput').value='-1'">🎲</button>
      </div>
    </div>

    <div style="margin-top: auto;">
      <button class="btn-action" id="inpaintBtn" style="width: 100%;" onclick="submitInpaint()">
        <span id="btnText">✨ Run Inpainting</span>
        <span id="btnSpinner" class="spinner" style="display: none;"></span>
      </button>
    </div>
  </div>

  <div class="workspace" id="workspace">
    <div class="view-tabs" id="viewTabs" style="display: none; position: absolute; top: 16px; left: 16px; z-index: 10;">
      <button class="tool-btn active" id="tabInpaint" onclick="switchView('inpaint')">Inpaint View</button>
      <button class="tool-btn" id="tabOriginal" onclick="switchView('original')">Original</button>
      <button class="tool-btn" id="tabMask" onclick="switchView('mask')">Mask</button>
    </div>

    <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
      <p style="font-size: 1.1rem; font-weight: 500; margin-bottom: 8px;">Drag and drop an image here</p>
      <p style="font-size: 0.85rem;">or click to browse from your computer</p>
    </div>

    <div class="canvas-wrapper" id="canvasWrapper" style="display: none; transform-origin: 0 0;">
      <canvas id="bgCanvas"></canvas>
      <canvas id="maskCanvas"></canvas>
      <canvas id="drawCanvas"></canvas>
    </div>
  </div>
</div>

<div class="status-toast" id="toast" style="display: none;">
  <span id="toastMsg">Ready</span>
</div>

<script>
let currentTool = 'brush';
let brushSize = 30;
let isDrawing = false;
let isPanning = false;
let spacePressed = false;
let startX = 0, startY = 0;
let panStartX = 0, panStartY = 0;
let translateX = 0, translateY = 0;
let scale = 1.0;
let maskHistory = [];
let imgOriginal = null;
let imgInpainted = null;
let currentView = 'inpaint';

const workspace = document.getElementById('workspace');
const canvasWrapper = document.getElementById('canvasWrapper');
const bgCanvas = document.getElementById('bgCanvas');
const maskCanvas = document.getElementById('maskCanvas');
const drawCanvas = document.getElementById('drawCanvas');
const bgCtx = bgCanvas.getContext('2d');
const maskCtx = maskCanvas.getContext('2d');
const drawCtx = drawCanvas.getContext('2d');

function updateTransform() {
  canvasWrapper.style.transform = `translate(${translateX}px, ${translateY}px) scale(${scale})`;
}

function resetZoomAndPan() {
  if (!imgOriginal) return;
  const wsRect = workspace.getBoundingClientRect();
  scale = Math.min((wsRect.width - 40) / imgOriginal.width, (wsRect.height - 40) / imgOriginal.height, 1.0);
  translateX = Math.max(0, (wsRect.width - imgOriginal.width * scale) / 2);
  translateY = Math.max(0, (wsRect.height - imgOriginal.height * scale) / 2);
  updateTransform();
}

function showToast(msg, duration = 3000) {
  const toast = document.getElementById('toast');
  document.getElementById('toastMsg').innerText = msg;
  toast.style.display = 'flex';
  setTimeout(() => { toast.style.display = 'none'; }, duration);
}

async function checkModelStatus() {
  try {
    const res = await fetch('/api/model_status');
    const data = await res.json();
    const badge = document.getElementById('modelStatusBadge');
    if (data.ready) {
      badge.innerText = 'Model Ready (In-Memory)';
      badge.style.background = 'rgba(16, 185, 129, 0.2)';
      badge.style.color = '#6ee7b7';
    } else if (data.error) {
      badge.innerText = 'Model Load Failed';
      badge.style.background = 'rgba(239, 68, 68, 0.2)';
      badge.style.color = '#fca5a5';
    } else {
      setTimeout(checkModelStatus, 1500);
    }
  } catch (e) {
    setTimeout(checkModelStatus, 2000);
  }
}
checkModelStatus();

function setTool(tool) {
  currentTool = tool;
  document.querySelectorAll('.sidebar .tool-btn').forEach(btn => btn.classList.remove('active'));
  if (tool === 'brush') document.getElementById('toolBrush').classList.add('active');
  if (tool === 'rect') document.getElementById('toolRect').classList.add('active');
  if (tool === 'eraser') document.getElementById('toolEraser').classList.add('active');
  if (tool === 'pan') document.getElementById('toolPan').classList.add('active');
  drawCanvas.style.cursor = (tool === 'pan' || spacePressed) ? 'grab' : 'crosshair';
}

function updateBrushSize(val) {
  brushSize = parseInt(val);
  document.getElementById('brushSizeVal').innerText = val;
}

function initCanvases(width, height) {
  document.getElementById('dropZone').style.display = 'none';
  canvasWrapper.style.display = 'block';
  canvasWrapper.style.width = width + 'px';
  canvasWrapper.style.height = height + 'px';

  [bgCanvas, maskCanvas, drawCanvas].forEach(c => {
    c.width = width;
    c.height = height;
  });

  maskCtx.clearRect(0, 0, width, height);
  drawCtx.clearRect(0, 0, width, height);
  maskHistory = [];
  saveMaskState();
  resetZoomAndPan();
}

function saveMaskState() {
  maskHistory.push(maskCtx.getImageData(0, 0, maskCanvas.width, maskCanvas.height));
  if (maskHistory.length > 15) maskHistory.shift();
}

function undoMask() {
  if (maskHistory.length > 1) {
    maskHistory.pop();
    const prev = maskHistory[maskHistory.length - 1];
    maskCtx.putImageData(prev, 0, 0);
  } else if (maskHistory.length === 1) {
    maskCtx.clearRect(0, 0, maskCanvas.width, maskCanvas.height);
  }
}

function clearMask() {
  maskCtx.clearRect(0, 0, maskCanvas.width, maskCanvas.height);
  saveMaskState();
}

function loadImage(src) {
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = () => {
    imgOriginal = img;
    imgInpainted = null;
    initCanvases(img.width, img.height);
    bgCtx.drawImage(img, 0, 0);
    document.getElementById('viewTabs').style.display = 'none';
    showToast(`Loaded image: ${img.width}x${img.height}`);
  };
  img.src = src;
}

function handleFileSelect(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (event) => loadImage(event.target.result);
  reader.readAsDataURL(file);
}

// Drag & drop
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => {
  e.preventDefault();
  if (e.dataTransfer.files.length) {
    const file = e.dataTransfer.files[0];
    const reader = new FileReader();
    reader.onload = (event) => loadImage(event.target.result);
    reader.readAsDataURL(file);
  }
});

function getImagePos(e) {
  const rect = drawCanvas.getBoundingClientRect();
  const x = (e.clientX - rect.left) * (drawCanvas.width / rect.width);
  const y = (e.clientY - rect.top) * (drawCanvas.height / rect.height);
  return { x, y };
}

// Spacebar key tracking for pan navigation
window.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && !spacePressed && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
    spacePressed = true;
    drawCanvas.style.cursor = 'grab';
    e.preventDefault();
  }
});
window.addEventListener('keyup', (e) => {
  if (e.code === 'Space') {
    spacePressed = false;
    drawCanvas.style.cursor = currentTool === 'pan' ? 'grab' : 'crosshair';
  }
});

// Zoom with mouse wheel
workspace.addEventListener('wheel', (e) => {
  if (!imgOriginal) return;
  e.preventDefault();
  const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9;
  const newScale = Math.min(Math.max(0.1, scale * zoomFactor), 5.0);

  const wsRect = workspace.getBoundingClientRect();
  const mouseX = e.clientX - wsRect.left;
  const mouseY = e.clientY - wsRect.top;

  translateX = mouseX - (mouseX - translateX) * (newScale / scale);
  translateY = mouseY - (mouseY - translateY) * (newScale / scale);
  scale = newScale;
  updateTransform();
}, { passive: false });

// Canvas Mouse Interactions
workspace.addEventListener('mousedown', (e) => {
  if (!imgOriginal) return;
  // Middle click (button 1) or pan tool or spacebar held
  if (e.button === 1 || currentTool === 'pan' || spacePressed) {
    isPanning = true;
    panStartX = e.clientX - translateX;
    panStartY = e.clientY - translateY;
    drawCanvas.style.cursor = 'grabbing';
    e.preventDefault();
    return;
  }

  if (e.button === 0) { // Left click drawing
    const pos = getImagePos(e);
    if (pos.x < 0 || pos.x > drawCanvas.width || pos.y < 0 || pos.y > drawCanvas.height) return;

    isDrawing = true;
    startX = pos.x;
    startY = pos.y;

    if (currentTool === 'brush' || currentTool === 'eraser') {
      maskCtx.beginPath();
      maskCtx.moveTo(pos.x, pos.y);
      maskCtx.lineCap = 'round';
      maskCtx.lineJoin = 'round';
      maskCtx.lineWidth = brushSize;
      if (currentTool === 'eraser') {
        maskCtx.globalCompositeOperation = 'destination-out';
      } else {
        maskCtx.globalCompositeOperation = 'source-over';
        maskCtx.strokeStyle = 'rgba(239, 68, 68, 0.7)';
      }
    }
  }
});

window.addEventListener('mousemove', (e) => {
  if (isPanning) {
    translateX = e.clientX - panStartX;
    translateY = e.clientY - panStartY;
    updateTransform();
    return;
  }

  if (!isDrawing) return;
  const pos = getImagePos(e);

  if (currentTool === 'brush' || currentTool === 'eraser') {
    maskCtx.lineTo(pos.x, pos.y);
    maskCtx.stroke();
  } else if (currentTool === 'rect') {
    drawCtx.clearRect(0, 0, drawCanvas.width, drawCanvas.height);
    drawCtx.fillStyle = 'rgba(239, 68, 68, 0.5)';
    drawCtx.strokeStyle = 'rgba(239, 68, 68, 0.9)';
    drawCtx.lineWidth = 2;
    const w = pos.x - startX;
    const h = pos.y - startY;
    drawCtx.fillRect(startX, startY, w, h);
    drawCtx.strokeRect(startX, startY, w, h);
  }
});

window.addEventListener('mouseup', (e) => {
  if (isPanning) {
    isPanning = false;
    drawCanvas.style.cursor = (currentTool === 'pan' || spacePressed) ? 'grab' : 'crosshair';
  }

  if (!isDrawing) return;
  isDrawing = false;
  const pos = getImagePos(e);

  if (currentTool === 'rect') {
    drawCtx.clearRect(0, 0, drawCanvas.width, drawCanvas.height);
    maskCtx.globalCompositeOperation = 'source-over';
    maskCtx.fillStyle = 'rgba(239, 68, 68, 0.7)';
    const w = pos.x - startX;
    const h = pos.y - startY;
    maskCtx.fillRect(startX, startY, w, h);
  }
  maskCtx.globalCompositeOperation = 'source-over';
  saveMaskState();
});

async function loadFromUrl() {
  const url = document.getElementById('imgUrlInput').value.trim();
  if (!url) return;
  showToast('Fetching image / mask from URL...');
  try {
    const res = await fetch('/api/load_url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to load URL');
    loadImage(data.image_data);
    if (data.mask_data) {
      const maskImg = new Image();
      maskImg.onload = () => {
        maskCtx.drawImage(maskImg, 0, 0);
        saveMaskState();
      };
      maskImg.src = data.mask_data;
    }
  } catch (err) {
    alert('Error loading URL: ' + err.message);
  }
}

async function submitInpaint() {
  if (!imgOriginal) {
    alert('Please load an image first.');
    return;
  }

  // Create clean binary mask (white on black)
  const tempCanvas = document.createElement('canvas');
  tempCanvas.width = maskCanvas.width;
  tempCanvas.height = maskCanvas.height;
  const tempCtx = tempCanvas.getContext('2d');
  tempCtx.fillStyle = '#000000';
  tempCtx.fillRect(0, 0, tempCanvas.width, tempCanvas.height);

  const maskImgData = maskCtx.getImageData(0, 0, maskCanvas.width, maskCanvas.height);
  const binaryData = tempCtx.getImageData(0, 0, tempCanvas.width, tempCanvas.height);
  let hasDrawn = false;
  for (let i = 0; i < maskImgData.data.length; i += 4) {
    if (maskImgData.data[i + 3] > 10) { // If drawn
      binaryData.data[i] = 255;
      binaryData.data[i + 1] = 255;
      binaryData.data[i + 2] = 255;
      binaryData.data[i + 3] = 255;
      hasDrawn = true;
    }
  }
  if (!hasDrawn) {
    alert('Please mark at least one area with the brush or rectangle tool.');
    return;
  }
  tempCtx.putImageData(binaryData, 0, 0);

  const imgBase64 = bgCanvas.toDataURL('image/png');
  const maskBase64 = tempCanvas.toDataURL('image/png');

  const btn = document.getElementById('inpaintBtn');
  const btnText = document.getElementById('btnText');
  const btnSpinner = document.getElementById('btnSpinner');
  btn.disabled = true;
  btnText.innerText = 'Inpainting in progress...';
  btnSpinner.style.display = 'inline-block';
  showToast('Running Moebius neural inpainting...');

  const startTime = performance.now();
  try {
    const res = await fetch('/api/inpaint', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image_base64: imgBase64,
        mask_base64: maskBase64,
        prompt: document.getElementById('promptInput').value.trim(),
        weight_type: document.getElementById('weightSelect').value,
        cfg: parseFloat(document.getElementById('cfgScale').value),
        mask_dilation: parseInt(document.getElementById('dilation').value) || 0,
        num_steps: parseInt(document.getElementById('stepsInput').value) || 20,
        seed: parseInt(document.getElementById('seedInput').value) || -1
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Inpainting failed');

    const totalSeconds = ((performance.now() - startTime) / 1000).toFixed(2);
    const resultImg = new Image();
    resultImg.onload = () => {
      imgInpainted = resultImg;
      bgCtx.clearRect(0, 0, bgCanvas.width, bgCanvas.height);
      bgCtx.drawImage(resultImg, 0, 0);
      maskCanvas.style.display = 'none';
      document.getElementById('viewTabs').style.display = 'flex';
      switchView('inpaint');
      showToast(`Inpainted in ${totalSeconds}s! (Inference: ${data.duration_seconds}s)`);
    };
    resultImg.src = data.result_base64;
  } catch (err) {
    alert('Inpainting error: ' + err.message);
  } finally {
    btn.disabled = false;
    btnText.innerText = '✨ Run Inpainting';
    btnSpinner.style.display = 'none';
  }
}

function switchView(mode) {
  currentView = mode;
  document.querySelectorAll('#viewTabs .tool-btn').forEach(btn => btn.classList.remove('active'));
  if (mode === 'inpaint') {
    document.getElementById('tabInpaint').classList.add('active');
    bgCtx.clearRect(0, 0, bgCanvas.width, bgCanvas.height);
    if (imgInpainted) bgCtx.drawImage(imgInpainted, 0, 0);
    maskCanvas.style.display = 'none';
  } else if (mode === 'original') {
    document.getElementById('tabOriginal').classList.add('active');
    bgCtx.clearRect(0, 0, bgCanvas.width, bgCanvas.height);
    if (imgOriginal) bgCtx.drawImage(imgOriginal, 0, 0);
    maskCanvas.style.display = 'none';
  } else if (mode === 'mask') {
    document.getElementById('tabMask').classList.add('active');
    bgCtx.clearRect(0, 0, bgCanvas.width, bgCanvas.height);
    if (imgOriginal) bgCtx.drawImage(imgOriginal, 0, 0);
    maskCanvas.style.display = 'block';
  }
}
</script>
</body>
</html>
"""


# Global in-memory cache for warm pipelines
PIPELINE_CACHE: dict[str, Any] = {}


def get_or_load_pipeline(weight_type: str = "pretrained"):
    """Load and cache Moebius pipeline directly in memory."""
    if weight_type in PIPELINE_CACHE:
        return PIPELINE_CACHE[weight_type]

    print(f"\n[Model Loader]: Loading and warming up Moebius pipeline ({weight_type})...")
    import torch
    from types import SimpleNamespace
    from functools import partial

    moebius_path = str(MOEBIUS_REPO)
    if moebius_path not in sys.path:
        sys.path.insert(0, moebius_path)

    from infer.utils import build_pipeline

    repo_dir, weight_file = ensure_moebius_repo_and_weights(MOEBIUS_REPO, weight_type=weight_type)

    if torch.cuda.is_available():
        device_str = "cuda"
    elif torch.backends.mps.is_available():
        device_str = "mps"
    else:
        device_str = "cpu"

    args = SimpleNamespace(
        model_config=str(MOEBIUS_REPO / "config/model_cfg/moebius.yaml"),
        model_weight=str(weight_file),
        device=device_str,
    )
    pipe = build_pipeline(args)
    PIPELINE_CACHE[weight_type] = pipe
    print(f"[Model Loader]: Moebius pipeline ({weight_type}) loaded on {device_str} and ready in memory!\n")
    return pipe


class UrlRequest(BaseModel):
    url: str


class InpaintRequest(BaseModel):
    image_base64: str
    mask_base64: str
    prompt: str = ""
    weight_type: str = "pretrained"
    cfg: float = 2.5
    mask_dilation: int = 8
    num_steps: int = 20
    seed: int = -1


MODEL_READY = False
MODEL_LOADING_ERROR: str | None = None


def _background_preload():
    global MODEL_READY, MODEL_LOADING_ERROR
    try:
        get_or_load_pipeline("pretrained")
        MODEL_READY = True
    except Exception as e:
        MODEL_LOADING_ERROR = str(e)
        print(f"[Startup Warning]: Background model preload failed: {e}")


@app.on_event("startup")
async def on_startup():
    import threading
    threading.Thread(target=_background_preload, daemon=True).start()


@app.get("/api/model_status")
async def model_status():
    return {
        "ready": MODEL_READY,
        "error": MODEL_LOADING_ERROR,
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_CONTENT


@app.post("/api/load_url")
async def load_url(req: UrlRequest):
    origin, page_ref = parse_page_reference(req.url)
    try:
        if origin and page_ref:
            img, mask = fetch_server_page_assets(origin, page_ref)
        else:
            data = download_or_read_bytes(req.url)
            img = Image.open(io.BytesIO(data)).convert("RGB")
            mask = None

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

        mask_b64 = None
        if mask is not None:
            mbuf = io.BytesIO()
            mask.save(mbuf, format="PNG")
            mask_b64 = "data:image/png;base64," + base64.b64encode(mbuf.getvalue()).decode("utf-8")

        return {"image_data": img_b64, "mask_data": mask_b64}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/inpaint")
async def inpaint_api(req: InpaintRequest):
    try:
        import random
        t0 = time.perf_counter()
        img_data = base64.b64decode(req.image_base64.split(",")[-1])
        mask_data = base64.b64decode(req.mask_base64.split(",")[-1])

        image = Image.open(io.BytesIO(img_data)).convert("RGB")
        mask = Image.open(io.BytesIO(mask_data)).convert("L")

        pipe = get_or_load_pipeline(req.weight_type)

        # Pad to square to satisfy Moebius LλMI and MixFFN square grid constraints
        sq_img, sq_mask, crop_box = pad_to_square(image, mask)

        seed_val = req.seed if req.seed >= 0 else random.randint(1, 2147483647)

        t_infer_start = time.perf_counter()
        # Direct in-memory batch execution with paste/compensate
        image_inpaint_list = pipe(
            [sq_img],
            [sq_mask],
            guidance_scale=req.cfg,
            mask_dilate_kernel_size=req.mask_dilation,
            num_steps=req.num_steps,
            retry=seed_val,
            paste=True,
            compensate=False,
            noise_offset=0.0357,
        )
        result_sq_img = image_inpaint_list[0]
        # Moebius pipeline returns 512x512 square; scale back to padded square size before cropping
        result_sq_full = result_sq_img.resize(sq_img.size, Image.Resampling.LANCZOS)
        result_img = result_sq_full.crop(crop_box).resize(image.size, Image.Resampling.LANCZOS)
        infer_duration = round(time.perf_counter() - t_infer_start, 2)
        total_duration = round(time.perf_counter() - t0, 2)
        print(f"[Fast Inpainting]: Inferred in {infer_duration}s (Seed: {seed_val}, Total: {total_duration}s)")

        buf = io.BytesIO()
        result_img.save(buf, format="PNG")
        result_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

        return {"result_base64": result_b64, "duration_seconds": infer_duration, "seed": seed_val}
    except Exception as e:
        print(f"[Inpainting Error]: {e}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7860, help="Port to run the web UI server on")
    parser.add_argument("--host", default="127.0.0.1", help="Host binding")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open the web browser")
    args = parser.parse_args(argv)

    url = f"http://{args.host}:{args.port}"
    print(f"\n=======================================================")
    print(f"🚀 Moebius Inpainting Web Studio running at: {url}")
    print(f"=======================================================\n")

    if not args.no_browser:
        webbrowser.open(url)

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
