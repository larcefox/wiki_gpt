# Wiki GPT

Приложение для ведения вики-статей с полнотекстовым и семантическим поиском, основанным на YandexGPT и векторной базе Qdrant.

## Стек

- **Backend**: [FastAPI](https://fastapi.tiangolo.com/) + PostgreSQL (SQLAlchemy)
- **Векторный поиск**: YandexGPT embeddings, [Qdrant](https://qdrant.tech/)
- **Frontend**: [Streamlit](https://streamlit.io/)
- **Инфраструктура**: Docker Compose (PostgreSQL, Qdrant, backend, frontend)

## Запуск через Docker

1. Создайте файл `.env` рядом с `docker-compose.yml` и задайте переменные:

   ```env
   YANDEX_OAUTH_TOKEN=<токен доступа Yandex>
   YANDEX_FOLDER_ID=<id каталога Yandex Cloud>
   QDRANT_URL=http://qdrant:6333
   ```

2. Соберите и запустите контейнеры:

   ```bash
   docker-compose up --build
   ```

   После запуска backend доступен по адресу `http://localhost:8000` (документация `http://localhost:8000/docs`),
   frontend — `http://localhost:8501`.

## Разработка локально

- Backend запускается командой `uvicorn main:app --reload` из каталога `backend`.
- Frontend запускается командой `streamlit run streamlit_app.py` из каталога `frontend`.

## Возможности

- Создание, редактирование и удаление статей
- Версионирование и просмотр истории изменений
- Семантический поиск и ранжирование с помощью YandexGPT

## Аутентификация

Backend поддерживает регистрацию и вход по электронной почте и паролю с выдачей JWT (access и refresh) и проверкой ролей.
Доступные роли: `admin`, `author`, `reader`.
Основные эндпоинты:

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/refresh`

Защищённые маршруты требуют корректного access‑токена и соответствующей роли.
