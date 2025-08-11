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


def render_article_editor(
    state_key: str, editor_key: str, placeholder: str = "Напишите статью."
):
    """Unified article editor bound to session_state.

    ``st_quill`` may return ``None`` on initial render or when the user
    hasn't modified the editor in the current run. When submitting a
    form immediately after typing, this could lead to the latest content
    not being captured. To avoid losing text, fall back to the widget's
    own state when ``content`` is ``None`` and always persist it under
    ``state_key``.
    """

    content = st_quill(
        value=_state_str(state_key),
        html=True,
        placeholder=placeholder,
        key=editor_key,
    )

    if content is None:
        # When the component doesn't report an update (e.g. submit right
        # after typing), retrieve the latest value directly from
        # session_state.
        content = st.session_state.get(editor_key)

    if content is not None:
        st.session_state[state_key] = content

    return _state_str(state_key)


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


def search_articles(query: str, tags=None, group_id: str | None = None):
    payload = {"q": query}
    if tags:
        payload["tags"] = tags
    if group_id:
        payload["group_id"] = group_id
    return api_post("/articles/search/", payload)


def create_article(
    title: str,
    content: str,
    tags,
    group_id: str | None = None,
    group: dict | None = None,
):
    payload = {"title": title, "content": content, "tags": tags}
    if group_id:
        payload["group_id"] = group_id
    if group:
        payload["group"] = group
    return api_post("/articles/", payload)


def update_article(article_id: str, title: str, content: str, tags, group_id: str | None = None):
    payload = {"title": title, "content": content, "tags": tags}
    if group_id:
        payload["group_id"] = group_id
    return api_put(f"/articles/{article_id}", payload)


def get_article(article_id: str):
    return api_get(f"/articles/{article_id}")


def delete_article(article_id: str):
    return api_delete(f"/articles/{article_id}")


def get_history(article_id: str):
    return api_get(f"/articles/{article_id}/history")


def admin_list_users():
    return api_get("/admin/users")


def admin_update_roles(user_id: str, roles: list[str]):
    return api_post(f"/admin/users/{user_id}/roles", {"roles": roles})


def admin_reset_password(user_id: str, new_password: str):
    return api_post(
        f"/admin/users/{user_id}/password", {"new_password": new_password}
    )


def list_my_teams():
    return api_get("/teams/")


def create_team(name: str):
    return api_post("/teams/", {"name": name})


def invite_to_team(team_id: str, email: str):
    return api_post(f"/teams/{team_id}/invite", {"email": email})


def remove_from_team(team_id: str, user_id: str):
    return api_post(f"/teams/{team_id}/remove", {"user_id": user_id})


def switch_team(team_id: str):
    return api_post("/teams/switch", {"team_id": team_id})


def get_team(team_id: str):
    return api_get(f"/teams/{team_id}")


def fetch_groups() -> list[dict]:
    try:
        return api_get("/article-groups/flat")
    except Exception:
        return []


def build_group_options(
    groups: list[dict], include_none: str | None = None
) -> list[tuple[str | None, str]]:
    children: dict[str | None, list[dict]] = {}
    for g in groups:
        children.setdefault(g.get("parent_id"), []).append(g)
    result: list[tuple[str | None, str]] = []

    def visit(parent_id: str | None, level: int) -> None:
        for g in sorted(children.get(parent_id, []), key=lambda x: x.get("order") or 0):
            result.append((g["id"], "  " * level + g["name"]))
            visit(g["id"], level + 1)

    visit(None, 0)
    if include_none is not None:
        result = [(None, include_none)] + result
    return result


def fetch_group_options(include_none: str | None = None):
    groups = fetch_groups()
    return build_group_options(groups, include_none)


def render_sidebar_tree() -> None:
    try:
        tree = api_get("/article-groups/tree")
    except Exception:
        tree = []
    st.sidebar.markdown("### Разделы")

    def _open_article(art: dict) -> None:
        st.session_state.view_id = art["id"]
        try:
            st.session_state.view_article = get_article(art["id"])
            st.session_state.view_history = get_history(art["id"])
        except Exception:
            st.session_state.view_article = None
            st.session_state.view_history = None
        st.session_state.page = "Статья по ID"
        if hasattr(st, "rerun"):
            st.rerun()
        else:  # pragma: no cover - for older Streamlit versions
            st.experimental_rerun()

    def _article_button(art: dict) -> None:
        if st.button(art["title"], key=f"sb_{art['id']}"):
            _open_article(art)

    def show(nodes: list[dict]) -> None:
        for node in nodes:
            with st.sidebar.expander(node["name"], expanded=False):
                for art in node.get("articles", []):
                    _article_button(art)
                show(node.get("children", []))

    show(tree)

    try:
        articles = api_get("/articles/")
        ungrouped = [a for a in articles if not a.get("group_id")]
    except Exception:
        ungrouped = []
    if ungrouped:
        with st.sidebar.expander("Без группы", expanded=False):
            for art in ungrouped:
                _article_button(art)


