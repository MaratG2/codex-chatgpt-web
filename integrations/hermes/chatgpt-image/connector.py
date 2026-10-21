"""Collect original GPT Image files proven by the bound Codex thread's tool log.

No generation, network calls, model calls or arbitrary model-supplied source paths.
"""
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import json
import re
from . import style

ROOT = Path('/home/codexweb/experiment')
CODEX = Path.home() / '.codex'
PROVIDER_STATE = ROOT / 'hermes-home/plugins/model-providers/chatgpt-web-local/conversation-state'
MAX_LINE = 40 * 1024 * 1024


def _thread(session):
    state = json.loads((PROVIDER_STATE / (session + '.json')).read_text())
    thread = state.get('thread_id', '')
    if not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', thread):
        raise ValueError('IMAGE_THREAD_BINDING_INVALID: no exact provider thread')
    return thread


def prepare(session, request, prompt):
    thread = _thread(session)
    logs = list((CODEX / 'sessions').glob('*/*/*/rollout-*-' + thread + '.jsonl'))
    if len(logs) != 1 or logs[0].is_symlink():
        raise ValueError('IMAGE_THREAD_LOG_UNAVAILABLE: expected one bound rollout')
    log = logs[0]
    # The pending Hermes prepare call has already been written by Codex. New image
    # calls will be appended only after this handler returns to the same Web turn.
    with log.open('rb') as handle:
        handle.seek(0, 2)
        offset = handle.tell()
        if offset:
            handle.seek(-1, 2)
            if handle.read(1) != b'\n':
                raise ValueError('IMAGE_THREAD_LOG_PENDING: retry prepare after log flush')
    marker = 'HERMES_IMAGE_REQUEST=' + request
    return {'generation_route': 'chatgpt-web-via-codex-native2-gpt-image',
            'thread_id': thread, 'rollout': str(log), 'rollout_offset': offset,
            'rollout_inode': log.stat().st_ino,
            'generation_prompt': prompt + '\n\nInternal tracking reference; do not draw or print this reference in the image: ' + marker}


def collect(record, session):
    if _thread(session) != record['thread_id']:
        raise ValueError('IMAGE_THREAD_CHANGED: refusing a different conversation')
    log = Path(record['rollout'])
    stat = log.stat()
    if log.is_symlink() or stat.st_ino != record['rollout_inode'] or stat.st_size < record['rollout_offset']:
        raise ValueError('IMAGE_THREAD_LOG_CHANGED: refusing replaced or truncated evidence')
    marker = 'HERMES_IMAGE_REQUEST=' + record['request_id']
    calls, outputs = {}, {}
    with log.open('rb') as handle:
        handle.seek(record['rollout_offset'])
        while True:
            line = handle.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE:
                raise ValueError('IMAGE_THREAD_RECORD_TOO_LARGE')
            if not line.endswith(b'\n'):
                break  # A concurrent append is not yet a complete record.
            event = json.loads(line)
            if event.get('type') != 'response_item':
                continue
            payload = event.get('payload', {})
            if payload.get('type') == 'function_call' and payload.get('name') == 'imagegen' and payload.get('namespace') == 'image_gen':
                args = json.loads(payload.get('arguments') or '{}')
                if marker in args.get('prompt', ''):
                    if args['prompt'].strip() != record['generation_prompt'].strip():
                        raise ValueError('IMAGE_PROMPT_MISMATCH: use the exact prompt returned by prepare')
                    style.validate_call(record, args)
                    calls[payload['call_id']] = event.get('timestamp')
            elif payload.get('type') == 'function_call_output':
                call = payload.get('call_id')
                if call in calls:
                    outputs[call] = (payload.get('output'), event.get('timestamp'))
    if len(calls) > 1:
        raise ValueError('IMAGE_DUPLICATE_GENERATION: multiple calls for one request; preserve originals and inspect')
    if not outputs:
        return None
    call, (output, completed) = next(iter(outputs.items()))
    # Use only textual paths returned by this exact generator, never assistant text,
    # previews, signed URLs or base64 carried by the model.
    if isinstance(output, list):
        texts = [part.get('text', '') for part in output if part.get('type') in ('input_text', 'output_text', 'text')]
    else:
        texts = [output] if isinstance(output, str) else []
    directory = CODEX / 'generated_images' / record['thread_id']
    pattern = re.escape(str(directory)) + r'/[A-Za-z0-9_.-]+\.png\b'
    paths = sorted({p for text in texts for p in re.findall(pattern, text)})
    if len(paths) != 1:
        raise ValueError('IMAGE_GENERATOR_NO_SINGLE_PNG: tool completed without one original PNG; do not regenerate blindly')
    path = Path(paths[0])
    if directory.is_symlink() or path.is_symlink() or path.resolve().parent != directory.resolve():
        raise ValueError('IMAGE_SOURCE_PATH_INVALID')
    stat = path.stat()
    if not path.is_file() or stat.st_size > 25 * 1024 * 1024 or stat.st_mtime < datetime.fromisoformat(record['created_at']).timestamp():
        raise ValueError('IMAGE_SOURCE_INVALID: stale, oversized or non-file source')
    raw = path.read_bytes()
    return {'raw': raw, 'provider': 'gpt-image-via-codex-native2',
            'model': 'GPT Image (exact version not exposed by tool)',
            'generator_tool': 'image_gen.imagegen', 'generator_call_id': call,
            'generator_completed_at': completed, 'source_filename': path.name,
            'source_key_sha256': sha256((record['thread_id'] + '/' + call).encode()).hexdigest(),
            'original_sha256': sha256(raw).hexdigest(),
            'orchestrator': 'ChatGPT Web in the existing Hermes task conversation',
            'method': 'Original PNG returned by the registered Native2 GPT Image tool; matched request prompt and bound thread rollout'}
