#!/usr/bin/env bash
# Вспомогательные функции демонстрации: команды выполняются в настоящем
# окне терминала xterm на виртуальном экране Xvfb, окно сохраняется в PNG.
# Используется сценарием run-demo.sh.

TERM_FONT_SIZE="${TERM_FONT_SIZE:-13}"
TERM_WID=""
TERM_PID=""

# term_open <колонки> <строки> <команда...>
term_open() {
    local cols="$1" rows="$2"; shift 2
    xterm -fa 'DejaVu Sans Mono' -fs "$TERM_FONT_SIZE" -geometry "${cols}x${rows}+0+0" \
        -bg white -fg black -cr black +sb -b 14 -e "$@" &
    TERM_PID=$!
    for _ in $(seq 1 50); do
        TERM_WID="$(xdotool search --pid "$TERM_PID" 2>/dev/null | tail -1 || true)"
        [[ -n "$TERM_WID" ]] && break
        sleep 0.2
    done
    xdotool mousemove 200 200
    sleep 1.5
}

# term_run <пауза после команды, с> <команда>
term_run() {
    local pause="$1"; shift
    xdotool type --delay 8 "$*"
    xdotool key Return
    sleep "$pause"
}

term_shot() {
    import -window "$TERM_WID" "$1"
    # Обрезка пустого поля под выводом
    convert "$1" -trim +repage -bordercolor white -border 18 "$1"
    echo "saved $1"
}

term_close() {
    kill "$TERM_PID" 2>/dev/null || true
    wait "$TERM_PID" 2>/dev/null || true
    sleep 0.5
}
