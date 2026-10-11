"""Soundtrack for the Driftline brag video: pad, sub, pulse and effects written together in C
major at 96 BPM, one shared reverb, mixed so effects sit under the music."""

import wave

import numpy as np

SR, DUR = 48000, 20.5
N = int(SR * DUR)
t = np.arange(N) / SR
rng = np.random.default_rng(7)


def hz(note):  # MIDI note -> Hz
    return 440.0 * 2 ** ((note - 69) / 12)


def env(start, end, attack=0.6, release=0.9):
    e = np.clip((t - start) / attack, 0, 1) * np.clip((end + release - t) / release, 0, 1)
    return np.where((t >= start) & (t <= end + release), e, 0) ** 1.5


# scene chords (MIDI): Am9, Fmaj9, Cmaj9, G6, Am9, Cmaj9 (resolve)
CHORDS = [
    (0.0, 3.6, [45, 57, 60, 64, 71]),
    (3.6, 7.2, [41, 57, 60, 64, 67]),
    (7.2, 11.0, [48, 55, 59, 62, 64]),
    (11.0, 14.6, [43, 55, 59, 62, 64]),
    (14.6, 17.6, [45, 57, 60, 64, 71]),
    (17.6, 20.5, [48, 55, 59, 64, 67]),
]

left = np.zeros(N)
right = np.zeros(N)

# pad: soft detuned sines (plus a quiet octave), slow swell
for a, b, notes in CHORDS:
    e = env(a, b, attack=0.7 if a else 1.4, release=0.9)
    for i, n in enumerate(notes[1:]):
        f = hz(n)
        for det, pan in ((-0.12, 0.7), (0.12, 0.3)):
            v = np.sin(2 * np.pi * (f + det) * t + i) + 0.18 * np.sin(2 * np.pi * 2 * f * t)
            left += 0.050 * e * v * pan
            right += 0.050 * e * v * (1 - pan)
    sub = np.sin(2 * np.pi * hz(notes[0] - 12) * t)
    left += 0.11 * e * sub
    right += 0.11 * e * sub

# muted pulse from the reveal: a soft kick on beats 1 and 3, a breath of noise on the "and"
beat = 60 / 96
for k in range(int(DUR / beat) + 1):
    bt = 3.6 + k * beat
    if bt > 19.0:
        break
    m = t >= bt
    d = t[m] - bt
    if k % 2 == 0:
        kick = np.sin(2 * np.pi * (48 + 60 * np.exp(-d * 30)) * d) * np.exp(-d * 9)
        left[m] += 0.16 * kick
        right[m] += 0.16 * kick
    hat_t = bt + beat / 2
    hm = (t >= hat_t) & (t < hat_t + 0.08)
    noise = rng.standard_normal(hm.sum()) * np.exp(-(t[hm] - hat_t) * 60)
    noise = np.convolve(noise, np.ones(3) / 3, mode="same") - np.convolve(
        noise, np.ones(25) / 25, mode="same"
    )
    left[hm] += 0.012 * noise
    right[hm] += 0.012 * noise


def bell(at, note, gain, pan=0.5, decay=2.2):
    m = t >= at
    d = t[m] - at
    f = hz(note)
    v = (
        (
            np.sin(2 * np.pi * f * d)
            + 0.35 * np.sin(2 * np.pi * 2.01 * f * d)
            + 0.12 * np.sin(2 * np.pi * 3.02 * f * d)
        )
        * np.exp(-d * decay)
        * np.clip(d / 0.004, 0, 1)
    )
    left[m] += gain * v * (1 - pan)
    right[m] += gain * v * pan


# effects, all on chord tones
m = t >= 1.95  # the metric falls: low thump on A
d = t[m] - 1.95
thump = np.sin(2 * np.pi * hz(33) * d) * np.exp(-d * 5) + 0.3 * np.sin(
    2 * np.pi * hz(45) * d
) * np.exp(-d * 7)
left[m] += 0.22 * thump
right[m] += 0.22 * thump
bell(3.85, 76, 0.07, 0.6)  # "noticed" (E5 over F)
bell(3.85, 81, 0.035, 0.4)
bell(7.45, 79, 0.06, 0.45)  # routing (G5 over C)
bell(11.55, 74, 0.06, 0.55)  # gate passed (D5 over G)
for i, n in enumerate([72, 74, 76, 79]):  # four cards light up: C D E G
    bell(15.0 + i * 0.42, n + 12, 0.035, 0.3 + 0.13 * i, decay=6)
bell(17.75, 72, 0.05, 0.5, decay=1.2)  # resolve
bell(17.75, 79, 0.035, 0.5, decay=1.2)

# soft air on scene changes
for at in (3.6, 7.2, 11.0, 14.6, 17.6):
    w = (t > at - 0.5) & (t < at + 0.3)
    x = (t[w] - (at - 0.5)) / 0.8
    n = rng.standard_normal(w.sum())
    n = np.convolve(n, np.ones(40) / 40, mode="same")
    shape = np.sin(np.pi * x) ** 2
    left[w] += 0.05 * n * shape
    right[w] += 0.05 * n * shape


# one shared room: short exponential-noise reverb
def reverb(x, seconds=1.4, mix=0.22):
    length = int(SR * seconds)
    ir = rng.standard_normal(length) * np.exp(-np.arange(length) / (SR * 0.35))
    ir /= np.sqrt((ir**2).sum())
    wet = np.fft.irfft(np.fft.rfft(x, 2 * N) * np.fft.rfft(ir, 2 * N))[:N]
    return x * (1 - mix) + wet * mix


left, right = reverb(left), reverb(right)
fade = np.clip((DUR - t) / 1.5, 0, 1) * np.clip(t / 0.05, 0, 1)
left *= fade
right *= fade
peak = max(np.abs(left).max(), np.abs(right).max())
left, right = left / peak * 0.85, right / peak * 0.85

stereo = (np.stack([left, right], axis=1) * 32767).astype(np.int16)
with wave.open("music.wav", "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(stereo.tobytes())
print("wrote music.wav", round(DUR, 1), "s")
