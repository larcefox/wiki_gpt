import os
import json
import threading
import logging

import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit_quill import st_quill
from dotenv import load_dotenv
from streamlit.errors import StreamlitSecretNotFoundError

try:
    from streamlit.runtime.scriptrunner import add_script_run_ctx
except ModuleNotFoundError:  # Streamlit < 1.18
    from streamlit.scriptrunner import add_script_run_ctx  # type: ignore

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _env_or_secret(key: str) -> str | None:
    env_val = os.getenv(key)
    if env_val:
        return env_val.strip()
    try:
        return st.secrets[key].strip()
    except (KeyError, StreamlitSecretNotFoundError):
        return None


API_BASE = os.getenv("WIKI_API_BASE", "http://localhost:8000")
YANDEX_TOKEN = _env_or_secret("YANDEX_OAUTH_TOKEN")
YANDEX_FOLDER_ID = _env_or_secret("YANDEX_FOLDER_ID")

st.set_page_config(page_title="Wiki GPT – Frontend", layout="wide")


# ---------------------------
# Helpers
# ---------------------------
def _state_str(key: str) -> str:
    """Return string value from session state or empty string."""
    val = st.session_state.get(key, "")
    return val if isinstance(val, str) else ""


def api_request(
    method: str, path: str, payload: dict | None = None, retry: bool = True
):
    url = f"{API_BASE}{path}"
    headers = {}
    token = st.session_state.get("access_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.request(method, url, json=payload, headers=headers, timeout=60)
    if r.status_code == 401 and retry and st.session_state.get("refresh_token"):
        refresh = requests.post(
            f"{API_BASE}/auth/refresh",
            json={"refresh_token": st.session_state.get("refresh_token")},
            timeout=60,
        )
        if refresh.status_code < 300:
            data = refresh.json()
            st.session_state["access_token"] = data["access_token"]
            st.session_state["refresh_token"] = data["refresh_token"]
            return api_request(method, path, payload, retry=False)
        else:
            for k in ["access_token", "refresh_token", "user"]:
                st.session_state.pop(k, None)
            st.error("Сессия истекла. Войдите снова.")
            st.stop()
    if r.status_code >= 400:
        raise RuntimeError(f"{method.upper()} {path} failed: {r.status_code} {r.text}")
    return r.json() if r.text else None


def api_post(path: str, payload: dict):
    return api_request("post", path, payload)


def api_put(path: str, payload: dict):
    return api_request("put", path, payload)


def api_get(path: str):
    return api_request("get", path)


def api_delete(path: str):
    return api_request("delete", path)


def search_articles(query: str, tags=None):
    payload = {"q": query}
    if tags:
        payload["tags"] = tags
    return api_post("/articles/search/", payload)


def create_article(title: str, content: str, tags):
    return api_post("/articles/", {"title": title, "content": content, "tags": tags})


def update_article(article_id: str, title: str, content: str, tags):
    return api_put(
        f"/articles/{article_id}", {"title": title, "content": content, "tags": tags}
    )


def get_article(article_id: str):
    return api_get(f"/articles/{article_id}")


def delete_article(article_id: str):
    return api_delete(f"/articles/{article_id}")


def get_history(article_id: str):
    return api_get(f"/articles/{article_id}/history")


def suggest_related(
    title: str, content: str, exclude_id: str | None = None, top_k: int = 5
):
    results = search_articles(f"{title}\n{content}")
    unique = []
    for hit in results:
        if exclude_id and hit["id"] == exclude_id:
            continue
        unique.append(hit)
    return unique[:top_k]


def llm_recommendations(title: str, content: str) -> str:
    if not (YANDEX_TOKEN and YANDEX_FOLDER_ID):
        return "LLM выключен: не заданы YANDEX_OAUTH_TOKEN / YANDEX_FOLDER_ID."

    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Bearer {YANDEX_TOKEN}",
        "Content-Type": "application/json",
    }

    prompt = (
        "Ты – редактор и техписатель. Дай практичные рекомендации по улучшению статьи: "
        "структура, ясность, недостающие разделы, теги. Пиши кратко и по пунктам.\n\n"
        f"Заголовок: {title}\n\n"
        f"Текст статьи:\n{content}\n\n"
        "Ответ формируй в формате маркдаун-списка, каждый пункт начинай с одной из пометок: "
        "[структура], [пробелы в фактах], [предложенные теги]."
    )

    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {"stream": False, "temperature": 0.2, "maxTokens": 400},
        "messages": [
            {"role": "system", "text": "Ты помогаешь улучшать статьи в базе знаний."},
            {"role": "user", "text": prompt},
        ],
    }

    logger.info("LLM request: title=%s content_len=%d", title, len(content))
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=90)
    except Exception as e:
        logger.exception("LLM request failed")
        return f"Ошибка LLM: {e}"
    if r.status_code != 200:
        logger.error("LLM error %s: %s", r.status_code, r.text)
        return f"Ошибка LLM: {r.status_code} {r.text}"

    data = r.json()
    alternatives = data.get("result", {}).get("alternatives") or data.get(
        "alternatives"
    )
    if alternatives:
        text = alternatives[0].get("message", {}).get("text", "").strip()
        if text:
            logger.info("LLM response received, %d chars", len(text))
            return text
    logger.warning("LLM response parsing failed")
    return "Не удалось распарсить ответ LLM:\n\n" + json.dumps(
        data, ensure_ascii=False, indent=2
    )


