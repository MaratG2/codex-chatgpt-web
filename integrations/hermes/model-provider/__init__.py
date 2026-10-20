"""External Hermes provider: real Codex dynamic calls, Hermes's normal tool loop."""
from providers import register_provider
from providers.base import ProviderProfile
from pathlib import Path
import json

MODELS = tuple('chatgpt-web/' + name for name in (
    'gpt-6-sol', 'gpt-5.6-sol', 'gpt-5.6-pro', 'gpt-6-pro',
    'gpt-6-sol-instant', 'gpt-5.6-sol-instant'))
COMMAND = '/home/codexweb/experiment/bin/codex'


def _context_budget(model):
    """Conservative input budgets within launcher 6.1.10's transport limits.

    These describe this adapter, not an entitlement from a ChatGPT plan.
    Re-read at provider registration so a standard-context rollback is safe.
    """
    try:
        config=json.loads((Path.home()/'.codex-chatgpt-web/config.json').read_text())
    except (OSError,ValueError):
        return 80000
    if not config.get('proAvailable'):
        return 32000 if 'instant' in model else 80000
    if 'instant' in model or not config.get('experimentalBiggerContext'):
        return 94000
    return 220000 if model=='chatgpt-web/gpt-6-sol' else 280000


class ChatGPTWebProfile(ProviderProfile):
    def create_client(self, **kwargs):
        from .client import ChatGPTWebClient
        return ChatGPTWebClient(**kwargs)

    def fetch_models(self, **kwargs):
        from .rpc import AppServer
        with AppServer(COMMAND) as rpc:
            rows = rpc.request('model/list', {'includeHidden': False}).get('data', [])
            return [m['id'] for m in rows if m.get('id', '').startswith('chatgpt-web/')]


register_provider(ChatGPTWebProfile(
    name='chatgpt-web-local', display_name='ChatGPT Web (local launcher)',
    description='ChatGPT Web models through the local Codex launcher; Hermes owns tools and memory.',
    api_mode='chat_completions', auth_type='external_process',
    base_url='codex-web://local', process_command=COMMAND, process_args=('app-server',),
    supports_health_check=False, supports_model_listing=True, supports_vision=True,
    fallback_models=MODELS, default_aux_model='chatgpt-web/gpt-6-sol',
    model_capabilities={m: {'supports_tools': True, 'supports_vision': True,
        'supports_reasoning': 'instant' not in m, 'context_window': _context_budget(m),
        'model_family': 'chatgpt-web'} for m in MODELS},
))
