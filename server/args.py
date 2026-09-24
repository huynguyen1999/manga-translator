import argparse
import os
from urllib.parse import unquote

def url_decode(s):
    s = unquote(s)
    if s.startswith('file:///'):
        s = s[len('file://'):]
    return s

# Additional argparse types
def path(string):
    if not string:
        return ''
    s = url_decode(os.path.expanduser(string))
    if not os.path.exists(s):
        raise argparse.ArgumentTypeError(f'No such file or directory: "{string}"')
    return s

def file_path(string):
    if not string:
        return ''
    s = url_decode(os.path.expanduser(string))
    if not os.path.exists(s):
        raise argparse.ArgumentTypeError(f'No such file: "{string}"')
    return s

def dir_path(string):
    if not string:
        return ''
    s = url_decode(os.path.expanduser(string))
    if not os.path.exists(s):
        raise argparse.ArgumentTypeError(f'No such directory: "{string}"')
    return s

def positive_int(string):
    value = int(string)
    if value < 1:
        raise argparse.ArgumentTypeError('value must be at least 1')
    return value

def nonnegative_int(string):
    value = int(string)
    if value < 0:
        raise argparse.ArgumentTypeError('value must be zero or greater')
    return value

def cpu_stage_workers(string):
    return None if string.lower() == 'auto' else positive_int(string)

def parse_arguments(args=None):
    parser = argparse.ArgumentParser(description="Specify host and port for the server.")
    parser.add_argument('--host', type=str, default='0.0.0.0', help='The host address (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=8000, help='The port number (default: 8000)')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Print debug info and save intermediate images in result folder')
    parser.add_argument('--start-instance', action='store_true',
                        help='If a translator should be launched automatically')
    parser.add_argument('--ignore-errors', action='store_true', help='Skip image on encountered error.')
    parser.add_argument('--nonce', default=os.getenv('MT_WEB_NONCE', 'None'), type=str, help='Nonce for securing internal web server communication, set to "None" to disable')
    parser.add_argument('--models-ttl', default=120, type=nonnegative_int,
                        help='Keep idle models cached for this many seconds; 0 keeps them forever (default: 120)')
    parser.add_argument('--pre-dict', default=None, type=file_path, help='Path to the pre-translation dictionary file')
    parser.add_argument('--post-dict', default=None, type=file_path, help='Path to the post-translation dictionary file')    
    parser.add_argument('--executor-mode', choices=['inprocess', 'subprocess'], default='inprocess',
                        help='Execution mode: "inprocess" shares models and allows up to two concurrent GPU tasks; "subprocess" spawns separate worker processes (default: inprocess)')
    parser.add_argument('--workers', type=int, default=3, help='Number of active image pipelines; in-process workers share models (default: 3)')
    parser.add_argument('--cpu-stage-workers', type=cpu_stage_workers, default=None,
                        help='Maximum concurrent background CPU stages (default: auto, capped at 3; keeps 2 for --workers=2)')
    parser.add_argument('--inpainting-concurrency', type=int, default=0, help='Maximum concurrent inpainting passes to avoid VRAM spikes (0 = unlimited, default: 0)')
    parser.add_argument('--gpu-ids', type=str, default=None, help='Comma-separated list of GPU indices to distribute workers across, e.g. "0,1"')
    g = parser.add_mutually_exclusive_group()
    g.add_argument('--use-gpu', dest='use_gpu', action='store_true', default=None,
                   help='Turn on/off gpu (auto switch between mps and cuda) (default: True)')
    g.add_argument('--no-gpu', '--no-use-gpu', dest='use_gpu', action='store_false',
                   help='Disable gpu usage and run on cpu')
    g.add_argument('--use-gpu-limited', action='store_true', default=False,
                   help='Turn on/off gpu (excluding offline translator)')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default=os.getenv('LOG_LEVEL', 'INFO').upper(),
                        help='Console log level (default: INFO)')
    parser.add_argument('--log-dir', type=str, default='logs',
                        help='Directory to store daily log files (default: logs)')
    parsed = parser.parse_args(args)
    if parsed.verbose:
        parsed.log_level = 'DEBUG'
    if parsed.use_gpu_limited:
        parsed.use_gpu = False
    elif parsed.use_gpu is None:
        parsed.use_gpu = True
    return parsed
