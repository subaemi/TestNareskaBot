"""Нарезка видео под TikTok (9:16) с gameplay-подложкой.

Использование: положить видео в input/, gameplay в gameplay/, запустить
    python cut.py
Готовые клипы появятся в output/.
ffmpeg ставится вместе с пакетом imageio-ffmpeg (pip install -r requirements.txt).
"""
import random
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import config

BASE = Path(__file__).resolve().parent
FFMPEG = "ffmpeg"


def folder(name):
    p = Path(name)
    return p if p.is_absolute() else BASE / p


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")


def check_tools():
    """Ищет ffmpeg: сначала встроенный из imageio-ffmpeg, затем системный."""
    global FFMPEG
    try:
        import imageio_ffmpeg
        FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
        return
    except Exception:
        pass
    found = shutil.which("ffmpeg")
    if found is None:
        sys.exit("[ОШИБКА] ffmpeg не найден. Выполните: "
                 "pip install -r requirements.txt")
    FFMPEG = found


def list_files(directory, exts):
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir()
                  if p.is_file() and p.suffix.lower() in exts)


def get_duration(path):
    """Длительность файла в секундах (разбор вывода ffmpeg, без ffprobe)."""
    r = run([FFMPEG, "-hide_banner", "-i", str(path)])
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


# ---------------------------------------------------------------- паузы ----

def pauses_vad(wav_path):
    """Паузы через webrtcvad: список (start, end) в секундах."""
    import webrtcvad
    vad = webrtcvad.Vad(config.VAD_MODE)
    with wave.open(str(wav_path), "rb") as w:
        rate = w.getframerate()
        pcm = w.readframes(w.getnframes())
    frame_sec = 0.03
    fbytes = int(rate * frame_sec) * 2
    n = len(pcm) // fbytes
    pauses, start = [], None
    for i in range(n):
        t = i * frame_sec
        speech = vad.is_speech(pcm[i * fbytes:(i + 1) * fbytes], rate)
        if not speech and start is None:
            start = t
        elif speech and start is not None:
            if t - start >= config.MIN_SILENCE_SEC:
                pauses.append((start, t))
            start = None
    return pauses


