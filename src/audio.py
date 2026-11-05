"""
Fase 6.3 (Edición Balatro): Motor de Audio Multicanal y Música Hipnótica (audio.py)
Sintetiza:
1. Música ambiental estilo Balatro (progresión hipnótica Dm9 - Gm7 - Bbmaj7 - A7 con bajo y pad).
2. 10 efectos de sonido retro-casino multicanal (clack de fichas, giro de bola, ding de
   multiplicador estilo Balatro, activación de Comodín, Tarot místico, caja registradora
   de tienda, victoria, derrota, rugido de Furia y destello Suertudo).
Usa `pygame.mixer` para mezclar música en bucle + múltiples efectos simultáneos sin cortarse,
con respaldo automático en `winsound`.
"""

import math
from pathlib import Path
import random
import struct
import sys
from typing import Dict, List, Tuple
import wave
from tqdm import tqdm

try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    pygame = None  # type: ignore
    HAS_PYGAME = False

try:
    import winsound
except ImportError:
    winsound = None  # type: ignore


# Formantes aproximados (F1, F2) de las vocales para la voz de Ruletín
VOWEL_FORMANTS: Dict[str, Tuple[float, float]] = {
    "a": (780.0, 1250.0),
    "e": (520.0, 1850.0),
    "i": (330.0, 2250.0),
    "o": (520.0, 920.0),
    "u": (360.0, 820.0),
}

# (tono inicial, tono final, vocal, duración, con consonante) — voz aguda y juguetona
RULETIN_SYLLABLES: List[Tuple[float, float, str, float, bool]] = [
    (260.0, 300.0, "a", 0.085, True),
    (300.0, 250.0, "e", 0.075, True),
    (280.0, 340.0, "i", 0.070, True),
    (240.0, 210.0, "o", 0.090, True),
    (320.0, 280.0, "a", 0.065, False),
    (230.0, 270.0, "u", 0.080, True),
    (340.0, 300.0, "e", 0.060, False),
    (270.0, 230.0, "o", 0.075, True),
    (290.0, 330.0, "a", 0.070, True),
    (250.0, 260.0, "i", 0.065, False),
]


