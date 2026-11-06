"""
Rocola del casino (music_jukebox.py)
1. Sintetiza 2 pistas musicales originales en bucle perfecto:
   - Juego:  Jazz lounge de casino (swing suave, Rhodes, contrabajo caminante,
             escobillas y vibráfono).
   - Tienda: Bossa nova de ascensor de casino (guitarra de nylon sincopada,
             bajo bossa, shaker, rimshot y flauta).
2. Cambia automáticamente entre la música del Juego y la de la Tienda según la fase.
3. Palabras secretas: escribiendo con el teclado (sin cuadro visible) en cualquier
   momento, se reproduce la canción asociada si el archivo existe; si no existe,
   no pasa nada (silencio total). Al terminar la canción, vuelve la música normal.
"""

from array import array
import math
from pathlib import Path
import random
import struct
import subprocess
import sys
from typing import Dict, List, Optional, Sequence, Tuple
import wave

from src.mezclador import AVAILABLE as HAS_MIXER, mixer


SAMPLE_RATE = 22050
GAME_TRACK_FILE = "musica_juego_lounge_v1.wav"
SHOP_TRACK_FILE = "musica_tienda_bossa_v1.wav"
AMBIENT_VOLUME = 0.42
SECRET_SONG_VOLUME = 0.7


SECRET_WORDS: Dict[str, Tuple[str, ...]] = {
    "ruleta": ("ruleta_piojos", "ruleta piojos", "ruleta-piojos", "ruleta"),
    "abuelo": ("abuelo", "buelo", "el_abuelo", "el abuelo"),
}
SONG_EXTENSIONS = (".mp3", ".ogg", ".wav", ".flac")





def _midi(note: int) -> float:
    return 440.0 * 2.0 ** ((note - 69) / 12.0)


class _Mixdown:
    """Buffer flotante en bucle: lo que se pasa del final se suma al principio."""

    def __init__(self, seconds: float) -> None:
        self.length = int(seconds * SAMPLE_RATE)
        self.buf = array("f", bytes(4 * self.length))

    def add(self, start_sec: float, samples: Sequence[float], gain: float = 1.0) -> None:
        start = int(start_sec * SAMPLE_RATE)
        n = self.length
        buf = self.buf
        for i, s in enumerate(samples):
            buf[(start + i) % n] += s * gain

    def write_wav(self, path: Path, peak: float = 0.82) -> None:
        mx = max(1e-06, max(abs(v) for v in self.buf))
        scale = peak / mx
        frames = bytearray()
        for v in self.buf:
            frames.extend(struct.pack("<h", int(max(-1.0, min(1.0, v * scale)) * 32767)))
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(bytes(frames))


def _rhodes(freq: float, dur: float, vel: float = 1.0) -> List[float]:
    n = int(dur * SAMPLE_RATE)
    out = []
    w = 2.0 * math.pi * freq / SAMPLE_RATE
    for i in range(n):
        t = i / SAMPLE_RATE
        env = min(1.0, t / 0.006) * math.exp(-t * 2.2)
        rel = 1.0 if i < n - 400 else max(0.0, (n - i) / 400.0)
        trem = 1.0 + 0.18 * math.sin(2.0 * math.pi * 5.0 * t)
        bell = 0.3 * math.sin(4.0 * w * i) * math.exp(-t * 9.0)
        out.append((math.sin(w * i) + 0.22 * math.sin(2.0 * w * i) + bell) * env * rel * trem * vel)
    return out


def _upright_bass(freq: float, dur: float, vel: float = 1.0) -> List[float]:
    n = int(dur * SAMPLE_RATE)
    out = []
    w = 2.0 * math.pi * freq / SAMPLE_RATE
    for i in range(n):
        t = i / SAMPLE_RATE
        env = min(1.0, t / 0.008) * math.exp(-t * 3.2)
        rel = 1.0 if i < n - 300 else max(0.0, (n - i) / 300.0)
        thump = 0.45 * math.exp(-t * 40.0)
        out.append((math.sin(w * i) * (0.85 + thump) + 0.28 * math.sin(2.0 * w * i) * math.exp(-t * 6.0)) * env * rel * vel)
    return out


