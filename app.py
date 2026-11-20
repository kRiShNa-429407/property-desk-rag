import os
import re
from pathlib import Path

import streamlit as st
from dotenv import dotenv_values
from pymongo.errors import PyMongoError

from llm import ChatError, ask, clean_answer
from prompts import NO_MATCH_REPLY
from rag import Retriever, knowledge_files, load_passages

st.set_page_config(page_title='Property Desk', page_icon='🏠', layout='centered')
st.markdown(f'<style>{Path(__file__).with_name("styles.css").read_text(encoding="utf-8")}</style>', unsafe_allow_html=True)


def configured_keys():
    values = dotenv_values(Path(__file__).parent / '.env')
    names = ['OPENROUTER_API_KEY'] + [f'OPENROUTER_API_KEY_{number}' for number in range(1, 5)]
    return [os.getenv(name) or values.get(name) or '' for name in names]


@st.cache_resource(max_entries=1)
def build_retriever(file_versions):
    return Retriever(load_passages())


def show_sources(sources):
    if not sources:
        return
    with st.expander('Sources'):
        seen = set()
        for item in sources:
            if item['url'] not in seen:
                st.markdown(f'[{item["title"]}]({item["url"]})')
                seen.add(item['url'])


def scroll_to_latest():
    tick = st.session_state.get('scroll_tick', 0) + 1
    st.session_state.scroll_tick = tick
    script = Path(__file__).with_name('chat_scroll.js').read_text(encoding='utf-8')
    st.html(f'<script data-scroll-tick="{tick}">{script}</script>', unsafe_allow_javascript=True)


st.markdown('''<div class="brand-label">
<svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
<path d="M5 27V10l11-6 11 6v17M11 27V16h10v11M16 4v8M3 27h26" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>PROPERTY RESEARCH</div>''', unsafe_allow_html=True)
heading, actions = st.columns([4, 1.2], vertical_alignment='center')
with heading:
    st.title('Property Desk')
    st.caption('Ask about DarGlobal and Wasalt.')
with actions:
    if st.button('New chat', use_container_width=True):
        st.session_state.messages = []
        st.rerun()

files = knowledge_files()
if not files:
    st.error('Add a JSON knowledge file to the data folder.')
    st.stop()
try:
    versions = tuple((path.name, path.stat().st_mtime_ns, path.stat().st_size) for path in files)
    retriever = build_retriever(versions)
except (ValueError, OSError) as error:
    st.error(str(error))
    st.stop()

api_keys = configured_keys()

if 'messages' not in st.session_state:
    st.session_state.messages = []

question = st.chat_input('Ask a question…', max_chars=2000)


if not st.session_state.messages and not question:
    st.markdown('''<div class="welcome-card">
    <div class="welcome-icon"><svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
    <path d="M5 27V13l8-5v19M13 27V5h14v22M18 11h4M18 16h4M18 21h4M3 27h26" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg></div><h2>What would you like to know?</h2></div>''', unsafe_allow_html=True)

for message in st.session_state.messages:
    with st.chat_message(message['role']):
        content = clean_answer(message['content']) if message['role'] == 'assistant' else message['content']
        st.markdown(content)
        show_sources(message.get('sources', []))

if question:
    with st.chat_message('user'):
        st.write(question)
    scroll_to_latest()
    print('PropertyDesk: starting retrieval', flush=True)
    history = st.session_state.messages[-6:]
    search_question = question
    if re.search(r'\b(it|they|those|these|them|that)\b', question, re.I):
        previous = next((item['content'] for item in reversed(history) if item['role'] == 'user'), '')
        search_question = f'{previous} {question}'
    try:
        passages = retriever.search(search_question)
        print('PropertyDesk: retrieval completed', flush=True)
    except PyMongoError:
        st.error('Property search is temporarily unavailable. Please try again shortly.')
        st.stop()
    with st.chat_message('assistant'):
        try:
            if not passages:
                answer = NO_MATCH_REPLY
            else:
                with st.spinner('Thinking…'):
                    answer, _ = ask(question, passages, api_keys, history)
            st.markdown(answer)
            sources = [vars(item) for item in passages]
            show_sources(sources)
            st.session_state.messages.extend([
                {'role': 'user', 'content': question},
                {'role': 'assistant', 'content': answer, 'sources': sources},
            ])
            st.session_state.messages = st.session_state.messages[-40:]
        except ChatError as error:
            st.error(str(error))
            show_sources([vars(item) for item in passages])
    scroll_to_latest()
