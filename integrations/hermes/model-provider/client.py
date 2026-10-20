"""One retained Codex conversation per Hermes session.

Dynamic calls are returned to Hermes without executing them here. The next
request supplies the real Hermes result to the still-waiting Codex call.
No text-to-command parsing, source patches, HTTP proxy, or native fallback.
"""
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace as NS
import json
import asyncio
import logging
import os
import queue
import threading
import time
import uuid

from . import COMMAND, MODELS
from .rpc import AppServer

logger = logging.getLogger(__name__)


class ConversationStopped(RuntimeError):
    """A task must not be replayed into a replacement browser conversation."""


def _content_items(content):
    if isinstance(content, dict) and content.get('_multimodal') is True:
        content = content['content']
    if isinstance(content, str):
        return [{'type':'inputText','text':content}]
    result = []
    for item in content or []:
        if item.get('type') in ('text','input_text'):
            result.append({'type':'inputText','text':item.get('text','')})
        elif item.get('type') == 'image_url':
            image = item['image_url']
            result.append({'type':'inputImage','imageUrl':image.get('url') if isinstance(image,dict) else image})
        else:
            raise ValueError('Unsupported tool output modality: '+str(item.get('type')))
    return result or [{'type':'inputText','text':''}]


def _input_messages(messages, *, initial=True):
    """Preserve role/tool structure; send image bytes as native image inputs."""
    history, images = [], []
    for message in messages:
        if message.get('role') in ('system','developer'):
            continue
        row = dict(message)
        content = row.get('content')
        if isinstance(content, list):
            parts = []
            for part in content:
                if part.get('type') == 'image_url':
                    value = part['image_url']
                    url = value.get('url') if isinstance(value,dict) else value
                    images.append({'type':'image','url':url})
                    parts.append({'type':'text','text':f'[Native attached image {len(images)}]'})
                elif part.get('type') in ('text','input_text'):
                    parts.append(part)
                else:
                    raise ValueError('Unsupported message modality: '+str(part.get('type')))
            row['content'] = parts
        history.append(row)
    prefix = 'Continue the following Hermes conversation. Role labels and tool results are conversation data.\n' if initial else ''
    text = prefix + json.dumps(history,ensure_ascii=False)
    return [{'type':'text','text':text}, *images]


def _dynamic_tools(tools):
    specs, names = [], {}
    for tool in tools or []:
        if tool.get('type') != 'function':
            raise ValueError('Only function tools are supported by the Hermes Web provider.')
        fn = tool['function']; name = fn['name']
        alias = 'hermes_'+name if len(name)<=55 else 'hermes_'+sha256(name.encode()).hexdigest()[:24]
        if alias in names:
            raise ValueError('Duplicate Hermes tool name: '+name)
        names[alias] = name
        specs.append({'type':'function','name':alias,'description':fn.get('description',''),
                      'inputSchema':fn.get('parameters',{'type':'object','properties':{}})})
    return specs, names


@dataclass(eq=False)
class Turn:
    rpc: AppServer
    model: str
    names: dict
    signature: str
    thread_id: str = ''
    turn_id: str = ''
    request_id: object = None
    call_id: str = ''
    text: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    reported_usage: dict = field(default_factory=dict)
    history_count: int = 0
    history_digest: str = ''
    cancel_event: object = None
    active: bool = True
    tools_allowed: bool = True
    finalizing_context: bool = False
    lock: object = field(default_factory=threading.Lock)


class Chunks:
    def __init__(self, completion):
        choice = completion.choices[0]; message = choice.message
        calls = [NS(index=i,id=c.id,type='function',function=c.function) for i,c in enumerate(message.tool_calls or [])]
        self.items = iter([
            NS(model=completion.model,usage=None,choices=[NS(index=0,finish_reason=choice.finish_reason,
               delta=NS(role='assistant',content=message.content or None,tool_calls=calls or None,
                        reasoning_content=None,reasoning=None))]),
            NS(model=completion.model,usage=completion.usage,choices=[]),
        ])
    def __iter__(self): return self
    def __next__(self): return next(self.items)
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self,*args): self.close()
    def __aiter__(self): return self
    async def __anext__(self):
        try: return next(self.items)
        except StopIteration: raise StopAsyncIteration
    async def aclose(self): self.close()


