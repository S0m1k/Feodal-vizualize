# Rstone — визуализатор материалов

FastAPI-приложение: клиентский визуализатор (`index.html`), панель сотрудников и админка (`/dashboard`), генерация через GenAPI (Nano Banana 2).

## Требования

- Python 3.10+
- Redis (лимиты клиента и статусы генерации)

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

В `.env` укажите `GEN_API_KEY` и при необходимости `PUBLIC_BASE_URL` (публичный URL сервера, если GenAPI должен скачивать ваши `temp/` и текстуры по ссылке).

## Запуск

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

- Вход сотрудников: `http://localhost:8000/login`
- Дашборд: `http://localhost:8000/dashboard`
- Статический клиентский блок: откройте `index.html` или раздавайте через свой хостинг (в `index.html` задайте `API_BASE`).

## GitHub

Файл `.env` в репозиторий не коммитится. После клонирования скопируйте `.env.example` в `.env` и заполните ключи.
