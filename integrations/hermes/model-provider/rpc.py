"""Bounded stdio JSON-RPC transport; no network listener or credential handling."""
from collections import deque
from pathlib import Path
import json
import os
import queue
import signal
import subprocess
import threading
import time
import tomllib


class AppServer:
    def __init__(self, command):
        config = tomllib.loads((Path.home()/'.codex/config.toml').read_text())
        if config.get('openai_base_url', '').rstrip('/') != 'http://127.0.0.1:17841/v1':
            raise RuntimeError('ChatGPT Web local route is not configured; refusing native fallback.')
        self.events = queue.Queue()
        self.deferred = deque()
        self.serial = 0
        self.write_lock = threading.Lock()
        self.proc = subprocess.Popen([command, 'app-server'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
            bufsize=1, start_new_session=True)
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.request('initialize', {'clientInfo': {'name':'hermes-web-local','version':'0.1.1'},
                'capabilities': {'experimentalApi':True}})
            self.send({'method':'initialized'})
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.proc.stdout:
                self.events.put(json.loads(line))
        except (ValueError, OSError):
            pass
        finally:
            self.events.put(None)

    def send(self, value):
        with self.write_lock:
            self.proc.stdin.write(json.dumps(value, ensure_ascii=False)+'\n')
            self.proc.stdin.flush()

    def request(self, method, params, timeout=30):
        self.serial += 1
        request_id = self.serial
        self.send({'id':request_id, 'method':method, 'params':params})
        deadline = time.monotonic()+timeout
        while True:
            message = self.events.get(timeout=max(.01, deadline-time.monotonic()))
            if message is None:
                raise RuntimeError('Codex App Server exited before its reply.')
            if message.get('id') == request_id and 'method' not in message:
                if 'error' in message:
                    raise RuntimeError(str(message['error']))
                return message.get('result', {})
            self.deferred.append(message)

    def next(self, timeout):
        message = self.deferred.popleft() if self.deferred else self.events.get(timeout=timeout)
        if message is None:
            raise RuntimeError('Codex App Server disconnected; the turn was not replayed.')
        return message

    def close(self):
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=5)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream:
                stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
