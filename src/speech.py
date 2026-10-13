"""Interruptible Russian speech with automatic USB hotplug and output recovery."""
import logging
import io
import math
from pathlib import Path
import struct
import subprocess
import threading
import time
import wave

from audio import Audio


class Speech:
    def __init__(self, audio=None, watch=True):
        self.audio = audio or Audio()
        self.volume = 75
        self.voice = 'espeak'
        self.rate = 100
        self.pitch = 100
        self.signals = True
        self.fallback_voice = False
        self.index = 0
        self.last = ''
        self._stopped = False
        self.error = ''
        self.outputs = []
        self.manual = None
        self.failed = {}
        self.retry_at = 0
        self.processes = []
        self.generation = 0
        self.completion = threading.Event()
        self.completion.set()
        self.deadline = 0
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.refresh(announce=False)
        if watch:
            threading.Thread(target=self._watch, daemon=True).start()

    def _current(self):
        return self.outputs[self.index] if self.outputs else None

    def refresh(self, announce=True):
        try:
            discovered = self.audio.discover()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            logging.warning('Audio discovery: %s', error)
            # A timeout is not evidence that a device was disconnected.
            return
        with self.lock:
            if self.closed.is_set():
                return
            previous = self._current()
            self.outputs = discovered
            now = time.monotonic()
            self.failed = {name: until for name, until in self.failed.items() if until > now}
            available = [i for i, item in enumerate(discovered) if item.name not in self.failed]
            manual = next((i for i in available if discovered[i].name == self.manual), None)
            if not any(item.name == self.manual for item in discovered):
                self.manual = None
            self.index = manual if manual is not None else available[0] if available else 0
            current = self._current()
            changed = ((previous.name, previous.serial) if previous else None) != (
                (current.name, current.serial) if current else None)
            if not current:
                self.error = 'Звук пока не найден. Подключите USB наушники; поиск продолжается автоматически.'
                if changed:
                    self._stop_processes()
                    self.completion.set()
            elif announce and self.last and not self._stopped and available and (
                    changed or (self.retry_at and now >= self.retry_at)):
                self.retry_at = 0
                self._schedule('Звуковое устройство: ' + current.title + '. ' + self.last)

    def _watch(self):
        while not self.closed.wait(2):
            self.refresh()

    def _stop_processes(self):
        """Cancel a worker without releasing a waiter that is being rerouted."""
        with self.lock:
            self.generation += 1
            for process in self.processes:
                try:
                    if process.poll() is None:
                        process.terminate()
                        if process.poll() is None:
                            process.kill()
                except OSError:
                    pass
            self.processes = []

    def stop(self):
        """Stop the current utterance, including its waiter and hotplug retry."""
        with self.lock:
            self._stop_processes()
            self._stopped = True
            self.retry_at = 0
            self.completion.set()

    def close(self):
        with self.lock:
            self.closed.set()
            self.stop()
            self.completion.set()

    def say(self, text, wait=False):
        with self.lock:
            self.completion.set()  # Explicit selection cancels the previous request.
            self.completion = threading.Event()
            self.deadline = time.monotonic() + 90
            self.last = str(text)[:2000]
            self._stopped = False
            done = self._schedule(self.last)
        if wait:
            if not done.wait(timeout=100):
                with self.lock:
                    if done is self.completion:
                        self.stop()
                        self.error = 'Время ожидания озвучки истекло.'
                    done.set()
        return done

    def say_if_idle(self, text):
        """Background status must never interrupt selection or launch speech."""
        with self.lock:
            if self.completion.is_set() and not self.closed.is_set():
                self.say(text)

    def configure(self, settings):
        with self.lock:
            self.voice = settings['voice']
            self.rate = settings['rate']
            self.pitch = settings['pitch']
            self.volume = settings['volume']
            self.signals = settings['signals']
            self.fallback_voice = False

    def voice_command(self):
        if self.voice != 'espeak' and not self.fallback_voice and Path('/usr/bin/RHVoice-test').is_file():
            return ['RHVoice-test', '-p', self.voice, '-r', str(self.rate), '-t', str(self.pitch), '-o', '-']
        return ['espeak-ng', '-v', 'ru', '-s', str(round(165 * self.rate / 100)),
                '-p', str(min(99, self.pitch // 2)), '-a', '100', '--stdout', '--stdin']

    def signal(self, kind='progress', channel='both', force=False, wait=False):
        with self.lock:
            output = self._current()
            if (not self.signals and not force) or output is None or self.closed.is_set():
                return False
            command = self.audio.player(output, self.volume)
        def play():
            sequence = {'progress': [550], 'success': [600, 900], 'error': [350, 220, 350]}[kind]
            data = io.BytesIO()
            with wave.open(data, 'wb') as stream:
                stream.setnchannels(2)
                stream.setsampwidth(2)
                stream.setframerate(24000)
                for frequency in sequence:
                    frames = bytearray()
                    for index in range(3600):
                        envelope = min(1, index / 240, (3599 - index) / 240)
                        value = round(5000 * envelope * math.sin(2 * math.pi * frequency * index / 24000))
                        frames.extend(struct.pack('<hh', value if channel != 'right' else 0,
                                                  value if channel != 'left' else 0))
                    stream.writeframes(frames)
            try:
                result = subprocess.run(command, input=data.getvalue(), stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=3)
                return result.returncode == 0
            except (OSError, subprocess.SubprocessError):
                return False
        if wait:
            return play()
        threading.Thread(target=play, daemon=True).start()

    def _schedule(self, text):
        """Called under the lock. Only this generation may change speech state."""
        self._stop_processes()
        generation = self.generation
        # Hotplug reroutes the same request: its waiter must follow the new worker.
        if self.completion.is_set():
            self.completion = threading.Event()
            self.deadline = time.monotonic() + 90
        done = self.completion
        if not self.outputs or self.closed.is_set():
            done.set()
            return done
        threading.Thread(target=self._speak, args=(generation, text, done), daemon=True).start()
        return done

    def _speak(self, generation, text, done):
        attempted = set()
        deadline = self.deadline
        try:
            while not self.closed.is_set():
                with self.lock:
                    if generation != self.generation:
                        return
                    if time.monotonic() >= deadline:
                        self.error = 'Время ожидания озвучки истекло.'
                        return
                    output = self._current()
                    if output is None or output.name in attempted:
                        return
                    attempted.add(output.name)
                voice = player = None
                try:
                    self.audio.activate(output)
                    with self.lock:
                        if generation != self.generation or self.closed.is_set():
                            return
                        if time.monotonic() >= deadline:
                            self.error = 'Время ожидания озвучки истекло.'
                            return
                        voice_command = self.voice_command()
                        try:
                            voice = subprocess.Popen(voice_command, stdin=subprocess.PIPE,
                                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                        except OSError:
                            if voice_command[0] != 'RHVoice-test':
                                raise
                            self.fallback_voice = True
                            attempted.discard(output.name)
                            text = 'Включён резервный голос. ' + text
                            continue
                        self.processes = [voice]
                        player = subprocess.Popen(self.audio.player(output, self.volume), stdin=voice.stdout,
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        self.processes.append(player)
                        voice.stdout.close()
                        voice.stdin.write(text.encode('utf-8'))
                        voice.stdin.close()
                    for process in (voice, player):
                        process.wait(timeout=max(0.1, deadline - time.monotonic()))
                    if voice.returncode and self.voice != 'espeak' and not self.fallback_voice:
                        with self.lock:
                            if generation != self.generation:
                                return
                            self.fallback_voice = True
                        attempted.discard(output.name)
                        text = 'Включён резервный голос. ' + text
                        continue
                    if voice.returncode or player.returncode:
                        raise OSError('Speech player failed')
                    with self.lock:
                        if generation == self.generation:
                            self.error = ''
                            self.retry_at = 0
                    return
                except (OSError, subprocess.SubprocessError) as error:
                    with self.lock:
                        if generation != self.generation:
                            return
                        logging.warning('Speech output %s: %s', output.name, error)
                        self.failed[output.name] = time.monotonic() + 20
                        choices = [i for i, item in enumerate(self.outputs)
                                   if item.name not in attempted
                                   and self.failed.get(item.name, 0) <= time.monotonic()]
                        self.error = 'Не удалось включить звук. Пробую другой выход. F6 — выбор вручную.'
                        if not choices:
                            self.retry_at = time.monotonic() + 20
                            return
                        self.index = choices[0]
                finally:
                    for process in (player, voice):
                        if process is not None:
                            try:
                                if process.poll() is None:
                                    process.kill()
                                process.wait(timeout=1)
                            except (OSError, subprocess.SubprocessError):
                                pass
                            for stream in (process.stdin, process.stdout):
                                if stream is not None and not stream.closed:
                                    stream.close()
                    with self.lock:
                        if generation == self.generation:
                            self.processes = []
        finally:
            with self.lock:
                if generation == self.generation or self.closed.is_set():
                    done.set()

    def next_output(self):
        with self.lock:
            if self.outputs:
                self.index = (self.index + 1) % len(self.outputs)
                current = self._current()
                self.manual = current.name
                self.failed.pop(current.name, None)
                self._stopped = False
                self._schedule(f'Звуковое устройство {self.index + 1}. {current.title}. ' + self.last)
