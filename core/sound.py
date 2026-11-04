"""
提示音 —— 转换完成时给一声轻响。

为什么不直接用系统提示音：Windows 的 MessageBeep 是警告音，短促刺耳，
在安静办公室里一响就是「出事了」的感觉。这里用正弦波合成一个柔和的双音，
音头带淡入、音尾指数衰减，听着像 macOS 的完成提示。

音色不写死音频文件，而是首次运行时按参数合成并缓存成 wav，
这样不用往仓库塞二进制，也方便改音高。
"""

import math
import os
import struct
import sys
import wave

from .paths import DATA_DIR

SOUND_PATH = os.path.join(DATA_DIR, "done.wav")

SAMPLE_RATE = 44100


def _tone(freq, dur, vol=0.30, fade_in=0.012, decay=7.0):
    """
    生成一段带淡入和指数衰减的正弦波采样。

    单纯的正弦听起来像电子蜂鸣，加一点点二次谐波（0.12 倍）会柔和很多，
    更接近木琴或玻璃琴的音色。
    """
    n = int(SAMPLE_RATE * dur)
    out = []
    for i in range(n):
        t = i / SAMPLE_RATE
        env = math.exp(-decay * t)                       # 指数衰减
        if t < fade_in:                                  # 音头淡入，避免爆音
            env *= t / fade_in
        s = math.sin(2 * math.pi * freq * t)
        s += 0.12 * math.sin(4 * math.pi * freq * t)     # 二次谐波
        out.append(s * env * vol)
    return out


def _write_wav(path, samples):
    """把浮点采样写成 16 位单声道 wav"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        frames = b"".join(
            struct.pack("<h", max(-32767, min(32767, int(s * 32767))))
            for s in samples)
        w.writeframes(frames)


def ensure_sound():
    """
    首次运行时合成提示音。

    用两个音（上行五度）而不是一个长音：单音听起来像「警告」，
    两个上行的短音才是「完成」。第二个音稍轻、稍长，收尾更自然。
    """
    if os.path.exists(SOUND_PATH):
        return SOUND_PATH
    try:
        first = _tone(880.0, 0.16, vol=0.26, decay=9.0)     # A5
        gap = [0.0] * int(SAMPLE_RATE * 0.045)              # 两音之间留一点空
        second = _tone(1318.5, 0.34, vol=0.20, decay=6.0)   # E6
        _write_wav(SOUND_PATH, first + gap + second)
        return SOUND_PATH
    except Exception:
        return None


def play(style="soft"):
    """
    播放提示音。style: soft（合成音）/ system（系统音）/ none（不响）
    失败一律静默——提示音不该成为新的故障点。
    """
    if style == "none":
        return
    if sys.platform != "win32":
        return
    try:
        import winsound
    except Exception:
        return

    try:
        if style == "system":
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
            return
        path = ensure_sound()
        if path and os.path.exists(path):
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass
