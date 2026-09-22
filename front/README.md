[中文说明](README_CN.md)

## Features

- 🖼️ Multi-image upload support (drag & drop, paste, file picker)
- 🔄 Real-time translation status updates
- 🚀 Server-side rendering
- ⚡️ Hot Module Replacement (HMR)
- 📦 Asset bundling and optimization
- 🔄 Data loading and mutations
- 🔒 TypeScript by default
- 🎉 TailwindCSS for styling
- 📖 [React Router docs](https://reactrouter.com/)

## Tech Stack

- **Framework**: React 18
- **Build Tool**: Vite
- **Styling**: TailwindCSS
- **Language**: TypeScript
- **Icons**: Iconify
- **State Management**: React Hooks
- **API Communication**: Fetch API with streaming support

## Getting Started

### Installation

Install the dependencies:

```bash
npm install
```

### Development

Prepare the FastAPI server at `http://127.0.0.1:8000/`. Use verbose mode for the diagnostic artifacts:

```bash
python server/main.py --verbose --start-instance
```

According to this repository:

https://github.com/zyddnys/manga-image-translator

Start the development server with HMR:

```bash
npm run dev
```

Your application will be available at `http://localhost:6868`.

The development server listens on all interfaces, so it can also be opened from another device on the local network. Configure the backend URLs in `front/.env`:

```env
DESKTOP_API_URL=http://127.0.0.1:8000
PHONE_API_URL=http://192.168.1.100:8000
```

The frontend selects `PHONE_API_URL` for phones and `DESKTOP_API_URL` for other devices. Leave either value empty to use the frontend's relative `/api` proxy for that device.

Open `/pipeline-lab` to load one image, choose the fixed-order pipeline stages, run the real translation, and inspect saved images, JSON, timings, skips, and failures. Runs are stored in the existing `result/` folders and can be reopened or deleted from the lab.

## Building for Production

Create a production build:

```bash
npm run build
```

Build and serve the production artifact:

```bash
npm run serve
```

## Image

<img src="docs/img/no_image.png" width=600 />

<img src="docs/img/present_image.png" width=600 />

## Backend Code

https://github.com/zyddnys/manga-image-translator