def pauses_silencedetect(video):
    """Запасной вариант, если webrtcvad не установлен."""
    r = run([FFMPEG, "-hide_banner", "-i", str(video), "-vn",
             "-af", f"silencedetect=noise=-30dB:d={config.MIN_SILENCE_SEC}",
             "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return list(zip(starts, ends))


def find_pauses(video):
    try:
        import webrtcvad  # noqa: F401
    except ImportError:
        print("  [!] webrtcvad не установлен — использую ffmpeg silencedetect "
              "(pip install webrtcvad-wheels для настоящего VAD)")
        return pauses_silencedetect(video)

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        r = run([FFMPEG, "-y", "-i", str(video), "-vn", "-ac", "1",
                 "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
        if r.returncode != 0 or not wav.exists():
            print("  [!] Не удалось извлечь аудио (нет звуковой дорожки?)")
            return []
        return pauses_vad(wav)


# -------------------------------------------------------------- нарезка ----

def plan_cuts(pauses, duration):
    """Возвращает список сегментов (start, end). Граница — середина паузы."""
    mids = [(s + e) / 2 for s, e in pauses]
    lo, hi = config.MIN_CLIP_SEC, config.MAX_CLIP_SEC
    segments, cur = [], 0.0

    while duration - cur > hi:
        in_range = [m for m in mids if cur + lo <= m <= cur + hi]
        if in_range:
            cut = in_range[0]                      # первая подходящая пауза
        else:
            after = [m for m in mids if m > cur + lo]
            cut = after[0] if after else duration  # ближайшая после минимума
        segments.append((cur, cut))
        cur = cut

    tail = duration - cur
    if tail >= lo:
        segments.append((cur, duration))
    elif tail > 1:
        print(f"  [i] Хвост {tail:.0f} сек короче {lo} сек — пропущен")
    return segments


# --------------------------------------------------------------- ffmpeg ----

def build_filter(use_gameplay):
    W, TOP, BOT = config.TARGET_W, config.TOP_HEIGHT, config.BOTTOM_HEIGHT
    B, FPS = config.BLUR_STRENGTH, config.FPS

    if use_gameplay:
        head = "[0:v]split=2[a][b];"
        bottom = (f"[1:v]scale={W}:{BOT}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{BOT},setsar=1,fps={FPS}[bot];")
    else:
        head = "[0:v]split=3[a][b][c];"
        bottom = (f"[c]scale={W}:{BOT}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{BOT},boxblur={B * 2}:2,setsar=1,fps={FPS}[bot];")

    top = (f"[a]scale={W}:{TOP}:force_original_aspect_ratio=increase,"
           f"crop={W}:{TOP},boxblur={B}:2[bg];"
           f"[b]scale={W}:{TOP}:force_original_aspect_ratio=decrease,"
           f"setsar=1[fg];"
           f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS}[top];")

    return head + top + bottom + "[top][bot]vstack=inputs=2[v]"


def render_clip(src, start, end, gameplay, out_path):
    dur = end - start
    use_gp = gameplay is not None
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
    if use_gp:
        cmd += ["-stream_loop", "-1", "-i", str(gameplay)]
    cmd += ["-filter_complex", build_filter(use_gp),
            "-map", "[v]", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", config.PRESET,
            "-crf", str(config.CRF), "-pix_fmt", "yuv420p",
            "-r", str(config.FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-t", f"{dur:.3f}", "-shortest",
            "-movflags", "+faststart", str(out_path)]
    r = run(cmd)
    if r.returncode != 0:
        print("  [ОШИБКА] ffmpeg:\n    " +
              "\n    ".join(r.stderr.strip().splitlines()[-6:]))
        return False
    return True


# ----------------------------------------------------------------- main ----

def process(src, gameplay_files, out_dir):
    print(f"\n=== {src.name} ===")
    duration = get_duration(src)
    if duration <= 0:
        print("  [ОШИБКА] Не удалось определить длительность, пропускаю")
        return 0
    print(f"  Длительность: {duration / 60:.1f} мин. Ищу паузы в речи...")

    pauses = find_pauses(src)
    print(f"  Найдено пауз ≥ {config.MIN_SILENCE_SEC} сек: {len(pauses)}")
    segments = plan_cuts(pauses, duration)
    if not segments:
        print(f"  [i] Нечего резать (видео короче {config.MIN_CLIP_SEC} сек)")
        return 0
    print(f"  Запланировано клипов: {len(segments)}")

    done = 0
    for i, (s, e) in enumerate(segments, 1):
        out = out_dir / f"{src.stem}_clip_{i:03d}.mp4"
        gp = random.choice(gameplay_files) if gameplay_files else None
        label = f"gameplay: {gp.name}" if gp else "фон: блюр"
        print(f"  [{i}/{len(segments)}] {out.name}  "
              f"{s:7.1f}–{e:7.1f} сек ({e - s:.1f} сек, {label})")
        if render_clip(src, s, e, gp, out):
            done += 1
    print(f"  Готово: {done}/{len(segments)} клипов")
    return done


def main():
    check_tools()
    in_dir, gp_dir, out_dir = (folder(config.INPUT_DIR),
                               folder(config.GAMEPLAY_DIR),
                               folder(config.OUTPUT_DIR))
    in_dir.mkdir(exist_ok=True)
    out_dir.mkdir(exist_ok=True)

    sources = list_files(in_dir, config.INPUT_EXTS)
    if not sources:
        sys.exit(f"В папке {in_dir.name}/ нет видео "
                 f"({', '.join(config.INPUT_EXTS)}).")

    gameplay_files = []
    if config.USE_GAMEPLAY:
        gameplay_files = list_files(gp_dir, config.GAMEPLAY_EXTS)
        if gameplay_files:
            print(f"Gameplay-файлов: {len(gameplay_files)}")
        else:
            print(f"[!] В {gp_dir.name}/ нет gameplay — снизу будет блюр-фон")
    else:
        print("USE_GAMEPLAY = False — снизу будет блюр-фон")

    print(f"Исходников: {len(sources)}")
    total = sum(process(src, gameplay_files, out_dir) for src in sources)
    print(f"\nИтого создано клипов: {total}. Папка: {out_dir}")


if __name__ == "__main__":
    main()
