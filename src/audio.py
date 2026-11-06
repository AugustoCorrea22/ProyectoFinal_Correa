"""
Motor de Audio Multicanal (audio.py)
Sintetiza en memoria, sin archivos de sonido:
1. Efectos retro-casino (clack de fichas, giro de bola, ding de multiplicador, Comodín, Tarot,
   caja registradora, victoria, derrota, rugido de Furia y destello Suertudo).
2. La voz de balbuceo de Ruletín.
La música en bucle la genera y elige `src.music_jukebox`; todo se mezcla con `src.mezclador`.
"""

from array import array
import math
from pathlib import Path
import random
from typing import Dict, List, Tuple

from src import mezclador
from src.mezclador import mixer

SAMPLE_RATE = mezclador.SAMPLE_RATE


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
    """Efectos de sonido simultáneos y control de la música de fondo."""

    def __init__(self, audio_dir: Path, cache_dir: Path, enabled: bool = True) -> None:
        self.audio_dir: Path = audio_dir  # Canciones secretas del jugador
        self.cache_dir: Path = cache_dir  # Música ya generada
        self.enabled: bool = enabled
        self.music_playing: bool = enabled
        self.sound_files: Dict[str, Path] = {}
        self._sounds: Dict[str, mezclador.Sound] = {}
        self._mixer_ready: bool = False

        if self.enabled and mezclador.AVAILABLE:
            try:
                mixer.init()
                mixer.set_num_channels(16)
                self._mixer_ready = True
            except Exception:
                self._mixer_ready = False

    def generate_sounds(self) -> None:
        """Sintetiza todos los efectos y las sílabas de Ruletín."""
        # Definiciones de efectos (frecuencia_inicial, frecuencia_final, duracion, volumen, forma_onda)
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

        for name, segments in sfx_specs:
            self._sounds[name] = mezclador.Sound(self._synth_samples(segments))

        # Voz de Ruletín: sílabas de balbuceo (se eligen al azar mientras habla)
        for idx, (f0_start, f0_end, vowel, duration, consonant) in enumerate(RULETIN_SYLLABLES):
            sound = mezclador.Sound(self._voice_syllable(f0_start, f0_end, VOWEL_FORMANTS[vowel], duration, consonant))
            sound.set_volume(0.8)
            self._sounds[f"ruletin_{idx}"] = sound

    @staticmethod
    def _synth_samples(segments: List[Tuple[float, float, float, float, str]]) -> array:
        samples = array("h")
        for f_start, f_end, duration_sec, volume, wave_kind in segments:
            n_samples = max(1, int(SAMPLE_RATE * duration_sec))
            phase = 0.0
            for i in range(n_samples):
                t_ratio = i / n_samples
                freq = f_start + (f_end - f_start) * t_ratio
                phase += (2.0 * math.pi * freq) / SAMPLE_RATE

                # Envolvente ADSR percusiva y limpia
                attack = min(1.0, i / max(1.0, SAMPLE_RATE * 0.004))
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

                samples.append(int(max(-1.0, min(1.0, raw * volume * env)) * 32767))
        return samples

    @staticmethod
    def _voice_syllable(
        f0_start: float,
        f0_end: float,
        formants: Tuple[float, float],
        duration_sec: float,
        consonant: bool,
    ) -> array:
        """
        Sílaba de "balbuceo" de personaje: pulso glotal (diente de sierra) con deslizamiento
        de tono, filtrado por dos formantes de vocal y un golpecito de consonante al inicio.
        """
        n_samples = max(1, int(SAMPLE_RATE * duration_sec))
        rng = random.Random(int(f0_start * 10 + formants[0]))

        # Filtros resonantes de 2 polos (uno por formante)
        def resonator(freq: float, bandwidth: float):
            r = math.exp(-math.pi * bandwidth / SAMPLE_RATE)
            c1 = 2.0 * r * math.cos(2.0 * math.pi * freq / SAMPLE_RATE)
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
            phase = (phase + f0 / SAMPLE_RATE) % 1.0
            source = 2.0 * phase - 1.0  # diente de sierra
            voiced = f1(source) + 0.7 * f2(source)
            if consonant and i < SAMPLE_RATE * 0.012:
                voiced += rng.uniform(-0.6, 0.6) * (1.0 - i / (SAMPLE_RATE * 0.012))
            attack = min(1.0, i / (SAMPLE_RATE * 0.008))
            release = min(1.0, (n_samples - i) / (SAMPLE_RATE * 0.03))
            samples.append(voiced * attack * release)

        peak = max(1e-6, max(abs(v) for v in samples))
        return array("h", [int(v / peak * 0.75 * 32767) for v in samples])

    def start_ambient_music(self) -> None:
        """Inicia la música de fondo en bucle continuo (o la deja pedida si aún se está generando)."""
        if not self.enabled:
            return
        self.music_playing = True
        bgm_path = self.sound_files.get("ambient")
        if self._mixer_ready and bgm_path is not None and bgm_path.exists():
            try:
                mixer.music.load(str(bgm_path))
                mixer.music.set_volume(0.42)
                mixer.music.play(loops=-1)
            except Exception:
                pass

    def toggle_ambient_music(self) -> bool:
        """Activa o pausa la música de fondo y devuelve el nuevo estado."""
        if self.music_playing:
            if self._mixer_ready:
                mixer.music.pause()
            self.music_playing = False
            return False
        if self._mixer_ready:
            mixer.music.unpause()
        if not self._mixer_ready or not mixer.music.get_busy():
            self.start_ambient_music()
        self.music_playing = True
        return True

    def play(self, sound_name: str) -> None:
        """Reproduce un efecto en un canal libre sin interrumpir la música ni otros efectos."""
        if self.enabled and self._mixer_ready and sound_name in self._sounds:
            self._sounds[sound_name].play()
