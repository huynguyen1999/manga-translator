# Constants for execution modes (local, share, ws)

# WebSocket status messages
WS_STATUS_PENDING = 'pending'
WS_STATUS_DOWNLOADING = 'downloading'
WS_STATUS_ERROR_DOWNLOAD = 'error-download'
WS_STATUS_PREPARING = 'preparing'
WS_STATUS_SAVING = 'saving'
WS_STATUS_UPLOADING = 'uploading'
WS_STATUS_ERROR_UPLOAD = 'error-upload'

# WebSocket communication settings
WS_HEADER_SECRET = 'x-secret'
WS_MSG_TYPE_NEW_TASK = 'new_task'
WS_MAX_MESSAGE_SIZE = 1_000_000
WS_DEFAULT_TIMEOUT_SEC = 30
WS_THROTTLE_INTERVAL_SEC = 0.2

# Debug output file names
WS_FILE_FINAL = 'ws_final.png'
WS_FILE_RENDER_IN = 'ws_render_in.png'
WS_FILE_RENDER_OUT = 'ws_render_out.png'
WS_FILE_MASK = 'ws_mask.png'
WS_FILE_INMASK = 'ws_inmask.png'
WS_FILE_OUTPUT = 'ws_output.png'