def _vibraphone(freq: float, dur: float, vel: float = 1.0) -> List[float]:
    n = int(dur * SAMPLE_RATE)
    out = []
    w = 2.0 * math.pi * freq / SAMPLE_RATE
    for i in range(n):
        t = i / SAMPLE_RATE
        env = min(1.0, t / 0.003) * math.exp(-t * 1.6)
        rel = 1.0 if i < n - 500 else max(0.0, (n - i) / 500.0)
        trem = 1.0 + 0.32 * math.sin(2.0 * math.pi * 5.5 * t)
        out.append((math.sin(w * i) + 0.12 * math.sin(4.0 * w * i) * math.exp(-t * 7.0)) * env * rel * trem * vel)
    return out


def _nylon_pluck(freq: float, dur: float, vel: float = 1.0) -> List[float]:
    n = int(dur * SAMPLE_RATE)
    out = []
    w = 2.0 * math.pi * freq / SAMPLE_RATE
    for i in range(n):
        t = i / SAMPLE_RATE
        env = min(1.0, t / 0.002) * math.exp(-t * 4.5)
        rel = 1.0 if i < n - 250 else max(0.0, (n - i) / 250.0)
        s = (
            math.sin(w * i)
            + 0.45 * math.sin(2.0 * w * i) * math.exp(-t * 6.0)
            + 0.2 * math.sin(3.0 * w * i) * math.exp(-t * 10.0)
        )
        out.append(s * env * rel * vel)
    return out


def _flute(freq: float, dur: float, vel: float = 1.0) -> List[float]:
    n = int(dur * SAMPLE_RATE)
    out = []
    rng = random.Random(int(freq * 100))
    phase = 0.0
    for i in range(n):
        t = i / SAMPLE_RATE
        att = min(1.0, t / 0.06)
        rel = 1.0 if i < n - 900 else max(0.0, (n - i) / 900.0)
        vib = 1.0 + 0.006 * math.sin(2.0 * math.pi * 5.2 * t) * min(1.0, t / 0.25)
        phase += 2.0 * math.pi * freq * vib / SAMPLE_RATE
        breath = (rng.random() - 0.5) * 0.06
        out.append((math.sin(phase) + 0.1 * math.sin(2.0 * phase) + breath) * att * rel * vel)
    return out


def _noise_hit(dur: float, decay: float, vel: float, seed: int, bright: float = 0.6) -> List[float]:
    n = int(dur * SAMPLE_RATE)
    rng = random.Random(seed)
    out = []
    prev = 0.0
    for i in range(n):
        t = i / SAMPLE_RATE
        raw = rng.random() * 2.0 - 1.0
        hp = raw - prev * bright
        prev = raw
        out.append(hp * math.exp(-t * decay) * min(1.0, t / 0.002) * vel)
    return out


def _rim_click(vel: float = 1.0) -> List[float]:
    n = int(0.05 * SAMPLE_RATE)
    out = []
    for i in range(n):
        t = i / SAMPLE_RATE
        out.append((math.sin(2.0 * math.pi * 1700.0 * t) * 0.6 + math.sin(2.0 * math.pi * 820.0 * t) * 0.4) * math.exp(-t * 90.0) * vel)
    return out


