#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
worktree_root="$(git -C "$script_dir" rev-parse --show-toplevel)"
original_root="/Users/ice-h/Source/manga-image-translator"

die() {
  printf '%s\n' "$1" >&2
  exit 1
}

ensure_link() {
  local relative_path="$1"
  local target="$original_root/$2"
  local link_path="$worktree_root/$relative_path"

  if [[ -L "$link_path" ]]; then
    [[ "$(readlink "$link_path")" == "$target" ]] || die "Unexpected symlink: $link_path"
    return
  fi

  [[ ! -e "$link_path" ]] || die "Refusing to replace existing path: $link_path"
  ln -s "$target" "$link_path"
}

[[ $# -eq 1 ]] || die "Usage: $0 {backend|frontend}"

case "$1" in
  backend)
    [[ -d "$original_root/manga_translator/utils/panel/lib" ]] || die "Missing panel library: $original_root/manga_translator/utils/panel/lib"
    [[ -d "$original_root/models" ]] || die "Missing models directory: $original_root/models"
    [[ -x "$original_root/.venv/bin/python" ]] || die "Missing Python venv: $original_root/.venv/bin/python"

    ensure_link "manga_translator/utils/panel/lib" "manga_translator/utils/panel/lib"
    ensure_link "models" "models"

    cd "$worktree_root"
    exec "$original_root/.venv/bin/python" server/main.py --workers 2
    ;;
  frontend)
    command -v npm >/dev/null || die "npm is required to run the frontend"
    [[ -d "$original_root/front/node_modules" ]] || die "Missing frontend dependencies: $original_root/front/node_modules"
    ensure_link "front/node_modules" "front/node_modules"

    cd "$worktree_root/front"
    exec env VITE_BACKEND_URL=http://127.0.0.1:8000 npm run dev
    ;;
  *)
    die "Usage: $0 {backend|frontend}"
    ;;
esac
