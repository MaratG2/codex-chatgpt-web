"""GPT Image through the existing Web agent's Native2 connector; no nested model turn."""
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import re
import time
import uuid
from . import connector, style

ROOT = Path('/home/codexweb/experiment')
STATE = ROOT / 'hermes-home/cache/chatgpt-image'
WORKSPACES = ROOT / 'workspaces'


def _save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.chmod(0o600)
    temporary.replace(path)


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


def _finish_save(state_path, record):
    """Resume a partially committed PNG/provenance pair without regeneration."""
    result = record['result']
    destination = _destination(result['image'])
    png = state_path.with_suffix('.original').read_bytes()
    if sha256(png).hexdigest() != result['sha256']:
        raise ValueError('IMAGE_CACHE_CHANGED: preserve source and inspect')
    current = sha256(destination.read_bytes()).hexdigest() if destination.exists() else None
    if current not in (record['previous_sha256'], result['sha256']):
        raise ValueError('Destination changed while saving; refusing to overwrite newer work')
    if current != result['sha256']:
        temporary = destination.with_name(destination.name + '.' + record['request_id'] + '.tmp')
        temporary.write_bytes(png)
        temporary.replace(destination)
    _save_json(destination.with_suffix('.provenance.json'), record['provenance_data'])
    record['status'] = 'saved'
    _save_json(state_path, record)
    return result


def _reuse_template(directory, session, destination, template):
    """Owner-designated shared slides are exact copies, never model generations."""
    request = uuid.uuid4().hex
    png = Path(template['reuse_source']).read_bytes()
    digest = sha256(png).hexdigest()
    if digest != template['reuse_sha256']:
        raise ValueError('STYLE_REFERENCE_CHANGED: shared slide changed before copy')
    from PIL import Image
    with Image.open(BytesIO(png)) as image:
        image.load()
        width, height = image.size
    destination.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    original = directory / (request + '.original')
    original.write_bytes(png)
    original.chmod(0o600)
    provenance = {'provider': 'owner-approved-template-reuse', 'generated': False,
                  'template_id': template['template_id'], 'template_slot': template['template_slot'],
                  'source_path': str(Path(template['reuse_source']).relative_to(style.REPO)),
                  'sha256': digest, 'original_sha256': digest, 'width': width, 'height': height,
                  'conversion': 'none; exact owner-approved original',
                  'saved_at': datetime.now(timezone.utc).isoformat()}
    result = {'success': True, 'status': 'saved', 'image': str(destination), 'sha256': digest,
              'width': width, 'height': height, 'provider': provenance['provider'], 'generated': False,
              'provenance': str(destination.with_suffix('.provenance.json')),
              'next_action': 'Shared owner-approved slide copied unchanged. Do not invoke generation; include it in the normal commit/push.'}
    record = {'status': 'saving', 'session': session, 'request_id': request, 'result': result,
              'previous_sha256': sha256(destination.read_bytes()).hexdigest() if destination.exists() else None,
              'provenance_data': provenance}
    state_path = directory / (request + '.json')
    _save_json(state_path, record)
    return _finish_save(state_path, record)


def handle(args, **kwargs):
    try:
        return json.dumps(_handle(args), ensure_ascii=False)
    except Exception as error:
        result = {'success': False, 'status': 'blocked', 'error': str(error),
                  'fallback_allowed': False}
        binding_error = re.search(r'\b(?:HERMES_IMAGE_BINDING|STYLE|IMAGE)_[A-Z_]+\b', str(error))
        if binding_error:
            result.update(error_code=binding_error.group(),
                          next_action='Record this transport error and continue other eligible queued work. '
                          'Do not regenerate, open another chat or repeat the same failed call. '
                          'Preserve the original files and record the specific missing asset or binding.')
        return json.dumps(result, ensure_ascii=False)