def write_game_lounge_track(path: Path) -> None:
    bpm = 92.0
    beat = 60.0 / bpm
    swing = beat * (2.0 / 3.0)

    chords = [
        (41, [57, 60, 64, 67]),
        (38, [54, 57, 60, 63]),
        (43, [58, 62, 65, 69]),
        (36, [58, 62, 64, 69]),
        (45, [55, 60, 64, 67]),
        (38, [54, 57, 60, 66]),
        (43, [53, 58, 62, 65]),
        (36, [52, 58, 61, 64]),
    ]
    bar = beat * 4
    mix = _Mixdown(bar * len(chords))

    melody = [
        (0, 0.0, 72, 1.5), (0, 1.67, 69, 0.6), (0, 2.5, 67, 1.4),
        (1, 0.0, 66, 0.6), (1, 0.67, 69, 0.6), (1, 1.5, 72, 1.0), (1, 3.0, 75, 0.9),
        (2, 0.0, 74, 2.0), (2, 2.67, 70, 0.6), (2, 3.33, 69, 0.6),
        (3, 0.0, 67, 1.5), (3, 2.0, 64, 0.6), (3, 2.67, 65, 0.6), (3, 3.33, 67, 0.6),
        (4, 0.0, 72, 1.0), (4, 1.0, 76, 1.5), (4, 3.0, 74, 0.6), (4, 3.67, 72, 0.3),
        (5, 0.0, 69, 1.4), (5, 2.0, 66, 0.6), (5, 2.67, 69, 1.2),
        (6, 0.0, 70, 0.6), (6, 0.67, 69, 0.6), (6, 1.33, 67, 0.6), (6, 2.0, 65, 1.8),
        (7, 0.0, 64, 1.0), (7, 1.5, 67, 0.6), (7, 2.67, 70, 0.6), (7, 3.33, 72, 0.6),
    ]

    rng = random.Random(7)
    for bar_idx, (root, voicing) in enumerate(chords):
        t0 = bar_idx * bar
        next_root = chords[(bar_idx + 1) % len(chords)][0]

        # Contrabajo caminante
        third = voicing[0] - 12 if voicing[0] - 12 > root else voicing[0] - 24
        walk = [root, third, root + 7, next_root + (1 if rng.random() < 0.5 else -1)]
        for b, note in enumerate(walk):
            mix.add(t0 + b * beat, _upright_bass(_midi(note), beat * 0.95, 0.9 if b == 0 else 0.75), 0.55)

        # Acordes de Rhodes
        for hit_t, vel in ((0.0, 0.85), (beat + swing, 0.65)):
            for k, note in enumerate(voicing):
                mix.add(t0 + hit_t + k * 0.008, _rhodes(_midi(note), beat * 1.6, vel), 0.13)

        # Escobillas
        for b in range(4):
            mix.add(t0 + b * beat, _noise_hit(0.22, 18.0, 0.55 if b % 2 else 0.4, 100 + bar_idx * 4 + b), 0.11)
            if b % 2 == 1:
                mix.add(t0 + b * beat + swing, _noise_hit(0.12, 30.0, 0.35, 900 + bar_idx * 4 + b), 0.11)
            mix.add(t0 + b * beat, _noise_hit(beat, 3.5, 0.1, 500 + bar_idx * 4 + b, bright=0.2), 0.1)

    for bar_idx, pos, note, dur_beats in melody:
        mix.add(bar_idx * bar + pos * beat, _vibraphone(_midi(note), dur_beats * beat + 0.25, 0.8), 0.2)

    mix.write_wav(path)


def write_shop_bossa_track(path: Path) -> None:
    bpm = 128.0
    beat = 60.0 / bpm
    eighth = beat / 2.0

    chords = [
        (36, [52, 55, 59, 62]),
        (45, [55, 60, 64, 71]),
        (38, [53, 57, 60, 64]),
        (43, [53, 59, 64, 65]),
        (40, [50, 55, 59, 62]),
        (45, [55, 61, 64, 67]),
        (38, [53, 57, 60, 65]),
        (43, [53, 56, 59, 65]),
    ]
    bar = beat * 4
    mix = _Mixdown(bar * len(chords))

    guitar_pattern = [0, 3, 5, 6]
    rim_pattern_2bars = [0, 3, 6, 10, 13]

    melody = [
        (0, 0.0, 76, 1.5), (0, 1.5, 74, 0.5), (0, 2.0, 72, 1.5),
        (1, 0.0, 71, 0.5), (1, 0.5, 72, 0.5), (1, 1.0, 76, 2.5),
        (2, 0.0, 77, 1.5), (2, 1.5, 76, 0.5), (2, 2.0, 74, 1.5),
        (3, 0.0, 71, 1.0), (3, 1.5, 74, 0.5), (3, 2.0, 77, 1.8),
        (4, 0.0, 74, 1.5), (4, 1.5, 71, 0.5), (4, 2.0, 67, 1.8),
        (5, 0.0, 73, 1.0), (5, 1.0, 76, 1.0), (5, 2.0, 79, 1.8),
        (6, 0.0, 77, 1.5), (6, 1.5, 76, 0.5), (6, 2.0, 74, 0.5), (6, 2.5, 72, 1.3),
        (7, 0.0, 71, 2.0), (7, 2.5, 74, 0.5), (7, 3.0, 79, 0.9),
    ]

    for bar_idx, (root, voicing) in enumerate(chords):
        t0 = bar_idx * bar

        # Bajo de bossa
        for pos_beats, offset, vel in ((0.0, 0, 0.95), (1.5, 7, 0.75), (2.0, 0, 0.85), (3.5, 7, 0.7)):
            mix.add(t0 + pos_beats * beat, _upright_bass(_midi(root + offset), beat * 1.3, vel), 0.55)

        # Guitarra de nylon sincopada
        for e in guitar_pattern:
            for k, note in enumerate(voicing):
                mix.add(t0 + e * eighth + k * 0.012, _nylon_pluck(_midi(note), eighth * 2.2, 0.75), 0.1)

        # Shaker
        for s in range(16):
            vel = 0.5 if s % 4 == 2 else (0.32 if s % 2 else 0.22)
            mix.add(t0 + s * (beat / 4.0), _noise_hit(0.06, 70.0, vel, 3000 + bar_idx * 16 + s, bright=0.9), 0.09)

        # Rimshot (patrón de 2 compases)
        bar_in_pair = bar_idx % 2
        for e in rim_pattern_2bars:
            if e // 8 == bar_in_pair:
                mix.add(t0 + (e % 8) * eighth, _rim_click(0.8), 0.11)

        # Bombo suave
        for b in (0, 2):
            kick = [
                math.sin(2.0 * math.pi * (60.0 + 50.0 * math.exp(-i / 600.0)) * i / SAMPLE_RATE) * math.exp(-i / 2600.0)
                for i in range(int(0.25 * SAMPLE_RATE))
            ]
            mix.add(t0 + b * beat, kick, 0.22)

    for bar_idx, pos, note, dur_beats in melody:
        mix.add(bar_idx * bar + pos * beat, _flute(_midi(note), dur_beats * beat, 0.8), 0.17)

    mix.write_wav(path)