# ---------------------------
# Markdown editor via streamlit-ace
# ---------------------------
def markdown_editor(
    label: str,
    key: str,
    *,
    height: int = 300,
    placeholder: str | None = None,
    on_change=None,
):
    """Markdown editor with formatting toolbar based on EasyMDE."""
    st.markdown(f"#### {label}")

    prev_key = f"{key}__prev"
    initial = _state_str(key)
    placeholder_js = json.dumps(placeholder or "")
    initial_js = json.dumps(initial)
    # EasyMDE provides a markdown editor with a toolbar loaded from CDN
    editor_id = f"editor_{key}"
    html = f"""
    <link rel='stylesheet' href='https://unpkg.com/easymde/dist/easymde.min.css'>
    <textarea id='{editor_id}'></textarea>
    <script src='https://unpkg.com/easymde/dist/easymde.min.js'></script>
    <script>
      const easyMDE = new EasyMDE({{
        element: document.getElementById('{editor_id}'),
        placeholder: {placeholder_js},
        spellChecker: false,
        status: false,
        toolbar: [
          'bold', 'italic', 'heading', '|',
          'quote', 'unordered-list', 'ordered-list', 'link', 'code'
        ],
        forceSync: true,
        initialValue: {initial_js}
      }});
      const root = window.parent;
      // Notify Streamlit that the component is ready and push the initial value
      root.postMessage({{
        isStreamlitMessage: true,
        type: 'streamlit:componentReady',
        height: 0,
      }}, '*');
      root.postMessage({{
        isStreamlitMessage: true,
        type: 'streamlit:setComponentValue',
        value: easyMDE.value(),
      }}, '*');
      easyMDE.codemirror.on('change', function() {{
        root.postMessage({{
          isStreamlitMessage: true,
          type: 'streamlit:setComponentValue',
          value: easyMDE.value()
        }}, '*');
      }});
    </script>
    """

    # Render the editor and fetch its value. Support Streamlit versions
    # that may not accept newer arguments for components.html.
    try:
        component_val = components.html(
            html,
            height=height + 80,
            key=key,
            scrolling=False,
            always_emit_events=True,
        )

    except TypeError:
        component_val = components.html(html, height=height + 80)

    if isinstance(component_val, str):
        content = component_val
        st.session_state[key] = content
    else:
        content = _state_str(key)
    prev = _state_str(prev_key)
    if on_change and content != prev:
        on_change()
    st.session_state[prev_key] = content
    return content


# ---------------------------
# Auth & UI
# ---------------------------
if "access_token" not in st.session_state:
    tab_login, tab_register = st.tabs(["Вход", "Регистрация"])
    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Пароль", type="password")
            submitted = st.form_submit_button("Войти")
        if submitted:
            try:
                data = api_post("/auth/login", {"email": email, "password": password})
                st.session_state["access_token"] = data["access_token"]
                st.session_state["refresh_token"] = data["refresh_token"]
                st.session_state["user"] = api_get("/auth/me")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    with tab_register:
        with st.form("register_form"):
            r_email = st.text_input("Email", key="reg_email")
            r_password = st.text_input("Пароль", type="password", key="reg_password")
            r_submitted = st.form_submit_button("Зарегистрироваться")
        if r_submitted:
            try:
                data = api_post(
                    "/auth/register", {"email": r_email, "password": r_password}
                )
                st.session_state["access_token"] = data["access_token"]
                st.session_state["refresh_token"] = data["refresh_token"]
                st.session_state["user"] = api_get("/auth/me")
                st.rerun()
            except Exception as e:
                st.error(str(e))
    st.stop()

if "user" not in st.session_state:
    st.session_state["user"] = api_get("/auth/me")

