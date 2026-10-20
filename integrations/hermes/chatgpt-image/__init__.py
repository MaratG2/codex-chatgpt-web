"""Two-phase native Web generation. Never starts a second LLM request or a new chat."""
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import base64
import json
import os
import re
import subprocess
import time
import uuid

ROOT = Path('/home/codexweb/experiment')
BUN = Path('/home/codexweb/.codex-chatgpt-web/versions/6.1.11-marat.1-linux-x64/runtime/bun')
BRIDGE = ROOT / 'launcher-fork/scripts/hermes-image-bridge.ts'
STATE = ROOT / 'hermes-home/cache/chatgpt-image'
WORKSPACES = ROOT / 'workspaces'


def _save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.chmod(0o600)
    temporary.replace(path)


def _bridge(value):
    result = subprocess.run([str(BUN), str(BRIDGE)], input=json.dumps(value),
                            text=True, capture_output=True, timeout=70, cwd=BRIDGE.parent.parent)
    if result.returncode:
        # Do not return arbitrary stderr/stack traces with potentially signed resource URLs.
        raise RuntimeError('ChatGPT image export failed: ' + result.stderr.strip().split('\n')[0][:300])
    return json.loads(result.stdout)


def _destination(raw):
    destination = Path(raw).expanduser()
    if not destination.is_absolute():
        raise ValueError('output_path must be an absolute workspace PNG path')
    destination = destination.resolve()
    if not destination.is_relative_to(WORKSPACES.resolve()) or destination.suffix.lower() != '.png':
        raise ValueError('output_path must be a PNG inside experiment/workspaces')
    return destination


def _session():
    from gateway.session_context import get_session_env
    session = get_session_env('HERMES_SESSION_ID', '')
    if not session:
        raise ValueError('No bound Hermes session; refusing auxiliary conversation')
    return sha256(session.encode()).hexdigest()


def handle(args, **kwargs):
    try:
        return json.dumps(_handle(args), ensure_ascii=False)
    except Exception as error:
        result = {'success': False, 'status': 'blocked', 'error': str(error),
                  'fallback_allowed': False}
        binding_error = re.search(r'\bHERMES_IMAGE_BINDING_[A-Z_]+\b', str(error))
        if binding_error:
            result.update(error_code=binding_error.group(), generated=False,
                          next_action='Record this transport error and continue other eligible queued work. '
                          'Do not regenerate, open another chat or repeat the same failed call. '
                          'The exporter already attempted exact-session metadata recovery.')
        return json.dumps(result, ensure_ascii=False)


