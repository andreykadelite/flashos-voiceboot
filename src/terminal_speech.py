"""Bounded terminal speech, driven by the caller's event loop.

This module only receives text supplied by the UI. It never reads terminal input,
passwords, processes or widgets. Call all methods from the same UI thread.
"""
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata


OVERFLOW_NOTICE = 'Часть вывода не озвучена. Полный текст остаётся на экране.'
_ESCAPE = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[ -/]*[@-~])')


def normalize_text(text):
    """Remove terminal controls and empty lines, retaining punctuation and paths."""
    text = _ESCAPE.sub('', text)
    text = ''.join(char if char in '\n\r\t' or unicodedata.category(char)
                   not in ('Cc', 'Cf', 'Cs') else '' for char in text)
    return '\n'.join(' '.join(line.split()) for line in text.splitlines() if line.strip())


def _chunks(text, size):
    while text:
        end = min(size, len(text))
        if end < len(text):
            boundary = max(text.rfind(' ', 0, end + 1), text.rfind('\n', 0, end + 1))
            if boundary >= size // 2:
                end = boundary
        chunk = text[:end].strip()
        if chunk:
            yield chunk
        text = text[end:].lstrip()


@dataclass(frozen=True)
class _Utterance:
    text: str
    automatic: bool
    notice: bool = False
    input_echo: bool = False


class TerminalSpeech:
    """Serialize Speech.say() calls without interrupting earlier output chunks.

    ``speak`` is manual by default; priority speech interrupts the current
    utterance and goes first. Manual speech clears ``stopped``, never ``paused``.
    Paused/stopped output is discarded, so resume never reads a stale backlog.
    ``close`` stops speech but leaves the shared Speech object open.
    """
    def __init__(self, speech, max_messages=80, chunk_chars=800):
        if not isinstance(max_messages, int) or isinstance(max_messages, bool) or max_messages < 2:
            raise ValueError('max_messages must be an integer of at least 2')
        if not isinstance(chunk_chars, int) or isinstance(chunk_chars, bool) or chunk_chars < len(OVERFLOW_NOTICE):
            raise ValueError('chunk_chars must fit the overflow notice')
        self.speech = speech
        self.max_messages = max_messages
        self.chunk_chars = chunk_chars
        self.paused = False
        self.stopped = False
        self.closed = False
        self._queue = deque()
        self._active = None
        self._completion = None
        self._overflow_announced = False

    @property
    def pending_count(self):
        return len(self._queue)

    def speak(self, text, *, priority=False, manual=True):
        """Queue explicit speech; return whether any text was accepted."""
        if self.closed or (not manual and (self.paused or self.stopped)):
            return False
        accepted = self._enqueue(text, automatic=not manual, priority=priority)
        if manual and accepted:
            self.stopped = False
        return accepted

    def enqueue_output(self, text):
        """Queue new visible output, unless automatic reading is disabled."""
        return self.speak(text, manual=False)

    def echo(self, text, interrupt=True):
        """Echo input in order without resuming stopped or paused output."""
        if self.closed:
            return False
        text = normalize_text(text)
        if not text:
            return False
        if interrupt and self._active is not None and not self._active.input_echo:
            self._interrupt()
        queued = list(self._queue)
        index = next((index for index, item in enumerate(queued) if not item.input_echo), len(queued))
        queued.insert(index, _Utterance(text[:self.chunk_chars], False, input_echo=True))
        if len(queued) > self.max_messages and not self._overflow_announced and not any(item.notice for item in queued[:self.max_messages]):
            queued[self.max_messages - 1] = _Utterance(OVERFLOW_NOTICE, False, notice=True)
        self._queue = deque(queued[:self.max_messages])
        return True

    def _enqueue(self, text, *, automatic, priority):
        # Also bound temporary normalization/chunking work for a huge paste/log.
        limit = self.max_messages * self.chunk_chars * 4
        truncated = len(text) > limit
        normalized = normalize_text(text[:limit])
        if not normalized:
            return False
        incoming = []
        for chunk in _chunks(normalized, self.chunk_chars):
            if len(incoming) == self.max_messages:
                truncated = True
                break
            incoming.append(_Utterance(chunk, automatic))
        if priority:
            self._interrupt()
            combined = incoming + list(self._queue)
        else:
            combined = list(self._queue) + incoming
        overflow = truncated or len(combined) > self.max_messages
        self._queue = deque(combined[:self.max_messages])
        if overflow and not self._overflow_announced and not any(item.notice for item in self._queue):
            if len(self._queue) == self.max_messages:
                self._queue.pop()
            self._queue.append(_Utterance(OVERFLOW_NOTICE, automatic, notice=True))
        return True

    def _interrupt(self):
        self.speech.stop()
        self._active = None
        self._completion = None

    def tick(self):
        """Start at most one utterance. Return False only after close()."""
        if self.closed:
            return False
        if self._completion is not None:
            if not self._completion.is_set():
                return True
            self._active = None
            self._completion = None
        if not self._queue:
            self._overflow_announced = False
            return True
        # Respect speech started outside this queue as well.
        if not self.speech.completion.is_set():
            return True
        self._active = self._queue.popleft()
        if self._active.notice:
            self._overflow_announced = True
        self._completion = self.speech.say(self._active.text)
        return True

    def pause(self):
        """Discard automatic backlog and silence only current automatic speech."""
        if self.closed:
            return
        self.paused = True
        self._queue = deque(item for item in self._queue if not item.automatic)
        if (self._active is not None and self._active.automatic
                and self.speech.completion is self._completion):
            self._interrupt()
        self._overflow_announced = False

    def resume(self):
        """Enable future automatic output after pause or stop."""
        if not self.closed:
            self.paused = False
            self.stopped = False

    def stop(self):
        """Stop current speech and discard all queued text until explicit action."""
        if self.closed:
            return
        self._interrupt()
        self._queue.clear()
        self._overflow_announced = False
        self.stopped = True

    def close(self):
        if not self.closed:
            self.stop()
            self.closed = True