roles = st.session_state["user"].get("roles", [])
st.sidebar.title("Wiki GPT")
st.sidebar.write(st.session_state["user"]["email"])
if st.sidebar.button("Выйти"):
    for k in ["access_token", "refresh_token", "user"]:
        st.session_state.pop(k, None)
    st.rerun()

options: list[str] = []
if "author" in roles or "admin" in roles:
    options += ["Создать статью", "Редактировать статью"]
if any(r in roles for r in ["reader", "author", "admin"]):
    options += ["Поиск", "Статья по ID"]
if "admin" in roles:
    options += ["Диагностика"]
page = st.sidebar.radio("Навигация", options)

st.sidebar.markdown("---")
st.sidebar.caption(f"Backend: {API_BASE}")

# --- Создать ---
if page == "Создать статью":
    st.header("Создать статью")
    main_col, rec_col = st.columns([3, 2])

    if "create_title" not in st.session_state:
        st.session_state.create_title = ""
    if "create_tags" not in st.session_state:
        st.session_state.create_tags = ""
    if "create_content" not in st.session_state:
        st.session_state.create_content = ""
    if "llm_tips" not in st.session_state:
        st.session_state.llm_tips = ""
    if "llm_timer" not in st.session_state:
        st.session_state.llm_timer = None

    def schedule_llm() -> None:
        if not (YANDEX_TOKEN and YANDEX_FOLDER_ID):

            logger.debug("LLM disabled, skipping schedule")
            return
        if st.session_state.llm_timer:
            st.session_state.llm_timer.cancel()

        def run():
            title = _state_str("create_title").strip()
            content = _state_str("create_content").strip()
            if not title and not content:
                st.session_state.llm_tips = ""
            else:
                logger.info("Auto-updating LLM recommendations")
                st.session_state.llm_tips = llm_recommendations(title, content)
            st.session_state.llm_timer = None
            st.rerun()

        st.session_state.llm_timer = threading.Timer(1.5, run)
        add_script_run_ctx(st.session_state.llm_timer)
        st.session_state.llm_timer.start()

    with main_col:
        st.text_input("Заголовок", key="create_title", on_change=schedule_llm)
        st.text_input("Теги (через запятую)", key="create_tags")
        content = st_quill(
            value=_state_str("create_content"),
            html=True,
            placeholder="Напишите статью...",
            key="create_content",
        )

        if content is not None:
            st.session_state["create_content"] = content
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("Сохранить статью", key="create_save"):
                st.session_state.create_submit = True
                st.rerun()

        if st.session_state.pop("create_submit", False):
            title_val = _state_str("create_title").strip()
            content_val = _state_str("create_content").strip()
            st.write(f"[DEBUG] title='{title_val}' content='{content_val}'")
            if not title_val or not content_val:
                st.error("Заполните заголовок и текст.")
            else:
                try:
                    tag_list = [
                        t.strip()
                        for t in _state_str("create_tags").split(",")
                        if t.strip()
                    ]
                    res = create_article(title_val, content_val, tag_list)
                    st.success(f"Создано! ID: {res['id']}")
                    with st.expander("Похожие статьи сразу после сохранения"):
                        related = suggest_related(
                            title_val, content_val, exclude_id=res["id"], top_k=5
                        )
                        for hit in related:
                            st.write(
                                f"**{hit['title']}** · score={hit.get('score'):.3f}"
                            )
                            st.caption(
                                f"{hit['id']} · теги: {', '.join(hit.get('tags', []))}"
                            )
                            st.write(hit["content"])
                            st.markdown("---")
                except Exception as e:
                    st.error(str(e))

        with col2:
            if st.button("Рекомендации (LLM)"):
                title_val = _state_str("create_title").strip()
                content_val = _state_str("create_content").strip()
                if not title_val and not content_val:
                    st.warning("Сначала заполни заголовок/текст.")
                else:
                    with st.spinner("Генерирую рекомендации..."):
                        logger.info("Manual LLM request")
                        st.session_state.llm_tips = llm_recommendations(
                            title_val, content_val
                        )
                    st.rerun()

    with rec_col:
        st.markdown("### Рекомендации ИИ")
        if not (YANDEX_TOKEN and YANDEX_FOLDER_ID):
            st.info("LLM выключен: не заданы YANDEX_OAUTH_TOKEN / YANDEX_FOLDER_ID.")
        else:
            tips = st.session_state.get("llm_tips", "")
            if tips:
                st.markdown(tips)
            else:
                st.caption("Начните ввод, чтобы получить рекомендации.")