def _handle(args):
    session = _session()
    directory = STATE / session
    if args.get('action') == 'prepare':
        prompt = str(args.get('prompt', '')).strip()
        if not prompt or len(prompt) > 20000:
            raise ValueError('A specific image prompt of 1–20000 characters is required')
        destination = _destination(args['output_path'])
        # Preparing twice must not silently abandon an unfinished native generation.
        for path in directory.glob('*.json'):
            previous = json.loads(path.read_text())
            if previous.get('status') == 'awaiting_native_generation':
                return {'status': 'awaiting_native_generation', 'request_id': path.stem,
                        'output_path': previous['output_path'], 'generated': False,
                        'next_action': 'Finish or explicitly cancel this request before preparing another.'}
        captured = _bridge({'action': 'prepare', 'session': session})
        request = uuid.uuid4().hex
        old_hash = sha256(destination.read_bytes()).hexdigest() if destination.exists() else None
        _save_json(directory / (request + '.json'), {
            'status': 'awaiting_native_generation', 'session': session, 'prompt': prompt,
            'output_path': str(destination), 'previous_sha256': old_hash,
            'created_at': datetime.now(timezone.utc).isoformat(), **captured})
        return {'status': 'awaiting_native_generation', 'request_id': request, 'generated': False,
                'generation_route': 'chatgpt_native_only',
                'next_action': 'Now call the first-party image generation tool declared by ChatGPT in THIS chat. '
                'Do NOT invoke Codex Native/Native2 image_gen__imagegen or any guessed connector wire name: '
                'that is a separate generator whose server PNG is not a Web-chat image. '
                'Await and display the actual native tool result using its declared image-output helper. '
                'Then call chatgpt_image action collect with this request_id. Do not use code drawing, screenshots, '
                'another chat or an API. If the first-party native tool is absent/refused, cancel with the actual blocker.',
                'prompt': prompt, 'output_path': str(destination)}
    request = args.get('request_id', '')
    if len(request) != 32 or any(c not in '0123456789abcdef' for c in request):
        raise ValueError('Invalid request_id')
    state_path = directory / (request + '.json')
    record = json.loads(state_path.read_text())
    if record['session'] != session:
        raise ValueError('Image request belongs to another session')
    if record['status'] == 'saved':
        saved = record['result']
        if sha256(Path(saved['image']).read_bytes()).hexdigest() != saved['sha256']:
            raise ValueError('Previously saved image was modified')
        return saved
    if args.get('action') == 'cancel':
        record.update(status='blocked', reason=str(args.get('reason') or 'Native image generation unavailable'))
        _save_json(state_path, record)
        return {'status': 'blocked', 'success': False, 'error': record['reason'], 'generated': False}
    if args.get('action') != 'collect' or record['status'] != 'awaiting_native_generation':
        raise ValueError('Request is not awaiting collection')
    # Diagnose a connector-generated PNG instead of mislabelling it a DOM failure.
    provider_record = ROOT / 'hermes-home/plugins/model-providers/chatgpt-web-local/conversation-state' / (session + '.json')
    if provider_record.exists():
        thread = json.loads(provider_record.read_text()).get('thread_id', '')
        if re.fullmatch(r'[a-f0-9-]{36}', thread):
            native_dir = Path.home() / '.codex/generated_images' / thread
            prepared_at = datetime.fromisoformat(record['created_at']).timestamp()
            if any(p.stat().st_mtime >= prepared_at for p in native_dir.glob('*.png')):
                return {'success': False, 'status': 'blocked', 'error_code': 'WRONG_IMAGE_GENERATION_ROUTE',
                        'error': 'A Codex connector image was created for this thread. It is not native Web generation.',
                        'fallback_allowed': False, 'generated_via_web': False,
                        'next_action': 'Preserve that original file; do not label or import it as ChatGPT Web output. '
                        'Use only the first-party image tool declared directly by ChatGPT.'}
    if 'collection_started_at' not in record:
        record['collection_started_at'] = time.time()
        _save_json(state_path, record)
    data = _bridge({'action': 'collect', 'session': session, 'conversation': record['conversation'],
                    'baseline': record['baseline'], 'index': args.get('index')})
    if data.get('status') == 'awaiting_render':
        if time.time() - record['collection_started_at'] >= 120:
            return {'success': False, 'status': 'blocked', 'error_code': 'WEB_IMAGE_RENDER_TIMEOUT',
                    'error': 'No new native Web image became visible within the collection window.',
                    'fallback_allowed': False, 'generated_via_web': False}
        return {'status': 'awaiting_render', 'request_id': request, 'generated': False,
                'next_action': 'Call collect again for this same request_id after the native result renders. '
                'Do not call generation again or cancel solely because rendering is pending. '
                'The collection window is bounded to 120 seconds.', 'retry_after_seconds': 5}
    if data.get('status') == 'choose_image':
        return data
    raw = base64.b64decode(data['base64'], validate=True)
    from PIL import Image
    with Image.open(BytesIO(raw)) as image:
        if image.format not in ('PNG', 'JPEG', 'WEBP') or min(image.size) < 512:
            raise ValueError('Downloaded resource is not a complete full-size raster')
        if image.width * image.height > 40_000_000:
            raise ValueError('Image dimensions exceed the export limit')
        image.load()
        output = BytesIO()
        # Format conversion only: no drawing, resizing, cropping or screenshotting.
        image.save(output, format='PNG')
        width, height = image.size
    png = output.getvalue()
    digest = sha256(png).hexdigest()
    destination = _destination(record['output_path'])
    current_hash = sha256(destination.read_bytes()).hexdigest() if destination.exists() else None
    if current_hash != record['previous_sha256']:
        raise ValueError('Destination changed after prepare; refusing to overwrite newer work')
    if digest == current_hash:
        raise ValueError('Export equals the existing image; it is not a replacement')
    destination.parent.mkdir(parents=True, exist_ok=True)
    original = directory / (request + '.original')
    original.write_bytes(raw)
    original.chmod(0o600)
    provenance = {
        'provider': 'chatgpt-web-native-image', 'model': 'ChatGPT Image (UI-selected; exact version not exposed)',
        'request_id': request, 'prompt': record['prompt'], 'generated_at': datetime.now(timezone.utc).isoformat(),
        'source_key_sha256': data['source_key_sha256'], 'original_sha256': sha256(raw).hexdigest(),
        'generated_image_label': data['generated_image_label'],
        'sha256': digest, 'width': width, 'height': height,
        'method': 'native ChatGPT generation; original displayed raster downloaded in its browser session',
        'conversion': 'PNG encoding only; no programmatic drawing',
    }
    temporary = destination.with_name(destination.name + '.' + request + '.tmp')
    temporary.write_bytes(png)
    temporary.replace(destination)
    _save_json(destination.with_suffix('.provenance.json'), provenance)
    result = {'success': True, 'status': 'saved', 'image': str(destination), 'sha256': digest,
              'width': width, 'height': height, 'provider': provenance['provider'],
              'provenance': str(destination.with_suffix('.provenance.json')),
              'next_action': 'Inspect this file with vision_analyze. Review actual text and composition before accepting.'}
    record.update(status='saved', result=result)
    _save_json(state_path, record)
    return result


def register(ctx):
    ctx.register_tool(
        name='chatgpt_image', toolset='chatgpt_image', handler=handle, emoji='🎨',
        description='Prepare and collect genuine GPT Image artwork in the current ChatGPT Web chat.',
        schema={'name': 'chatgpt_image', 'description':
            'Two-phase ChatGPT Web image tool. prepare records the prompt/output PNG and current images; '
            'then YOU must call ChatGPT native image generation in this same conversation; '
            'collect saves only a new assistant raster and provenance. prepare is not generation or success. '
            'Never draw SVG/HTML/Canvas as a substitute. cancel records a real unavailability/refusal. '
            'No API keys, paid API requests, new tabs or new model chats.',
            'parameters': {'type': 'object', 'properties': {
                'action': {'type': 'string', 'enum': ['prepare', 'collect', 'cancel']},
                'prompt': {'type': 'string'}, 'output_path': {'type': 'string'},
                'request_id': {'type': 'string'}, 'index': {'type': 'integer', 'minimum': 0},
                'reason': {'type': 'string'}}, 'required': ['action']}},
        check_fn=lambda: BUN.is_file() and BRIDGE.is_file(), requires_env=[], is_async=False)
