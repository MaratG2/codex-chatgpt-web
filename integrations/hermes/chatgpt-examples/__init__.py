"""Two-phase, same-model text examples. Never launches a model or a browser.

The answer is the current ChatGPT Web model's structured tool-call output.
This is not an isolated learner account, UI test, or independent QA.
"""
from pathlib import Path
from hashlib import sha256
from datetime import datetime, timezone
import json
import re
import uuid

ROOT = Path('/home/codexweb/experiment')
REPO = ROOT / 'workspaces/ai-education'
STATE = ROOT / 'hermes-home/cache/chatgpt-examples'
PROVIDER = ROOT / 'hermes-home/plugins/model-providers/chatgpt-web-local/conversation-state'


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(text):
    return sha256(text.encode()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.chmod(0o600)
    temp.replace(path)


def binding():
    from gateway.session_context import get_session_env
    sid = get_session_env('HERMES_SESSION_ID', '')
    if not sid:
        raise ValueError('EXAMPLE_NO_SESSION: no bound Hermes task')
    key = digest(sid)
    state = json.loads((PROVIDER / (key + '.json')).read_text())
    thread = state.get('thread_id', '')
    if not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', thread):
        raise ValueError('EXAMPLE_NO_WEB_THREAD: no bound ChatGPT Web conversation')
    if not str(state.get('model', '')).startswith('chatgpt-web/'):
        raise ValueError('EXAMPLE_WRONG_PROVIDER: expected ChatGPT Web model')
    return key, thread, state['model']


def product_path(product):
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', product):
        raise ValueError('Invalid product_id')
    p = REPO / 'products' / product
    if p.is_symlink() or p.resolve().parent != (REPO / 'products').resolve() or not (p / 'brief.md').is_file():
        raise ValueError('Unknown or redirected product')
    return p.resolve()


def inside(raw, product):
    p = Path(raw)
    if not p.is_absolute():
        p = REPO / p
    p = p.resolve()
    if not p.is_relative_to(product):
        raise ValueError('Source must belong to this product')
    return p


def handle(args, **kwargs):
    try:
        return json.dumps(run(args), ensure_ascii=False)
    except Exception as error:
        return json.dumps({'success': False, 'error': str(error),
                           'next_action': 'Preserve existing evidence and report this exact error. Do not open a separate browser, new chat or paid API.'}, ensure_ascii=False)


def prepared_result(record):
    return {'success': True, 'status': 'awaiting_current_model_answer',
            'request_id': record['request_id'], 'prompt': record['prompt'],
            'sources': record['sources'], 'previous': record.get('previous'),
            'next_action': 'YOU are the live ChatGPT Web model. On this continuation, answer the prepared learner prompt using the supplied sources and, if present, the actual previous answer. Do not consult author answer keys or invent a previous conversation. Submit your actual answer as answer_text to chatgpt_example action record with this request_id. Do not call a browser, another model, shell/API or start a new chat. This is a structured model output in the current agent context, not an isolated UI/account test.'}


def run(args):
    session, thread, model = binding()
    directory = STATE / session
    action = args.get('action')
    if action == 'prepare':
        product = product_path(str(args.get('product_id', '')))
        case = str(args.get('case_id', ''))
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', case):
            raise ValueError('Invalid case_id')
        prompt = str(args.get('prompt', '')).strip()
        if not 1 <= len(prompt) <= 12000:
            raise ValueError('Prompt must contain 1–12000 characters')
        paths = args.get('source_paths', [])
        if not isinstance(paths, list) or not 1 <= len(paths) <= 5:
            raise ValueError('Provide 1–5 existing text source paths')
        sources = []
        for raw in paths:
            p = inside(raw, product)
            if p.suffix.lower() not in ('.txt', '.md') or p.stat().st_size > 160000:
                raise ValueError('Use bounded TXT/Markdown learner sources, not answer keys')
            text = p.read_text()
            sources.append({'path': str(p.relative_to(REPO)), 'sha256': digest(text), 'text': text})
        if sum(len(s['text']) for s in sources) > 80000:
            raise ValueError('Source text exceeds 80000 characters')
        previous = None
        if args.get('previous_record'):
            prev = inside(args['previous_record'], product)
            if not prev.is_relative_to(product / 'examples/runtime'):
                raise ValueError('Previous record must be runtime evidence from this product')
            old = json.loads(prev.read_text())
            if old.get('provider') != 'chatgpt-web-local' or old.get('answer_sha256') != digest(old['answer_text']):
                raise ValueError('Previous model evidence is invalid')
            if old.get('case_id') != case:
                raise ValueError('Previous answer belongs to a different case')
            previous = {'path': str(prev.relative_to(REPO)), 'file_sha256': sha256(prev.read_bytes()).hexdigest(),
                        'prompt': old['prompt'], 'answer_text': old['answer_text'], 'request_id': old['request_id']}
        signature = digest(json.dumps([case, prompt, sources, previous], sort_keys=True, ensure_ascii=False))
        for p in directory.glob('*.json'):
            old = json.loads(p.read_text())
            if old.get('signature') == signature and old.get('product_id') == args['product_id']:
                if old.get('status') == 'saved':
                    saved = inside(old['record'], product)
                    evidence = json.loads(saved.read_text())
                    if evidence.get('answer_sha256') != digest(evidence['answer_text']):
                        raise ValueError('Saved response changed; preserve and inspect')
                    return {'success': True, 'status': 'saved', 'record': old['record'], 'next_action': 'Use the already saved response; do not regenerate.'}
                return prepared_result(old)
        request = uuid.uuid4().hex
        record = {'request_id': request, 'session': session, 'thread_id': thread, 'model': model,
                  'product_id': args['product_id'], 'case_id': case, 'prompt': prompt,
                  'sources': sources, 'previous': previous, 'signature': signature,
                  'created_at': now(), 'status': 'prepared'}
        save(directory / (request + '.json'), record)
        return prepared_result(record)
    if action != 'record':
        raise ValueError('Use prepare or record')
    request = str(args.get('request_id', ''))
    if not re.fullmatch(r'[a-f0-9]{32}', request):
        raise ValueError('Invalid request_id')
    state_path = directory / (request + '.json')
    record = json.loads(state_path.read_text())
    if record['session'] != session or record['thread_id'] != thread or record['model'] != model:
        raise ValueError('EXAMPLE_BINDING_CHANGED: preserve the prepared request')
    product = product_path(record['product_id'])
    dest = product / 'examples/runtime' / record['case_id'] / (request + '.json')
    if not dest.resolve().is_relative_to(product):
        raise ValueError('Evidence destination redirected')
    answer = str(args.get('answer_text', '')).strip()
    if not 1 <= len(answer) <= 60000:
        raise ValueError('Provide the actual current model answer, 1–60000 characters')
    for source in record['sources']:
        if digest(inside(source['path'], product).read_text()) != source['sha256']:
            raise ValueError('EXAMPLE_SOURCE_CHANGED: prepare again against current text')
    previous = record.get('previous')
    if previous and sha256(inside(previous['path'], product).read_bytes()).hexdigest() != previous['file_sha256']:
        raise ValueError('EXAMPLE_PREVIOUS_CHANGED: preserve the original response')
    evidence = {k:v for k,v in record.items() if k not in ('session', 'signature', 'status')}
    evidence.update(provider='chatgpt-web-local', transport='existing Hermes ChatGPT Web task',
                    response_capture='structured answer_text from the live model tool call after prepare',
                    context_isolated=False, ui_verified=False, independent_qa=False,
                    answer_text=answer, answer_sha256=digest(answer), recorded_at=now())
    if dest.exists():
        old = json.loads(dest.read_text())
        if old.get('request_id') != request or old.get('answer_sha256') != digest(answer):
            raise ValueError('Evidence already exists with a different answer; refusing overwrite')
    else:
        save(dest, evidence)
    relative = str(dest.relative_to(REPO))
    record.update(status='saved', record=relative)
    save(state_path, record)
    return {'success': True, 'status': 'saved', 'record': relative, 'answer_sha256': digest(answer),
            'ui_verified': False, 'independent_qa': False,
            'next_action': 'Check the saved answer against source passages. For an actual correction, prepare another prompt with previous_record set to this record, then record its new model answer. Preserve first answers verbatim; do not invent errors. Commit evidence with the course changes.'}


def register(ctx):
    ctx.register_tool(name='chatgpt_example', toolset='chatgpt_example', handler=handle, emoji='📝',
        description='Prepare and record a text learning example from the current ChatGPT Web model, in the same task chat.',
        schema={'name':'chatgpt_example', 'description':
            'For actual text course examples: prepare returns source snapshots and learner prompt; YOU then generate the answer in the existing ChatGPT Web context and record it as structured answer_text. Does not invoke another model/browser. Follow-up prepare uses previous_record. Saves immutable model evidence; never proves UI access or independent QA.',
            'parameters':{'type':'object','properties':{
                'action':{'type':'string','enum':['prepare','record']},
                'product_id':{'type':'string'}, 'case_id':{'type':'string'},
                'prompt':{'type':'string'}, 'source_paths':{'type':'array','items':{'type':'string'}},
                'previous_record':{'type':'string'}, 'request_id':{'type':'string'},
                'answer_text':{'type':'string'}}, 'required':['action']}},
        check_fn=lambda: PROVIDER.is_dir(), requires_env=[], is_async=False)