# --- Редактировать ---
elif page == "Редактировать статью":
    st.header("Редактировать статью")
    st.caption("Укажи ID статьи (можно взять из результата создания/поиска).")
    article_id = st.text_input("Article ID", "")
    title = st.text_input("Новый заголовок", "")
    tags = st.text_input("Новые теги (через запятую)", "")
    markdown_editor("Новый текст статьи", key="edit_content", height=300)

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("Сохранить изменения", key="edit_save"):
            st.session_state.edit_submit = True
            st.rerun()

    if st.session_state.pop("edit_submit", False):
        title_val = title.strip()
        content_val = _state_str("edit_content").strip()
        st.write(f"[DEBUG] title='{title_val}' content='{content_val}'")
        if not article_id.strip():
            st.error("Укажи ID статьи.")
        elif not title_val or not content_val:
            st.error("Заполни заголовок и текст.")
        else:
            try:
                tag_list = [t.strip() for t in tags.split(",") if t.strip()]
                res = update_article(
                    article_id.strip(), title_val, content_val, tag_list
                )
                st.success(f"Обновлено: {res['id']}")
            except Exception as e:
                st.error(str(e))

    with col2:
        if st.button("Рекомендации к статье (LLM)"):
            content_val = _state_str("edit_content").strip()
            if not title.strip() and not content_val:
                st.warning("Сначала заполни заголовок/текст.")
            else:
                with st.spinner("Генерирую рекомендации..."):
                    logger.info("Manual LLM request on edit page")
                    tips = llm_recommendations(title.strip(), content_val)
                st.markdown("### Рекомендации")
                st.markdown(tips)

    st.markdown("---")
    if st.button("Найти похожие статьи"):
        content_val = _state_str("edit_content").strip()
        if not title.strip() and not content_val:
            st.warning("Сначала заполни заголовок/текст — по ним ищем похожие.")
        else:
            related = suggest_related(
                title, content_val, exclude_id=article_id.strip(), top_k=10
            )
            st.subheader("Похожие статьи")
            for hit in related:
                st.write(f"**{hit['title']}** · score={hit.get('score'):.3f}")
                st.caption(f"{hit['id']} · теги: {', '.join(hit.get('tags', []))}")
                st.write(hit["content"])
                st.markdown("---")

# --- Поиск ---
elif page == "Поиск":
    st.header("Поиск по базе знаний")
    q = st.text_input("Запрос", placeholder="например: YandexGPT эмбеддинги")
    tags_filter = st.text_input("Фильтр по тегам (через запятую)", "")
    topk = st.slider("Сколько результатов показать", 1, 20, 5)
    if st.button("Искать") and q.strip():
        try:
            tag_list = [t.strip() for t in tags_filter.split(",") if t.strip()]
            results = search_articles(q.strip(), tag_list)[:topk]
            st.subheader("Результаты")
            for hit in results:
                st.write(f"**{hit['title']}** · score={hit.get('score'):.3f}")
                st.caption(f"{hit['id']} · теги: {', '.join(hit.get('tags', []))}")
                st.write(hit["content"])
                st.markdown("---")
        except Exception as e:
            st.error(str(e))

# --- Статья по ID ---
elif page == "Статья по ID":
    st.header("Статья по ID")
    with st.form("view_form"):
        view_id = st.text_input("Article ID", value=st.session_state.get("view_id", ""))
        submitted = st.form_submit_button("Загрузить")
    if submitted and view_id.strip():
        try:
            st.session_state.view_id = view_id.strip()
            st.session_state.view_article = get_article(view_id.strip())
            st.session_state.view_history = get_history(view_id.strip())
        except Exception as e:
            st.error(str(e))
    article = st.session_state.get("view_article")
    if article:
        tabs = st.tabs(["Статья", "История"])
        with tabs[0]:
            st.subheader(article["title"])
            st.write(article["content"])
            st.caption(f"Теги: {', '.join(article.get('tags', []))}")
            if st.button("Удалить статью"):
                try:
                    delete_article(article["id"])
                    st.success("Удалено")
                    st.session_state.view_article = None
                    st.session_state.view_history = None
                except Exception as e:
                    st.error(str(e))
        with tabs[1]:
            history = st.session_state.get("view_history", [])
            if history:
                st.table(history)
            else:
                st.info("История пуста")

# --- Диагностика ---
else:
    st.header("Диагностика")
    st.write("Проверка окружения:")
    st.json(
        {
            "API_BASE": API_BASE,
            "YANDEX_OAUTH_TOKEN": bool(YANDEX_TOKEN),
            "YANDEX_FOLDER_ID": YANDEX_FOLDER_ID or "",
        }
    )
    st.caption("Если LLM выключен — рекомендации и рерэнк будут недоступны.")