class SnapshotDiff:
    """Find new/rewritten lines in bounded visible-screen snapshots.

    Pass a string or an iterable of rendered lines, not the terminal byte stream.
    Initial update reads the snapshot; reset(initial_lines) sets a silent baseline.
    If start_row is supplied, it is the absolute row of the first supplied line:
    identical text in a new row is new output. A row rollback starts a new screen.
    A continued line returns only its appended suffix. Without row coordinates,
    identical screens (including repeated-line scrolling) produce no change.
    """
    def __init__(self, max_lines=500, max_line_chars=4000):
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1
               for value in (max_lines, max_line_chars)):
            raise ValueError('snapshot limits must be positive integers')
        self.max_lines = max_lines
        self.max_line_chars = max_line_chars
        self._lines = ()
        self._rows = {}
        self._start_row = None

    def _snapshot(self, lines):
        if isinstance(lines, str):
            # Slice each retained line before copying; never split a huge screen
            # into an unbounded list or retain a huge individual line.
            text = lines
            def rendered_lines():
                start = 0
                while start < len(text):
                    end = text.find('\n', start)
                    if end < 0:
                        end = len(text)
                    yield text[max(start, end - self.max_line_chars):end]
                    start = end + 1
            lines = rendered_lines()
        bounded = deque(maxlen=self.max_lines)
        skipped = 0
        for line in lines:
            if len(bounded) == self.max_lines:
                skipped += 1
            bounded.append(normalize_text(line[-self.max_line_chars:]))
        while bounded and not bounded[-1]:
            bounded.pop()
        return tuple(bounded), skipped

    @staticmethod
    def _validate_row(start_row):
        if start_row is not None and (not isinstance(start_row, int)
                                     or isinstance(start_row, bool) or start_row < 0):
            raise ValueError('start_row must be a nonnegative integer or None')

    def reset(self, lines=(), *, start_row=None):
        self._validate_row(start_row)
        self._lines, skipped = self._snapshot(lines)
        self._rows = ({start_row + skipped + offset: line for offset, line in enumerate(self._lines)}
                      if start_row is not None else {})
        self._start_row = start_row

    def update(self, lines, *, start_row=None):
        self._validate_row(start_row)
        current, skipped = self._snapshot(lines)
        result = []
        if start_row is not None:
            rows = {start_row + skipped + offset: line for offset, line in enumerate(current)}
            previous = self._rows if self._start_row is not None and start_row >= self._start_row else {}
            for row, line in rows.items():
                old = previous.get(row, '')
                if line.startswith(old):
                    line = line[len(old):].strip()
                if line:
                    result.append(line)
            self._rows = rows
        else:
            matcher = SequenceMatcher(None, self._lines, current, autojunk=False)
            for kind, old_start, old_end, new_start, new_end in matcher.get_opcodes():
                if kind not in ('insert', 'replace'):
                    continue
                previous = self._lines[old_start:old_end]
                for offset, line in enumerate(current[new_start:new_end]):
                    if kind == 'replace' and offset < len(previous) and line.startswith(previous[offset]):
                        line = line[len(previous[offset]):].strip()
                    if line:
                        result.append(line)
            self._rows = {}
        self._lines = current
        self._start_row = start_row
        return result