class SoundManager:
    """
    Motor de sonido multicanal con música de fondo en bucle continuo y
    efectos de sonido simultáneos estilo Balatro.
    """

    def __init__(self, assets_dir: Path, enabled: bool = True) -> None:
        self.assets_dir: Path = assets_dir
        self.enabled: bool = enabled
        self.music_playing: bool = False
        self.sound_files: Dict[str, Path] = {}
        self._pygame_sounds: Dict[str, "pygame.mixer.Sound"] = {}
        self._mixer_ready: bool = False

        if self.enabled and HAS_PYGAME:
            try:
                if not pygame.mixer.get_init():
                    pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)
                pygame.mixer.set_num_channels(16)
                self._mixer_ready = True
            except Exception:
                self._mixer_ready = False

    def generate_sound_assets(self, show_progress: bool = True) -> None:
        """
        Sintetiza todos los archivos WAV de efectos y la pista musical estilo Balatro,
        cargándolos en memoria para reproducción instantánea sin latencia.
        """
        self.assets_dir.mkdir(parents=True, exist_ok=True)

        # Definiciones ricas de efectos (frecuencia_inicial, frecuencia_final, duracion, volumen, forma_onda)
        sfx_specs: List[Tuple[str, List[Tuple[float, float, float, float, str]]]] = [
            # Clack cerámico de ficha de póker/ruleta
            ("chip", [(1800.0, 950.0, 0.035, 0.55, "tri"), (620.0, 480.0, 0.045, 0.45, "sine")]),
            # Tick de la bola de marfil en el cilindro
            ("tick", [(1350.0, 820.0, 0.028, 0.48, "tri")]),
            # Ding de puntuación / multiplicador estilo Balatro
            ("mult_ding", [(587.33, 587.33, 0.06, 0.55, "bell"), (880.0, 932.33, 0.11, 0.60, "bell")]),
            # Activación de Comodín (Joker pop!)
            ("joker", [(330.0, 440.0, 0.05, 0.55, "square"), (659.25, 880.0, 0.09, 0.55, "bell")]),
            # Carta de Tarot mística
            ("tarot", [
                (440.0, 440.0, 0.06, 0.45, "bell"),
                (554.37, 554.37, 0.06, 0.45, "bell"),
                (659.25, 659.25, 0.06, 0.50, "bell"),
                (880.0, 987.77, 0.18, 0.55, "bell"),
            ]),
            # Compra en tienda (Cha-ching!)
            ("buy", [(1046.50, 1046.50, 0.055, 0.50, "bell"), (1567.98, 1567.98, 0.16, 0.60, "bell")]),
            # Limpiar apuestas
            ("clear", [(520.0, 240.0, 0.09, 0.40, "sine")]),
            # Fanfarria de tiro ganador
            ("win", [
                (523.25, 523.25, 0.07, 0.55, "bell"),
                (659.25, 659.25, 0.07, 0.55, "bell"),
                (783.99, 783.99, 0.08, 0.60, "bell"),
                (1046.50, 1046.50, 0.22, 0.65, "bell"),
            ]),
            # Tiro fallido
            ("loss", [(261.63, 220.0, 0.12, 0.50, "square"), (196.0, 146.83, 0.22, 0.55, "square")]),
            # Activación Modo Furia (fuego / potencia)
            ("fury", [
                (146.83, 164.81, 0.09, 0.65, "square"),
                (220.0, 293.66, 0.10, 0.65, "square"),
                (440.0, 587.33, 0.20, 0.70, "square"),
            ]),
            # Activación Modo Suertudo (destello dorado)
            ("lucky", [
                (587.33, 587.33, 0.06, 0.50, "bell"),
                (739.99, 739.99, 0.06, 0.50, "bell"),
                (880.00, 880.00, 0.06, 0.55, "bell"),
                (1174.66, 1174.66, 0.20, 0.65, "bell"),
            ]),
        ]

        iterator = tqdm(
            sfx_specs,
            desc="Sintetizando audio Balatro (Fase 6)",
            disable=(not show_progress or sys.stderr is None),
            leave=False,
        )
        for name, segments in iterator:
            file_path = self.assets_dir / f"{name}_v2.wav"
            if not file_path.exists():
                self._write_synth_wav(file_path, segments)
            self.sound_files[name] = file_path
            if self._mixer_ready:
                try:
                    self._pygame_sounds[name] = pygame.mixer.Sound(str(file_path))
                except Exception:
                    pass

        # Voz de Ruletín: sílabas de balbuceo sintetizadas (se eligen al azar mientras habla)
        for idx, (f0_start, f0_end, vowel, duration, consonant) in enumerate(RULETIN_SYLLABLES):
            name = f"ruletin_{idx}"
            file_path = self.assets_dir / f"{name}_v1.wav"
            if not file_path.exists():
                self._write_voice_syllable(file_path, f0_start, f0_end, VOWEL_FORMANTS[vowel], duration, consonant)
            self.sound_files[name] = file_path
            if self._mixer_ready:
                try:
                    self._pygame_sounds[name] = pygame.mixer.Sound(str(file_path))
                    self._pygame_sounds[name].set_volume(0.8)
                except Exception:
                    pass

        # Pista de música ambiental hipnótica estilo Balatro (bucle de ~9.6 segundos)
        bgm_path = self.assets_dir / "ambient_balatro_loop.wav"
        if not bgm_path.exists():
            self._write_balatro_ambient_loop(bgm_path)
        self.sound_files["ambient"] = bgm_path

    @staticmethod
    def _write_synth_wav(
        filepath: Path,
        segments: List[Tuple[float, float, float, float, str]],
        sample_rate: int = 22050,
    ) -> None:
        frames = bytearray()
        for f_start, f_end, duration_sec, volume, wave_kind in segments:
            n_samples = max(1, int(sample_rate * duration_sec))
            phase = 0.0
            for i in range(n_samples):
                t_ratio = i / n_samples
                freq = f_start + (f_end - f_start) * t_ratio
                phase += (2.0 * math.pi * freq) / sample_rate

                # Envolvente ADSR percusiva y limpia
                attack = min(1.0, i / max(1.0, sample_rate * 0.004))
                decay = (1.0 - t_ratio) ** 1.45
                env = attack * decay

                if wave_kind == "square":
                    raw = 0.65 * (1.0 if math.sin(phase) >= 0 else -1.0) + 0.35 * math.sin(phase)
                elif wave_kind == "tri":
                    raw = (2.0 / math.pi) * math.asin(math.sin(phase))
                elif wave_kind == "bell":
                    raw = (
                        0.65 * math.sin(phase)
                        + 0.25 * math.sin(phase * 2.0)
                        + 0.10 * math.sin(phase * 3.0)
                    )
                else:
                    raw = math.sin(phase)

                val = int(max(-1.0, min(1.0, raw * volume * env)) * 32767)
                frames.extend(struct.pack("<h", val))

        with wave.open(str(filepath), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(bytes(frames))

    @staticmethod
    def _write_voice_syllable(
        filepath: Path,
        f0_start: float,
        f0_end: float,
        formants: Tuple[float, float],
        duration_sec: float,
        consonant: bool,
        sample_rate: int = 22050,
    ) -> None:
        """
        Sílaba de "balbuceo" de personaje: pulso glotal (diente de sierra) con deslizamiento
        de tono, filtrado por dos formantes de vocal y un golpecito de consonante al inicio.
        """
        n_samples = max(1, int(sample_rate * duration_sec))
        rng = random.Random(int(f0_start * 10 + formants[0]))

        # Filtros resonantes de 2 polos (uno por formante)
        def resonator(freq: float, bandwidth: float):
            r = math.exp(-math.pi * bandwidth / sample_rate)
            c1 = 2.0 * r * math.cos(2.0 * math.pi * freq / sample_rate)
            c2 = -r * r
            gain = (1.0 - r) * 1.8
            state = [0.0, 0.0]

            def step(x: float) -> float:
                y = gain * x + c1 * state[0] + c2 * state[1]
                state[1], state[0] = state[0], y
                return y

            return step

        f1 = resonator(formants[0], 90.0)
        f2 = resonator(formants[1], 130.0)
        phase = 0.0
        samples: List[float] = []
        for i in range(n_samples):
            t_ratio = i / n_samples
            f0 = f0_start + (f0_end - f0_start) * t_ratio
            phase = (phase + f0 / sample_rate) % 1.0
            source = 2.0 * phase - 1.0  # diente de sierra
            voiced = f1(source) + 0.7 * f2(source)
            if consonant and i < sample_rate * 0.012:
                voiced += rng.uniform(-0.6, 0.6) * (1.0 - i / (sample_rate * 0.012))
            attack = min(1.0, i / (sample_rate * 0.008))
            release = min(1.0, (n_samples - i) / (sample_rate * 0.03))
            samples.append(voiced * attack * release)

        peak = max(1e-6, max(abs(v) for v in samples))
        frames = bytearray()
        for v in samples:
            frames.extend(struct.pack("<h", int(v / peak * 0.75 * 32767)))
        with wave.open(str(filepath), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(bytes(frames))

    @staticmethod
    def _write_balatro_ambient_loop(filepath: Path, sample_rate: int = 22050) -> None:
        """
        Genera un loop musical hipnótico estilo Balatro Lounge:
        Acordes de piano eléctrico Rhodes con vibrato suave + línea de bajo pulsante.
        """
        # 4 compases de 2.0 segundos cada uno = 8.0 segundos por ciclo
        chords = [
            # Dm9 (D3, F3, A3, C4, E4) + Bajo D2
            (73.42, [146.83, 174.61, 220.00, 261.63, 329.63]),
            # Gm9 (G2, F3, A3, Bb3, D4) + Bajo G2
            (98.00, [174.61, 220.00, 233.08, 293.66, 392.00]),
            # Bbmaj7 (Bb2, D3, F3, A3, D4) + Bajo Bb2
            (116.54, [146.83, 174.61, 220.00, 293.66, 349.23]),
            # A7alt (A2, G3, C#4, F4) + Bajo A2
            (110.00, [196.00, 220.00, 277.18, 349.23, 440.00]),
        ]
        bar_duration = 2.0
        frames = bytearray()
        rng = random.Random(42)

        for bass_freq, pad_freqs in chords:
            n_samples = int(sample_rate * bar_duration)
            for i in range(n_samples):
                t = i / sample_rate
                # Vibrato lento estilo cinta / CRT (LFO de 4.5 Hz)
                vibrato = 1.0 + 0.003 * math.sin(2.0 * math.pi * 4.5 * t)

                # Acorde Rhodes tibio
                pad_sum = 0.0
                for idx, pf in enumerate(pad_freqs):
                    detune = 1.0 + (idx - 2) * 0.0012
                    w = 2.0 * math.pi * pf * vibrato * detune * t
                    pad_sum += math.sin(w) + 0.25 * math.sin(2.0 * w)
                pad_signal = (pad_sum / len(pad_freqs)) * 0.16

                # Pulso de bajo rítmico suave cada 0.5s
                beat_pos = (t % 0.5) / 0.5
                bass_env = (1.0 - beat_pos) ** 2.2
                bass_signal = math.sin(2.0 * math.pi * bass_freq * t) * 0.22 * bass_env

                # Arpegio cristalino sutil en corcheas
                step_idx = int(t / 0.25) % len(pad_freqs)
                arp_pos = (t % 0.25) / 0.25
                arp_env = (1.0 - arp_pos) ** 3.0
                arp_freq = pad_freqs[step_idx] * 2.0
                arp_signal = math.sin(2.0 * math.pi * arp_freq * t) * 0.07 * arp_env

                # Textura muy leve de vinilo
                vinyl = (rng.random() - 0.5) * 0.004

                mix = max(-1.0, min(1.0, pad_signal + bass_signal + arp_signal + vinyl))
                frames.extend(struct.pack("<h", int(mix * 32767)))

        with wave.open(str(filepath), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(bytes(frames))

    def start_ambient_music(self) -> None:
        """Inicia la música de fondo en bucle continuo."""
        if not self.enabled:
            return
        bgm_path = self.sound_files.get("ambient")
        if bgm_path is None or not bgm_path.exists():
            return

        if self._mixer_ready:
            try:
                pygame.mixer.music.load(str(bgm_path))
                pygame.mixer.music.set_volume(0.42)
                pygame.mixer.music.play(loops=-1)
                self.music_playing = True
                return
            except Exception:
                pass

        if winsound is not None:
            try:
                winsound.PlaySound(
                    str(bgm_path),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP | winsound.SND_NODEFAULT,
                )
                self.music_playing = True
            except Exception:
                pass

    def toggle_ambient_music(self) -> bool:
        """Activa o pausa la música ambiental de fondo y devuelve el nuevo estado."""
        if self._mixer_ready:
            if self.music_playing:
                pygame.mixer.music.pause()
                self.music_playing = False
            else:
                try:
                    pygame.mixer.music.unpause()
                    if not pygame.mixer.music.get_busy():
                        self.start_ambient_music()
                    else:
                        self.music_playing = True
                except Exception:
                    self.start_ambient_music()
            return self.music_playing

        if self.music_playing and winsound is not None:
            winsound.PlaySound(None, winsound.SND_PURGE)
            self.music_playing = False
        else:
            self.start_ambient_music()
        return self.music_playing

    def play(self, sound_name: str) -> None:
        """
        Reproduce un efecto de sonido instantáneo en un canal libre sin interrumpir
        la música de fondo ni otros efectos simultáneos.
        """
        if not self.enabled:
            return

        # Alias retrocompatibles con los nombres de pruebas de Fase 6
        if sound_name == "ambient":
            self.toggle_ambient_music()
            return

        if self._mixer_ready and sound_name in self._pygame_sounds:
            try:
                self._pygame_sounds[sound_name].play()
                return
            except Exception:
                pass

        path = self.sound_files.get(sound_name)
        if path is not None and path.exists() and winsound is not None:
            try:
                winsound.PlaySound(
                    str(path),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
            except Exception:
                pass