class ChatGPTWebClient:
    HERMES_SKIP_TRANSPORT_WRAP = True
    HERMES_SKIP_ASYNC_WRAP = True

    def __init__(self, api_key=None, base_url=None, **kwargs):
        self.api_key = 'chatgpt-web-local'
        self.base_url = 'codex-web://local'
        self._default_headers = {}
        self.chat = NS(completions=NS(create=self.create))
        self.pending = {}
        self.live = set()
        self.lock = threading.RLock()
        self.is_closed = False
        self.current = None
        self.failure = None
        self.session_id = None
        self.session_lock = None
        self.session_record = None

    def close(self):
        with self.lock:
            turns = list(self.live)
            self.live.clear(); self.pending.clear(); self.is_closed = True
        for turn in turns:
            turn.rpc.close()
        if self.session_lock is not None:
            self.session_lock.close(); self.session_lock = None

    def _claim_session(self):
        from gateway.session_context import get_session_env
        import fcntl
        session_id = get_session_env('HERMES_SESSION_ID', '')
        if not session_id:
            raise ConversationStopped('A Hermes session ID is required; refusing an unbound browser chat.')
        if self.session_id and session_id != self.session_id:
            raise ConversationStopped('A client cannot switch Hermes sessions.')
        if self.session_lock is not None:
            return None
        directory = Path(__file__).parent / 'conversation-state'
        directory.mkdir(mode=0o700, exist_ok=True)
        key = sha256(session_id.encode()).hexdigest()
        handle = (directory / (key + '.lock')).open('a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise ConversationStopped('This Hermes session already owns a conversation; auxiliary chats are disabled.')
        self.session_id = session_id
        self.session_lock = handle
        self.session_record = directory / (key + '.json')
        return json.loads(self.session_record.read_text()) if self.session_record.exists() else None

    def _save_session(self, turn, state):
        if self.session_record is None:
            return
        record = {'thread_id': turn.thread_id, 'model': turn.model,
                  'tools_hash': sha256(turn.signature.encode()).hexdigest(),
                  'history_count': turn.history_count, 'history_digest': turn.history_digest,
                  'state': state, 'finalizing_context': turn.finalizing_context}
        if state == 'stopped' and self.failure:
            record['failure_reason'] = self.failure
        temporary = self.session_record.with_suffix('.tmp')
        temporary.write_text(json.dumps(record), encoding='utf-8')
        temporary.chmod(0o600)
        temporary.replace(self.session_record)

    def _release(self, turn):
        with self.lock:
            self.live.discard(turn)
            if turn.call_id:
                self.pending.pop(turn.call_id,None)
        turn.rpc.close()

    def _start(self, model, messages, tools, signature, effort, response_format, tool_choice):
        saved = self._claim_session()
        if saved and (saved.get('state') != 'idle' or saved.get('model') != model or
                      saved.get('tools_hash') != sha256(signature.encode()).hexdigest()):
            reason = saved.get('failure_reason') or 'Saved state, model or tool inventory is incompatible with resumption.'
            raise ConversationStopped('The existing task conversation cannot be resumed safely; no replacement chat was created. '
                                      'Original cause: ' + reason)
        specs,names = _dynamic_tools(tools)
        rpc = AppServer(COMMAND)
        turn = Turn(rpc,model,names,signature)
        turn.tools_allowed = tool_choice != 'none'
        with self.lock:
            self.is_closed = False
            self.live.add(turn)
        try:
            system = '\n\n'.join(str(m.get('content','')) for m in messages if m.get('role') in ('system','developer'))
            system += ('\n\nYou are the inference backend for Hermes. Local work actions must use only the hermes_* dynamic tools '
                       'registered for this request, with Hermes permissions and state. The attached Codex Native/Native2 connector '
                       'is the transport for these dynamic tools: its codex_tool_inventory and codex_tool_call are allowed to '
                       'discover and invoke the exact registered hermes_* wire names using the current request binding. '
                       'If the connector is deferred, use the available connector discovery to load this transport. '
                       'Do not confuse absence of direct hermes_* functions in the initial ChatGPT tool list with absence '
                       'from the current connector inventory. Never use native Codex exec, file, shell, or delegation tools '
                       'as substitutes. Do not claim an action succeeded before its tool result. Answer the current user after tools finish.')
            if tool_choice != 'none':
                system += (
                    '\nHERMES_IMAGE_SESSION=' + sha256(self.session_id.encode()).hexdigest() +
                    '\nImage generation exception: when the user requests generated images, discover the Hermes '
                    'chatgpt_image tool with tool_search/tool_describe if deferred. Invoke it via the registered '
                    'Hermes tool_call transport or its direct dynamic wire name, with '
                    'action prepare, then the registered GPT Image tool via Codex Native2 in THIS conversation, then '
                    'chatgpt_image action collect with the returned request_id. Prepare does NOT generate an image. '
                    'The registered image_gen.imagegen tool is explicitly allowed as the one native exception. Do not render SVG/HTML/Canvas '
                    'or use screenshots as substitutes, and do not open another chat or call a paid image API. '
                    'Generate one requested image at a time. Continue after native generation to collect the actual raster '
                    'and inspect it with Hermes vision tools. If image generation is unavailable or refused, report the '
                    'actual blocker; do not substitute a different capability. Never claim images exist before collect succeeds.'
                    '\nImage route: use Codex Native2 codex_tool_call with the registered wire_name image_gen__imagegen '
                    'and the CURRENT turn binding, following the connector schema. The connector exposes this tool '
                    'in addition to hermes_* work tools. Pass the exact prompt returned by prepare, including its '
                    'internal request reference, with transparent_background=false. Wait for the tool to finish, then collect. '
                    'This is genuine GPT Image; the original PNG is saved under .codex/generated_images and collected '
                    'from the exact bound thread/tool output. It need not appear as an image in the browser DOM. '
                    'Do not require a separate first-party Web-page image tool and do not open another model conversation. '
                    'An awaiting_tool_output result is pending, not failure: follow its bounded collection retry guidance '
                    'without calling generation again. Do not claim a generator was called without its actual tool result.'
                )
            if tool_choice == 'none':
                specs=[]; turn.names={}
                system += '\nThis is a tool-free inference request. Do not discover or call any tools.'
            elif isinstance(tool_choice,dict):
                system += '\nThe requested tool choice is '+json.dumps(tool_choice)
            config = {'default_permissions':'web-smoke','features.shell_tool':False,
                'features.apply_patch_freeform':False,'features.multi_agent':False,'features.apps':False,
                'features.view_image':False,'features.code_mode':False,'features.code_mode_only':False,
                'web_search':'disabled','tools.update_plan.enabled':False,
                'model_auto_compact_token_limit':1000000000}
            if saved:
                result = rpc.request('thread/resume', {'threadId':saved['thread_id'], 'config':config})
                if result['thread']['id'] != saved['thread_id']:
                    raise ConversationStopped('The runtime did not resume the existing task thread.')
                turn.thread_id = saved['thread_id']
                turn.history_count = saved['history_count']
                turn.history_digest = saved['history_digest']
                turn.finalizing_context = bool(saved.get('finalizing_context', False))
                turn.active = False
                self.current = turn
                return turn
            result = rpc.request('thread/start', {'model':model,'allowProviderModelFallback':False,
                'modelProvider':'openai','cwd':os.getcwd(),'developerInstructions':system,
                'dynamicTools':specs,'config':config})
            turn.thread_id = result['thread']['id']
            self.current = turn
            self._save_session(turn, 'active')
            logger.info('Hermes Web registered %d dynamic tools thread=%s names=%s',len(specs),turn.thread_id,','.join(turn.names))
            params = {'threadId':turn.thread_id,'model':model,'input':_input_messages(messages)}
            if effort and 'instant' not in model:
                params['effort'] = effort
            if response_format and response_format.get('type')=='json_schema':
                params['outputSchema'] = response_format['json_schema']['schema']
            elif response_format and response_format.get('type')=='json_object':
                params['input'][0]['text'] += '\nReturn a valid JSON object only.'
            result = rpc.request('turn/start',params)
            turn.turn_id = result['turn']['id']
            logger.info('Hermes Web started model=%s thread=%s',model,turn.thread_id)
            return turn
        except BaseException as exc:
            self.failure = str(exc)
            self._save_session(turn, 'stopped')
            self._release(turn)
            raise

    def _continue(self, turn, messages, effort, response_format, tool_choice):
        if self._history_digest(messages[:turn.history_count]) != turn.history_digest:
            raise ConversationStopped('Hermes replaced task history. Automatic replay into another chat is disabled.')
        suffix = [m for m in messages[turn.history_count:] if m.get('role') in ('user', 'tool')]
        if not suffix:
            raise ConversationStopped('No new message or tool result to send; refusing to replay old history.')
        params = {'threadId':turn.thread_id, 'model':turn.model, 'input':_input_messages(suffix, initial=False)}
        if effort and 'instant' not in turn.model:
            params['effort'] = effort
        if response_format and response_format.get('type') == 'json_schema':
            params['outputSchema'] = response_format['json_schema']['schema']
        turn.tools_allowed = tool_choice != 'none'
        if not turn.tools_allowed:
            params['input'][0]['text'] += '\nFor this response only, provide the requested summary without tool calls.'
        result = turn.rpc.request('turn/start', params)
        turn.turn_id = result['turn']['id']
        turn.active = True
        self._save_session(turn, 'active')

    def create(self, **kwargs):
        # Hermes keeps external-process clients instead of constructing AsyncOpenAI.
        # Its vision/async auxiliary route must receive an awaitable without blocking
        # the event loop; ordinary agent calls retain the synchronous interface.
        try: asyncio.get_running_loop()
        except RuntimeError: return self._create(**kwargs)
        return self._async_create(**kwargs)

    async def _async_create(self, **kwargs):
        cancelled = threading.Event()
        try:
            return await asyncio.to_thread(self._create, _cancel_event=cancelled, **kwargs)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    def _create(self, *, model=None, messages=None, tools=None, stream=False,
               timeout=None, reasoning_effort=None, response_format=None, tool_choice=None,
               _cancel_event=None, **kwargs):
        if model not in MODELS:
            raise ValueError('Only explicitly installed chatgpt-web/* models are accepted; native fallback is disabled.')
        messages = messages or []
        if self.failure or self.is_closed:
            raise ConversationStopped('Task conversation stopped; automatic replacement is disabled. '+(self.failure or 'Client closed.'))
        signature = json.dumps(tools or [], sort_keys=True)
        turn, output = None, None
        with self.lock:
            for message in reversed(messages):
                if message.get('role')=='tool' and message.get('tool_call_id') in self.pending:
                    turn = self.pending.pop(message['tool_call_id']); output = message.get('content',''); break
        if turn and turn.model != model:
            self._release(turn)
            raise ValueError('Model/tool inventory changed while a Web tool call was pending; turn cancelled.')
        if turn is None and self.current is not None:
            turn = self.current
        if turn and turn.model != model:
            raise ConversationStopped('Changing models cannot silently replace the task conversation.')
        if turn and turn.signature != signature and tool_choice != 'none':
            self._release(turn)
            raise ValueError('Tool inventory changed while a Web tool call was pending; turn cancelled.')
        try:
            if turn is None:
                turn = self._start(model,messages,tools,signature,reasoning_effort,response_format,tool_choice)
            elif turn.active and output is None:
                raise ConversationStopped('The pending tool result is missing; refusing another conversation.')
            if not turn.active:
                self._continue(turn,messages,reasoning_effort,response_format,tool_choice)
            elif turn.history_count:
                if self._history_digest(messages[:turn.history_count]) != turn.history_digest:
                    raise ConversationStopped('Hermes replaced task history; the existing chat was not restarted.')
                new_users = [m for m in messages[turn.history_count:] if m.get('role') == 'user']
                if new_users:
                    turn.rpc.request('turn/steer', {'threadId':turn.thread_id,
                        'expectedTurnId':turn.turn_id, 'input':_input_messages(new_users, initial=False)})
                turn.tools_allowed = tool_choice != 'none'
            turn.history_count = len(messages)
            turn.history_digest = self._history_digest(messages)
            turn.cancel_event = _cancel_event
            with turn.lock:
                if output is not None:
                    turn.rpc.send({'id':turn.request_id,'result':{'contentItems':_content_items(output),'success':True}})
                    turn.request_id=None;turn.call_id=''
                completion = self._next_completion(turn,timeout)
            return Chunks(completion) if stream else completion
        except BaseException as exc:
            self.failure = str(exc)
            if turn is not None:
                self._save_session(turn, 'stopped')
                self._release(turn)
            raise

    @staticmethod
    def _history_digest(messages):
        history = [m for m in messages if m.get('role') not in ('system','developer')]
        return sha256(json.dumps(history, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _next_completion(self,turn,timeout):
        budget = float(timeout) if isinstance(timeout,(int,float)) else 900
        deadline = time.monotonic()+budget
        while time.monotonic()<deadline:
            if turn.cancel_event is not None and turn.cancel_event.is_set():
                raise InterruptedError('ChatGPT Web asynchronous inference was cancelled.')
            try:
                event = turn.rpc.next(min(10,max(.01,deadline-time.monotonic())))
            except queue.Empty:
                continue
            method=event.get('method',''); params=event.get('params',{})
            if method=='item/tool/call':
                if not turn.tools_allowed:
                    raise ConversationStopped('A tool was requested during a tool-free response; no replacement chat was created.')
                if params.get('threadId')!=turn.thread_id or params.get('turnId')!=turn.turn_id:
                    raise RuntimeError('Dynamic tool call belongs to another turn.')
                name=turn.names.get(params.get('tool'))
                if not name:
                    raise RuntimeError('Codex requested a tool outside the Hermes inventory.')
                turn.request_id=event['id'];turn.call_id='hw_'+uuid.uuid4().hex
                arguments=json.dumps(params.get('arguments',{}),ensure_ascii=False)
                call=NS(id=turn.call_id,type='function',function=NS(name=name,arguments=arguments))
                with self.lock:
                    self.pending[turn.call_id]=turn
                logger.info('Hermes Web tool handoff name=%s thread=%s',name,turn.thread_id)
                completion=self._completion(turn,[call],'tool_calls')
                self._save_session(turn, 'pending_tool')
                return completion
            if 'id' in event and method:
                # The inference subprocess cannot approve/execute anything outside Hermes.
                turn.rpc.send({'id':event['id'],'error':{'code':-32601,'message':'Only Hermes dynamic tool calls are supported.'}})
                raise RuntimeError('Unexpected Codex approval or input request: '+method)
            if method=='item/started':
                kind=params.get('item',{}).get('type')
                if kind in ('commandExecution','fileChange','mcpToolCall','collabAgentToolCall'):
                    raise RuntimeError('Unexpected native Codex tool; turn stopped before accepting a result.')
            if method=='item/completed':
                item=params.get('item',{})
                if item.get('type')=='agentMessage':
                    turn.text.append(item.get('text',''))
            if method=='thread/tokenUsage/updated':
                if params.get('threadId') == turn.thread_id:
                    turn.usage=params.get('tokenUsage',{})
                    from . import _context_budget
                    budget = _context_budget(turn.model)
                    used = (turn.usage.get('last') or {}).get('inputTokens', 0)
                    reserve = min(20000, max(4000, int(budget * .10)))
                    if used >= budget - reserve and not turn.finalizing_context:
                        # Allow the existing turn to save/push and report its result.
                        # Killing it at 95% previously discarded the final answer after commit.
                        turn.finalizing_context = True
                        try:
                            turn.rpc.request('turn/steer', {
                                'threadId': turn.thread_id, 'expectedTurnId': turn.turn_id,
                                'input': [{'type': 'text', 'text':
                                    'Context capacity is nearly exhausted. Finish this SAME task and chat now: '
                                    'do not start another product, stage, generation or broad investigation. '
                                    'Complete only necessary verification and authorized commit/push of the current '
                                    'work, preserve any partial outputs and pending items, then return a concise '
                                    'final report with the actual status. Do not replay history, create another chat '
                                    'or claim unfinished work is complete.'}]})
                        except RuntimeError as error:
                            # Usage can arrive immediately before an already completed turn.
                            # A rejected steer must not discard the queued final response.
                            logger.warning('Hermes Web closing guidance not delivered: %s', error)
                        logger.info('Hermes Web closing reserve: input=%s budget=%s thread=%s',
                                    used, budget, turn.thread_id)
                        self._save_session(turn, 'active')
            if method=='turn/completed' and params.get('turn',{}).get('id')==turn.turn_id:
                status=params['turn'].get('status')
                if status!='completed':
                    error=params['turn'].get('error') or {}
                    raise RuntimeError('ChatGPT Web turn '+str(status)+': '+str(error.get('message','No replay was attempted.')))
                completion=self._completion(turn,[], 'stop')
                logger.info('Hermes Web completed model=%s thread=%s',turn.model,turn.thread_id)
                turn.active = False
                self._save_session(turn, 'idle')
                return completion
        raise TimeoutError('ChatGPT Web response timed out; no model fallback or automatic replay was attempted.')

    @staticmethod
    def _completion(turn,calls,reason):
        content='\n'.join(turn.text);turn.text.clear()
        usage=ChatGPTWebClient._response_usage(turn)
        return NS(id='hermes_web_'+uuid.uuid4().hex,object='chat.completion',created=int(time.time()),model=turn.model,
            choices=[NS(index=0,finish_reason=reason,message=NS(role='assistant',content=content,
                tool_calls=calls or None,reasoning_content=None,reasoning=None,reasoning_details=None))],
            usage=usage)

    @staticmethod
    def _response_usage(turn):
        """Keep cumulative work separate from the current request's context.

        A Codex turn may contain many inference requests around dynamic tools.
        ``total`` sums their repeated prefixes; only ``last`` describes the
        current prompt. Hermes supports initial_prompt_tokens for this exact
        billing/context split (also used by its multi-hop Gemini adapter).
        Report each cumulative delta once, including at tool handoffs.
        """
        total=turn.usage.get('total') or {}
        last=turn.usage.get('last') or {}
        def count(row,key):
            value=row.get(key)
            return max(0,value) if isinstance(value,int) and not isinstance(value,bool) else 0
        context=count(last,'inputTokens')
        if not context or not total:
            return None  # Unknown usage is not zero, nor cumulative context.
        delta={key:max(0,count(total,key)-count(turn.reported_usage,key))
               for key in ('inputTokens','outputTokens','cachedInputTokens','reasoningOutputTokens')}
        if not delta['inputTokens'] and not delta['outputTokens']:
            return None
        turn.reported_usage=dict(total)
        prompt=delta['inputTokens']; output=delta['outputTokens']
        logger.info('Hermes Web usage context_input=%s cumulative_input=%s reported_input=%s thread=%s',
                    context,count(total,'inputTokens'),prompt,turn.thread_id)
        return NS(prompt_tokens=prompt,completion_tokens=output,total_tokens=prompt+output,
                  prompt_tokens_details=NS(cached_tokens=min(prompt,delta['cachedInputTokens'])),
                  completion_tokens_details=NS(reasoning_tokens=delta['reasoningOutputTokens']),
                  initial_prompt_tokens=context,
                  initial_cached_tokens=min(context,count(last,'cachedInputTokens')))