def _handle(args):
    session = _session()
    directory = STATE / session
    if args.get('action') == 'prepare':
        prompt = str(args.get('prompt', '')).strip()
        destination = _destination(args['output_path'])
        template = style.prepare(destination, args.get('template_slot'))
        if template.get('template_mode') == 'reuse':
            return _reuse_template(directory, session, destination, template)
        if not prompt or len(prompt) > 20000:
            raise ValueError('A specific image prompt of 1–20000 characters is required')
        # Preparing twice must not silently abandon an unfinished native generation.
        for path in directory.glob('*.json'):
            previous = json.loads(path.read_text())
            if previous.get('status') == 'saving':
                return _finish_save(path, previous)
            if previous.get('status') == 'awaiting_native_generation':
                return {'status': 'awaiting_native_generation', 'request_id': path.stem,
                        'output_path': previous['output_path'], 'generated': False,
                        'prompt': previous.get('generation_prompt', previous['prompt']),
                        'generation_route': previous.get('generation_route'),
                        'referenced_image_paths': [x['path'] for x in previous.get('reference_images', [])],
                        'next_action': 'Finish or explicitly cancel this request before preparing another.'}
        request = uuid.uuid4().hex
        generation_prompt = (template.get('style_instruction', '') + '\n\n' + prompt).strip()
        captured = connector.prepare(session, request, generation_prompt)
        old_hash = sha256(destination.read_bytes()).hexdigest() if destination.exists() else None
        _save_json(directory / (request + '.json'), {
            'status': 'awaiting_native_generation', 'session': session, 'request_id': request, 'prompt': prompt,
            'output_path': str(destination), 'previous_sha256': old_hash,
            'created_at': datetime.now(timezone.utc).isoformat(), **template, **captured})
        return {'status': 'awaiting_native_generation', 'request_id': request, 'generated': False,
                'generation_route': captured['generation_route'],
                'generator_tool': 'image_gen__imagegen',
                'referenced_image_paths': [x['path'] for x in template.get('reference_images', [])],
                'template_id': template.get('template_id'), 'template_slot': template.get('template_slot'),
                'next_action': 'In this SAME task conversation, invoke the registered GPT Image tool through '
                'Codex Native2 codex_tool_call using wire_name image_gen__imagegen and its CURRENT turn binding. '
                'Use the returned prompt EXACTLY, including the internal reference, and transparent_background=false. '
                'Inspect the approved reference images first using Hermes vision. Pass the returned '
                'referenced_image_paths unchanged to GPT Image; text-only imitation is not acceptable. '
                'Wait for completion, then call chatgpt_image collect with this request_id. The collector reads the '
                'original PNG on the server: no image needs to appear in the browser DOM. Do not generate again '
                'while collection is pending. Do not use SVG, code drawing, another chat or a separate paid API.',
                'prompt': captured['generation_prompt'], 'output_path': str(destination)}
    request = args.get('request_id', '')
    if len(request) != 32 or any(c not in '0123456789abcdef' for c in request):
        raise ValueError('Invalid request_id')
    state_path = directory / (request + '.json')
    record = json.loads(state_path.read_text())
    if record['session'] != session:
        raise ValueError('Image request belongs to another session')
    if record['status'] == 'saving' and args.get('action') == 'collect':
        return _finish_save(state_path, record)
    if record['status'] == 'saved':
        saved = record['result']
        if sha256(Path(saved['image']).read_bytes()).hexdigest() != saved['sha256']:
            raise ValueError('Previously saved image was modified')
        return saved
    if args.get('action') == 'cancel':
        record.update(status='blocked', reason=str(args.get('reason') or 'Native image generation unavailable'))
        _save_json(state_path, record)
        return {'status': 'blocked', 'success': False, 'error': record['reason'], 'image_saved': False,
                'next_action': 'Preserve any generator output; cancellation does not delete generated files.'}
    if args.get('action') != 'collect' or record['status'] != 'awaiting_native_generation':
        raise ValueError('Request is not awaiting collection')
    if record.get('generation_route') != 'chatgpt-web-via-codex-native2-gpt-image':
        raise ValueError('IMAGE_REQUEST_OLD_ROUTE: preserve old request; prepare a new request with the current route')
    if 'collection_started_at' not in record:
        record['collection_started_at'] = time.time()
        _save_json(state_path, record)
    data = connector.collect(record, session)
    # Codex may notify its continuation slightly before flushing the tool output.
    # Wait locally for that durable output; do not spend another generation call.
    deadline = time.monotonic() + 10
    while data is None and time.monotonic() < deadline:
        time.sleep(0.5)
        data = connector.collect(record, session)
    if data is None:
        if time.time() - record['collection_started_at'] >= 120:
            return {'success': False, 'status': 'blocked', 'error_code': 'IMAGE_OUTPUT_NOT_RECORDED',
                    'error': 'No completed GPT Image output matching this prepared request was recorded.',
                    'fallback_allowed': False, 'generation_confirmed': False}
        return {'status': 'awaiting_tool_output', 'request_id': request, 'generation_confirmed': False,
                'next_action': 'Call collect again for this same request_id after the generator completes. '
                'Do not call generation again or cancel solely because the output log is pending. '
                'The collection window is bounded to 120 seconds.', 'retry_after_seconds': 5}
    raw = data.pop('raw')
    from PIL import Image
    with Image.open(BytesIO(raw)) as image:
        if image.format not in ('PNG', 'JPEG', 'WEBP') or min(image.size) < 512:
            raise ValueError('Downloaded resource is not a complete full-size raster')
        if image.width * image.height > 40_000_000:
            raise ValueError('Image dimensions exceed the export limit')
        image.load()
        if image.format != 'PNG':
            raise ValueError('GPT Image output is not PNG; preserve original and inspect')
        width, height = image.size
    png = raw  # Copy exactly; do not re-encode, draw, crop, resize or screenshot.
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
        **data, 'request_id': request, 'prompt': record['prompt'], 'saved_at': datetime.now(timezone.utc).isoformat(),
        'template_id': record.get('template_id'), 'template_slot': record.get('template_slot'),
        'reference_images': [{**x, 'path': str(Path(x['path']).relative_to(style.REPO))}
                             for x in record.get('reference_images', [])],
        'sha256': digest, 'width': width, 'height': height,
        'conversion': 'none; original PNG bytes preserved',
    }
    result = {'success': True, 'status': 'saved', 'image': str(destination), 'sha256': digest,
              'width': width, 'height': height, 'provider': provenance['provider'],
              'provenance': str(destination.with_suffix('.provenance.json')),
              'next_action': 'Inspect this file with vision_analyze. Review actual text and composition before accepting.'}
    record.update(status='saving', result=result, provenance_data=provenance)
    _save_json(state_path, record)
    return _finish_save(state_path, record)


