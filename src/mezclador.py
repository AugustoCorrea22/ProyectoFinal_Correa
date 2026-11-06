"""
Mezclador de audio en Python puro para Windows (mezclador.py)
Usa solo la biblioteca estándar:
1. Efectos: hasta 16 sonidos simultáneos mezclados muestra por muestra.
2. Música: una pista en bucle continuo con volumen propio, pausa y reanudación.
3. Salida: la API waveOut de Windows, llamada con `ctypes`.
Los WAV de 16 bits se mezclan aquí. Otros formatos (como MP3) los reproduce el
propio Windows mediante MCI, también con `ctypes`.
"""

from array import array
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import threading
import time
from typing import Dict, List, Optional
import wave

SAMPLE_RATE = 22050
BUFFER_SAMPLES = 512  # ~23 ms por búfer
BUFFER_COUNT = 4
WHDR_DONE = 0x00000001
WAVE_MAPPER = 0xFFFFFFFF
WAVE_FORMAT_PCM = 1

AVAILABLE = sys.platform == "win32"


class _WaveFormatEx(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class _WaveHdr(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
    ]


def read_wav_samples(path: Path) -> array:
    """Lee un WAV PCM de 16 bits y lo devuelve en mono a SAMPLE_RATE."""
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        rate = wav_file.getframerate()
        if wav_file.getsampwidth() != 2:
            raise ValueError("Solo se mezclan WAV de 16 bits.")
        raw = wav_file.readframes(wav_file.getnframes())
    samples = array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if channels > 1:
        samples = array("h", [
            sum(samples[i:i + channels]) // channels
            for i in range(0, len(samples) - channels + 1, channels)
        ])
    if rate != SAMPLE_RATE:
        step = rate / SAMPLE_RATE
        samples = array("h", [samples[int(i * step)] for i in range(int(len(samples) / step))])
    return samples


class _Voice:
    __slots__ = ("data", "pos", "gain")

    def __init__(self, data: array, gain: float) -> None:
        self.data = data
        self.pos = 0
        self.gain = gain


class Sound:
    """Efecto en memoria que se puede sonar varias veces a la vez."""

    def __init__(self, samples: array) -> None:
        self._data = samples
        self._volume = 1.0

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, volume))

    def play(self) -> None:
        mixer.play_voice(self._data, self._volume)


class _SystemTrack:
    """Pista que reproduce Windows por MCI (para formatos que aquí no se decodifican)."""

    ALIAS = "the_roulete_musica"

    def __init__(self, path: Path) -> None:
        self._mci = ctypes.WinDLL("winmm").mciSendStringW
        self._mci.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HANDLE]
        self._mci.restype = wintypes.DWORD
        self._send(f"close {self.ALIAS}", check=False)
        self._send(f'open "{path}" type mpegvideo alias {self.ALIAS}')

    def _send(self, command: str, check: bool = True) -> str:
        answer = ctypes.create_unicode_buffer(64)
        error = self._mci(command, answer, 64, None)
        if error and check:
            raise OSError(f"MCI no pudo ejecutar: {command} ({error})")
        return answer.value

    def set_volume(self, volume: float) -> None:
        self._send(f"setaudio {self.ALIAS} volume to {int(volume * 1000)}", check=False)

    def play(self, loop: bool) -> None:
        self._send(f"play {self.ALIAS} from 0{' repeat' if loop else ''}")

    def pause(self) -> None:
        self._send(f"pause {self.ALIAS}", check=False)

    def resume(self) -> None:
        self._send(f"resume {self.ALIAS}", check=False)

    def is_playing(self) -> bool:
        return self._send(f"status {self.ALIAS} mode", check=False) == "playing"

    def close(self) -> None:
        self._send(f"close {self.ALIAS}", check=False)


class _Music:
    """Canal de música: una pista en bucle, con volumen, pausa y reanudación."""

    def __init__(self, owner: "_Mixer") -> None:
        self._owner = owner
        self._cache: Dict[str, array] = {}
        self.data: Optional[array] = None
        self.pos = 0
        self.volume = 1.0
        self.loops = 0
        self.playing = False
        self.paused = False
        self._track: Optional[_SystemTrack] = None

    def load(self, path: str) -> None:
        self.stop()
        try:
            data = self._cache.get(path)
            if data is None:
                data = read_wav_samples(Path(path))
                self._cache[path] = data
            self.data = data
        except (wave.Error, ValueError, EOFError):
            self.data = None
            self._track = _SystemTrack(Path(path))

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))
        self.apply_system_volume()

    def apply_system_volume(self) -> None:
        if self._track is not None:
            self._track.set_volume(self.volume * self._owner.music_gain)

    def play(self, loops: int = 0) -> None:
        self.paused = False
        if self._track is not None:
            self.apply_system_volume()
            self._track.play(loops != 0)
            return
        with self._owner.lock:
            self.pos = 0
            self.loops = loops
            self.playing = self.data is not None

    def pause(self) -> None:
        self.paused = True
        if self._track is not None:
            self._track.pause()

    def unpause(self) -> None:
        self.paused = False
        if self._track is not None:
            self._track.resume()

    def get_busy(self) -> bool:
        if self.paused:
            return False
        if self._track is not None:
            return self._track.is_playing()
        return self.playing

    def stop(self) -> None:
        with self._owner.lock:
            self.playing = False
            self.paused = False
        if self._track is not None:
            self._track.close()
            self._track = None

    def fill(self, out: List[float], count: int) -> None:
        """Suma la música al búfer de salida, dando la vuelta si la pista está en bucle."""
        if not self.playing or self.paused or self.data is None:
            return
        data, gain, written = self.data, self.volume * self._owner.music_gain, 0
        while written < count:
            chunk = data[self.pos:self.pos + count - written]
            for i, sample in enumerate(chunk, written):
                out[i] += sample * gain
            written += len(chunk)
            self.pos += len(chunk)
            if self.pos >= len(data):
                self.pos = 0
                if self.loops == 0:
                    self.playing = False
                    return
                if self.loops > 0:
                    self.loops -= 1


