# Нарезка
MIN_CLIP_SEC = 60       # минимум 1 минута
MAX_CLIP_SEC = 120      # максимум 2 минуты
MIN_SILENCE_SEC = 0.3   # минимальная пауза для границы

# VAD (webrtcvad): 0 — мягкий ... 3 — самый строгий к речи
VAD_MODE = 2

# Формат
TARGET_W = 1080
TARGET_H = 1920
FPS = 30
BLUR_STRENGTH = 20      # лёгкий блюр полей по бокам
CRF = 20                # качество H.264 (меньше = лучше и тяжелее)
PRESET = "medium"       # скорость кодирования x264

# Компоновка
TOP_HEIGHT = 1080       # высота зоны с нарезкой
BOTTOM_HEIGHT = 840     # высота зоны с gameplay
USE_GAMEPLAY = True     # False → вместо gameplay снизу блюр-фон

# Папки
INPUT_DIR = "input"
GAMEPLAY_DIR = "gameplay"
OUTPUT_DIR = "output"

# Расширения
INPUT_EXTS = (".mp4", ".mkv", ".avi", ".mov")
GAMEPLAY_EXTS = (".mp4", ".mov")