def get_group_articles(group_id: str) -> list[dict]:
    try:
        tree = api_get("/article-groups/tree")
    except Exception:
        return []

    def find(nodes: list[dict]) -> dict | None:
        for n in nodes:
            if n.get("id") == group_id:
                return n
            res = find(n.get("children", []))
            if res:
                return res
        return None

    node = find(tree)
    return node.get("articles", []) if node else []


def suggest_related(
    title: str,
    content: str,
    exclude_id: str | None = None,
    top_k: int = 5,
    group_id: str | None = None,
):
    results = search_articles(f"{title}\n{content}", group_id=group_id)
    unique = []
    for hit in results:
        if exclude_id and hit["id"] == exclude_id:
            continue
        unique.append(hit)
    return unique[:top_k]


def llm_recommendations(
    title: str,
    content: str,
    group_id: str | None = None,
    prompt_template: str | None = None,
) -> str:
    if not (YANDEX_TOKEN and YANDEX_FOLDER_ID):
        return "LLM выключен: не заданы YANDEX_OAUTH_TOKEN / YANDEX_FOLDER_ID."

    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Bearer {YANDEX_TOKEN}",
        "Content-Type": "application/json",
    }

    articles_info = ""
    if group_id:
        titles = [a.get("title", "") for a in get_group_articles(group_id)][:5]
        if titles:
            articles_info = "\n\nДругие статьи в группе:\n" + "\n".join(
                f"- {t}" for t in titles
            )

    if prompt_template:
        try:
            prompt = prompt_template.format(
                title=title, content=content, group_articles=articles_info
            )
        except Exception:
            prompt = prompt_template

        if "{title}" not in prompt_template:
            prompt = f"Заголовок: {title}\n\n" + prompt
        if "{content}" not in prompt_template:
            prompt += f"\n\nТекст статьи:\n{content}"
        if "{group_articles}" not in prompt_template and articles_info:
            prompt += articles_info
    else:
        prompt = (
            "Ты – редактор и техписатель. Дай практичные рекомендации по улучшению статьи: "
            "структура, ясность, недостающие разделы, теги. Пиши кратко и по пунктам.\n\n"
            f"Заголовок: {title}\n\n"
            f"Текст статьи:\n{content}{articles_info}\n\n"
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

render_sidebar_tree()

# Allow other callbacks to request a page switch before the navigation widget
if st.session_state.get("pending_page"):
    st.session_state.page = st.session_state.pop("pending_page")

options: list[str] = []
if "author" in roles or "admin" in roles:
    options += ["Создать статью", "Редактировать статью"]
if any(r in roles for r in ["reader", "author", "admin"]):
    options += ["Поиск", "Статья по ID", "Команды"]
if "admin" in roles:
    options += ["Группы статей", "Панель администратора", "Диагностика"]
page = st.sidebar.radio("Навигация", options, key="page")

st.sidebar.markdown("---")
st.sidebar.caption(f"Backend: {API_BASE}")

# --- Создать ---
if page == "Создать статью":
    st.header("Создать статью")
    main_col, rec_col = st.columns([3, 2])

    groups_data = fetch_groups()
    group_prompt_map = {g["id"]: g.get("prompt_template") for g in groups_data}
    group_opts = build_group_options(groups_data, "Без группы")
    group_opts.append(("__new__", "Создать новую группу"))

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
                sel = st.session_state.get("create_group")
                g_id = None
                prompt = None
                if isinstance(sel, tuple) and sel[0] not in (None, "__new__"):
                    g_id = sel[0]
                    prompt = group_prompt_map.get(g_id)
                st.session_state.llm_tips = llm_recommendations(
                    title, content, g_id, prompt
                )
            st.session_state.llm_timer = None
            st.rerun()

        st.session_state.llm_timer = threading.Timer(1.5, run)
        add_script_run_ctx(st.session_state.llm_timer)
        st.session_state.llm_timer.start()

    with main_col:
        st.text_input("Заголовок", key="create_title", on_change=schedule_llm)
        st.text_input("Теги (через запятую)", key="create_tags")
        st.selectbox(
            "Группа",
            group_opts,
            format_func=lambda x: x[1],
            key="create_group",
        )
        if (
            isinstance(st.session_state.get("create_group"), tuple)
            and st.session_state["create_group"][0] == "__new__"
        ):
            st.text_input("Название новой группы", key="new_group_name")
        render_article_editor("create_content", "create_content_editor")
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("Сохранить статью", key="create_save"):
                st.session_state.create_submit = True
                st.rerun()

        if st.session_state.pop("create_submit", False):
            title_val = _state_str("create_title").strip()
            content_val = _state_str("create_content").strip()
            if not title_val or not content_val:
                st.error("Заполните заголовок и текст.")
            else:
                try:
                    tag_list = [
                        t.strip()
                        for t in _state_str("create_tags").split(",")
                        if t.strip()
                    ]
                    group_id = None
                    group = None
                    sel = st.session_state.get("create_group")
                    if isinstance(sel, tuple):
                        if sel[0] == "__new__":
                            name = _state_str("new_group_name").strip()
                            if not name:
                                st.error("Введите название группы")
                                st.stop()
                            group = {"name": name}
                        else:
                            group_id = sel[0]
                    res = create_article(title_val, content_val, tag_list, group_id, group)
                    st.success(f"Создано! ID: {res['id']}")
                    with st.expander("Похожие статьи сразу после сохранения"):
                        related = suggest_related(
                            title_val,
                            content_val,
                            exclude_id=res["id"],
                            top_k=5,
                            group_id=group_id,
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
                        sel = st.session_state.get("create_group")
                        g_id = None
                        prompt = None
                        if isinstance(sel, tuple) and sel[0] not in (None, "__new__"):
                            g_id = sel[0]
                            prompt = group_prompt_map.get(g_id)
                        st.session_state.llm_tips = llm_recommendations(
                            title_val, content_val, g_id, prompt
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
    groups_data = fetch_groups()
    group_prompt_map = {g["id"]: g.get("prompt_template") for g in groups_data}
    group_opts = build_group_options(groups_data, "Без группы")

    st.header("Редактировать статью")
    st.caption("Укажи ID статьи (можно взять из результата создания/поиска).")

    def _load_edit_article() -> None:
        art_id = st.session_state.get("edit_article_id", "").strip()
        if not art_id or st.session_state.get("edit_loaded_id") == art_id:
            return
        try:
            art = get_article(art_id)
            st.session_state["edit_title"] = art["title"]
            st.session_state["edit_tags"] = ", ".join(art.get("tags", []))
            st.session_state["edit_content"] = art["content"]
            for opt in group_opts:
                if opt[0] == art.get("group_id"):
                    st.session_state["edit_group"] = opt
                    break
            else:
                st.session_state["edit_group"] = group_opts[0]
            st.session_state["edit_loaded_id"] = art_id
        except Exception as e:
            st.error(str(e))

    article_id = st.text_input(
        "Article ID", key="edit_article_id", on_change=_load_edit_article
    )

    # If article ID was preset (e.g. from another page), load it automatically
    _load_edit_article()

    title = st.text_input("Новый заголовок", key="edit_title")
    tags = st.text_input("Новые теги (через запятую)", key="edit_tags")
    st.selectbox(
        "Группа",
        group_opts,
        format_func=lambda x: x[1],
        key="edit_group",
    )
    render_article_editor("edit_content", "edit_content_editor")

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("Сохранить изменения", key="edit_save"):
            st.session_state.edit_submit = True
            st.rerun()

    if st.session_state.pop("edit_submit", False):
        title_val = title.strip()
        content_val = _state_str("edit_content").strip()
        if not article_id.strip():
            st.error("Укажи ID статьи.")
        elif not title_val or not content_val:
            st.error("Заполни заголовок и текст.")
        else:
            try:
                tag_list = [t.strip() for t in tags.split(",") if t.strip()]
                group_id = None
                if isinstance(st.session_state.get("edit_group"), tuple):
                    group_id = st.session_state["edit_group"][0]
                res = update_article(
                    article_id.strip(), title_val, content_val, tag_list, group_id
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
                    sel = st.session_state.get("edit_group")
                    g_id = None
                    prompt = None
                    if isinstance(sel, tuple) and sel[0]:
                        g_id = sel[0]
                        prompt = group_prompt_map.get(g_id)
                    tips = llm_recommendations(
                        title.strip(), content_val, g_id, prompt
                    )
                st.markdown("### Рекомендации")
                st.markdown(tips)

    st.markdown("---")
    if st.button("Найти похожие статьи"):
        content_val = _state_str("edit_content").strip()
        if not title.strip() and not content_val:
            st.warning("Сначала заполни заголовок/текст — по ним ищем похожие.")
        else:
            sel = st.session_state.get("edit_group")
            g_id = None
            if isinstance(sel, tuple):
                g_id = sel[0]
            related = suggest_related(
                title,
                content_val,
                exclude_id=article_id.strip(),
                top_k=10,
                group_id=g_id,
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
    group_opts = fetch_group_options("Любая")
    selected_group = st.selectbox(
        "Группа",
        group_opts,
        format_func=lambda x: x[1],
        key="search_group",
    )
    topk = st.slider("Сколько результатов показать", 1, 20, 5)
    if st.button("Искать") and q.strip():
        try:
            tag_list = [t.strip() for t in tags_filter.split(",") if t.strip()]
            group_id = None
            if isinstance(selected_group, tuple):
                group_id = selected_group[0]
            results = search_articles(q.strip(), tag_list, group_id)[:topk]
            st.subheader("Результаты")
            for hit in results:
                st.write(f"**{hit['title']}** · score={hit.get('score'):.3f}")
                st.caption(f"{hit['id']} · теги: {', '.join(hit.get('tags', []))}")
                st.markdown(hit["content"], unsafe_allow_html=True)
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
            st.markdown(article["content"], unsafe_allow_html=True)
            st.caption(f"Теги: {', '.join(article.get('tags', []))}")
            if "author" in roles or "admin" in roles:
                if st.button("Редактировать"):
                    st.session_state.edit_article_id = article["id"]
                    st.session_state.pop("edit_loaded_id", None)
                    st.session_state.pending_page = "Редактировать статью"
                    st.rerun()
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

# --- Команды ---
elif page == "Команды":
    st.header("Мои команды")
    try:
        teams = list_my_teams()
    except Exception as e:
        st.error(str(e))
        teams = []
    current_team = st.session_state["user"].get("team_id")
    for t in teams:
        col1, col2 = st.columns([4, 1])
        label = f"**{t['name']}**"
        if t["id"] == current_team:
            label += " (активная)"
        col1.write(label)
        if t["id"] != current_team and col2.button("Перейти", key=f"switch_{t['id']}"):
            try:
                switch_team(t["id"])
                st.session_state["user"] = api_get("/auth/me")
                st.success("Команда переключена")
                st.rerun()
            except Exception as e:
                st.error(str(e))
        if t["id"] == current_team:
            try:
                detail = get_team(t["id"])
            except Exception as e:
                st.error(str(e))
                detail = {"users": []}
            st.subheader("Участники")
            for u in detail.get("users", []):
                c1, c2 = st.columns([4, 1])
                c1.write(u["email"])
                if u["id"] != st.session_state["user"]["id"] and c2.button(
                    "Удалить", key=f"rm_{u['id']}"
                ):
                    try:
                        remove_from_team(t["id"], u["id"])
                        st.success("Удален")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
            invite_email = st.text_input("Пригласить по email", key="invite_email")
            if st.button("Отправить приглашение") and invite_email.strip():
                try:
                    invite_to_team(t["id"], invite_email.strip())
                    st.success("Приглашение отправлено")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
    st.markdown("---")
    with st.form("create_team_form"):
        name = st.text_input("Название новой команды")
        submitted = st.form_submit_button("Создать")
    if submitted and name.strip():
        try:
            create_team(name.strip())
            st.session_state["user"] = api_get("/auth/me")
            st.success("Команда создана")
            st.rerun()
        except Exception as e:
            st.error(str(e))

# --- Группы статей ---
elif page == "Группы статей":
    st.header("Группы статей")
    groups = fetch_groups()
    parent_opts = build_group_options(groups, "Нет")

    with st.expander("Создать группу"):
        new_name = st.text_input("Название", key="adm_new_group_name")
        new_desc = st.text_area("Описание", key="adm_new_group_desc")
        parent_sel = st.selectbox(
            "Родитель",
            parent_opts,
            format_func=lambda x: x[1],
            key="adm_new_group_parent",
        )
        new_order = st.number_input("Порядок", value=0, key="adm_new_group_order")
        new_prompt = st.text_area("Prompt template", key="adm_new_group_prompt")
        if st.button("Создать", key="adm_new_group_btn"):
            payload = {
                "name": new_name,
                "description": new_desc,
                "parent_id": parent_sel[0] if isinstance(parent_sel, tuple) else None,
                "prompt_template": new_prompt,
                "order": int(new_order),
            }
            try:
                api_post("/article-groups/", payload)
                st.success("Группа создана")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    st.markdown("### Список групп")
    for g in groups:
        with st.expander(g["name"], expanded=False):
            name = st.text_input(
                "Название", value=g["name"], key=f"grp_name_{g['id']}"
            )
            desc = st.text_area(
                "Описание", value=g.get("description") or "", key=f"grp_desc_{g['id']}"
            )
            parent_index = next(
                (i for i, opt in enumerate(parent_opts) if opt[0] == g.get("parent_id")),
                0,
            )
            parent_sel = st.selectbox(
                "Родитель",
                parent_opts,
                format_func=lambda x: x[1],
                index=parent_index,
                key=f"grp_parent_{g['id']}",
            )
            order = st.number_input(
                "Порядок", value=g.get("order") or 0, key=f"grp_order_{g['id']}"
            )
            prompt = st.text_area(
                "Prompt template",
                value=g.get("prompt_template") or "",
                key=f"grp_prompt_{g['id']}",
            )
            if st.button("Сохранить", key=f"grp_save_{g['id']}"):
                payload = {
                    "name": name,
                    "description": desc,
                    "parent_id": parent_sel[0] if isinstance(parent_sel, tuple) else None,
                    "prompt_template": prompt,
                    "order": int(order),
                }
                try:
                    api_put(f"/article-groups/{g['id']}", payload)
                    st.success("Сохранено")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
            if st.button("Удалить", key=f"grp_del_{g['id']}"):
                try:
                    api_delete(f"/article-groups/{g['id']}")
                    st.warning("Удалено")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

            st.markdown("#### Тестовый промт")
            test_title = st.text_input(
                "Заголовок", key=f"grp_test_title_{g['id']}"
            )
            test_content = st.text_area(
                "Текст", key=f"grp_test_content_{g['id']}"
            )
            if st.button("Проверить", key=f"grp_test_btn_{g['id']}"):
                with st.spinner("LLM..."):
                    st.session_state[f"grp_test_res_{g['id']}"] = llm_recommendations(
                        test_title, test_content, g["id"], prompt
                    )
            test_res = st.session_state.get(f"grp_test_res_{g['id']}")
            if test_res:
                st.markdown(test_res)

# --- Панель администратора ---
elif page == "Панель администратора":
    st.header("Панель администратора")
    try:
        users = admin_list_users()
    except Exception as e:
        st.error(str(e))
        users = []
    if users:
        header_cols = st.columns([3, 3, 3, 2])
        header_cols[0].write("Email")
        header_cols[1].write("Роли")
        header_cols[2].write("Новый пароль")
        header_cols[3].write("Действие")
        for u in users:
            c1, c2, c3, c4 = st.columns([3, 3, 3, 2])
            c1.write(u["email"])
            disabled = u["id"] == st.session_state["user"]["id"]
            roles_sel = c2.multiselect(
                "Roles",
                ["admin", "author", "reader"],
                default=u.get("roles", []),
                key=f"roles_{u['id']}",
                disabled=disabled,
                label_visibility="collapsed",
            )
            new_pass = c3.text_input(
                "New password",
                type="password",
                key=f"pwd_{u['id']}",
                disabled=disabled,
                label_visibility="collapsed",
            )
            if c4.button("Сохранить", key=f"save_{u['id']}", disabled=disabled):
                try:
                    admin_update_roles(u["id"], roles_sel)
                    if new_pass:
                        admin_reset_password(u["id"], new_pass)
                    st.success("Обновлено")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
    else:
        st.info("Нет пользователей")

# --- Диагностика ---
elif page == "Диагностика":
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