def register(ctx):
    ctx.register_tool(
        name='chatgpt_image', toolset='chatgpt_image', handler=handle, emoji='🎨',
        description='Prepare and collect genuine GPT Image artwork through the current Web agent and Native2.',
        schema={'name': 'chatgpt_image', 'description':
            'Two-phase GPT Image tool. prepare records a prompt/output PNG and the exact current task thread; '
            'then YOU must call the registered Native2 image_gen__imagegen tool with the returned exact prompt; '
            'collect verifies its completed tool output and saves the original server PNG plus provenance. '
            'Browser image rendering is not required. prepare is not generation or success. '
            'AI Education ad galleries require template_slot 01–06 and the owner-approved reference files. '
            'Pass referenced_image_paths returned by prepare to GPT Image. Missing references stop preparation. '
            'For a shared reuse slot, prepare directly returns saved after an exact copy: do NOT invoke generation. '
            'Never draw SVG/HTML/Canvas as a substitute. cancel records a real unavailability/refusal. '
            'No API keys, paid API requests, new tabs or new model chats.',
            'parameters': {'type': 'object', 'properties': {
                'action': {'type': 'string', 'enum': ['prepare', 'collect', 'cancel']},
                'prompt': {'type': 'string'}, 'output_path': {'type': 'string'},
                'template_slot': {'type': 'string', 'enum': ['01', '02', '03', '04', '05', '06']},
                'request_id': {'type': 'string'}, 'index': {'type': 'integer', 'minimum': 0},
                'reason': {'type': 'string'}}, 'required': ['action']}},
        check_fn=lambda: connector.PROVIDER_STATE.is_dir(), requires_env=[], is_async=False)
