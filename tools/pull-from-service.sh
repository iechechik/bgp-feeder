#!/bin/bash
# Забирает с service.local ТОЛЬКО исходники bgp-routes и конфиги bird2 (rsync по ssh, без промежуточных архивов).
#
#   /opt/bgp-routes/{bgp-routes,README.md,config.toml,bgproutes/,manual/,systemd/}  -> $DEST/
#   /opt/docker-compose/configs/bird2/{Dockerfile,bird.conf}                        -> $DEST/bird2/
#
# Всё остальное (state/, out/, кэши, бэкапы, заметки) не копируется. Новый файл верхнего уровня
# в проекте нужно добавить в PROJECT_PATHS ниже. Файлы, которых нет на service, из белого списка удаляются и здесь.
#
#   tools/pull-from-service.sh        # забрать
#   tools/pull-from-service.sh -n     # показать, что изменится, ничего не копируя
set -euo pipefail
SRC_HOST="${SRC_HOST-root@service.local}"
DEST="${DEST:-/opt/software/bgp-routes}"
SRC_PROJECT="${SRC_PROJECT:-/opt/bgp-routes}"
SRC_BIRD="${SRC_BIRD:-/opt/docker-compose/configs/bird2}"
PROJECT_PATHS=(/bgp-routes /README.md /config.toml '/bgproutes/***' '/manual/***' '/systemd/***')
BIRD_FILES=(Dockerfile bird.conf)

DRY=(); [ "${1:-}" = "-n" ] && DRY=(-n -v) && echo "== пробный запуск (-n), ничего не копируется"
pfx=""; [ -n "$SRC_HOST" ] && pfx="$SRC_HOST:"
mkdir -p "$DEST/bird2"

# белый список: include нужное, всё остальное exclude; pycache внутри разрешённых каталогов тоже отсекаем
filters=(--exclude '__pycache__/' --exclude '*.pyc' --exclude '*.tmp' --exclude '*.bak')
for p in "${PROJECT_PATHS[@]}"; do filters+=(--include "$p"); done
filters+=(--exclude '*')

echo "== проект"
rsync -a --delete --no-owner --no-group "${DRY[@]}" "${filters[@]}" "${pfx}${SRC_PROJECT}/" "$DEST/"
echo "== bird2"
args=(); for f in "${BIRD_FILES[@]}"; do args+=("${pfx}${SRC_BIRD}/$f"); done
rsync -a --no-owner --no-group "${DRY[@]}" "${args[@]}" "$DEST/bird2/"
[ "${#DRY[@]}" -gt 0 ] && exit 0

# git: первый раз заводим репозиторий и .gitignore
cd "$DEST"
[ -d .git ] || { git init -q && echo "== git init"; }
[ -f .gitignore ] || { printf 'state/\nout/\n__pycache__/\n*.pyc\n*.tmp\n*.bak\n' > .gitignore; echo "== создан .gitignore"; }

# страховка: не затащили ли секреты
hits=$(grep -rIlEi '(bot[0-9]+:|token|passw|secret|api[_-]?key|BEGIN [A-Z ]*PRIVATE)' --exclude-dir=.git . 2>/dev/null || true)
[ -n "$hits" ] && { echo "!! похоже на секреты, проверьте перед коммитом:"; echo "$hits"; }

echo "== git status"; git status --short
echo "== изменения"; git diff --stat 2>/dev/null | tail -5
exit 0
