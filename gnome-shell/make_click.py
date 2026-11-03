#!/usr/bin/env python3
"""Writes the touch bar's press click, screenpad-touchbar@screenpad/click.wav.

The extension plays it through GNOME's sound player each time the daemon
accepts a press (feedback.js), so a tap is confirmed without looking: a
soft, low tick of 24 ms, like a light haptic tap. It is generated here, not
recorded, so it carries this project's license and is rebuilt the same way
every time; tests/test_touchbar_extension.py checks the committed file is
what this script writes.

Run with: python3 gnome-shell/make_click.py
"""

import math
import os
import struct
import wave

RATE = 48000
DURATION = 0.024  # seconds
PITCH = 300.0  # Hz: low, yet within what laptop speakers play
OVERTONE = 0.2  # a little of the octave above, so it reads as a tick
DECAY = 0.005  # seconds for the tick to fall to 1/e
ATTACK = 0.0005  # a raised-cosine onset, so it does not pop
PEAK = 0.3  # of full scale: quiet, under the desktop's alert volume
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenpad-touchbar@screenpad", "click.wav")


def samples():
    """The click as 16-bit samples, starting and ending at silence."""
    n = round(RATE * DURATION)
    out = []
    for i in range(n):
        t = i / RATE
        onset = 0.5 - 0.5 * math.cos(math.pi * t / ATTACK) if t < ATTACK else 1.0
        # A last fade to zero, so the end does not click either.
        tail = min(1.0, (n - 1 - i) / (RATE * 0.002))
        tone = math.sin(2 * math.pi * PITCH * t) + OVERTONE * math.sin(4 * math.pi * PITCH * t)
        value = PEAK / (1 + OVERTONE) * tone * onset * tail * math.exp(-t / DECAY)
        out.append(round(value * 32767))
    return out


def write(path=PATH):
    s = samples()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack("<%dh" % len(s), *s))


if __name__ == "__main__":
    write()