class _Mixer:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.music = _Music(self)
        self._voices: List[_Voice] = []
        self._max_voices = 16
        self._thread: Optional[threading.Thread] = None
        self._ready = False
        self.underruns = 0
        self.music_gain = 1.0  # Volumen general de la música (Opciones)
        self.effects_gain = 1.0  # Volumen general de los efectos (Opciones)

    def set_music_gain(self, gain: float) -> None:
        self.music_gain = max(0.0, min(1.0, gain))
        self.music.apply_system_volume()

    def set_num_channels(self, count: int) -> None:
        self._max_voices = count

    def play_voice(self, data: array, gain: float) -> None:
        with self.lock:
            if len(self._voices) < self._max_voices:
                self._voices.append(_Voice(data, gain))

    def init(self) -> None:
        """Abre la salida de audio de Windows y arranca el hilo que la alimenta."""
        if self._ready:
            return
        if not AVAILABLE:
            raise OSError("El mezclador de audio solo funciona en Windows.")
        winmm = ctypes.WinDLL("winmm")
        winmm.waveOutOpen.argtypes = [
            ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.POINTER(_WaveFormatEx),
            ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD,
        ]
        winmm.waveOutPrepareHeader.argtypes = [wintypes.HANDLE, ctypes.POINTER(_WaveHdr), wintypes.UINT]
        winmm.waveOutWrite.argtypes = [wintypes.HANDLE, ctypes.POINTER(_WaveHdr), wintypes.UINT]

        wave_format = _WaveFormatEx(WAVE_FORMAT_PCM, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16, 0)
        handle = wintypes.HANDLE()
        error = winmm.waveOutOpen(ctypes.byref(handle), WAVE_MAPPER, ctypes.byref(wave_format), 0, 0, 0)
        if error:
            raise OSError(f"No se pudo abrir la salida de audio (error {error}).")

        byte_count = BUFFER_SAMPLES * 2
        buffers = [ctypes.create_string_buffer(byte_count) for _ in range(BUFFER_COUNT)]
        headers = [_WaveHdr() for _ in range(BUFFER_COUNT)]
        for buffer, header in zip(buffers, headers):
            header.lpData = ctypes.cast(buffer, ctypes.c_void_p)
            header.dwBufferLength = byte_count
            error = winmm.waveOutPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(_WaveHdr))
            if error:
                raise OSError(f"No se pudo preparar el búfer de audio (error {error}).")

        def feed() -> None:
            for buffer, header in zip(buffers, headers):
                self._render_into(buffer)
                winmm.waveOutWrite(handle, ctypes.byref(header), ctypes.sizeof(_WaveHdr))
            while True:
                pending = [i for i, header in enumerate(headers) if header.dwFlags & WHDR_DONE]
                if len(pending) == BUFFER_COUNT:
                    self.underruns += 1
                for i in pending:
                    self._render_into(buffers[i])
                    headers[i].dwFlags &= ~WHDR_DONE
                    winmm.waveOutWrite(handle, ctypes.byref(headers[i]), ctypes.sizeof(_WaveHdr))
                time.sleep(0.003)

        self._thread = threading.Thread(target=feed, name="mezclador-audio", daemon=True)
        self._thread.start()
        self._ready = True

    def mix(self, count: int) -> array:
        """Mezcla música y efectos en `count` muestras de 16 bits, recortando los picos."""
        out = [0.0] * count
        with self.lock:
            self.music.fill(out, count)
            alive = []
            for voice in self._voices:
                chunk = voice.data[voice.pos:voice.pos + count]
                gain = voice.gain * self.effects_gain
                for i, sample in enumerate(chunk):
                    out[i] += sample * gain
                voice.pos += count
                if voice.pos < len(voice.data):
                    alive.append(voice)
            self._voices = alive
        return array("h", [
            32767 if s > 32767 else (-32768 if s < -32768 else int(s))
            for s in out
        ])

    def _render_into(self, buffer) -> None:
        samples = self.mix(BUFFER_SAMPLES)
        ctypes.memmove(buffer, samples.buffer_info()[0], BUFFER_SAMPLES * 2)


mixer = _Mixer()
