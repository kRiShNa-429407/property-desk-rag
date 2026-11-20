import re
from itertools import count
from threading import Lock
from time import monotonic

import requests

from prompts import NO_MATCH_REPLY, build_messages

FREE_MODEL = 'inclusionai/ling-3.2-flash:free'
FREE_FALLBACKS = ['nvidia/nemotron-3.5-lightning:free', 'google/gemma-4-31b-it:free']
API_URL = 'https://openrouter.ai/api/v1/chat/completions'
_key_index = count()
_key_lock = Lock()
_key_cooldowns = {}


def next_api_key(keys):
    keys = list(dict.fromkeys(key.strip() for key in keys if key and key.strip()))
    if not keys:
        return ''
    with _key_lock:
        available = [key for key in keys if _key_cooldowns.get(key, 0) <= monotonic()]
        return available[next(_key_index) % len(available)] if available else ''


def pause_key(key, response):
    try:
        message = response.json().get('error', {}).get('message', '')
    except (ValueError, AttributeError, TypeError):
        message = ''
    daily_limit = isinstance(message, str) and 'free-models-per-day' in message
    delay = 3600 if daily_limit else 60
    retry_after = response.headers.get('Retry-After')
    if isinstance(retry_after, str) and retry_after.isdigit():
        delay = max(1, int(retry_after))
    with _key_lock:
        _key_cooldowns[key] = monotonic() + delay
    if daily_limit:
        return "Today's free chat limit has been reached. Please try again after the daily limit resets."
    return 'Chat has reached a rate limit. Please try again shortly.'


class ChatError(Exception):
    pass


def clean_answer(answer):
    # Remove reference markers if the model adds them.
    answer = re.sub(r'[^\S\n]*\[\^?\d+(?:\s*[,;–-]\s*\d+)*\](?:\([^\n)]*\))?', '', answer).strip()
    return re.sub(
        r'^(?:based on|according to)\s+(?:the\s+)?(?:provided|retrieved)\s+'
        r'(?:passages|context|documents|records|sources)\s*[,;:–—-]\s*',
        '', answer, flags=re.IGNORECASE,
    ).strip()


def ask(question, passages, api_key, history=(), session=None):
    if passages and passages[0].answer:
        return passages[0].answer, 'wasalt-catalog'
    keys = [api_key] if isinstance(api_key, str) else api_key
    keys = list(dict.fromkeys(key.strip() for key in keys if key and key.strip()))
    if not keys:
        raise ChatError('Chat is not configured yet.')
    if not passages:
        return NO_MATCH_REPLY, None
    messages = build_messages(question, passages, history)
    client = session or requests
    error_message = 'Chat has reached a rate limit. Please try again shortly.'
    remaining = keys.copy()
    while remaining:
        key = next_api_key(remaining)
        if not key:
            raise ChatError(error_message)
        remaining.remove(key)
        try:
            response = client.post(
                API_URL,
                headers={'Authorization': f'Bearer {key}', 'X-Title': 'Property Desk'},
                json={
                    'model': FREE_MODEL, 'models': FREE_FALLBACKS,
                    'messages': messages, 'temperature': 0.2,
                    'max_tokens': 1200,
                    'reasoning': {'enabled': False},
                    'provider': {'max_price': {'prompt': 0, 'completion': 0}},
                },
                timeout=(10, 60),
            )
        except requests.RequestException as error:
            raise ChatError('Could not reach OpenRouter. Please try again shortly.') from error
        if response.status_code != 429:
            break
        error_message = pause_key(key, response)
    else:
        raise ChatError(error_message)
    if response.status_code in (401, 403):
        raise ChatError('Chat authentication failed. Please check the server configuration.')
    if response.status_code != 200:
        raise ChatError('The free model is unavailable. Please try again later.')
    try:
        data = response.json()
        answer = data['choices'][0]['message']['content']
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError('Empty answer')
        answer = clean_answer(answer)
        if not answer:
            raise ValueError('Empty answer')
        return answer, data.get('model', FREE_MODEL)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ChatError('The model returned an empty response. Please try again.') from error