def track_paths(cache_dir: Path) -> Tuple[Path, Path]:
    return cache_dir / GAME_TRACK_FILE, cache_dir / SHOP_TRACK_FILE


def generate_missing_tracks(cache_dir: Path) -> None:
    """Sintetiza las pistas que falten. El archivo aparece recién cuando está completo."""
    for path, writer in zip(track_paths(cache_dir), (write_game_lounge_track, write_shop_bossa_track)):
        if not path.exists():
            temp = path.with_suffix(".tmp")
            writer(temp)
            temp.replace(path)


def start_track_generation(cache_dir: Path) -> None:
    """Genera la música en un proceso de baja prioridad: el juego no se traba mientras tanto."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
    subprocess.Popen(
        [sys.executable, "-m", "src.music_jukebox", str(cache_dir)],
        cwd=str(Path(__file__).resolve().parent.parent),
        creationflags=flags,
    )


def _norm_stem(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def find_secret_songs(audio_dir: Path, candidates: Sequence[str]) -> List[Path]:
    """Devuelve en orden de prioridad todos los archivos candidatos en audio del juego y Escritorio."""
    search_dirs = [audio_dir, audio_dir / "canciones"]
    try:
        search_dirs.extend(list(audio_dir.resolve().parents)[:4])
    except OSError:
        pass
    home = Path.home()
    search_dirs += [
        home / "OneDrive" / "Desktop",
        home / "Desktop",
        home / "OneDrive" / "Escritorio",
        home / "Escritorio",
        home / "Downloads",
    ]
    wanted = {_norm_stem(c) for c in candidates}
    found = []
    seen = set()
    for folder in search_dirs:
        try:
            if not folder.is_dir():
                continue
            for f in folder.iterdir():
                if f.is_file() and f.suffix.lower() in SONG_EXTENSIONS and _norm_stem(f.stem) in wanted:
                    key = str(f.resolve()).lower()
                    if key not in seen:
                        seen.add(key)
                        found.append(f)
        except OSError:
            continue
    return found


class MusicDirector:
    """Elige qué pista suena (Juego / Tienda / canción secreta) y escucha las palabras secretas."""

    POLL_MS = 400

    def __init__(self, app) -> None:
        self.app = app
        self.audio = app.audio
        self.audio_dir = Path(self.audio.audio_dir)
        self.cache_dir = Path(self.audio.cache_dir)
        self.game_track = None
        self.shop_track = None
        self.current_track = None
        self.secret_playing = False
        self._typed = ""

        self._refresh_tracks()
        if self.game_track is None or self.shop_track is None:
            try:
                start_track_generation(self.cache_dir)
            except OSError:
                pass

        # El botón de música del juego sigue funcionando, pero con la pista que toca ahora
        orig_start = self.audio.start_ambient_music

        def start_ambient_music_synced() -> None:
            orig_start()
            self.current_track = self.audio.sound_files.get("ambient")
            self.secret_playing = False

        self.audio.start_ambient_music = start_ambient_music_synced

        if self.game_track is not None:
            self.audio.sound_files["ambient"] = self.game_track

            if getattr(self.audio, "music_playing", False):
                self._load_and_play(self.game_track, loops=-1, volume=AMBIENT_VOLUME)

        try:
            app.root.bind_all("<KeyPress>", self._on_key, add="+")
        except Exception:
            pass
        self._schedule()

    @property
    def _mixer_ok(self) -> bool:
        return bool(HAS_MIXER and getattr(self.audio, "_mixer_ready", False))

    def _load_and_play(self, path: Path, loops: int, volume: float) -> bool:
        if not self._mixer_ok:
            return False
        try:
            mixer.music.load(str(path))
            mixer.music.set_volume(volume)
            mixer.music.play(loops=loops)
            self.current_track = path
            self.audio.music_playing = True
            return True
        except Exception:
            return False

    def _refresh_tracks(self) -> None:
        game_path, shop_path = track_paths(self.cache_dir)
        if self.game_track is None and game_path.exists():
            self.game_track = game_path
        if self.shop_track is None and shop_path.exists():
            self.shop_track = shop_path

    def _desired_track(self) -> Optional[Path]:
        # En los menús suena la música del Juego; en la Tienda, la bossa
        if (
            getattr(self.app, "in_intro_screen", False)
            or getattr(self.app, "in_main_menu", False)
            or getattr(self.app, "in_collection_menu", False)
            or getattr(self.app, "in_options_menu", False)
        ):
            return self.game_track
        try:
            from src.economy import GamePhase
            in_shop = self.app.engine.economy.phase == GamePhase.SHOP
        except Exception:
            in_shop = False
        if in_shop and self.shop_track is not None:
            return self.shop_track
        return self.game_track

    def _schedule(self) -> None:
        try:
            self.app.root.after(self.POLL_MS, self._tick)
        except Exception:
            pass

    def _tick(self) -> None:
        try:
            self._update()
        finally:
            self._schedule()

    def update(self) -> None:
        """Se llama al cambiar de pantalla: la música no debe poder trabar el juego."""
        try:
            self._update()
        except Exception:
            pass

    def _update(self) -> None:
        self._refresh_tracks()
        desired = self._desired_track()
        if desired is not None:
            self.audio.sound_files["ambient"] = desired
        if not self._mixer_ok or not getattr(self.audio, "music_playing", False):
            return

        if self.secret_playing:
            if mixer.music.get_busy():
                return
            self.secret_playing = False
            self.current_track = None

        if desired is not None and desired != self.current_track:
            self._load_and_play(desired, loops=-1, volume=AMBIENT_VOLUME)

    def _on_key(self, event) -> None:
        ch = getattr(event, "char", "") or ""
        if len(ch) != 1 or not ch.isalpha():
            return
        self._typed = (self._typed + ch.lower())[-16:]
        for word, candidates in SECRET_WORDS.items():
            if self._typed.endswith(word):
                self._typed = ""
                self.play_secret_song(candidates)
                return

    def play_secret_song(self, candidates: Sequence[str]) -> bool:
        songs = find_secret_songs(self.audio_dir, candidates)
        if not songs:
            return False
        was_playing = bool(getattr(self.audio, "music_playing", False))
        prev_track = self.current_track
        for song in songs:
            if self._load_and_play(song, loops=0, volume=SECRET_SONG_VOLUME):
                self.secret_playing = True
                if not was_playing and not getattr(self.app, "is_spinning", False):
                    try:
                        self.app._redraw()
                    except Exception:
                        pass
                return True
        if was_playing and self._mixer_ok and not mixer.music.get_busy() and prev_track is not None:
            self._load_and_play(prev_track, loops=-1, volume=AMBIENT_VOLUME)
        return False


if __name__ == "__main__":
    generate_missing_tracks(Path(sys.argv[1]))
